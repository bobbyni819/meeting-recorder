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
            return 1.0  # Pass-through: treat all audio as speech if model not loaded

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


class SpeechHold:
    """Hangover (hold-over) around per-chunk VAD decisions.

    A raw per-chunk gate — write real audio when the chunk is speech, silence
    otherwise — clips the onsets/tails of words and punches silence into the
    brief pauses between words, which sounds choppy and hurts transcription at
    word boundaries. This applies a *hangover*: once speech is detected, keep
    treating audio as speech for a short window afterwards, so trailing sounds
    and short inter-word pauses are preserved as real audio.

    During genuinely long silence the countdown lapses and the gate closes
    again, so the room is still not recorded while the user is idle — only a
    bounded ~hangover window around actual speech is ever kept.
    """

    def __init__(self, hangover_chunks: int):
        self.hangover_chunks = max(0, int(hangover_chunks))
        self._countdown = 0

    @classmethod
    def from_ms(cls, hangover_ms: float, chunk_ms: float) -> "SpeechHold":
        """Build a hold whose hangover is ``hangover_ms`` long, given the
        per-chunk duration ``chunk_ms``."""
        if chunk_ms <= 0:
            return cls(0)
        return cls(round(hangover_ms / chunk_ms))

    def update(self, is_speech: bool) -> bool:
        """Feed one chunk's VAD verdict; return True if it should be WRITTEN as
        real audio (vs replaced with silence)."""
        if is_speech:
            self._countdown = self.hangover_chunks
            return True
        if self._countdown > 0:
            self._countdown -= 1
            return True
        return False

    def reset(self) -> None:
        """Close the gate immediately (e.g. on mute or between recordings)."""
        self._countdown = 0
