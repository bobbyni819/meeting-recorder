"""Real-time transcription preview using a lightweight whisper model."""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from collections import deque
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Default buffer: last 10 seconds of audio at 16kHz mono int16
DEFAULT_BUFFER_SECONDS = 10
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_TRANSCRIBE_INTERVAL = 3.0  # seconds between transcription attempts


class LiveTranscriber:
    """Background live transcription of audio stream.

    Buffers recent audio chunks and periodically runs a lightweight
    whisper model to produce preview transcriptions during recording.
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
    ):
        """
        Args:
            on_transcript: Callback receiving the latest transcript text.
            model_size: Whisper model size for live preview (tiny recommended).
            device: Device for inference (cpu recommended for live to not compete with GPU recording).
            compute_type: Compute type for the model.
            language: Language code for transcription.
            buffer_seconds: How many seconds of recent audio to keep.
            sample_rate: Audio sample rate (must be 16kHz for whisper).
            transcribe_interval: Seconds between transcription attempts.
        """
        self._on_transcript = on_transcript
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._buffer_seconds = buffer_seconds
        self._sample_rate = sample_rate
        self._transcribe_interval = transcribe_interval

        self._max_samples = int(buffer_seconds * sample_rate)
        self._buffer: deque[bytes] = deque()
        self._buffer_samples = 0
        self._buffer_lock = threading.Lock()

        self._model = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_transcript = ""
        self._transcript_lock = threading.Lock()

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

    def feed_audio(self, audio_bytes: bytes) -> None:
        """Feed a chunk of int16 PCM audio into the buffer.

        Args:
            audio_bytes: Raw 16-bit PCM audio bytes (16kHz mono).
        """
        chunk_samples = len(audio_bytes) // 2  # int16 = 2 bytes per sample
        with self._buffer_lock:
            self._buffer.append(audio_bytes)
            self._buffer_samples += chunk_samples

            # Trim oldest chunks to stay within buffer limit
            while self._buffer_samples > self._max_samples and self._buffer:
                oldest = self._buffer.popleft()
                self._buffer_samples -= len(oldest) // 2

    @property
    def last_transcript(self) -> str:
        """The most recent live transcript text."""
        with self._transcript_lock:
            return self._last_transcript

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _get_buffer_audio(self) -> Optional[np.ndarray]:
        """Get current buffer contents as a numpy array.

        Returns:
            int16 numpy array of buffered audio, or None if buffer is empty.
        """
        with self._buffer_lock:
            if not self._buffer:
                return None
            all_bytes = b"".join(self._buffer)

        if len(all_bytes) < 2:
            return None

        return np.frombuffer(all_bytes, dtype=np.int16)

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
        """Load the whisper model for live transcription."""
        try:
            from faster_whisper import WhisperModel

            logger.info("Loading live transcription model: %s", self._model_size)
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            logger.info("Live transcription model loaded.")
        except ImportError:
            logger.error("faster-whisper not installed. Live transcription unavailable.")
            self._stop_event.set()
        except Exception:
            logger.exception("Failed to load live transcription model")
            self._stop_event.set()

    def _transcription_loop(self) -> None:
        """Background loop that periodically transcribes the audio buffer."""
        self._load_model()
        if self._model is None:
            return

        while not self._stop_event.is_set():
            # Wait for the next transcription interval
            self._stop_event.wait(self._transcribe_interval)
            if self._stop_event.is_set():
                break

            audio = self._get_buffer_audio()
            if audio is None or len(audio) < self._sample_rate:
                # Less than 1 second of audio, skip
                continue

            try:
                # Transcribe the buffered audio
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

                texts = []
                for seg in segments_gen:
                    text = seg.text.strip()
                    if text:
                        texts.append(text)

                transcript = " ".join(texts)

                with self._transcript_lock:
                    self._last_transcript = transcript

                if transcript and self._on_transcript:
                    self._on_transcript(transcript)

            except Exception:
                logger.warning("Live transcription error (non-fatal)", exc_info=True)

    def clear_buffer(self) -> None:
        """Clear the audio buffer."""
        with self._buffer_lock:
            self._buffer.clear()
            self._buffer_samples = 0
        with self._transcript_lock:
            self._last_transcript = ""
