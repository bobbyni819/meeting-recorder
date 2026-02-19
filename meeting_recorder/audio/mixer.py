"""Post-recording audio track mixer."""

from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Chunk size for streaming mixer: 64KB (~2 seconds at 16kHz mono int16)
STREAMING_CHUNK_FRAMES = 32768


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


def mix_tracks_streaming(
    app_audio_path: Path,
    mic_audio_path: Path,
    output_path: Path,
    app_volume: float = 1.0,
    mic_volume: float = 1.0,
    chunk_frames: int = STREAMING_CHUNK_FRAMES,
) -> None:
    """Mix app audio and mic audio tracks using streaming to limit memory usage.

    Processes the audio in fixed-size chunks rather than loading entire files
    into memory. Suitable for long recordings (1+ hours).

    Both input files must have the same sample rate, channels, and sample width.

    Args:
        app_audio_path: Path to the app audio WAV file.
        mic_audio_path: Path to the mic audio WAV file.
        output_path: Path for the mixed output WAV file.
        app_volume: Volume multiplier for app audio (0.0-1.0).
        mic_volume: Volume multiplier for mic audio (0.0-1.0).
        chunk_frames: Number of frames to process per chunk.
    """
    logger.info(
        "Streaming mix: %s + %s -> %s",
        app_audio_path.name, mic_audio_path.name, output_path.name,
    )

    with wave.open(str(app_audio_path), "rb") as app_wf, \
         wave.open(str(mic_audio_path), "rb") as mic_wf:

        app_params = (app_wf.getnchannels(), app_wf.getsampwidth(), app_wf.getframerate())
        mic_params = (mic_wf.getnchannels(), mic_wf.getsampwidth(), mic_wf.getframerate())

        if app_params != mic_params:
            raise ValueError(
                f"Track format mismatch: app={app_params}, mic={mic_params}. "
                "Both must have same channels, sample_width, and sample_rate."
            )

        nchannels, sampwidth, framerate = app_params
        app_total = app_wf.getnframes()
        mic_total = mic_wf.getnframes()
        max_frames = max(app_total, mic_total)

        with wave.open(str(output_path), "wb") as out_wf:
            out_wf.setnchannels(nchannels)
            out_wf.setsampwidth(sampwidth)
            out_wf.setframerate(framerate)

            frames_written = 0
            while frames_written < max_frames:
                remaining = max_frames - frames_written
                n = min(chunk_frames, remaining)

                # Read chunks (readframes returns b"" at EOF)
                app_bytes = app_wf.readframes(n)
                mic_bytes = mic_wf.readframes(n)

                # Number of samples (frames * channels)
                expected_samples = n * nchannels

                app_samples = np.frombuffer(app_bytes, dtype=np.int16) if app_bytes else np.array([], dtype=np.int16)
                mic_samples = np.frombuffer(mic_bytes, dtype=np.int16) if mic_bytes else np.array([], dtype=np.int16)

                # Fast path: both tracks have expected length (common case)
                if len(app_samples) == expected_samples and len(mic_samples) == expected_samples:
                    pass  # no padding needed
                else:
                    # Zero-pad if one track is shorter (near EOF)
                    if len(app_samples) < expected_samples:
                        app_samples = np.pad(app_samples, (0, expected_samples - len(app_samples)))
                    if len(mic_samples) < expected_samples:
                        mic_samples = np.pad(mic_samples, (0, expected_samples - len(mic_samples)))

                # Mix and clip
                mixed = app_samples.astype(np.float32) * app_volume + mic_samples.astype(np.float32) * mic_volume
                mixed = np.clip(mixed, -32768, 32767).astype(np.int16)

                out_wf.writeframes(mixed.tobytes())
                frames_written += n

    duration = max_frames / framerate if framerate > 0 else 0
    logger.info("Streaming mix complete: %s (%.1fs)", output_path.name, duration)


def _read_wav(path: Path) -> tuple[tuple[int, int, int], np.ndarray]:
    """Read a WAV file and return (nchannels, sampwidth, framerate) and int16 numpy array."""
    with wave.open(str(path), "rb") as wf:
        params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
        frames = wf.readframes(wf.getnframes())
    data = np.frombuffer(frames, dtype=np.int16)
    return params, data
