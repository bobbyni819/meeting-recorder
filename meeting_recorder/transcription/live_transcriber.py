"""Real-time transcription preview using a lightweight whisper model.

Two-tier design: this module produces a fast rough draft during the
meeting (tier-selected local Whisper model); the post-hoc pipeline still
produces the accurate permanent transcript afterwards. Audio can be fed from multiple
sources ("app" = remote participants, "mic" = the user); each source is
transcribed from its own rolling buffer and stable text is merged into
one accumulated, speaker-labelled live transcript.

Stability model: each cycle re-transcribes the rolling window, so recent
text can still change on the next pass. Segments that end more than
``stability_margin`` seconds before the newest audio are considered
stable, appended to the accumulated transcript (and the live transcript
file), and never re-emitted; newer text is shown as a provisional tail.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Default buffer: last 10 seconds of audio at 16kHz mono int16
DEFAULT_BUFFER_SECONDS = 10
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_TRANSCRIBE_INTERVAL = 3.0  # seconds between transcription attempts
# Text ending this many seconds before the newest audio is committed.
DEFAULT_STABILITY_MARGIN = 3.0
# How often the cheap local concept extraction runs.
DEFAULT_INSIGHT_INTERVAL = 20.0
# Max committed entries kept in memory for the on-screen display. The full
# transcript is always written to live_transcript.txt; this only bounds the
# rolling display so a long meeting can't grow memory unbounded.
_MAX_COMMITTED = 400

# Display labels per audio source.
SOURCE_LABELS = {"app": "Them", "mic": "You"}


@dataclass
class _SourceState:
    """Rolling buffer and commit watermark for one audio source."""

    buffer: deque = field(default_factory=deque)
    buffer_samples: int = 0
    total_samples: int = 0  # samples ever fed (stream position)
    last_transcribed_total: int = 0
    watermark: float = 0.0  # absolute stream seconds committed so far
    provisional: str = ""


class LiveTranscriber:
    """Background live transcription of one or more audio streams.

    Buffers recent audio chunks per source and periodically runs a
    lightweight whisper model to produce an accumulating, speaker-
    labelled preview transcript during recording.
    """

    def __init__(
        self,
        on_transcript: Optional[Callable[[str], None]] = None,
        model_size: str = "tiny",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
        buffer_seconds: float = DEFAULT_BUFFER_SECONDS,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        transcribe_interval: float = DEFAULT_TRANSCRIBE_INTERVAL,
        output_path: Optional[Path] = None,
        on_insight: Optional[Callable[[dict], None]] = None,
        stability_margin: float = DEFAULT_STABILITY_MARGIN,
        insight_interval: float = DEFAULT_INSIGHT_INTERVAL,
        should_transcribe: Optional[Callable[[], bool]] = None,
    ):
        """
        Args:
            on_transcript: Callback receiving the latest display text
                (accumulated tail + provisional, speaker-labelled).
            model_size: Whisper model size for live preview.
            device: Device for inference.
            compute_type: Compute type for the model.
            language: Language code for transcription.
            buffer_seconds: How many seconds of recent audio to keep per source.
            sample_rate: Audio sample rate (must be 16kHz for whisper).
            transcribe_interval: Seconds between transcription attempts.
            output_path: Optional live_transcript.txt path; stable lines are
                appended as they are committed.
            on_insight: Optional callback receiving concept-extraction events
                ({"type": "topic", ...} or {"type": "keyword", ...}).
            stability_margin: Seconds behind the newest audio at which text
                is considered stable and committed.
            insight_interval: Seconds between concept-extraction passes.
        """
        self._on_transcript = on_transcript
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._buffer_seconds = buffer_seconds
        self._sample_rate = sample_rate
        self._transcribe_interval = transcribe_interval
        self._output_path = Path(output_path) if output_path else None
        self._on_insight = on_insight
        self._stability_margin = stability_margin
        self._insight_interval = insight_interval
        # Returns False when the recording is under load and live
        # transcription should skip a cycle (recording always wins).
        self._should_transcribe = should_transcribe

        self._max_samples = int(buffer_seconds * sample_rate)
        self._buffer_lock = threading.Lock()
        self._sources: dict[str, _SourceState] = {"app": _SourceState()}

        # Committed (stable) entries across all sources: (abs_sec, source, text)
        self._committed: list[tuple[float, str, str]] = []
        self._committed_lock = threading.Lock()

        self._model = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_transcript = ""
        self._transcript_lock = threading.Lock()

        self._file_write_failed = False
        self._file_retry_interval = 30.0
        self._file_retry_after = 0.0
        self._last_insight_time = 0.0
        self._current_topic: Optional[str] = None
        self._alerted_keywords: set[str] = set()

    # -- Backward-compatible single-source views (default source "app") ----

    @property
    def _buffer(self) -> deque:
        return self._sources["app"].buffer

    @property
    def _buffer_samples(self) -> int:
        return self._sources["app"].buffer_samples

    def start(self) -> None:
        """Start the live transcription background thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Live transcriber already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._transcription_loop,
            name="live-transcriber",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Live transcriber started (model=%s, interval=%.1fs).",
            self._model_size, self._transcribe_interval,
        )

    def stop(self) -> None:
        """Stop the live transcription thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                logger.warning("Live transcriber thread did not terminate.")
            self._thread = None
        self._model = None
        logger.info("Live transcriber stopped.")

    def feed_audio(self, audio_bytes: bytes, source: str = "app") -> None:
        """Feed a chunk of int16 PCM audio into a source's buffer.

        Args:
            audio_bytes: Raw 16-bit PCM audio bytes (16kHz mono).
            source: Which stream this audio belongs to ("app" or "mic").
        """
        chunk_samples = len(audio_bytes) // 2  # int16 = 2 bytes per sample
        with self._buffer_lock:
            state = self._sources.get(source)
            if state is None:
                state = _SourceState()
                self._sources[source] = state
            state.buffer.append(audio_bytes)
            state.buffer_samples += chunk_samples
            state.total_samples += chunk_samples

            # Trim oldest chunks to stay within buffer limit
            while state.buffer_samples > self._max_samples and state.buffer:
                oldest = state.buffer.popleft()
                state.buffer_samples -= len(oldest) // 2

    @property
    def last_transcript(self) -> str:
        """The most recent live display text."""
        with self._transcript_lock:
            return self._last_transcript

    @property
    def accumulated_text(self) -> str:
        """All stable (committed) transcript text, chronological, unlabelled."""
        with self._committed_lock:
            entries = sorted(self._committed)
        return " ".join(text for _, _, text in entries)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _get_buffer_audio(self, source: str = "app") -> Optional[np.ndarray]:
        """Get a source's current buffer contents as a numpy array."""
        audio, _total = self._snapshot(source)
        return audio

    def _snapshot(
        self, source: str = "app",
    ) -> tuple[Optional[np.ndarray], int]:
        """Atomically read a source's buffer and total stream position."""
        with self._buffer_lock:
            state = self._sources.get(source)
            if state is None or not state.buffer:
                return None, state.total_samples if state else 0
            all_bytes = b"".join(state.buffer)
            total = state.total_samples

        if len(all_bytes) < 2:
            return None, total
        return np.frombuffer(all_bytes, dtype=np.int16), total

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        """Convert int16 numpy array to WAV-format bytes in memory."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    def _load_model(self) -> None:
        """Load the whisper model for live transcription.

        Tries the configured device first (usually CUDA), then falls back to
        CPU/int8 if the GPU load fails (driver/cuDNN mismatch, VRAM) so a GPU
        hiccup degrades to slower live preview instead of none at all.
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("faster-whisper not installed. Live transcription unavailable.")
            self._stop_event.set()
            return

        requested_model = (self._model_size or "tiny").strip() or "tiny"
        attempts = [(self._device, self._compute_type)]
        if self._device != "cpu":
            attempts.append(("cpu", "int8"))

        model_candidates = [requested_model]
        if requested_model != "tiny":
            model_candidates.append("tiny")

        for model_index, model_size in enumerate(model_candidates):
            if model_index == 1:
                logger.warning(
                    "Requested live transcription model %r failed on all "
                    "devices; falling back to 'tiny'.",
                    requested_model,
                )
            for device, compute_type in attempts:
                try:
                    logger.info(
                        "Loading live transcription model: %s on %s (%s)",
                        model_size, device, compute_type,
                    )
                    self._model = WhisperModel(
                        model_size, device=device, compute_type=compute_type,
                    )
                    self._model_size = model_size
                    self._device, self._compute_type = device, compute_type
                    logger.info("Live transcription model loaded on %s.", device)
                    return
                except Exception:
                    on_last_device = (device, compute_type) == attempts[-1]
                    has_model_fallback = model_index == 0 and requested_model != "tiny"
                    if on_last_device and has_model_fallback:
                        next_step = "trying tiny fallback"
                    elif device != "cpu":
                        next_step = "trying CPU"
                    else:
                        next_step = "giving up"
                    logger.warning(
                        "Live model %s load failed on %s; %s",
                        model_size, device, next_step,
                        exc_info=True,
                    )
        self._stop_event.set()

    def _transcription_loop(self) -> None:
        """Background loop that periodically transcribes the audio buffers."""
        self._load_model()
        if self._model is None:
            return

        while not self._stop_event.is_set():
            # Wait for the next transcription interval
            self._stop_event.wait(self._transcribe_interval)
            if self._stop_event.is_set():
                break

            # Backpressure: the live preview is best-effort and must never
            # starve the audio WRITER. If the recording is falling behind
            # (ring buffers backing up), skip this GPU cycle entirely so the
            # writer threads get the CPU/GIL/GPU back.
            if self._should_transcribe is not None:
                try:
                    if not self._should_transcribe():
                        continue
                except Exception:
                    pass

            newly_committed: list[tuple[float, str, str]] = []
            with self._buffer_lock:
                source_names = list(self._sources)
            for source in source_names:
                try:
                    newly_committed.extend(self._transcribe_source(source))
                except Exception:
                    logger.warning(
                        "Live transcription error (non-fatal)", exc_info=True,
                    )

            if newly_committed:
                with self._committed_lock:
                    self._committed.extend(newly_committed)
                    # Bound in-memory history (the full transcript is in the
                    # live_transcript.txt file); keep the recent window for
                    # the on-screen display.
                    if len(self._committed) > _MAX_COMMITTED:
                        del self._committed[:-_MAX_COMMITTED]
                self._append_to_file(newly_committed)

            display = self._build_display_text()
            with self._transcript_lock:
                self._last_transcript = display
            if display and self._on_transcript:
                try:
                    self._on_transcript(display)
                except Exception:
                    logger.exception("on_transcript callback error")

            self._maybe_run_insights(newly_committed)

        self._final_flush()

    def _final_flush(self) -> None:
        """Commit any remaining provisional text when the recording stops."""
        final: list[tuple[float, str, str]] = []
        with self._buffer_lock:
            source_names = list(self._sources)
        for source in source_names:
            try:
                final.extend(self._transcribe_source(source, force=True))
            except Exception:
                logger.warning("Live transcript final flush failed", exc_info=True)
        if final:
            with self._committed_lock:
                self._committed.extend(final)
            self._append_to_file(final)
            display = self._build_display_text()
            with self._transcript_lock:
                self._last_transcript = display

    def _transcribe_source(
        self, source: str, force: bool = False,
    ) -> list[tuple[float, str, str]]:
        """Transcribe one source's window; return newly stable entries.

        With ``force`` (final flush at stop), the no-new-audio skip and the
        stability horizon are both bypassed so the tail is not lost.
        """
        with self._buffer_lock:
            state = self._sources[source]
            no_new_audio = state.total_samples == state.last_transcribed_total

        audio, total = self._snapshot(source)
        if audio is None or len(audio) < self._sample_rate:
            # Less than 1 second of audio, skip
            return []
        if no_new_audio and not force:
            return []  # silent source (e.g. nobody talking): save the CPU

        with self._buffer_lock:
            state.last_transcribed_total = total

        wav_bytes = self._audio_to_wav_bytes(audio)
        segments_gen, _ = self._model.transcribe(
            io.BytesIO(wav_bytes),
            language=self._language,
            beam_size=1,  # Fast, lower quality OK for preview
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=100,
            ),
        )

        buffer_start_sec = (total - len(audio)) / self._sample_rate
        stream_sec = total / self._sample_rate
        horizon = stream_sec + 1.0 if force else stream_sec - self._stability_margin
        # A segment whose start will scroll out of the buffer before the
        # next pass gets one last chance to commit — otherwise a single
        # long segment (continuous speech) would be lost entirely.
        scroll_deadline = buffer_start_sec + self._transcribe_interval

        committed: list[tuple[float, str, str]] = []
        provisional: list[str] = []
        for seg in segments_gen:
            text = seg.text.strip()
            if not text:
                continue
            try:
                abs_start = buffer_start_sec + float(seg.start)
                abs_end = buffer_start_sec + float(seg.end)
            except (TypeError, ValueError):
                # No usable timestamps (shouldn't happen with faster-whisper):
                # show as provisional, never commit.
                provisional.append(text)
                continue
            if abs_end <= state.watermark + 0.05:
                continue  # already committed in an earlier window
            if abs_start < state.watermark - 0.25:
                # Re-transcription of mostly-committed audio (e.g. a long
                # segment that grew a longer tail): committing again would
                # duplicate the text, so skip — the post-hoc transcript is
                # the accurate record.
                continue
            if abs_end <= horizon or abs_start <= scroll_deadline:
                committed.append((abs_start, source, text))
                state.watermark = max(state.watermark, abs_end)
            else:
                provisional.append(text)

        state.provisional = " ".join(provisional)
        return committed

    def _build_display_text(self, tail_entries: int = 60) -> str:
        """Merged display text: recent stable entries + provisional tails.

        Consecutive entries from the same source share one label, e.g.
        ``[Them] hello there [You] hi how are you``. Single-source
        recordings get no labels at all.
        """
        with self._committed_lock:
            entries = sorted(self._committed)[-tail_entries:]
        with self._buffer_lock:
            provisionals = [
                (source, state.provisional)
                for source, state in self._sources.items()
                if state.provisional
            ]
        multi_source = (
            len({s for _, s, _ in entries} | {s for s, _ in provisionals}) > 1
        )

        parts: list[str] = []
        last_source: Optional[str] = None
        for _, source, text in entries:
            if multi_source and source != last_source:
                parts.append(f"[{SOURCE_LABELS.get(source, source)}]")
                last_source = source
            parts.append(text)
        for source, text in provisionals:
            if multi_source and source != last_source:
                parts.append(f"[{SOURCE_LABELS.get(source, source)}]")
                last_source = source
            parts.append(text)
        return " ".join(parts)

    def _append_to_file(
        self, entries: list[tuple[float, str, str]],
    ) -> None:
        """Append newly stable lines to the live transcript file."""
        if self._output_path is None:
            return
        if self._file_write_failed:
            if time.monotonic() < self._file_retry_after:
                return
            if self._recover_file_write():
                return
            return
        try:
            with open(self._output_path, "a", encoding="utf-8") as f:
                for abs_sec, source, text in sorted(entries):
                    f.write(self._format_file_entry(abs_sec, source, text))
        except OSError:
            self._mark_file_write_failed()

    @staticmethod
    def _format_file_entry(abs_sec: float, source: str, text: str) -> str:
        minutes, seconds = divmod(int(abs_sec), 60)
        label = SOURCE_LABELS.get(source, source)
        return f"[{minutes:02d}:{seconds:02d}] {label}: {text}\n"

    def _recover_file_write(self) -> bool:
        """Rewrite the live transcript file from the retained committed window."""
        with self._committed_lock:
            entries = sorted(self._committed)
        try:
            with open(self._output_path, "w", encoding="utf-8") as f:
                for abs_sec, source, text in entries:
                    f.write(self._format_file_entry(abs_sec, source, text))
        except OSError:
            self._file_retry_after = time.monotonic() + self._file_retry_interval
            return False

        # Entries aged out of the bounded committed window during the outage
        # are unrecoverable; this backfill is best-effort.
        self._file_write_failed = False
        self._file_retry_after = 0.0
        return True

    def _mark_file_write_failed(self) -> None:
        first_failure = not self._file_write_failed
        self._file_write_failed = True
        self._file_retry_after = time.monotonic() + self._file_retry_interval
        if first_failure:
            self._surface_file_write_failure()

    def _surface_file_write_failure(self) -> None:
        warning_key = "live_transcript_write_failed"
        message = f"Cannot write live transcript file {self._output_path}"
        on_health_warning = getattr(self, "_on_health_warning", None)
        if callable(on_health_warning):
            try:
                on_health_warning(warning_key)
            except Exception:
                logger.exception("on_health_warning callback error")
            return
        if self._on_insight is not None:
            self._emit_insight({
                "type": "warning",
                "key": warning_key,
                "message": message,
                "path": str(self._output_path),
            })
            return
        logger.warning("%s", message, exc_info=True)

    # -- Cheap local concept extraction (no model, no network) -------------

    def _maybe_run_insights(
        self, newly_committed: list[tuple[float, str, str]],
    ) -> None:
        """Run keyword/topic extraction on the accumulated text periodically."""
        if self._on_insight is None:
            return
        now = time.monotonic()
        if now - self._last_insight_time < self._insight_interval:
            return
        self._last_insight_time = now
        try:
            self._detect_topic()
            if newly_committed:
                self._check_watched_keywords(
                    " ".join(text for _, _, text in newly_committed)
                )
        except Exception:
            logger.debug("Live insight extraction failed", exc_info=True)

    def _detect_topic(self) -> None:
        """Match recent stable text against the shared topic keyword sets."""
        try:
            from meeting_recorder.storage.auto_tag import _TOPIC_PATTERNS
        except Exception:
            return
        recent = self.accumulated_text[-1500:].lower()
        if not recent:
            return
        best_topic, best_hits = None, 0
        for topic, keywords in _TOPIC_PATTERNS.items():
            hits = sum(1 for kw in keywords if kw in recent)
            if hits > best_hits:
                best_topic, best_hits = topic, hits
        # Require at least two distinct keyword hits before claiming a topic
        if best_hits >= 2 and best_topic != self._current_topic:
            self._current_topic = best_topic
            self._emit_insight({"type": "topic", "topic": best_topic})

    def _check_watched_keywords(self, new_text: str) -> None:
        """Alert (once per keyword per recording) on watchlist hits."""
        try:
            from meeting_recorder.storage.keyword_alerts import (
                load_watched_keywords,
            )
        except Exception:
            return
        lowered = new_text.lower()
        for keyword in load_watched_keywords():
            kw = keyword.lower().strip()
            if not kw or kw in self._alerted_keywords:
                continue
            if re.search(rf"\b{re.escape(kw)}\b", lowered):
                self._alerted_keywords.add(kw)
                self._emit_insight(
                    {"type": "keyword", "keyword": keyword, "context": new_text}
                )

    def _emit_insight(self, event: dict) -> None:
        try:
            self._on_insight(event)
        except Exception:
            logger.exception("on_insight callback error")

    @property
    def current_topic(self) -> Optional[str]:
        """The most recently detected discussion topic, if any."""
        return self._current_topic

    def clear_buffer(self) -> None:
        """Clear all audio buffers and reset transcript state."""
        with self._buffer_lock:
            for state in self._sources.values():
                state.buffer.clear()
                state.buffer_samples = 0
                state.provisional = ""
        with self._committed_lock:
            self._committed.clear()
        with self._transcript_lock:
            self._last_transcript = ""
