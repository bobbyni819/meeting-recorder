"""Weekly meeting report generator.

Produces a comprehensive weekly summary combining recordings, costs,
focus time, efficiency trends, and health into a single report.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WeeklyReport:
    """Comprehensive weekly meeting report."""
    week_start: str  # ISO date
    week_end: str  # ISO date
    recording_count: int
    total_meeting_hours: float
    total_focus_hours: float  # assumes 40h work week
    focus_pct: float
    avg_duration_min: float
    avg_attendees: float
    total_action_items: int
    top_subjects: list[tuple[str, int]]  # (subject, count)
    top_speakers: list[tuple[str, float]]  # (name, hours)
    apps_used: dict[str, int]  # app -> count
    error_count: int
    avg_quality: int | None
    estimated_cost: float
    comparison: str  # vs previous week: "more", "less", "same"
    comparison_delta: float  # hours difference from previous week


def generate_weekly_report(
    recordings_dir: Path,
    week_offset: int = 0,
    hourly_rate: float = 75.0,
    work_hours: float = 40.0,
) -> WeeklyReport | None:
    """Generate a weekly meeting report.

    Args:
        recordings_dir: Base recordings directory.
        week_offset: 0 = current week, 1 = last week, etc.
        hourly_rate: Cost per person-hour.
        work_hours: Total work hours per week.

    Returns:
        WeeklyReport or None if no data.
    """
    if not recordings_dir.exists():
        return None

    today = date.today()
    week_start = today - timedelta(days=today.weekday()) - timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

    # Previous week for comparison
    prev_start = week_start - timedelta(weeks=1)

    week_start_str = week_start.isoformat()
    week_end_str = week_end.isoformat()
    prev_start_str = prev_start.isoformat()

    current_recs: list[dict] = []
    prev_hours = 0.0
    speaker_times: dict[str, float] = defaultdict(float)
    subjects: dict[str, int] = defaultdict(int)
    app_counts: dict[str, int] = defaultdict(int)
    total_attendees = 0
    quality_scores: list[int] = []
    total_actions = 0

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        date_str = rec_dir.name[:10]

        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        dur = meta.get("duration_seconds", 0)

        # Previous week comparison
        if prev_start_str <= date_str < week_start_str:
            prev_hours += dur / 3600

        # Current week
        if week_start_str <= date_str <= week_end_str:
            current_recs.append(meta)

            app = meta.get("app_name", "")
            if app:
                app_counts[app] += 1

            subject = meta.get("meeting_subject", "")
            if subject:
                subjects[subject] += 1

            attendees = meta.get("meeting_attendees", [])
            total_attendees += len(attendees)

            qs = meta.get("quality_scores", {})
            if qs and qs.get("overall_score") is not None:
                quality_scores.append(qs["overall_score"])

            # Speakers from transcript
            tj = rec_dir / "transcript.json"
            if tj.exists():
                try:
                    with open(tj, "r", encoding="utf-8") as f:
                        tdata = json.load(f)
                    smap = meta.get("speaker_map", {})
                    for seg in tdata.get("segments", []):
                        spk = seg.get("speaker", "Unknown")
                        spk = smap.get(spk, spk)
                        d = max(0, seg.get("end", 0) - seg.get("start", 0))
                        speaker_times[spk] += d
                except Exception:
                    pass

            # Action items
            try:
                ai_path = rec_dir / "action_items.json"
                if ai_path.exists():
                    with open(ai_path, "r", encoding="utf-8") as f:
                        items = json.load(f)
                    total_actions += len(items)
            except Exception:
                pass

    if not current_recs:
        return None

    total_hours = sum(m.get("duration_seconds", 0) for m in current_recs) / 3600
    focus_hours = max(0, work_hours - total_hours)
    focus_pct = (focus_hours / work_hours * 100) if work_hours > 0 else 100

    error_count = sum(1 for m in current_recs if m.get("status") == "error")
    avg_dur = (total_hours / len(current_recs)) * 60 if current_recs else 0
    avg_att = total_attendees / len(current_recs) if current_recs else 0
    avg_q = round(sum(quality_scores) / len(quality_scores)) if quality_scores else None

    # Total cost
    total_person_hours = sum(
        m.get("duration_seconds", 0) / 3600 * max(len(m.get("meeting_attendees", [])), 1)
        for m in current_recs
    )
    estimated_cost = total_person_hours * hourly_rate

    # Comparison with previous week
    delta = total_hours - prev_hours
    if abs(delta) < 0.5:
        comparison = "same"
    elif delta > 0:
        comparison = "more"
    else:
        comparison = "less"

    top_subjects = sorted(subjects.items(), key=lambda x: -x[1])[:5]
    top_speakers = sorted(
        [(s, t / 3600) for s, t in speaker_times.items()],
        key=lambda x: -x[1],
    )[:5]

    return WeeklyReport(
        week_start=week_start_str,
        week_end=week_end_str,
        recording_count=len(current_recs),
        total_meeting_hours=round(total_hours, 1),
        total_focus_hours=round(focus_hours, 1),
        focus_pct=round(focus_pct, 1),
        avg_duration_min=round(avg_dur, 0),
        avg_attendees=round(avg_att, 1),
        total_action_items=total_actions,
        top_subjects=top_subjects,
        top_speakers=[(s, round(h, 1)) for s, h in top_speakers],
        apps_used=dict(app_counts),
        error_count=error_count,
        avg_quality=avg_q,
        estimated_cost=round(estimated_cost, 2),
        comparison=comparison,
        comparison_delta=round(abs(delta), 1),
    )


def format_weekly_report(report: WeeklyReport) -> str:
    """Format weekly report as readable text."""
    lines = [
        "WEEKLY MEETING REPORT",
        f"Week of {report.week_start} to {report.week_end}",
        "=" * 50,
        "",
        f"  Meetings:        {report.recording_count}",
        f"  Meeting time:    {report.total_meeting_hours:.1f}h",
        f"  Focus time:      {report.total_focus_hours:.1f}h ({report.focus_pct:.0f}%)",
        f"  Avg duration:    {report.avg_duration_min:.0f} min",
        f"  Avg attendees:   {report.avg_attendees:.1f}",
        f"  Action items:    {report.total_action_items}",
        f"  Est. cost:       ${report.estimated_cost:,.0f}",
    ]

    if report.avg_quality is not None:
        lines.append(f"  Avg quality:     {report.avg_quality}/100")
    if report.error_count:
        lines.append(f"  Errors:          {report.error_count}")

    # Comparison
    if report.comparison == "more":
        lines.append(f"  vs last week:    +{report.comparison_delta:.1f}h more meetings")
    elif report.comparison == "less":
        lines.append(f"  vs last week:    -{report.comparison_delta:.1f}h fewer meetings")
    else:
        lines.append(f"  vs last week:    about the same")

    lines.append("")

    # Top subjects
    if report.top_subjects:
        lines.append("  Top Subjects")
        lines.append("  " + "-" * 30)
        for subj, count in report.top_subjects:
            lines.append(f"    {subj[:30]:<30} x{count}")
        lines.append("")

    # Top speakers
    if report.top_speakers:
        lines.append("  Top Speakers")
        lines.append("  " + "-" * 30)
        for spk, hours in report.top_speakers:
            lines.append(f"    {spk:<20} {hours:.1f}h")
        lines.append("")

    # Platforms
    if report.apps_used:
        apps = ", ".join(f"{app} ({c})" for app, c in sorted(report.apps_used.items(), key=lambda x: -x[1]))
        lines.append(f"  Platforms: {apps}")

    return "\n".join(lines)
