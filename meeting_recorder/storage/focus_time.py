"""Focus time analysis.

Calculates non-meeting (focus) time by subtracting meeting durations
from work hours. Shows daily and weekly focus time to help users
understand their meeting load.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Default work day: 8 hours
DEFAULT_WORK_HOURS = 8.0


@dataclass
class DayFocus:
    """Focus time analysis for a single day."""
    date: str  # ISO format
    work_hours: float
    meeting_hours: float
    focus_hours: float
    focus_pct: float  # percentage of work day that was focus time
    meeting_count: int


@dataclass
class WeekFocus:
    """Focus time analysis for a week."""
    week_start: str  # ISO format Monday
    days: list[DayFocus]
    total_work_hours: float
    total_meeting_hours: float
    total_focus_hours: float
    focus_pct: float
    meeting_count: int
    busiest_day: str  # day name with most meeting time
    focus_day: str  # day name with most focus time


def analyze_focus_time(
    recordings_dir: Path,
    work_hours: float = DEFAULT_WORK_HOURS,
    weeks: int = 4,
) -> list[WeekFocus]:
    """Analyze focus time across recent weeks.

    Args:
        recordings_dir: Base recordings directory.
        work_hours: Hours in a work day (default 8).
        weeks: Number of weeks to analyze (default 4).

    Returns:
        List of WeekFocus objects, most recent first.
    """
    if not recordings_dir.exists():
        return []

    # Collect meeting durations per day
    daily_meetings: dict[str, list[float]] = defaultdict(list)

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        date_str = rec_dir.name[:10]
        try:
            date.fromisoformat(date_str)
        except ValueError:
            continue

        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                dur = meta.get("duration_seconds", 0)
                if dur > 0:
                    daily_meetings[date_str].append(dur)
            except Exception:
                pass

    if not daily_meetings:
        return []

    # Build weekly analysis
    today = date.today()
    result: list[WeekFocus] = []

    for week_offset in range(weeks):
        # Find Monday of this week
        week_start = today - timedelta(days=today.weekday()) - timedelta(weeks=week_offset)

        days: list[DayFocus] = []
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

        for day_offset in range(5):  # Mon-Fri only
            d = week_start + timedelta(days=day_offset)
            date_key = d.isoformat()
            durations = daily_meetings.get(date_key, [])

            meeting_hours = sum(durations) / 3600
            focus = max(0, work_hours - meeting_hours)
            focus_pct = (focus / work_hours * 100) if work_hours > 0 else 100

            days.append(DayFocus(
                date=date_key,
                work_hours=work_hours,
                meeting_hours=round(meeting_hours, 2),
                focus_hours=round(focus, 2),
                focus_pct=round(focus_pct, 1),
                meeting_count=len(durations),
            ))

        total_work = work_hours * 5
        total_meeting = sum(d.meeting_hours for d in days)
        total_focus = max(0, total_work - total_meeting)
        total_count = sum(d.meeting_count for d in days)

        # Find busiest and most-focus days
        busiest_idx = max(range(5), key=lambda i: days[i].meeting_hours)
        focus_idx = max(range(5), key=lambda i: days[i].focus_hours)

        result.append(WeekFocus(
            week_start=week_start.isoformat(),
            days=days,
            total_work_hours=total_work,
            total_meeting_hours=round(total_meeting, 2),
            total_focus_hours=round(total_focus, 2),
            focus_pct=round(total_focus / total_work * 100, 1) if total_work > 0 else 100,
            meeting_count=total_count,
            busiest_day=day_names[busiest_idx],
            focus_day=day_names[focus_idx],
        ))

    return result


def format_focus_report(weeks: list[WeekFocus]) -> str:
    """Format focus time analysis as readable text."""
    if not weeks:
        return "No meeting data available for focus time analysis."

    lines: list[str] = []
    lines.append("FOCUS TIME REPORT")
    lines.append("=" * 50)
    lines.append("")

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]

    for week in weeks:
        lines.append(f"Week of {week.week_start}")
        lines.append("-" * 40)
        lines.append(f"  Focus: {week.total_focus_hours:.1f}h / {week.total_work_hours:.0f}h "
                     f"({week.focus_pct:.0f}%)")
        lines.append(f"  Meetings: {week.total_meeting_hours:.1f}h "
                     f"({week.meeting_count} meeting{'s' if week.meeting_count != 1 else ''})")

        # Daily breakdown
        for i, day in enumerate(week.days):
            if day.meeting_count > 0:
                bar_len = int(day.meeting_hours / week.total_work_hours * 50 * 5) if week.total_work_hours > 0 else 0
                bar_len = min(bar_len, 20)
                bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
                lines.append(
                    f"  {day_names[i]}  {bar}  "
                    f"{day.meeting_hours:.1f}h mtg  {day.focus_hours:.1f}h focus"
                )
            else:
                lines.append(f"  {day_names[i]}  {'.' * 20}  no meetings")

        if week.meeting_count > 0:
            lines.append(f"  Busiest: {week.busiest_day}  |  Most focus: {week.focus_day}")
        lines.append("")

    return "\n".join(lines)
