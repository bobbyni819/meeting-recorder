"""macOS recording orchestration.

This module is intentionally small and platform glue only: it captures
normalized PCM frames from the platform_support backends, writes the canonical
recording files, then hands the directory to the shared headless recovery
pipeline for all post-processing.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

from meeting_recorder import recovery
from meeting_recorder.config import Config
from meeting_recorder.platform_support import factory
from meeting_recorder.platform_support.base import AudioSource, ScreenTarget
from meeting_recorder.storage.metadata import RecordingMetadata
from meeting_recorder.storage.recording_store import RecordingStore

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2


class _WavWriter:
    """Drain one AudioSource into a canonical WAV file."""

    def __init__(
        self,
        source: AudioSource,
        output_path: Path,
        label: str,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
    ):
        self.source = source
        self.output_path = Path(output_path)
        self.label = label
        self.sample_rate = sample_rate
        self.channels = channels
        self.bytes_written = 0
        self.error_message = ""
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"macos-{label}-wav-writer",
            daemon=False,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("%s WAV writer did not stop within %.0fs", self.label, timeout)

    @property
    def has_audio(self) -> bool:
        return self.bytes_written > 0 and self.output_path.exists()

    def _run(self) -> None:
        wav_file = None
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            wav_file = wave.open(str(self.output_path), "wb")
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(self.sample_rate)
            logger.info("macOS WAV writer started: %s", self.output_path.name)

            while True:
                frame = self.source.get_frame(timeout=0.1)
                if frame:
                    wav_file.writeframes(frame)
                    self.bytes_written += len(frame)
                    continue
                if self._stop_event.is_set():
                    break
        except Exception as exc:
            self.error_message = str(exc)
            logger.exception("macOS %s WAV writer failed", self.label)
        finally:
            if wav_file is not None:
                try:
                    wav_file.close()
                except Exception:
                    logger.debug("Error closing macOS %s WAV file", self.label, exc_info=True)
            logger.info(
                "macOS WAV writer finished: %s (%d bytes)",
                self.output_path.name,
                self.bytes_written,
            )


class MacRecordingSession:
    """Runnable macOS recording session backed by platform_support adapters."""

    def __init__(self):
        self.config: Optional[Config] = None
        self.recording_dir: Optional[Path] = None
        self.metadata: Optional[RecordingMetadata] = None
        self.start_time: Optional[datetime] = None
        self._monotonic_start: Optional[float] = None
        self._mic_source: Optional[AudioSource] = None
        self._system_source: Optional[AudioSource] = None
        self._screen_recorder = None
        self._writers: list[_WavWriter] = []
        self._post_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
        self._stopping = False

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_processing(self) -> bool:
        return self._post_thread is not None and self._post_thread.is_alive()

    def start(self, subject: str = "") -> Path:
        """Start a macOS recording and return the created recording directory."""
        with self._lock:
            if self._running or self._stopping:
                raise RuntimeError("Recording already in progress")
            self._running = True

        try:
            config = Config.load()
            self.config = config
            recording_dir = RecordingStore(config.output_dir).create_recording_dir(
                "Meeting",
                subject,
            )
            self.recording_dir = recording_dir
            self.start_time = datetime.now()
            self._monotonic_start = time.monotonic()
            self.metadata = RecordingMetadata(
                app_name="Meeting",
                app_pid=os.getpid(),
                start_time=self.start_time.isoformat(),
                sample_rate=SAMPLE_RATE,
                channels=CHANNELS,
                language=config.recording.language,
                transcription_backend=config.transcription.backend,
                meeting_subject=subject,
                status="recording",
            )
            self.metadata.save(recording_dir)

            backends = factory.get_backends()
            self._start_audio_sources(backends, config, recording_dir)
            self._start_screen_recording(backends, config, recording_dir)

            if not self._writers:
                raise RuntimeError("No macOS audio sources started")

            logger.info("macOS recording started: %s", recording_dir)
            return recording_dir
        except Exception:
            logger.exception("Failed to start macOS recording")
            self._cleanup_after_start_failure()
            with self._lock:
                self._running = False
            raise

    def stop(self) -> Optional[Path]:
        """Stop capture, save metadata, and start shared post-processing."""
        with self._lock:
            if not self._running:
                return self.recording_dir
            self._running = False
            self._stopping = True

        recording_dir = self.recording_dir
        config = self.config
        metadata = self.metadata
        stop_time = datetime.now()
        stop_monotonic = time.monotonic()
        try:
            self._stop_audio_sources()
            self._stop_writers()
            self._stop_screen_recorder()

            if recording_dir is None or config is None or metadata is None:
                return recording_dir

            duration = (
                stop_monotonic - self._monotonic_start
                if self._monotonic_start is not None
                else 0.0
            )
            metadata.end_time = stop_time.isoformat()
            metadata.duration_seconds = max(0.0, duration)
            metadata.has_app_audio = _has_wav_payload(recording_dir / "app_audio.wav")
            metadata.has_mic_audio = _has_wav_payload(recording_dir / "mic_audio.wav")
            metadata.has_screen_recording = _has_file_payload(recording_dir / "screen.mp4")
            metadata.status = "processing"
            metadata.error_message = ""
            metadata.save(recording_dir)

            self._start_post_processing(recording_dir, copy.deepcopy(config))
            logger.info("macOS recording stopped: %s", recording_dir)
            return recording_dir
        finally:
            self._mic_source = None
            self._system_source = None
            self._screen_recorder = None
            self._writers = []
            self.metadata = None
            self.config = None
            self.start_time = None
            self._monotonic_start = None
            with self._lock:
                self._stopping = False

    def _start_audio_sources(self, backends, config: Config, recording_dir: Path) -> None:
        mic_kwargs = {
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "chunk_duration_ms": config.audio.chunk_duration_ms,
        }
        mic_device = _parse_device_index(config.audio.mic_device)
        if mic_device is not None:
            mic_kwargs["device_index"] = mic_device

        try:
            self._mic_source = backends.mic_audio_source(**mic_kwargs)
            self._mic_source.start()
            mic_writer = _WavWriter(
                self._mic_source,
                recording_dir / "mic_audio.wav",
                "mic",
            )
            mic_writer.start()
            self._writers.append(mic_writer)
        except Exception:
            self._mic_source = None
            logger.exception("macOS microphone capture unavailable")

        try:
            self._system_source = backends.app_audio_source(
                sample_rate=SAMPLE_RATE,
                channels=CHANNELS,
                chunk_duration_ms=config.audio.chunk_duration_ms,
            )
            self._system_source.start()
            system_writer = _WavWriter(
                self._system_source,
                recording_dir / "app_audio.wav",
                "app",
            )
            system_writer.start()
            self._writers.append(system_writer)
        except Exception:
            self._system_source = None
            logger.warning(
                "macOS system audio capture unavailable; continuing without "
                "app_audio.wav. Install/configure BlackHole for system audio.",
                exc_info=True,
            )

    def _start_screen_recording(self, backends, config: Config, recording_dir: Path) -> None:
        if not config.screen_recording.enabled:
            return
        try:
            self._screen_recorder = backends.screen_recorder()
            target = ScreenTarget(
                pid=os.getpid(),
                process_name="Meeting Recorder",
            )
            self._screen_recorder.start(
                target,
                recording_dir / "screen.mp4",
                fps=config.screen_recording.fps,
                quality=config.screen_recording.quality,
            )
        except Exception:
            self._screen_recorder = None
            logger.exception("macOS screen recording unavailable; continuing")

    def _stop_screen_recorder(self) -> None:
        if self._screen_recorder is None:
            return
        try:
            self._screen_recorder.stop()
        except Exception:
            logger.exception("Failed to stop macOS screen recorder")

    def _stop_audio_sources(self) -> None:
        for source in (self._system_source, self._mic_source):
            if source is None:
                continue
            try:
                source.stop()
            except Exception:
                logger.debug("Error stopping macOS audio source", exc_info=True)
            try:
                source.close()
            except Exception:
                logger.debug("Error closing macOS audio source", exc_info=True)

    def _stop_writers(self) -> None:
        for writer in self._writers:
            writer.stop()

    def _start_post_processing(self, recording_dir: Path, config: Config) -> None:
        def _run() -> None:
            try:
                recovery.reprocess_headless(recording_dir, config)
                logger.info("macOS post-processing complete: %s", recording_dir)
            except Exception as exc:
                logger.exception("macOS post-processing failed: %s", recording_dir)
                try:
                    metadata = RecordingMetadata.load(recording_dir)
                except Exception:
                    metadata = RecordingMetadata()
                metadata.set_error(str(exc), recording_dir)

        self._post_thread = threading.Thread(
            target=_run,
            name="macos-post-processing",
            daemon=False,
        )
        self._post_thread.start()

    def _cleanup_after_start_failure(self) -> None:
        try:
            self._stop_screen_recorder()
            self._stop_audio_sources()
            self._stop_writers()
        finally:
            if self.recording_dir is not None and self.metadata is not None:
                self.metadata.set_error("Failed to start macOS recording", self.recording_dir)
            self._mic_source = None
            self._system_source = None
            self._screen_recorder = None
            self._writers = []


def _parse_device_index(value: str) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning(
            "Ignoring non-numeric macOS mic_device %r; use a sounddevice input index",
            value,
        )
        return None


def _has_file_payload(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def _has_wav_payload(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 44
    except OSError:
        return False
