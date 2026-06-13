"""Daily meeting summary.

Generates a compact overview of all meetings recorded today —
total time in meetings, decisions made, action items, and per-meeting stats.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DailyMeetingEntry:
    """Summary of a single meeting from today."""
    time: str  # HH:MM
    subject: str
    duration_min: int
    speaker_count: int
    app_name: str
    action_count: int
    decision_count: int
    quality_score: int | None
    meeting_type: str
    path: str


@dataclass
class DailySummary:
    """Overview of all meetings from a given day."""
    date: str
    meetings: list[DailyMeetingEntry]
    total_minutes: int
    total_action_items: int
    total_decisions: int
    free_time_pct: float  # percentage of 8-hour workday NOT in meetings
    busiest_hour: str  # "09:00-10:00" etc.


def generate_daily_summary(
    recordings_dir: Path,
    target_date: date | None = None,
    work_hours: int = 8,
) -> DailySummary | None:
    """Generate a summary of all meetings for a given day.

    Args:
        recordings_dir: Base recordings directory.
        target_date: Date to summarize (defaults to today).
        work_hours: Working day length for free-time calculation.

    Returns:
        DailySummary or None if no meetings found.
    """
    if not recordings_dir.exists():
        return None

    if target_date is None:
        target_date = date.today()

    date_str = target_date.isoformat()
    entries: list[DailyMeetingEntry] = []
    hour_minutes: dict[int, int] = {}  # hour → total minutes

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir():
            continue
        name = rec_dir.name
        if len(name) < 10:
            continue
        if not name.startswith(date_str):
            continue

        # Load metadata
        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        dur_sec = meta.get("duration_seconds", 0)
        if dur_sec < 30:
            continue

        dur_min = round(dur_sec / 60)

        # Extract time from directory name: YYYY-MM-DD_HH-MM-SS
        time_str = ""
        if len(name) >= 19:
            try:
                time_str = name[11:13] + ":" + name[14:16]
            except (IndexError, ValueError):
                pass

        subject = meta.get("meeting_subject", "")
        if not subject and len(name) > 20:
            subject = name[20:].replace("_", " ").strip()
        subject = subject or "Meeting"

        # Count action items
        action_count = 0
        ai_path = rec_dir / "action_items.json"
        if ai_path.exists():
            try:
                with open(ai_path, "r", encoding="utf-8") as f:
                    items = json.load(f)
                action_count = len(items)
            except Exception:
                pass

        # Count decisions
        decision_count = 0
        dec_path = rec_dir / "decisions.json"
        if dec_path.exists():
            try:
                with open(dec_path, "r", encoding="utf-8") as f:
                    dec_data = json.load(f)
                decision_count = len(dec_data.get("decisions") or [])
            except Exception:
                pass

        # Quality score
        qs = meta.get("quality_scores", {})
        quality = qs.get("overall_score") if qs else None

        # Meeting type
        meeting_type = ""
        try:
            from meeting_recorder.storage.meeting_classifier import classify_recording
            cls = classify_recording(rec_dir, meta=meta)
            if cls and cls.confidence > 0.2:
                meeting_type = cls.meeting_type
        except Exception:
            pass

        entries.append(DailyMeetingEntry(
            time=time_str,
            subject=subject,
            duration_min=dur_min,
            speaker_count=meta.get("speaker_count", 0),
            app_name=meta.get("app_name", ""),
            action_count=action_count,
            decision_count=decision_count,
            quality_score=quality,
            meeting_type=meeting_type,
            path=str(rec_dir),
        ))

        # Track hour-by-hour meeting time
        if time_str:
            try:
                hour = int(time_str[:2])
                hour_minutes[hour] = hour_minutes.get(hour, 0) + dur_min
            except ValueError:
                pass

    if not entries:
        return None

    total_min = sum(e.duration_min for e in entries)
    total_actions = sum(e.action_count for e in entries)
    total_decisions = sum(e.decision_count for e in entries)
    free_pct = max(0, (work_hours * 60 - total_min) / (work_hours * 60) * 100)

    # Find busiest hour
    busiest = ""
    if hour_minutes:
        peak_hour = max(hour_minutes, key=lambda h: hour_minutes[h])
        next_hour = (peak_hour + 1) % 24
        busiest = f"{peak_hour:02d}:00-{next_hour:02d}:00"

    return DailySummary(
        date=date_str,
        meetings=entries,
        total_minutes=total_min,
        total_action_items=total_actions,
        total_decisions=total_decisions,
        free_time_pct=round(free_pct, 1),
        busiest_hour=busiest,
    )


def format_daily_summary(summary: DailySummary | None) -> str:
    """Format daily summary as readable text."""
    if summary is None:
        return "No meetings recorded today."

    lines = [
        "TODAY'S MEETINGS",
        "=" * 55,
        "",
        f"  Date:           {summary.date}",
        f"  Meetings:       {len(summary.meetings)}",
        f"  Total time:     {summary.total_minutes} min ({summary.total_minutes / 60:.1f}h)",
        f"  Free time:      {summary.free_time_pct:.0f}% of workday",
    ]

    if summary.total_action_items:
        lines.append(f"  Action items:   {summary.total_action_items}")
    if summary.total_decisions:
        lines.append(f"  Decisions:      {summary.total_decisions}")
    if summary.busiest_hour:
        lines.append(f"  Busiest hour:   {summary.busiest_hour}")
    lines.append("")

    # Meeting timeline
    type_labels = {
        "standup": "SU", "planning": "PL", "review": "RV",
        "one_on_one": "11", "all_hands": "AH", "brainstorm": "BS",
        "retrospective": "RT", "interview": "IV", "training": "TR",
        "incident": "IR",
    }

    lines.append("  Timeline")
    lines.append("  " + "-" * 50)
    for m in summary.meetings:
        type_tag = type_labels.get(m.meeting_type, "  ")
        parts = [f"    {m.time or '--:--'}"]
        parts.append(f"[{type_tag}]")
        parts.append(f"{m.subject[:30]:<30}")
        parts.append(f"{m.duration_min:>3}m")
        if m.speaker_count:
            parts.append(f"{m.speaker_count}spk")
        line = "  ".join(parts)
        extras = []
        if m.action_count:
            extras.append(f"{m.action_count} actions")
        if m.decision_count:
            extras.append(f"{m.decision_count} decisions")
        if extras:
            line += f"  ({', '.join(extras)})"
        lines.append(line)
    lines.append("")

    return "\n".join(lines)
