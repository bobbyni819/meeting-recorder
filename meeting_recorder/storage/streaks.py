"""Meeting streaks and habit tracking.

Track recording consistency, meeting-free days, and usage patterns
to encourage regular use and surface work-life balance insights.
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
class StreakInfo:
    """Recording streak information."""
    current_streak: int  # consecutive days with recordings
    longest_streak: int
    streak_start: str  # ISO date when current streak began
    total_recording_days: int
    total_days_tracked: int  # days between first and last recording
    meeting_free_days: int  # weekdays with no meetings in last 4 weeks
    meeting_free_streak: int  # consecutive weekdays without meetings (current)
    busiest_weekday: str  # day name with most recordings overall
    quietest_weekday: str  # day name with fewest recordings overall
    weekly_avg: float  # average recordings per week
    consistency_pct: float  # % of weekdays with recordings (last 4 weeks)


def analyze_streaks(recordings_dir: Path) -> StreakInfo | None:
    """Analyze recording streaks and habits.

    Args:
        recordings_dir: Base recordings directory.

    Returns:
        StreakInfo or None if no recordings found.
    """
    if not recordings_dir.exists():
        return None

    # Collect dates with recordings
    recording_dates: set[date] = set()
    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        try:
            d = date.fromisoformat(rec_dir.name[:10])
            recording_dates.add(d)
        except ValueError:
            continue

    if not recording_dates:
        return None

    sorted_dates = sorted(recording_dates)
    today = date.today()

    # Current streak (consecutive days ending today or yesterday)
    current_streak = 0
    check = today
    # Allow gap for today if no recording yet
    if check not in recording_dates and check.weekday() < 5:
        check = today - timedelta(days=1)
    # Skip weekends backwards
    while check.weekday() >= 5:
        check -= timedelta(days=1)
    while check in recording_dates or check.weekday() >= 5:
        if check in recording_dates:
            current_streak += 1
        check -= timedelta(days=1)

    streak_start = (check + timedelta(days=1)).isoformat()

    # Longest streak
    longest = 0
    run = 0
    prev = None
    for d in sorted_dates:
        if prev is not None:
            gap = (d - prev).days
            # Skip weekends in gap calculation
            weekdays_gap = sum(
                1 for i in range(1, gap)
                if (prev + timedelta(days=i)).weekday() < 5
            )
            if weekdays_gap <= 0:  # consecutive weekdays
                run += 1
            else:
                run = 1
        else:
            run = 1
        longest = max(longest, run)
        prev = d

    # Meeting-free days (weekdays with no meetings, last 4 weeks)
    four_weeks_ago = today - timedelta(weeks=4)
    recent_weekdays = set()
    recent_meeting_days = set()
    d = four_weeks_ago
    while d <= today:
        if d.weekday() < 5:
            recent_weekdays.add(d)
            if d in recording_dates:
                recent_meeting_days.add(d)
        d += timedelta(days=1)
    meeting_free_days = len(recent_weekdays - recent_meeting_days)

    # Current meeting-free streak (consecutive weekdays without meetings)
    mf_streak = 0
    check = today
    while check.weekday() >= 5:
        check -= timedelta(days=1)
    while check not in recording_dates and check >= four_weeks_ago:
        if check.weekday() < 5:
            mf_streak += 1
        check -= timedelta(days=1)

    # Busiest / quietest weekday
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    weekday_counts = [0] * 5
    for d in sorted_dates:
        if d.weekday() < 5:
            weekday_counts[d.weekday()] += 1

    busiest_idx = max(range(5), key=lambda i: weekday_counts[i])
    quietest_idx = min(range(5), key=lambda i: weekday_counts[i])

    # Total days tracked
    total_days = max((sorted_dates[-1] - sorted_dates[0]).days, 1)

    # Weekly average
    weeks_tracked = max(total_days / 7, 1)
    weekly_avg = len(sorted_dates) / weeks_tracked

    # Consistency (% of weekdays with recordings, last 4 weeks)
    consistency = (len(recent_meeting_days) / max(len(recent_weekdays), 1)) * 100

    return StreakInfo(
        current_streak=current_streak,
        longest_streak=longest,
        streak_start=streak_start,
        total_recording_days=len(sorted_dates),
        total_days_tracked=total_days,
        meeting_free_days=meeting_free_days,
        meeting_free_streak=mf_streak,
        busiest_weekday=day_names[busiest_idx],
        quietest_weekday=day_names[quietest_idx],
        weekly_avg=round(weekly_avg, 1),
        consistency_pct=round(consistency, 1),
    )


def format_streaks(info: StreakInfo | None) -> str:
    """Format streak info as readable text."""
    if info is None:
        return "No recording data available."

    lines = ["RECORDING STREAKS", "=" * 40, ""]

    # Current streak with visual
    flame = "\U0001f525" if info.current_streak >= 5 else ""
    lines.append(f"  Current streak:    {info.current_streak} day{'s' if info.current_streak != 1 else ''} {flame}")
    lines.append(f"  Longest streak:    {info.longest_streak} day{'s' if info.longest_streak != 1 else ''}")
    lines.append(f"  Streak start:      {info.streak_start}")
    lines.append("")

    # Usage stats
    lines.append("USAGE")
    lines.append("-" * 40)
    lines.append(f"  Total recording days:  {info.total_recording_days}")
    lines.append(f"  Days tracked:          {info.total_days_tracked}")
    lines.append(f"  Weekly average:        {info.weekly_avg} recordings/week")
    lines.append(f"  Consistency (4 wk):    {info.consistency_pct:.0f}%")
    lines.append("")

    # Work-life balance
    lines.append("WORK-LIFE BALANCE")
    lines.append("-" * 40)
    lines.append(f"  Meeting-free days (4 wk):  {info.meeting_free_days}")
    if info.meeting_free_streak > 0:
        lines.append(f"  Current meeting-free:      {info.meeting_free_streak} day{'s' if info.meeting_free_streak != 1 else ''}")
    lines.append(f"  Busiest day:               {info.busiest_weekday}")
    lines.append(f"  Quietest day:              {info.quietest_weekday}")

    return "\n".join(lines)
