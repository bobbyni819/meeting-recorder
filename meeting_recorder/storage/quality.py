"""Recording quality scoring — audio, transcript, and video health metrics."""

from __future__ import annotations

import json
import logging
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def score_recording(recording_dir: Path) -> dict:
    """Compute quality scores for a recording.

    Returns a dict with scores (0-100) and diagnostic details:
        audio_score, transcript_score, overall_score,
        audio_details, transcript_details
    """
    audio = _score_audio(recording_dir)
    transcript = _score_transcript(recording_dir)
    video = _score_video(recording_dir)

    # Weighted overall: audio 40%, transcript 40%, video 20%
    weights = []
    scores = []
    if audio["score"] is not None:
        weights.append(0.4)
        scores.append(audio["score"])
    if transcript["score"] is not None:
        weights.append(0.4)
        scores.append(transcript["score"])
    if video["score"] is not None:
        weights.append(0.2)
        scores.append(video["score"])

    if weights:
        # Normalize weights to sum to 1.0
        total_w = sum(weights)
        overall = sum(s * w / total_w for s, w in zip(scores, weights))
    else:
        overall = 0

    return {
        "overall_score": round(overall),
        "audio_score": audio["score"],
        "audio_details": audio["details"],
        "transcript_score": transcript["score"],
        "transcript_details": transcript["details"],
        "video_score": video["score"],
        "video_details": video["details"],
    }


def _score_audio(recording_dir: Path) -> dict:
    """Score audio quality from WAV files.

    Checks: duration, RMS level, clipping, silence ratio, noise floor.
    """
    app_wav = recording_dir / "app_audio.wav"
    mic_wav = recording_dir / "mic_audio.wav"

    best_score = None
    best_details = {}

    for label, wav_path in [("app", app_wav), ("mic", mic_wav)]:
        if not wav_path.exists():
            continue
        result = _analyze_wav(wav_path)
        if result is None:
            continue
        score = _compute_audio_score(result)
        if best_score is None or score > best_score:
            best_score = score
            best_details = {f"{label}_{k}": v for k, v in result.items()}
            best_details["scored_source"] = label

    if best_score is None:
        return {"score": None, "details": {}}

    return {"score": best_score, "details": best_details}


def _analyze_wav(wav_path: Path) -> Optional[dict]:
    """Analyze a WAV file and return raw metrics."""
    try:
        import numpy as np

        with wave.open(str(wav_path), "rb") as wf:
            n_frames = wf.getnframes()
            rate = wf.getframerate()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()

            if n_frames <= 0 or rate <= 0:
                return None

            duration = n_frames / rate

            # Read a sample (up to 60s from the middle for efficiency)
            max_sample_frames = rate * 60
            if n_frames > max_sample_frames:
                start = (n_frames - max_sample_frames) // 2
                wf.setpos(start)
                raw = wf.readframes(max_sample_frames)
            else:
                raw = wf.readframes(n_frames)

        # Convert to float [-1, 1]
        if sampwidth == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            return None

        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)

        if len(samples) == 0:
            return None

        # RMS level
        rms = float(np.sqrt(np.mean(samples ** 2)))
        rms_db = 20 * np.log10(max(rms, 1e-10))

        # Peak level
        peak = float(np.max(np.abs(samples)))
        peak_db = 20 * np.log10(max(peak, 1e-10))

        # Clipping: samples at >99% of max
        clip_threshold = 0.99
        clipped_count = int(np.sum(np.abs(samples) > clip_threshold))
        clip_ratio = clipped_count / len(samples)

        # Silence ratio: frames below -50 dB
        frame_size = rate // 10  # 100ms frames
        silence_frames = 0
        total_frames = 0
        for i in range(0, len(samples) - frame_size, frame_size):
            chunk = samples[i:i + frame_size]
            chunk_rms = float(np.sqrt(np.mean(chunk ** 2)))
            chunk_db = 20 * np.log10(max(chunk_rms, 1e-10))
            total_frames += 1
            if chunk_db < -50:
                silence_frames += 1

        silence_ratio = silence_frames / max(total_frames, 1)

        # Dynamic range
        dynamic_range = peak_db - rms_db

        return {
            "duration": round(duration, 1),
            "rms_db": round(rms_db, 1),
            "peak_db": round(peak_db, 1),
            "clip_ratio": round(clip_ratio, 4),
            "silence_ratio": round(silence_ratio, 2),
            "dynamic_range": round(dynamic_range, 1),
            "sample_rate": rate,
        }

    except Exception:
        logger.debug("Failed to analyze %s", wav_path, exc_info=True)
        return None


def _compute_audio_score(metrics: dict) -> int:
    """Compute 0-100 score from audio metrics."""
    score = 100

    # RMS level: ideal -30 to -15 dB. Penalize if too quiet or too hot.
    rms = metrics["rms_db"]
    if rms < -60:
        score -= 40  # nearly silent
    elif rms < -45:
        score -= 20  # very quiet
    elif rms < -35:
        score -= 5   # slightly quiet
    elif rms > -5:
        score -= 30  # way too hot
    elif rms > -10:
        score -= 10  # too hot

    # Clipping penalty
    clip = metrics["clip_ratio"]
    if clip > 0.01:
        score -= 30  # severe clipping
    elif clip > 0.001:
        score -= 15
    elif clip > 0.0001:
        score -= 5

    # Silence ratio penalty (>80% silence = problem)
    silence = metrics["silence_ratio"]
    if silence > 0.9:
        score -= 30  # mostly silent
    elif silence > 0.7:
        score -= 15
    elif silence > 0.5:
        score -= 5

    # Dynamic range: too compressed or too wide
    dr = metrics["dynamic_range"]
    if dr < 3:
        score -= 10  # very compressed
    elif dr > 50:
        score -= 5   # unusually wide (possible noise issues)

    return max(0, min(100, score))


def _score_transcript(recording_dir: Path) -> dict:
    """Score transcript quality from transcript.json."""
    transcript_path = recording_dir / "transcript.json"
    if not transcript_path.exists():
        return {"score": None, "details": {}}

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        segments = data.get("segments") or []
        if not segments:
            return {"score": 0, "details": {"error": "no segments"}}

        # Segment count
        seg_count = len(segments)

        # Total text length
        total_text = " ".join(s.get("text", "") for s in segments)
        word_count = len(total_text.split())

        # Duration covered
        if segments:
            first_start = segments[0].get("start", 0)
            last_end = segments[-1].get("end", 0)
            coverage_duration = last_end - first_start
        else:
            coverage_duration = 0

        # Words per minute (indicator of quality — too low = poor transcription)
        wpm = (word_count / (coverage_duration / 60)) if coverage_duration > 60 else 0

        # Average segment length (very short segments may indicate fragmented transcription)
        avg_seg_words = word_count / max(seg_count, 1)

        # Speaker coverage
        speakers = set(s.get("speaker", "") for s in segments)
        speakers.discard("")
        speaker_count = len(speakers)

        # Gap analysis: large gaps between segments
        gaps = []
        for i in range(1, len(segments)):
            gap = segments[i].get("start", 0) - segments[i - 1].get("end", 0)
            if gap > 10:  # >10s gap
                gaps.append(gap)

        details = {
            "word_count": word_count,
            "segment_count": seg_count,
            "wpm": round(wpm, 0),
            "avg_words_per_segment": round(avg_seg_words, 1),
            "speaker_count": speaker_count,
            "large_gaps": len(gaps),
            "coverage_seconds": round(coverage_duration, 0),
        }

        # Score computation
        score = 100

        # WPM check: normal speech is 100-160 WPM
        if coverage_duration > 60:
            if wpm < 30:
                score -= 30  # too few words — likely missed speech
            elif wpm < 60:
                score -= 15
            elif wpm > 250:
                score -= 10  # suspiciously high — repeated text?

        # Word count: very short transcripts are suspicious
        if word_count < 10:
            score -= 30
        elif word_count < 50:
            score -= 10

        # Large gaps penalty
        if len(gaps) > 10:
            score -= 15
        elif len(gaps) > 5:
            score -= 5

        # Very fragmented (avg <3 words per segment)
        if avg_seg_words < 3 and seg_count > 5:
            score -= 10

        return {"score": max(0, min(100, score)), "details": details}

    except Exception:
        logger.debug("Failed to score transcript", exc_info=True)
        return {"score": None, "details": {}}


def _score_video(recording_dir: Path) -> dict:
    """Score video quality from screen.mp4 presence and size."""
    video_path = recording_dir / "screen.mp4"
    if not video_path.exists():
        return {"score": None, "details": {}}

    try:
        size_bytes = video_path.stat().st_size
        size_mb = size_bytes / (1024 ** 2)

        details = {
            "file_size_mb": round(size_mb, 1),
            "exists": True,
        }

        # Basic heuristic: very small files are likely corrupt
        score = 100
        if size_mb < 0.1:
            score = 20  # likely corrupt / empty
        elif size_mb < 1:
            score = 60  # very short or low quality

        return {"score": score, "details": details}

    except Exception:
        logger.debug("Failed to score video", exc_info=True)
        return {"score": None, "details": {}}


def quality_label(score: int) -> str:
    """Convert a 0-100 score to a human-readable label."""
    if score >= 90:
        return "Excellent"
    elif score >= 75:
        return "Good"
    elif score >= 50:
        return "Fair"
    elif score >= 25:
        return "Poor"
    else:
        return "Bad"


def quality_bar(score: int, width: int = 20) -> str:
    """Create a visual bar for a quality score."""
    filled = int(score / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)
