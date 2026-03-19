"""Shared audio resampling utility for converting various formats to 16kHz mono int16."""

from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly
from math import gcd

# Noise gate defaults (relative to int16 full-scale 32768)
_GATE_THRESHOLD_DB = -50.0  # Open gate above this RMS level
_GATE_FLOOR_DB = -80.0      # Attenuate below-threshold audio to this level
_GATE_SMOOTHING = 0.05      # EMA smoothing factor (lower = smoother transitions)


def resample_to_16khz_mono(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int = 16000,
    source_channels: int = 1,
    target_length: int | None = None,
) -> np.ndarray:
    """Resample audio to target rate mono int16.

    Handles float32 or int16 input, stereo-to-mono averaging,
    polyphase resampling, and int16 clipping.

    Args:
        audio: Input audio as numpy array (float32 or int16).
        source_rate: Source sample rate in Hz.
        target_rate: Target sample rate in Hz (default 16000).
        source_channels: Number of source channels (1 or 2).
        target_length: If set, pad or truncate the output to exactly this
            many samples.  Useful for downstream consumers that need a
            fixed chunk size (e.g. Silero VAD needs exactly 512 samples).

    Returns:
        Resampled audio as 1D int16 numpy array.
    """
    # Fast path: already at target rate, mono, and int16
    if (source_rate == target_rate
            and source_channels == 1
            and audio.dtype == np.int16):
        if target_length is not None and len(audio) != target_length:
            audio = _fix_length(audio, target_length)
        return audio

    # Convert int16 to float32 for processing
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0

    # Stereo to mono: reshape and average channels
    if source_channels == 2 and len(audio) >= 2:
        audio = audio.reshape(-1, source_channels).mean(axis=1)

    # Resample if needed
    if source_rate != target_rate:
        g = gcd(source_rate, target_rate)
        up = target_rate // g
        down = source_rate // g
        audio = resample_poly(audio, up, down).astype(np.float32)

    # Convert float32 [-1, 1] to int16
    result = np.clip(audio * 32767, -32768, 32767).astype(np.int16)

    if target_length is not None and len(result) != target_length:
        result = _fix_length(result, target_length)

    return result


def _fix_length(audio: np.ndarray, target: int) -> np.ndarray:
    """Pad with zeros or truncate *audio* to exactly *target* samples."""
    if len(audio) >= target:
        return audio[:target]
    pad = np.zeros(target - len(audio), dtype=audio.dtype)
    return np.concatenate([audio, pad])


class NoiseGate:
    """Simple noise gate with smoothed gain transitions.

    Attenuates audio when the RMS level drops below a threshold,
    reducing background hiss during silence. Uses exponential
    moving average smoothing to avoid click artifacts at gate edges.
    """

    def __init__(
        self,
        threshold_db: float = _GATE_THRESHOLD_DB,
        floor_db: float = _GATE_FLOOR_DB,
        smoothing: float = _GATE_SMOOTHING,
    ):
        self._threshold = 10 ** (threshold_db / 20.0) * 32768.0  # RMS in int16 scale
        self._floor_gain = 10 ** (floor_db / 20.0) / (10 ** (threshold_db / 20.0))
        self._smoothing = smoothing
        self._gain = 0.0  # Current smoothed gain (0 = fully gated, 1 = open)

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Apply noise gate to int16 audio chunk.

        Returns the gated audio (int16). Maintains internal state
        for smooth gain transitions across consecutive chunks.
        """
        if len(audio) == 0:
            return audio

        rms = np.sqrt(np.mean(audio.astype(np.float64) ** 2))

        # Target gain: 1.0 when above threshold, floor_gain when below
        if rms >= self._threshold:
            target = 1.0
        else:
            target = self._floor_gain

        # Smooth gain transition (exponential moving average)
        self._gain += self._smoothing * (target - self._gain)
        self._gain = min(1.0, max(0.0, self._gain))

        # Fast path: gain ~1.0 means no processing needed
        if self._gain > 0.99:
            return audio

        return np.clip(
            audio.astype(np.float32) * self._gain, -32768, 32767
        ).astype(np.int16)
