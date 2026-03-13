"""Meeting cost budget tracker.

Tracks weekly meeting costs, compares against configurable budgets,
and surfaces cost trends and budget alerts.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_HOURLY_RATE = 75.0


@dataclass
class WeeklyCost:
    """Cost for a single week."""
    week_start: str  # ISO date
    meeting_count: int
    total_hours: float
    total_person_hours: float
    total_cost: float


@dataclass
class CostBudget:
    """Budget tracking across weeks."""
    weekly_costs: list[WeeklyCost]
    total_cost: float
    avg_weekly_cost: float
    budget_per_week: float  # 0 = no budget set
    over_budget_weeks: int
    cost_trend: str  # "increasing", "decreasing", "stable"
    trend_pct: float  # % change recent vs earlier weeks
    top_costly_subjects: list[tuple[str, float]]  # (subject, total_cost)
    top_costly_attendees: list[tuple[str, float]]  # (attendee, total_cost)


def analyze_cost_budget(
    recordings_dir: Path,
    weeks: int = 8,
    hourly_rate: float = DEFAULT_HOURLY_RATE,
    weekly_budget: float = 0.0,
) -> CostBudget | None:
    """Analyze meeting costs over recent weeks.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.
        hourly_rate: Cost per person-hour.
        weekly_budget: Weekly budget cap (0 = no budget).

    Returns:
        CostBudget or None if no recordings.
    """
    if not recordings_dir.exists():
        return None

    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    oldest_week_start = current_week_start - timedelta(weeks=weeks - 1)

    # Collect weekly data
    week_data: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "hours": 0.0, "person_hours": 0.0, "cost": 0.0,
    })
    subject_costs: dict[str, float] = defaultdict(float)
    attendee_costs: dict[str, float] = defaultdict(float)

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        try:
            rec_date = date.fromisoformat(rec_dir.name[:10])
        except ValueError:
            continue

        if rec_date < oldest_week_start:
            continue

        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        dur = meta.get("duration_seconds", 0)
        if dur <= 0:
            continue

        attendees = meta.get("meeting_attendees", [])
        count = max(len(attendees), 1)
        hours = dur / 3600
        person_hours = hours * count
        cost = person_hours * hourly_rate

        wk_start = rec_date - timedelta(days=rec_date.weekday())
        wk_key = wk_start.isoformat()
        week_data[wk_key]["count"] += 1
        week_data[wk_key]["hours"] += hours
        week_data[wk_key]["person_hours"] += person_hours
        week_data[wk_key]["cost"] += cost

        # Track by subject
        subject = meta.get("meeting_subject", "")
        if subject:
            subject_costs[subject] += cost

        # Track by attendee
        for att in attendees:
            att = att.strip()
            if att:
                attendee_costs[att] += cost

    if not week_data:
        return None

    # Build sorted weekly costs
    weekly_costs = []
    check = oldest_week_start
    while check <= current_week_start:
        wk_key = check.isoformat()
        wd = week_data.get(wk_key, {"count": 0, "hours": 0.0, "person_hours": 0.0, "cost": 0.0})
        weekly_costs.append(WeeklyCost(
            week_start=wk_key,
            meeting_count=wd["count"],
            total_hours=round(wd["hours"], 1),
            total_person_hours=round(wd["person_hours"], 1),
            total_cost=round(wd["cost"], 2),
        ))
        check += timedelta(weeks=1)

    total_cost = sum(wc.total_cost for wc in weekly_costs)
    non_zero = [wc for wc in weekly_costs if wc.total_cost > 0]
    avg_weekly = total_cost / max(len(non_zero), 1)

    # Budget check
    over_budget = 0
    if weekly_budget > 0:
        over_budget = sum(1 for wc in weekly_costs if wc.total_cost > weekly_budget)

    # Trend analysis — compare first half to second half
    mid = len(weekly_costs) // 2
    first_half = weekly_costs[:mid]
    second_half = weekly_costs[mid:]
    first_avg = sum(wc.total_cost for wc in first_half) / max(len(first_half), 1)
    second_avg = sum(wc.total_cost for wc in second_half) / max(len(second_half), 1)

    if first_avg > 0:
        trend_pct = ((second_avg - first_avg) / first_avg) * 100
    else:
        trend_pct = 0.0

    if trend_pct > 10:
        cost_trend = "increasing"
    elif trend_pct < -10:
        cost_trend = "decreasing"
    else:
        cost_trend = "stable"

    top_subjects = sorted(subject_costs.items(), key=lambda x: -x[1])[:5]
    top_attendees = sorted(attendee_costs.items(), key=lambda x: -x[1])[:5]

    return CostBudget(
        weekly_costs=weekly_costs,
        total_cost=round(total_cost, 2),
        avg_weekly_cost=round(avg_weekly, 2),
        budget_per_week=weekly_budget,
        over_budget_weeks=over_budget,
        cost_trend=cost_trend,
        trend_pct=round(trend_pct, 1),
        top_costly_subjects=[(s, round(c, 2)) for s, c in top_subjects],
        top_costly_attendees=[(a, round(c, 2)) for a, c in top_attendees],
    )


def format_cost_budget(cb: CostBudget) -> str:
    """Format cost budget report as readable text."""
    lines = [
        "MEETING COST TRACKER",
        "=" * 50,
        "",
        f"  Total cost ({len(cb.weekly_costs)} weeks):  ${cb.total_cost:,.0f}",
        f"  Avg weekly cost:        ${cb.avg_weekly_cost:,.0f}",
        f"  Trend:                  {cb.cost_trend} ({cb.trend_pct:+.0f}%)",
    ]

    if cb.budget_per_week > 0:
        lines.append(f"  Weekly budget:          ${cb.budget_per_week:,.0f}")
        if cb.over_budget_weeks > 0:
            lines.append(f"  Over-budget weeks:      {cb.over_budget_weeks}")
        else:
            lines.append(f"  Over-budget weeks:      none")
    lines.append("")

    # Weekly breakdown
    lines.append("  Weekly Breakdown")
    lines.append("  " + "-" * 44)
    for wc in cb.weekly_costs:
        bar_len = min(int(wc.total_cost / 100), 20)
        bar = "#" * bar_len
        budget_flag = ""
        if cb.budget_per_week > 0 and wc.total_cost > cb.budget_per_week:
            budget_flag = " (!)"
        lines.append(
            f"    w/{wc.week_start[5:]}  "
            f"{wc.meeting_count:>2} mtgs  "
            f"${wc.total_cost:>7,.0f}  "
            f"{bar}{budget_flag}"
        )
    lines.append("")

    # Top costly subjects
    if cb.top_costly_subjects:
        lines.append("  Most Expensive Meetings")
        lines.append("  " + "-" * 44)
        for subj, cost in cb.top_costly_subjects:
            lines.append(f"    {subj[:30]:<30}  ${cost:>7,.0f}")
        lines.append("")

    # Top costly attendees
    if cb.top_costly_attendees:
        lines.append("  Cost by Attendee")
        lines.append("  " + "-" * 44)
        for att, cost in cb.top_costly_attendees:
            lines.append(f"    {att[:25]:<25}  ${cost:>7,.0f}")
        lines.append("")

    return "\n".join(lines)
