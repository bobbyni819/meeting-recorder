"""Post-recording audio track mixer."""

from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def mix_tracks(
    app_audio_path: Path,
    mic_audio_path: Path,
    output_path: Path,
    app_volume: float = 1.0,
    mic_volume: float = 1.0,
) -> None:
    """Mix app audio and mic audio tracks into a single WAV file.

    Both input files must have the same sample rate, channels, and sample width.
    The output length matches the longer of the two tracks (shorter one is zero-padded).

    Args:
        app_audio_path: Path to the app audio WAV file.
        mic_audio_path: Path to the mic audio WAV file.
        output_path: Path for the mixed output WAV file.
        app_volume: Volume multiplier for app audio (0.0-1.0).
        mic_volume: Volume multiplier for mic audio (0.0-1.0).
    """
    logger.info("Mixing tracks: %s + %s -> %s", app_audio_path.name, mic_audio_path.name, output_path.name)

    # Read both tracks
    app_params, app_data = _read_wav(app_audio_path)
    mic_params, mic_data = _read_wav(mic_audio_path)

    # Validate compatibility
    if app_params[:3] != mic_params[:3]:
        raise ValueError(
            f"Track format mismatch: app={app_params[:3]}, mic={mic_params[:3]}. "
            "Both must have same channels, sample_width, and sample_rate."
        )

    # Convert to float for mixing
    app_float = app_data.astype(np.float32) * app_volume
    mic_float = mic_data.astype(np.float32) * mic_volume

    # Zero-pad shorter track
    max_len = max(len(app_float), len(mic_float))
    if len(app_float) < max_len:
        app_float = np.pad(app_float, (0, max_len - len(app_float)))
    if len(mic_float) < max_len:
        mic_float = np.pad(mic_float, (0, max_len - len(mic_float)))

    # Mix and clip
    mixed = app_float + mic_float
    mixed = np.clip(mixed, -32768, 32767).astype(np.int16)

    # Write output
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(app_params[0])
        wf.setsampwidth(app_params[1])
        wf.setframerate(app_params[2])
        wf.writeframes(mixed.tobytes())

    logger.info("Mixed track written: %s (%.1fs)", output_path.name, max_len / app_params[2])


def _read_wav(path: Path) -> tuple[tuple[int, int, int], np.ndarray]:
    """Read a WAV file and return (nchannels, sampwidth, framerate) and int16 numpy array."""
    with wave.open(str(path), "rb") as wf:
        params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
        frames = wf.readframes(wf.getnframes())
    data = np.frombuffer(frames, dtype=np.int16)
    return params, data
