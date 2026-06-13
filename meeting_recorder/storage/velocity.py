"""Meeting velocity score.

Measures how much ground a meeting covers per unit of time — decisions,
action items, topic changes, and speaker turns per minute. Higher velocity
indicates more productive use of time.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MeetingVelocity:
    """Velocity metrics for a recording."""
    duration_min: float
    decisions_per_hour: float
    actions_per_hour: float
    turns_per_minute: float
    words_per_minute: float
    topic_changes: int
    overall_velocity: int  # 0-100 composite score
    label: str  # "high", "moderate", "low"


def analyze_velocity(
    rec_path: Path,
    meta: dict | None = None,
) -> MeetingVelocity | None:
    """Analyze meeting velocity.

    Args:
        rec_path: Recording directory.
        meta: Pre-loaded metadata.

    Returns:
        MeetingVelocity or None if insufficient data.
    """
    if meta is None:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                return None
        else:
            return None

    dur = meta.get("duration_seconds", 0)
    if dur < 120:  # need at least 2 minutes
        return None

    dur_min = dur / 60.0
    dur_hour = dur / 3600.0

    # Count decisions
    decision_count = 0
    dec_path = rec_path / "decisions.json"
    if dec_path.exists():
        try:
            with open(dec_path, "r", encoding="utf-8") as f:
                dec_data = json.load(f)
            dec_list = (dec_data.get("decisions") or []) if isinstance(dec_data, dict) else dec_data
            decision_count = len(dec_list)
        except Exception:
            pass

    # Count action items
    action_count = 0
    ai_path = rec_path / "action_items.json"
    if ai_path.exists():
        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            action_count = len(items)
        except Exception:
            pass

    # Analyze transcript for turns and words
    turns = 0
    total_words = 0
    topic_changes = 0
    transcript_path = rec_path / "transcript.json"
    if transcript_path.exists():
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            segments = tdata.get("segments") or []
            prev_speaker = ""
            prev_keywords: set[str] = set()
            for seg in segments:
                speaker = seg.get("speaker", "")
                text = seg.get("text", "")

                if speaker and speaker != prev_speaker:
                    turns += 1
                    prev_speaker = speaker

                words = re.findall(r"\w+", text)
                total_words += len(words)

                # Simple topic change detection: significant keyword shift
                if words:
                    curr_kw = set(w.lower() for w in words if len(w) > 4)
                    if prev_keywords and curr_kw:
                        overlap = len(prev_keywords & curr_kw)
                        if overlap == 0 and len(curr_kw) >= 3:
                            topic_changes += 1
                    prev_keywords = curr_kw
        except Exception:
            pass

    decisions_per_hour = decision_count / dur_hour if dur_hour > 0 else 0
    actions_per_hour = action_count / dur_hour if dur_hour > 0 else 0
    turns_per_min = turns / dur_min if dur_min > 0 else 0
    wpm = total_words / dur_min if dur_min > 0 else 0

    # Composite velocity score (0-100)
    # Decision velocity: 0 = 0, 6+/hr = 30
    dec_score = min(30, decisions_per_hour * 5)
    # Action velocity: 0 = 0, 6+/hr = 30
    act_score = min(30, actions_per_hour * 5)
    # Turn velocity: measures engagement, 2-8 turns/min is good
    turn_score = min(20, turns_per_min * 5) if turns_per_min <= 8 else max(0, 20 - (turns_per_min - 8) * 3)
    # WPM: 100-180 is ideal range
    wpm_score = 20 if 100 <= wpm <= 180 else max(0, 20 - abs(wpm - 140) * 0.15)

    overall = int(min(100, dec_score + act_score + turn_score + wpm_score))

    if overall >= 70:
        label = "high"
    elif overall >= 40:
        label = "moderate"
    else:
        label = "low"

    return MeetingVelocity(
        duration_min=round(dur_min, 1),
        decisions_per_hour=round(decisions_per_hour, 1),
        actions_per_hour=round(actions_per_hour, 1),
        turns_per_minute=round(turns_per_min, 1),
        words_per_minute=round(wpm, 1),
        topic_changes=topic_changes,
        overall_velocity=overall,
        label=label,
    )


def format_velocity(velocity: MeetingVelocity | None) -> str:
    """Format velocity report as readable text."""
    if velocity is None:
        return "Not enough data for velocity analysis."

    lines = [
        "MEETING VELOCITY",
        "-" * 40,
        f"  Overall:   {velocity.overall_velocity}/100  ({velocity.label.title()})",
        "",
        f"  Decisions/hr:    {velocity.decisions_per_hour:.1f}",
        f"  Actions/hr:      {velocity.actions_per_hour:.1f}",
        f"  Turns/min:       {velocity.turns_per_minute:.1f}",
        f"  Words/min:       {velocity.words_per_minute:.0f}",
        f"  Topic changes:   {velocity.topic_changes}",
        "",
    ]

    if velocity.label == "high":
        lines.append("  Fast-paced meeting with strong output per minute.")
    elif velocity.label == "moderate":
        lines.append("  Balanced pace — room to optimize but productive overall.")
    else:
        lines.append("  Low output rate — consider tighter agenda or shorter meeting.")

    return "\n".join(lines)
