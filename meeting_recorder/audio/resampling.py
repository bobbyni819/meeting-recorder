"""Shared audio resampling utility for converting various formats to 16kHz mono int16."""

from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly
from math import gcd


def resample_to_16khz_mono(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int = 16000,
    source_channels: int = 1,
) -> np.ndarray:
    """Resample audio to target rate mono int16.

    Handles float32 or int16 input, stereo-to-mono averaging,
    polyphase resampling, and int16 clipping.

    Args:
        audio: Input audio as numpy array (float32 or int16).
        source_rate: Source sample rate in Hz.
        target_rate: Target sample rate in Hz (default 16000).
        source_channels: Number of source channels (1 or 2).

    Returns:
        Resampled audio as 1D int16 numpy array.
    """
    # Fast path: already at target rate, mono, and int16
    if (source_rate == target_rate
            and source_channels == 1
            and audio.dtype == np.int16):
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
    return np.clip(audio * 32767, -32768, 32767).astype(np.int16)
