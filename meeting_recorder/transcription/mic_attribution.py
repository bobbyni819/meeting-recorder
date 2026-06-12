"""Identify the user's own speaker label using the mic track as ground truth.

The recorder captures the user's microphone as a separate track from the
meeting audio. That mic track is ground truth for *when the user spoke*:
wherever the mic has energy, the user was talking. By overlapping each
diarized/Gemini speaker's segments with mic-active time, the speaker who
consistently coincides with mic energy is the user — so their generic
label ("Speaker 2") can be renamed to the user's name.

This works for the Gemini backend (which otherwise never sees the
separate mic track) and is purely additive: on any failure or ambiguity
the segments are returned unchanged.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 100 ms resolution for the mic-activity envelope.
_FRAME_MS = 100
# A speaker must overlap mic-active time for at least this fraction of
# their total talk time to be considered the user.
_MIN_OVERLAP_RATIO = 0.55
# And must be clearly more mic-aligned than the runner-up.
_MIN_MARGIN = 0.2
# Absolute mic-active seconds required (ignore a stray cough).
_MIN_ACTIVE_SECONDS = 2.0


def _mic_active_envelope(
    mic_wav_path: Path,
) -> tuple[Optional[np.ndarray], float, int]:
    """Return a per-frame boolean mic-active mask, frame seconds, frame count.

    Returns (None, ...) if the file can't be read. Activity threshold is
    relative to the track's own loudness so it adapts to mic gain.
    """
    try:
        with wave.open(str(mic_wav_path), "rb") as wf:
            sample_rate = wf.getframerate()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
    except (wave.Error, OSError, EOFError):
        logger.debug("Could not read mic track %s", mic_wav_path, exc_info=True)
        return None, 0.0, 0

    if sampwidth != 2 or not raw:
        return None, 0.0, 0

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)
    if samples.size == 0:
        return None, 0.0, 0

    frame_len = max(int(sample_rate * _FRAME_MS / 1000), 1)
    n_frames = samples.size // frame_len
    if n_frames == 0:
        return None, 0.0, 0
    framed = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(framed ** 2, axis=1) + 1e-9)

    # Relative threshold: a fraction of the track's loud level, with an
    # absolute floor so a near-silent track doesn't register noise as speech.
    loud = np.percentile(rms, 90)
    if loud < 50.0:  # int16 scale; track is essentially silent
        return np.zeros(n_frames, dtype=bool), frame_len / sample_rate, n_frames
    threshold = max(loud * 0.25, 80.0)
    active = rms > threshold
    return active, frame_len / sample_rate, n_frames


def identify_user_speaker(
    segments,
    mic_wav_path: Path,
    audio_duration: Optional[float] = None,
) -> Optional[str]:
    """Return the speaker label that best matches the mic track, or None.

    Args:
        segments: TranscriptSegments with .start/.end/.speaker.
        mic_wav_path: Path to the user's mic-only WAV.
        audio_duration: Optional total duration for clamping segment ends.
    """
    if not segments:
        return None
    active, frame_sec, n_frames = _mic_active_envelope(Path(mic_wav_path))
    if active is None or n_frames == 0 or not active.any():
        return None

    def frame_idx(t: float) -> int:
        return int(max(0.0, t) / frame_sec)

    # Accumulate, per speaker, total talk frames and mic-active talk frames.
    talk: dict[str, int] = {}
    overlap: dict[str, int] = {}
    for seg in segments:
        speaker = getattr(seg, "speaker", None)
        if not speaker:
            continue
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        if audio_duration:
            end = min(end, audio_duration)
        i0, i1 = frame_idx(start), min(frame_idx(end) + 1, n_frames)
        if i1 <= i0:
            continue
        span = i1 - i0
        talk[speaker] = talk.get(speaker, 0) + span
        overlap[speaker] = overlap.get(speaker, 0) + int(active[i0:i1].sum())

    if not talk:
        return None

    # Rank speakers by mic-overlap ratio.
    ratios = {s: overlap[s] / talk[s] for s in talk if talk[s] > 0}
    if not ratios:
        return None
    ranked = sorted(ratios.items(), key=lambda kv: kv[1], reverse=True)
    best_speaker, best_ratio = ranked[0]
    runner_ratio = ranked[1][1] if len(ranked) > 1 else 0.0

    active_seconds = overlap[best_speaker] * frame_sec
    if (
        best_ratio >= _MIN_OVERLAP_RATIO
        and best_ratio - runner_ratio >= _MIN_MARGIN
        and active_seconds >= _MIN_ACTIVE_SECONDS
    ):
        logger.info(
            "Mic attribution: %s matches the mic track (%.0f%% overlap, "
            "%.1fs) -> user",
            best_speaker, best_ratio * 100, active_seconds,
        )
        return best_speaker

    logger.debug(
        "Mic attribution inconclusive (best %s @ %.0f%%, runner %.0f%%)",
        best_speaker, best_ratio * 100, runner_ratio * 100,
    )
    return None


def attribute_user(
    segments,
    mic_wav_path: Path,
    user_name: str,
    audio_duration: Optional[float] = None,
) -> Optional[str]:
    """Relabel the mic-matched speaker to *user_name* in place.

    Returns the original label that was renamed, or None if no confident
    match was found. Never raises — attribution is best-effort.
    """
    try:
        if not user_name or not Path(mic_wav_path).exists():
            return None
        match = identify_user_speaker(segments, mic_wav_path, audio_duration)
        if match is None or match == user_name:
            return None
        for seg in segments:
            if getattr(seg, "speaker", None) == match:
                seg.speaker = user_name
        return match
    except Exception:
        logger.debug("Mic attribution failed (non-fatal)", exc_info=True)
        return None
