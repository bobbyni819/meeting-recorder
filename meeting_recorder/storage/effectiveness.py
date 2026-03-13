"""Meeting effectiveness analysis across recordings.

Compares productivity scores over time and across meeting types to
identify which meetings are most/least effective and track trends.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MeetingEffectiveness:
    """Effectiveness data for a single recording."""
    name: str
    date_str: str
    subject: str
    productivity_score: int
    duration_minutes: float
    action_items: int
    attendee_count: int
    cost_per_action: float  # estimated cost / action items


@dataclass
class EffectivenessReport:
    """Cross-recording effectiveness analysis."""
    total_meetings: int
    avg_productivity: float
    trend: str  # "improving", "declining", "stable"
    trend_pct: float
    most_effective: list[MeetingEffectiveness]  # top 5
    least_effective: list[MeetingEffectiveness]  # bottom 5
    by_subject: list[tuple[str, float, int]]  # (subject, avg_score, count)
    by_weekday: list[tuple[str, float]]  # (day_name, avg_score)
    by_time_of_day: list[tuple[str, float]]  # (slot, avg_score)
    recommendations: list[str]


def analyze_effectiveness(
    recordings_dir: Path,
    weeks: int = 8,
    hourly_rate: float = 75.0,
) -> EffectivenessReport | None:
    """Analyze meeting effectiveness across recordings.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to include.
        hourly_rate: For cost-per-action calculation.

    Returns:
        EffectivenessReport or None if insufficient data.
    """
    if not recordings_dir.exists():
        return None

    cutoff = date.today() - timedelta(weeks=weeks)
    meetings: list[MeetingEffectiveness] = []
    subject_scores: dict[str, list[int]] = defaultdict(list)
    weekday_scores: dict[int, list[int]] = defaultdict(list)
    hour_scores: dict[str, list[int]] = defaultdict(list)
    first_half: list[int] = []
    second_half: list[int] = []
    midpoint = date.today() - timedelta(weeks=weeks // 2)

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue

        try:
            rec_date = date.fromisoformat(rec_dir.name[:10])
        except ValueError:
            continue

        if rec_date < cutoff:
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
        if dur < 60:
            continue

        # Get productivity score
        score = _get_productivity_score(rec_dir, meta)
        if score is None:
            continue

        subject = meta.get("meeting_subject", "")
        attendees = meta.get("meeting_attendees", [])
        action_count = _count_actions(rec_dir)
        attendee_count = max(len(attendees), 1)

        # Cost per action item
        cost = (dur / 3600) * attendee_count * hourly_rate
        cost_per_action = cost / max(action_count, 1)

        me = MeetingEffectiveness(
            name=rec_dir.name,
            date_str=rec_dir.name[:10],
            subject=subject or rec_dir.name[20:].replace("_", " ").strip()[:40],
            productivity_score=score,
            duration_minutes=round(dur / 60, 1),
            action_items=action_count,
            attendee_count=attendee_count,
            cost_per_action=round(cost_per_action, 2),
        )
        meetings.append(me)

        # Group by subject
        if subject:
            subject_scores[subject].append(score)

        # Group by weekday
        weekday_scores[rec_date.weekday()].append(score)

        # Group by time of day
        if len(rec_dir.name) >= 13:
            try:
                hour = int(rec_dir.name[11:13])
                slot = _hour_to_slot(hour)
                hour_scores[slot].append(score)
            except (ValueError, IndexError):
                pass

        # Trend tracking
        if rec_date < midpoint:
            first_half.append(score)
        else:
            second_half.append(score)

    if len(meetings) < 2:
        return None

    avg_prod = sum(m.productivity_score for m in meetings) / len(meetings)

    # Trend
    first_avg = sum(first_half) / max(len(first_half), 1) if first_half else avg_prod
    second_avg = sum(second_half) / max(len(second_half), 1) if second_half else avg_prod

    if first_avg > 0:
        trend_pct = ((second_avg - first_avg) / first_avg) * 100
    else:
        trend_pct = 0.0

    if trend_pct > 10:
        trend = "improving"
    elif trend_pct < -10:
        trend = "declining"
    else:
        trend = "stable"

    # Sort by score
    sorted_meetings = sorted(meetings, key=lambda m: m.productivity_score, reverse=True)
    most_effective = sorted_meetings[:5]
    least_effective = sorted_meetings[-5:][::-1]

    # By subject
    by_subject = [
        (subj, round(sum(scores) / len(scores), 1), len(scores))
        for subj, scores in sorted(subject_scores.items(), key=lambda x: -sum(x[1]) / len(x[1]))
    ][:8]

    # By weekday
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_weekday = [
        (day_names[d], round(sum(scores) / len(scores), 1))
        for d, scores in sorted(weekday_scores.items())
        if scores
    ]

    # By time of day
    by_time = [
        (slot, round(sum(scores) / len(scores), 1))
        for slot, scores in sorted(hour_scores.items())
        if scores
    ]

    # Generate recommendations
    recommendations = _generate_recommendations(
        meetings, by_weekday, by_time, by_subject, trend
    )

    return EffectivenessReport(
        total_meetings=len(meetings),
        avg_productivity=round(avg_prod, 1),
        trend=trend,
        trend_pct=round(trend_pct, 1),
        most_effective=most_effective,
        least_effective=least_effective,
        by_subject=by_subject,
        by_weekday=by_weekday,
        by_time_of_day=by_time,
        recommendations=recommendations,
    )


def format_effectiveness(report: EffectivenessReport | None) -> str:
    """Format effectiveness report as readable text."""
    if report is None:
        return "Not enough data for effectiveness analysis."

    lines = [
        "MEETING EFFECTIVENESS",
        "=" * 55,
        "",
        f"  Meetings analyzed:  {report.total_meetings}",
        f"  Avg productivity:   {report.avg_productivity:.0f}/100",
        f"  Trend:              {report.trend} ({report.trend_pct:+.0f}%)",
        "",
    ]

    # Most effective
    if report.most_effective:
        lines.append("  Most Effective Meetings")
        lines.append("  " + "-" * 50)
        for me in report.most_effective:
            lines.append(
                f"    {me.productivity_score:>3}/100  "
                f"{me.subject[:30]:<30}  "
                f"{me.duration_minutes:.0f}m  "
                f"{me.action_items} actions"
            )
        lines.append("")

    # Least effective
    if report.least_effective:
        lines.append("  Least Effective Meetings")
        lines.append("  " + "-" * 50)
        for me in report.least_effective:
            lines.append(
                f"    {me.productivity_score:>3}/100  "
                f"{me.subject[:30]:<30}  "
                f"${me.cost_per_action:.0f}/action"
            )
        lines.append("")

    # By subject
    if report.by_subject:
        lines.append("  Effectiveness by Meeting Type")
        lines.append("  " + "-" * 50)
        for subj, avg, count in report.by_subject:
            bar = "#" * min(int(avg / 5), 20)
            lines.append(f"    {subj[:25]:<25}  {avg:>5.0f}  x{count:<3}  {bar}")
        lines.append("")

    # By weekday
    if report.by_weekday:
        lines.append("  Effectiveness by Day")
        lines.append("  " + "-" * 50)
        for day, avg in report.by_weekday:
            bar = "#" * min(int(avg / 5), 20)
            lines.append(f"    {day:<12}  {avg:>5.0f}  {bar}")
        lines.append("")

    # By time
    if report.by_time_of_day:
        lines.append("  Effectiveness by Time")
        lines.append("  " + "-" * 50)
        for slot, avg in report.by_time_of_day:
            bar = "#" * min(int(avg / 5), 20)
            lines.append(f"    {slot:<12}  {avg:>5.0f}  {bar}")
        lines.append("")

    # Recommendations
    if report.recommendations:
        lines.append("  Recommendations")
        lines.append("  " + "-" * 50)
        for rec in report.recommendations:
            lines.append(f"    * {rec}")
        lines.append("")

    return "\n".join(lines)


# --- Helpers ---


def _get_productivity_score(rec_dir: Path, meta: dict) -> int | None:
    """Get productivity score for a recording."""
    try:
        from meeting_recorder.storage.productivity import score_productivity
        result = score_productivity(rec_dir, meta=meta)
        return result.overall if result else None
    except Exception:
        return None


def _count_actions(rec_dir: Path) -> int:
    """Count action items for a recording."""
    ai_path = rec_dir / "action_items.json"
    if not ai_path.exists():
        return 0
    try:
        with open(ai_path, "r", encoding="utf-8") as f:
            return len(json.load(f))
    except Exception:
        return 0


def _hour_to_slot(hour: int) -> str:
    """Map hour to time-of-day slot."""
    if hour < 9:
        return "Early (< 9am)"
    elif hour < 12:
        return "Morning"
    elif hour < 14:
        return "Midday"
    elif hour < 17:
        return "Afternoon"
    else:
        return "Evening (5pm+)"


def _generate_recommendations(
    meetings: list[MeetingEffectiveness],
    by_weekday: list[tuple[str, float]],
    by_time: list[tuple[str, float]],
    by_subject: list[tuple[str, float, int]],
    trend: str,
) -> list[str]:
    """Generate actionable recommendations from the data."""
    recs: list[str] = []

    # Trend-based
    if trend == "declining":
        recs.append("Meeting productivity is declining — consider reviewing meeting formats")
    elif trend == "improving":
        recs.append("Meeting productivity is improving — keep up the good practices")

    # Find best/worst day
    if len(by_weekday) >= 2:
        sorted_days = sorted(by_weekday, key=lambda x: x[1])
        worst_day = sorted_days[0]
        best_day = sorted_days[-1]
        if best_day[1] - worst_day[1] > 15:
            recs.append(
                f"{best_day[0]} meetings are most effective ({best_day[1]:.0f}) — "
                f"schedule important meetings then"
            )
            recs.append(
                f"{worst_day[0]} meetings score lowest ({worst_day[1]:.0f}) — "
                f"consider reducing meetings on this day"
            )

    # Find best/worst time
    if len(by_time) >= 2:
        sorted_times = sorted(by_time, key=lambda x: x[1])
        worst_time = sorted_times[0]
        best_time = sorted_times[-1]
        if best_time[1] - worst_time[1] > 15:
            recs.append(
                f"{best_time[0]} meetings are most effective ({best_time[1]:.0f})"
            )

    # Cost efficiency
    high_cost = [m for m in meetings if m.cost_per_action > 500 and m.action_items == 0]
    if len(high_cost) > 2:
        recs.append(
            f"{len(high_cost)} meetings produced zero action items — "
            f"consider if these need a meeting format"
        )

    # Short vs long
    short = [m for m in meetings if m.duration_minutes < 15]
    long_meetings = [m for m in meetings if m.duration_minutes > 60]
    if short:
        avg_short = sum(m.productivity_score for m in short) / len(short)
        if long_meetings:
            avg_long = sum(m.productivity_score for m in long_meetings) / len(long_meetings)
            if avg_short > avg_long + 10:
                recs.append("Shorter meetings tend to be more productive than longer ones")

    return recs[:5]
