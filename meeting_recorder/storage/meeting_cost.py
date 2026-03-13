"""Meeting cost estimation.

Estimates the cost of meetings based on duration, number of attendees,
and a configurable hourly rate. Provides both per-meeting and aggregate
cost analysis.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default hourly rate in dollars — a reasonable average for knowledge workers
DEFAULT_HOURLY_RATE = 75.0


@dataclass
class MeetingCost:
    """Cost estimate for a single meeting."""
    duration_hours: float
    attendee_count: int
    hourly_rate: float
    total_cost: float  # duration_hours × attendee_count × hourly_rate
    cost_per_minute: float


def estimate_cost(
    duration_seconds: float,
    attendee_count: int = 1,
    hourly_rate: float = DEFAULT_HOURLY_RATE,
) -> MeetingCost:
    """Estimate the cost of a meeting.

    Args:
        duration_seconds: Meeting duration in seconds.
        attendee_count: Number of people in the meeting (minimum 1).
        hourly_rate: Cost per person per hour.

    Returns:
        MeetingCost with the estimate.
    """
    attendee_count = max(1, attendee_count)
    duration_hours = duration_seconds / 3600
    total = duration_hours * attendee_count * hourly_rate
    cost_per_min = total / max(duration_seconds / 60, 1)

    return MeetingCost(
        duration_hours=round(duration_hours, 2),
        attendee_count=attendee_count,
        hourly_rate=hourly_rate,
        total_cost=round(total, 2),
        cost_per_minute=round(cost_per_min, 2),
    )


def estimate_recording_cost(
    rec_path: Path,
    meta: dict | None = None,
    hourly_rate: float = DEFAULT_HOURLY_RATE,
) -> Optional[MeetingCost]:
    """Estimate the cost of a recorded meeting.

    Uses metadata for duration and attendee count.
    Falls back to 1 attendee if none listed.

    Returns:
        MeetingCost or None if duration is 0.
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
    if duration <= 0:
        return None

    attendees = meta.get("meeting_attendees", [])
    # Count attendees, minimum 1 (the recorder themselves)
    count = max(len(attendees), 1)

    return estimate_cost(duration, count, hourly_rate)


def format_cost(cost: MeetingCost) -> str:
    """Format cost estimate as readable text."""
    parts = [f"${cost.total_cost:,.0f}"]
    if cost.attendee_count > 1:
        parts.append(f"({cost.attendee_count} people \u00d7 {cost.duration_hours:.1f}h "
                     f"\u00d7 ${cost.hourly_rate:.0f}/hr)")
    else:
        parts.append(f"({cost.duration_hours:.1f}h \u00d7 ${cost.hourly_rate:.0f}/hr)")
    return "  ".join(parts)


def aggregate_costs(
    recordings_dir: Path,
    hourly_rate: float = DEFAULT_HOURLY_RATE,
) -> dict:
    """Compute aggregate meeting costs across all recordings.

    Returns:
        Dict with total_cost, meeting_count, avg_cost, most_expensive, etc.
    """
    if not recordings_dir.exists():
        return {"total_cost": 0, "meeting_count": 0}

    costs: list[tuple[Path, MeetingCost]] = []

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir():
            continue
        cost = estimate_recording_cost(rec_dir, hourly_rate=hourly_rate)
        if cost:
            costs.append((rec_dir, cost))

    if not costs:
        return {"total_cost": 0, "meeting_count": 0}

    total = sum(c.total_cost for _, c in costs)
    most_expensive = max(costs, key=lambda x: x[1].total_cost)

    return {
        "total_cost": round(total, 2),
        "meeting_count": len(costs),
        "avg_cost": round(total / len(costs), 2),
        "most_expensive_path": str(most_expensive[0]),
        "most_expensive_cost": most_expensive[1].total_cost,
    }
