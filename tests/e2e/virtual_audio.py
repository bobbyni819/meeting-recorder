"""VB-Cable virtual audio device utilities for E2E testing."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def find_vbcable_device() -> Optional[int]:
    """Find VB-Cable virtual audio input device index.

    Returns device index or None if not found.
    """
    try:
        import sounddevice as sd
    except ImportError:
        logger.debug("sounddevice not installed")
        return None

    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        name = dev["name"].lower()
        if "cable input" in name and dev["max_output_channels"] > 0:
            logger.info("Found VB-Cable: device %d (%s)", i, dev["name"])
            return i

    logger.debug("VB-Cable not found among %d devices", len(devices))
    return None


def generate_test_speech(
    duration: float = 10.0,
    sample_rate: int = 44100,
) -> np.ndarray:
    """Generate synthetic speech-like audio for testing.

    Creates multi-tone audio with amplitude modulation that resembles
    speech patterns. No external audio files needed.

    Returns float32 numpy array normalized to [-1, 1].
    """
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    # Fundamental frequencies (speech-like range)
    signal = np.zeros_like(t)
    for freq in [150, 250, 400, 800, 1200]:
        signal += np.sin(2 * np.pi * freq * t) / 5

    # Amplitude modulation (syllable-like rhythm, 3-5 Hz)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t)
    # Add slower variation (phrase-like, ~0.5 Hz)
    envelope *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t)

    signal *= envelope

    # Normalize to [-0.8, 0.8] to avoid clipping
    peak = np.max(np.abs(signal))
    if peak > 0:
        signal = signal / peak * 0.8

    return signal.astype(np.float32)


class VBCablePlayer:
    """Plays audio through VB-Cable virtual audio device."""

    def __init__(self, device_index: Optional[int] = None):
        self._device_index = device_index or find_vbcable_device()
        if self._device_index is None:
            raise RuntimeError(
                "VB-Cable not found. Install VB-Cable: https://vb-audio.com/Cable/"
            )

    def play_blocking(
        self,
        audio: np.ndarray,
        sample_rate: int = 44100,
    ) -> None:
        """Play audio through VB-Cable and block until complete."""
        import sounddevice as sd

        logger.info(
            "Playing %.1fs of audio through VB-Cable (device %d)",
            len(audio) / sample_rate,
            self._device_index,
        )
        sd.play(audio, samplerate=sample_rate, device=self._device_index)
        sd.wait()
        logger.info("Playback complete")

    def play_test_speech(self, duration: float = 10.0) -> None:
        """Generate and play test speech audio."""
        audio = generate_test_speech(duration=duration)
        self.play_blocking(audio)
