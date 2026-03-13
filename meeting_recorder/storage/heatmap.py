"""Meeting duration heatmap.

Builds a day-of-week × time-of-day grid showing when meetings
concentrate, helping users identify meeting-heavy time slots.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Time slots: 2-hour blocks from 7am to 7pm
TIME_SLOTS = [
    ("07-09", "7am-9am"),
    ("09-11", "9am-11am"),
    ("11-13", "11am-1pm"),
    ("13-15", "1pm-3pm"),
    ("15-17", "3pm-5pm"),
    ("17-19", "5pm-7pm"),
]

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]


@dataclass
class HeatmapCell:
    """A single cell in the heatmap."""
    day: int  # 0=Mon, 4=Fri
    slot: int  # index into TIME_SLOTS
    total_minutes: float
    count: int


@dataclass
class MeetingHeatmap:
    """Full heatmap data."""
    grid: list[list[float]]  # [day][slot] = total minutes
    counts: list[list[int]]  # [day][slot] = meeting count
    peak_day: str
    peak_slot: str
    peak_minutes: float
    total_meetings: int
    weeks_covered: int


def build_heatmap(
    recordings_dir: Path,
    weeks: int = 8,
) -> MeetingHeatmap | None:
    """Build a meeting heatmap from recording data.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of recent weeks to include.

    Returns:
        MeetingHeatmap or None if no data.
    """
    if not recordings_dir.exists():
        return None

    cutoff = date.today() - timedelta(weeks=weeks)
    grid = [[0.0] * len(TIME_SLOTS) for _ in range(5)]
    counts = [[0] * len(TIME_SLOTS) for _ in range(5)]
    total = 0
    weeks_seen: set[str] = set()

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 19:
            continue

        date_str = rec_dir.name[:10]
        time_str = rec_dir.name[11:19]  # HH-MM-SS

        try:
            rec_date = date.fromisoformat(date_str)
        except ValueError:
            continue

        if rec_date < cutoff or rec_date.weekday() >= 5:
            continue

        # Parse time
        try:
            hour = int(time_str[:2])
        except (ValueError, IndexError):
            continue

        # Find time slot
        slot_idx = _hour_to_slot(hour)
        if slot_idx < 0:
            continue

        # Get duration
        dur_min = 30.0  # default
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                dur_sec = meta.get("duration_seconds", 0)
                if dur_sec > 0:
                    dur_min = dur_sec / 60
            except Exception:
                pass

        day_idx = rec_date.weekday()
        grid[day_idx][slot_idx] += dur_min
        counts[day_idx][slot_idx] += 1
        total += 1
        monday = rec_date - timedelta(days=rec_date.weekday())
        weeks_seen.add(monday.isoformat())

    if total == 0:
        return None

    # Find peak
    peak_val = 0.0
    peak_d = 0
    peak_s = 0
    for d in range(5):
        for s in range(len(TIME_SLOTS)):
            if grid[d][s] > peak_val:
                peak_val = grid[d][s]
                peak_d = d
                peak_s = s

    return MeetingHeatmap(
        grid=grid,
        counts=counts,
        peak_day=DAY_NAMES[peak_d],
        peak_slot=TIME_SLOTS[peak_s][1],
        peak_minutes=peak_val,
        total_meetings=total,
        weeks_covered=len(weeks_seen),
    )


def format_heatmap(heatmap: MeetingHeatmap | None) -> str:
    """Format heatmap as a text grid."""
    if heatmap is None:
        return "No meeting data available for heatmap."

    lines = ["MEETING HEATMAP", "=" * 55, ""]

    # Header row
    header = "       "
    for _, label in TIME_SLOTS:
        header += f"{label:>9}"
    lines.append(header)
    lines.append("       " + "-" * (9 * len(TIME_SLOTS)))

    # Normalize to max for intensity
    max_val = max(max(row) for row in heatmap.grid) if heatmap.grid else 1
    if max_val == 0:
        max_val = 1

    for d in range(5):
        row = f"  {DAY_NAMES[d]}  |"
        for s in range(len(TIME_SLOTS)):
            val = heatmap.grid[d][s]
            count = heatmap.counts[d][s]
            if count == 0:
                row += "    \u00b7    "
            else:
                intensity = val / max_val
                block = _intensity_block(intensity)
                row += f"  {block}{count:2d}m  "
        lines.append(row)

    lines.append("")
    lines.append(f"  Peak: {heatmap.peak_day} {heatmap.peak_slot} "
                 f"({heatmap.peak_minutes:.0f} min total)")
    lines.append(f"  {heatmap.total_meetings} meetings across "
                 f"{heatmap.weeks_covered} week{'s' if heatmap.weeks_covered != 1 else ''}")

    return "\n".join(lines)


def _hour_to_slot(hour: int) -> int:
    """Map an hour (0-23) to a time slot index, or -1 if outside range."""
    if hour < 7 or hour >= 19:
        return -1
    return (hour - 7) // 2


def _intensity_block(ratio: float) -> str:
    """Return a block character based on intensity (0-1)."""
    blocks = " \u2591\u2592\u2593\u2588"
    idx = min(int(ratio * 4), 4)
    return blocks[idx]
