"""Meeting time-of-day pattern analysis.

Analyzes when meetings happen across the week, identifies peak hours,
and correlates time-of-day with meeting quality/engagement.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TimeSlotStats:
    """Statistics for a single time slot."""
    hour: int
    count: int
    total_minutes: int
    avg_quality: float | None
    avg_duration_min: float


@dataclass
class TimePatterns:
    """Meeting time-of-day analysis report."""
    total_meetings: int
    hourly_counts: dict[int, int]  # hour → meeting count
    hourly_minutes: dict[int, int]  # hour → total minutes
    peak_hour: int  # busiest hour (0-23)
    quiet_hours: list[int]  # hours with zero meetings
    morning_count: int  # 6-12
    afternoon_count: int  # 12-17
    evening_count: int  # 17-23
    busiest_day: str  # "Monday", etc.
    day_counts: dict[str, int]  # day name → count
    time_slots: list[TimeSlotStats]
    best_quality_hour: int | None  # hour with highest avg quality


def analyze_time_patterns(
    recordings_dir: Path,
    weeks: int = 12,
) -> TimePatterns | None:
    """Analyze meeting time-of-day patterns.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.

    Returns:
        TimePatterns or None if insufficient data.
    """
    if not recordings_dir.exists():
        return None

    cutoff = date.today() - timedelta(weeks=weeks)
    hourly_counts: dict[int, int] = defaultdict(int)
    hourly_minutes: dict[int, int] = defaultdict(int)
    hourly_quality: dict[int, list[int]] = defaultdict(list)
    hourly_durations: dict[int, list[float]] = defaultdict(list)
    day_counts: dict[str, int] = defaultdict(int)
    total = 0

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 19:
            continue

        try:
            rec_date = date.fromisoformat(rec_dir.name[:10])
        except ValueError:
            continue

        if rec_date < cutoff:
            continue

        # Extract hour
        try:
            hour = int(rec_dir.name[11:13])
        except (IndexError, ValueError):
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
        if dur_sec < 60:
            continue

        dur_min = dur_sec / 60.0
        total += 1
        hourly_counts[hour] += 1
        hourly_minutes[hour] += round(dur_min)
        hourly_durations[hour].append(dur_min)

        # Day of week
        day_name = day_names[rec_date.weekday()]
        day_counts[day_name] += 1

        # Quality
        qs = meta.get("quality_scores", {})
        if qs and qs.get("overall_score") is not None:
            hourly_quality[hour].append(qs["overall_score"])

    if total < 3:
        return None

    # Compute stats
    peak_hour = max(hourly_counts, key=lambda h: hourly_counts[h])
    quiet_hours = [h for h in range(6, 22) if hourly_counts.get(h, 0) == 0]

    morning = sum(hourly_counts.get(h, 0) for h in range(6, 12))
    afternoon = sum(hourly_counts.get(h, 0) for h in range(12, 17))
    evening = sum(hourly_counts.get(h, 0) for h in range(17, 23))

    busiest_day = max(day_counts, key=lambda d: day_counts[d]) if day_counts else ""

    # Time slot stats
    time_slots: list[TimeSlotStats] = []
    for hour in sorted(hourly_counts.keys()):
        cnt = hourly_counts[hour]
        durs = hourly_durations[hour]
        avg_dur = sum(durs) / len(durs) if durs else 0
        quals = hourly_quality.get(hour, [])
        avg_q = sum(quals) / len(quals) if quals else None
        time_slots.append(TimeSlotStats(
            hour=hour,
            count=cnt,
            total_minutes=hourly_minutes[hour],
            avg_quality=round(avg_q, 1) if avg_q is not None else None,
            avg_duration_min=round(avg_dur, 1),
        ))

    # Best quality hour
    best_quality_hour = None
    best_avg = 0
    for hour, quals in hourly_quality.items():
        if len(quals) >= 2:
            avg = sum(quals) / len(quals)
            if avg > best_avg:
                best_avg = avg
                best_quality_hour = hour

    return TimePatterns(
        total_meetings=total,
        hourly_counts=dict(hourly_counts),
        hourly_minutes=dict(hourly_minutes),
        peak_hour=peak_hour,
        quiet_hours=quiet_hours,
        morning_count=morning,
        afternoon_count=afternoon,
        evening_count=evening,
        busiest_day=busiest_day,
        day_counts=dict(day_counts),
        time_slots=time_slots,
        best_quality_hour=best_quality_hour,
    )


def format_time_patterns(report: TimePatterns | None) -> str:
    """Format time patterns as readable text."""
    if report is None:
        return "Not enough data for time pattern analysis."

    lines = [
        "MEETING TIME PATTERNS",
        "=" * 55,
        "",
        f"  Total meetings:     {report.total_meetings}",
        f"  Peak hour:          {report.peak_hour:02d}:00",
        f"  Morning (6-12):     {report.morning_count}",
        f"  Afternoon (12-17):  {report.afternoon_count}",
        f"  Evening (17-22):    {report.evening_count}",
    ]

    if report.busiest_day:
        lines.append(f"  Busiest day:        {report.busiest_day}")

    if report.best_quality_hour is not None:
        lines.append(f"  Best quality hour:  {report.best_quality_hour:02d}:00")

    lines.append("")

    # Hourly distribution chart
    lines.append("  Hourly Distribution")
    lines.append("  " + "-" * 50)
    max_count = max(report.hourly_counts.values()) if report.hourly_counts else 1
    for hour in range(6, 22):
        count = report.hourly_counts.get(hour, 0)
        bar_len = int((count / max(max_count, 1)) * 25) if count > 0 else 0
        bar = "\u2588" * bar_len
        lines.append(f"    {hour:02d}:00  {bar}  {count}")
    lines.append("")

    # Day of week distribution
    if report.day_counts:
        lines.append("  Day of Week")
        lines.append("  " + "-" * 50)
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for day in day_order:
            count = report.day_counts.get(day, 0)
            if count > 0:
                bar = "\u2588" * min(count, 20)
                lines.append(f"    {day[:3]}  {bar}  {count}")
        lines.append("")

    return "\n".join(lines)
