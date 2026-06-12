"""Local transcription using faster-whisper with CUDA support."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    """A single transcription segment."""
    start: float  # seconds
    end: float    # seconds
    text: str
    speaker: str = ""  # filled in by diarization


class LocalWhisperTranscriber:
    """Transcription backend using faster-whisper.

    Supports CUDA acceleration with configurable model size and compute type.
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str = "en",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None

    def load(self) -> None:
        """Load the whisper model.

        If the configured compute type is rejected (some GPU/driver combos
        refuse float16), retries once with int8_float32 before giving up.
        """
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper model: %s (device=%s, compute=%s)",
            self.model_size, self.device, self.compute_type,
        )
        try:
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as e:
            if self.compute_type == "int8_float32":
                raise
            logger.warning(
                "Model load failed with compute_type=%s (%s); "
                "retrying with int8_float32",
                self.compute_type, e,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type="int8_float32",
            )
        logger.info("Whisper model loaded.")

    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        """Transcribe an audio file.

        Args:
            audio_path: Path to a WAV audio file.

        Returns:
            List of TranscriptSegment with timestamps and text.
        """
        if self._model is None:
            self.load()

        logger.info("Transcribing: %s", audio_path.name)

        segments_gen, info = self._model.transcribe(
            str(audio_path),
            language=self.language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
        )

        segments = []
        for seg in segments_gen:
            segments.append(TranscriptSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
            ))

        logger.info(
            "Transcription complete: %d segments, detected language=%s (prob=%.2f)",
            len(segments), info.language, info.language_probability,
        )
        return segments
