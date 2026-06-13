"""Meeting preparation sheet generator.

Aggregates analytics for a recurring meeting series into a concise
prep document: last meeting summary, outstanding action items,
topic trends, duration predictions, and attendee context.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MeetingPrep:
    """Pre-meeting preparation sheet."""
    subject: str
    occurrence_count: int
    last_meeting_date: str
    last_summary: str
    outstanding_actions: list[str]
    recent_topics: list[str]
    predicted_duration_min: float
    duration_trend: str
    attendees: list[str]
    sentiment_trend: str  # "improving", "declining", "stable", "unknown"
    key_stats: dict = field(default_factory=dict)


def generate_prep(
    recordings_dir: Path,
    subject: str,
    max_actions: int = 10,
) -> MeetingPrep | None:
    """Generate a preparation sheet for a meeting series.

    Args:
        recordings_dir: Base recordings directory.
        subject: Meeting subject to match (case-insensitive partial match).
        max_actions: Maximum outstanding action items to include.

    Returns:
        MeetingPrep or None if no matching recordings found.
    """
    if not recordings_dir.exists():
        return None

    # Find all recordings matching the subject
    matches: list[tuple[str, Path, dict]] = []
    pattern = re.compile(re.escape(subject), re.IGNORECASE)

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        meta = _load_meta(rec_dir)
        rec_subject = meta.get("meeting_subject", "")
        if not rec_subject:
            rec_subject = rec_dir.name[20:].replace("_", " ").strip() if len(rec_dir.name) > 20 else ""
        if rec_subject and pattern.search(rec_subject):
            date = rec_dir.name[:10] if len(rec_dir.name) >= 10 else ""
            matches.append((date, rec_dir, meta))

    if not matches:
        return None

    # Most recent recording
    last_date, last_path, last_meta = matches[-1]

    # Last summary
    last_summary = ""
    summary_path = last_path / "summary.md"
    if summary_path.exists():
        try:
            last_summary = summary_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Outstanding action items from recent meetings
    outstanding = _collect_outstanding_actions(matches, max_actions)

    # Recent topics (from last 3 meetings)
    recent_topics = _collect_recent_topics(matches[-3:])

    # Duration prediction
    predicted_min = 0.0
    duration_trend = "unknown"
    try:
        from meeting_recorder.storage.duration_predict import predict_durations, _normalize_subject
        normalized = _normalize_subject(subject)
        predictions = predict_durations(recordings_dir)
        pred = next((p for p in predictions if p.subject == normalized), None)
        if pred:
            predicted_min = pred.predicted_minutes
            duration_trend = pred.trend.replace("_", " ")
    except Exception:
        pass

    # Attendees (union of recent meetings)
    attendees: list[str] = []
    seen: set[str] = set()
    for _, _, meta in reversed(matches[-5:]):
        for att in (meta.get("meeting_attendees") or []):
            att_lower = att.strip().lower()
            if att_lower and att_lower not in seen:
                seen.add(att_lower)
                attendees.append(att.strip())

    # Sentiment trend
    sentiment_trend = _analyze_sentiment_trend(matches[-5:])

    # Key stats
    durations = [m.get("duration_seconds", 0) / 60 for _, _, m in matches if m.get("duration_seconds", 0) > 0]
    key_stats = {}
    if durations:
        key_stats["avg_duration_min"] = round(sum(durations) / len(durations), 1)
        key_stats["total_meetings"] = len(matches)
        key_stats["total_hours"] = round(sum(durations) / 60, 1)

    return MeetingPrep(
        subject=subject,
        occurrence_count=len(matches),
        last_meeting_date=last_date,
        last_summary=last_summary,
        outstanding_actions=outstanding,
        recent_topics=recent_topics,
        predicted_duration_min=predicted_min,
        duration_trend=duration_trend,
        attendees=attendees,
        sentiment_trend=sentiment_trend,
        key_stats=key_stats,
    )


def format_prep(prep: MeetingPrep) -> str:
    """Format a preparation sheet as readable text."""
    lines = [
        f"MEETING PREP: {prep.subject}",
        "=" * 60,
        "",
        f"Occurrence #{prep.occurrence_count + 1}  |  "
        f"Last: {prep.last_meeting_date}  |  "
        f"Attendees: {len(prep.attendees)}",
    ]

    if prep.predicted_duration_min > 0:
        lines.append(
            f"Predicted duration: ~{prep.predicted_duration_min:.0f} min  "
            f"({prep.duration_trend})"
        )
    lines.append("")

    # Last meeting summary
    if prep.last_summary:
        lines.append("LAST MEETING SUMMARY")
        lines.append("-" * 40)
        # Truncate to first ~500 chars
        summary = prep.last_summary
        if len(summary) > 500:
            summary = summary[:500] + "..."
        for line in summary.split("\n"):
            lines.append(f"  {line}")
        lines.append("")

    # Outstanding action items
    if prep.outstanding_actions:
        lines.append(f"OUTSTANDING ACTION ITEMS ({len(prep.outstanding_actions)})")
        lines.append("-" * 40)
        for item in prep.outstanding_actions:
            lines.append(f"  [ ] {item}")
        lines.append("")

    # Recent topics
    if prep.recent_topics:
        lines.append("RECENT TOPICS")
        lines.append("-" * 40)
        lines.append("  " + "  \u2022  ".join(prep.recent_topics[:10]))
        lines.append("")

    # Attendees
    if prep.attendees:
        lines.append(f"EXPECTED ATTENDEES ({len(prep.attendees)})")
        lines.append("-" * 40)
        for att in prep.attendees:
            lines.append(f"  \u2022 {att}")
        lines.append("")

    # Stats
    if prep.key_stats:
        lines.append("SERIES STATS")
        lines.append("-" * 40)
        ks = prep.key_stats
        if ks.get("total_meetings"):
            lines.append(f"  Total meetings:  {ks['total_meetings']}")
        if ks.get("total_hours"):
            lines.append(f"  Total time:      {ks['total_hours']}h")
        if ks.get("avg_duration_min"):
            lines.append(f"  Avg duration:    {ks['avg_duration_min']} min")
        if prep.sentiment_trend != "unknown":
            lines.append(f"  Sentiment:       {prep.sentiment_trend}")
        lines.append("")

    return "\n".join(lines)


# --- Helpers ---


def _load_meta(rec_dir: Path) -> dict:
    """Load metadata from recording."""
    try:
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _collect_outstanding_actions(
    matches: list[tuple[str, Path, dict]],
    max_items: int,
) -> list[str]:
    """Collect unchecked action items from recent meetings."""
    actions: list[str] = []
    # Check last 3 meetings for action items
    for _, rec_path, _ in reversed(matches[-3:]):
        try:
            from meeting_recorder.storage.action_items import extract_action_items
            items = extract_action_items(rec_path)
            for item in items:
                text = item.text if hasattr(item, "text") else str(item)
                if text and len(actions) < max_items:
                    actions.append(text)
        except Exception:
            pass
    return actions


def _collect_recent_topics(
    matches: list[tuple[str, Path, dict]],
) -> list[str]:
    """Extract key topics from recent meetings."""
    all_topics: list[str] = []
    for _, rec_path, _ in matches:
        try:
            from meeting_recorder.storage.comparison import _extract_topics
            transcript_path = rec_path / "transcript.txt"
            if transcript_path.exists():
                text = transcript_path.read_text(encoding="utf-8")
                topics = _extract_topics(text, min_freq=2, top_n=5)
                all_topics.extend(topics)
        except Exception:
            pass

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in all_topics:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    return unique[:10]


def _analyze_sentiment_trend(
    matches: list[tuple[str, Path, dict]],
) -> str:
    """Analyze sentiment trend across recent meetings."""
    scores: list[float] = []
    for _, rec_path, _ in matches:
        try:
            from meeting_recorder.storage.sentiment import analyze_recording_sentiment
            result = analyze_recording_sentiment(rec_path)
            if result:
                scores.append(result.score)
        except Exception:
            pass

    if len(scores) < 2:
        return "unknown"

    # Compare first half to second half
    mid = len(scores) // 2
    first_avg = sum(scores[:mid]) / max(mid, 1)
    second_avg = sum(scores[mid:]) / max(len(scores) - mid, 1)

    diff = second_avg - first_avg
    if diff > 0.15:
        return "improving"
    elif diff < -0.15:
        return "declining"
    return "stable"
