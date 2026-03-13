"""Meeting engagement score.

Computes a single 0-100 score representing how engaged and productive
a meeting was, combining multiple signals: talk balance, sentiment,
action items, decisions, and speaker participation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class EngagementScore:
    """Composite engagement score for a meeting."""
    overall: int  # 0-100
    participation: int  # 0-100: multi-speaker balance
    output: int  # 0-100: action items + decisions produced
    tone: int  # 0-100: sentiment positivity
    quality: int  # 0-100: recording quality
    label: str  # "Highly Engaged", "Engaged", "Low Engagement", etc.
    breakdown: dict[str, str]  # human-readable breakdown


def compute_engagement(
    rec_path: Path,
    meta: dict | None = None,
) -> EngagementScore | None:
    """Compute engagement score for a recording.

    Args:
        rec_path: Recording directory.
        meta: Pre-loaded metadata.

    Returns:
        EngagementScore or None if insufficient data.
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
    if dur < 60:
        return None

    breakdown: dict[str, str] = {}

    # --- Participation score (0-100) ---
    participation = 50  # default if no speaker data
    try:
        from meeting_recorder.storage.talk_balance import analyze_talk_balance
        tb = analyze_talk_balance(rec_path, meta=meta)
        if tb is not None:
            participation = int(tb.balance_score)
            breakdown["participation"] = (
                f"Balance: {tb.balance_score:.0f}/100, "
                f"{tb.speaker_count} speakers"
            )
        else:
            speaker_count = meta.get("speaker_count", 0)
            if speaker_count >= 3:
                participation = 60
            elif speaker_count == 2:
                participation = 50
            elif speaker_count == 1:
                participation = 20
            breakdown["participation"] = f"{speaker_count} speaker(s), no segment data"
    except Exception:
        breakdown["participation"] = "Could not analyze"

    # --- Output score (0-100) ---
    output = 0
    action_count = 0
    decision_count = 0

    ai_path = rec_path / "action_items.json"
    if ai_path.exists():
        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            action_count = len(items)
        except Exception:
            pass

    dec_path = rec_path / "decisions.json"
    if dec_path.exists():
        try:
            with open(dec_path, "r", encoding="utf-8") as f:
                dec_data = json.load(f)
            decision_count = len(dec_data.get("decisions", []))
        except Exception:
            pass

    # Score based on outputs produced
    output = min(100, action_count * 15 + decision_count * 20)
    if action_count == 0 and decision_count == 0:
        # Check if there's a summary (some signal of productive meeting)
        if (rec_path / "summary.md").exists():
            output = 20
    breakdown["output"] = f"{action_count} actions, {decision_count} decisions"

    # --- Tone score (0-100) ---
    tone = 50  # neutral default
    try:
        from meeting_recorder.storage.sentiment import analyze_recording_sentiment
        sent = analyze_recording_sentiment(rec_path)
        if sent is not None:
            # Map -1..+1 to 0..100
            tone = int(max(0, min(100, (sent.score + 1) * 50)))
            breakdown["tone"] = f"{sent.label.title()} ({sent.score:+.2f})"
        else:
            breakdown["tone"] = "No transcript for analysis"
    except Exception:
        breakdown["tone"] = "Could not analyze"

    # --- Quality score (0-100) ---
    quality = 50  # default
    qs = meta.get("quality_scores", {})
    if qs and qs.get("overall_score") is not None:
        quality = qs["overall_score"]
        breakdown["quality"] = f"{quality}/100"
    else:
        breakdown["quality"] = "Not scored"

    # --- Composite ---
    overall = int(
        participation * 0.30
        + output * 0.30
        + tone * 0.20
        + quality * 0.20
    )
    overall = max(0, min(100, overall))

    if overall >= 80:
        label = "Highly Engaged"
    elif overall >= 60:
        label = "Engaged"
    elif overall >= 40:
        label = "Moderate"
    elif overall >= 20:
        label = "Low Engagement"
    else:
        label = "Disengaged"

    return EngagementScore(
        overall=overall,
        participation=participation,
        output=output,
        tone=tone,
        quality=quality,
        label=label,
        breakdown=breakdown,
    )


def format_engagement(score: EngagementScore | None) -> str:
    """Format engagement score as readable text."""
    if score is None:
        return "Unable to compute engagement score."

    bar = "\u2588" * (score.overall // 10) + "\u2591" * (10 - score.overall // 10)

    lines = [
        "ENGAGEMENT SCORE",
        "-" * 40,
        f"  Overall:       [{bar}] {score.overall}/100  {score.label}",
        f"  Participation: {score.participation}/100  ({score.breakdown.get('participation', '')})",
        f"  Output:        {score.output}/100  ({score.breakdown.get('output', '')})",
        f"  Tone:          {score.tone}/100  ({score.breakdown.get('tone', '')})",
        f"  Quality:       {score.quality}/100  ({score.breakdown.get('quality', '')})",
    ]

    return "\n".join(lines)
