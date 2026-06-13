"""Meeting productivity scoring.

Analyzes recordings to score how productive a meeting was based on
objective signals: action items generated, discussion density, speaker
participation balance, and time efficiency.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProductivityScore:
    """Productivity analysis for a single recording."""
    overall: int  # 0-100
    action_density: int  # 0-100: action items per hour
    participation: int  # 0-100: how balanced speaker participation was
    discussion_density: int  # 0-100: words per minute of meeting
    time_efficiency: int  # 0-100: proportion of meeting with active speech
    breakdown: dict  # Raw data behind scores


def score_productivity(rec_path: Path, meta: dict | None = None) -> Optional[ProductivityScore]:
    """Score the productivity of a recording.

    Args:
        rec_path: Recording directory.
        meta: Optional pre-loaded metadata.

    Returns:
        ProductivityScore or None if insufficient data.
    """
    if meta is None:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        else:
            meta = {}

    duration = meta.get("duration_seconds", 0)
    if duration < 60:  # Skip very short recordings
        return None

    duration_hours = duration / 3600
    duration_minutes = duration / 60
    breakdown: dict = {}

    # 1. Action density: action items per hour
    action_count = 0
    ai_path = rec_path / "action_items.json"
    if ai_path.exists():
        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                action_count = len(json.load(f))
        except Exception:
            pass

    items_per_hour = action_count / max(duration_hours, 0.1)
    # 4+ items/hour is excellent, 0 is poor
    action_score = min(100, int(items_per_hour / 4 * 100))
    breakdown["action_items"] = action_count
    breakdown["items_per_hour"] = round(items_per_hour, 1)

    # 2. Participation balance: how evenly speakers participated
    participation_score = 50  # default if no speaker data
    transcript_json = rec_path / "transcript.json"
    speaker_times: dict[str, float] = {}
    total_speech = 0.0
    if transcript_json.exists():
        try:
            with open(transcript_json, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            for seg in (tdata.get("segments") or []):
                spk = seg.get("speaker", "Unknown")
                seg_dur = max(0, seg.get("end", 0) - seg.get("start", 0))
                speaker_times[spk] = speaker_times.get(spk, 0) + seg_dur
                total_speech += seg_dur
        except Exception:
            pass

    if len(speaker_times) >= 2 and total_speech > 0:
        # Calculate Gini coefficient-like balance
        times = sorted(speaker_times.values())
        n = len(times)
        mean_time = total_speech / n
        # Perfect balance = 100, one person speaking 100% = 0
        max_dev = sum(abs(t - mean_time) for t in times) / (2 * total_speech)
        participation_score = max(0, int((1 - max_dev) * 100))
    elif len(speaker_times) == 1:
        participation_score = 20  # Monologue = low participation

    breakdown["speaker_count"] = len(speaker_times)
    breakdown["total_speech_seconds"] = round(total_speech)

    # 3. Discussion density: words per minute of meeting
    word_count = 0
    transcript_txt = rec_path / "transcript.txt"
    if transcript_txt.exists():
        try:
            text = transcript_txt.read_text(encoding="utf-8")
            word_count = len(text.split())
        except Exception:
            pass

    wpm = word_count / max(duration_minutes, 1)
    # 100-150 WPM is good pace, <50 is sparse, >200 is too fast
    if wpm < 20:
        density_score = 10
    elif wpm < 50:
        density_score = int(30 + (wpm - 20) / 30 * 40)
    elif wpm <= 180:
        density_score = int(70 + min(30, (wpm - 50) / 130 * 30))
    else:
        density_score = max(60, 100 - int((wpm - 180) / 2))

    breakdown["word_count"] = word_count
    breakdown["wpm"] = round(wpm, 1)

    # 4. Time efficiency: speech-to-meeting ratio
    if total_speech > 0 and duration > 0:
        speech_ratio = total_speech / duration
        # 70%+ speech is very efficient, <30% is lots of dead air
        efficiency_score = min(100, int(speech_ratio / 0.7 * 100))
    else:
        efficiency_score = 50  # default

    breakdown["speech_ratio"] = round(total_speech / max(duration, 1), 2)

    # Overall: weighted average
    overall = int(
        action_score * 0.30 +
        participation_score * 0.25 +
        density_score * 0.25 +
        efficiency_score * 0.20
    )

    return ProductivityScore(
        overall=overall,
        action_density=action_score,
        participation=participation_score,
        discussion_density=density_score,
        time_efficiency=efficiency_score,
        breakdown=breakdown,
    )


def productivity_label(score: int) -> str:
    """Get a human-readable label for a productivity score."""
    if score >= 80:
        return "Highly Productive"
    if score >= 60:
        return "Productive"
    if score >= 40:
        return "Average"
    if score >= 20:
        return "Low Productivity"
    return "Unproductive"
