"""Voice Activity Detection using Silero VAD."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class VoiceActivityDetector:
    """Silero VAD wrapper for real-time voice activity detection.

    Processes 16kHz mono audio in 30ms chunks and returns speech probability.
    """

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self._model = None
        self._torch = None
        self._sample_rate = 16000

    @property
    def is_loaded(self) -> bool:
        """Whether the VAD model has been loaded."""
        return self._model is not None

    def load(self) -> None:
        """Load the Silero VAD model (idempotent — skips if already loaded)."""
        if self._model is not None:
            return
        import torch
        self._torch = torch
        logger.info("Loading Silero VAD model...")
        self._model, _ = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            trust_repo=True,
        )
        self._model.eval()
        logger.info("Silero VAD model loaded.")

    def is_speech(self, audio_chunk: bytes) -> bool:
        """Check if an audio chunk contains speech.

        Args:
            audio_chunk: Raw 16-bit PCM audio bytes (16kHz mono, 30ms = 960 bytes).

        Returns:
            True if speech probability exceeds threshold.
        """
        prob = self.speech_probability(audio_chunk)
        return prob >= self.threshold

    def speech_probability(self, audio_chunk: bytes) -> float:
        """Get speech probability for an audio chunk.

        Args:
            audio_chunk: Raw 16-bit PCM audio bytes (16kHz mono).

        Returns:
            Speech probability between 0.0 and 1.0.
        """
        if self._model is None:
            raise RuntimeError("VAD model not loaded. Call load() first.")

        # Convert bytes to float32 tensor
        audio_int16 = np.frombuffer(audio_chunk, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        tensor = self._torch.from_numpy(audio_float)

        with self._torch.no_grad():
            prob = self._model(tensor, self._sample_rate).item()

        return prob

    def reset(self) -> None:
        """Reset the VAD model state (call between recordings)."""
        if self._model is not None:
            self._model.reset_states()
