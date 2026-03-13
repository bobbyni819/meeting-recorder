"""Meeting insights engine.

Scans all recordings and generates actionable observations about meeting
patterns, trends, and issues. Each insight has a category, priority, and
human-readable text.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Insight:
    """A single observation or recommendation."""
    category: str  # time_management, productivity, collaboration, issues
    text: str
    priority: int  # 1=high, 2=medium, 3=low
    data: dict  # supporting data for the insight


def generate_insights(
    recordings_dir: Path,
    max_insights: int = 15,
) -> list[Insight]:
    """Generate insights from all recordings.

    Args:
        recordings_dir: Base recordings directory.
        max_insights: Maximum number of insights to return.

    Returns:
        List of Insight objects sorted by priority.
    """
    if not recordings_dir.exists():
        return []

    # Load all recording metadata
    recordings: list[tuple[Path, dict, datetime]] = []
    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        meta = _load_meta(rec_dir)
        try:
            dt = datetime.strptime(rec_dir.name[:10], "%Y-%m-%d")
        except ValueError:
            continue
        recordings.append((rec_dir, meta, dt))

    if not recordings:
        return []

    recordings.sort(key=lambda x: x[2], reverse=True)
    insights: list[Insight] = []

    # Generate insights from different analyzers
    insights.extend(_time_insights(recordings))
    insights.extend(_trend_insights(recordings))
    insights.extend(_collaboration_insights(recordings))
    insights.extend(_issue_insights(recordings))
    insights.extend(_followup_insights(recordings_dir))

    # Sort by priority, deduplicate
    insights.sort(key=lambda i: i.priority)
    return insights[:max_insights]


def format_insights(insights: list[Insight]) -> str:
    """Format insights as readable text."""
    if not insights:
        return "No insights available yet. Record more meetings to see patterns."

    lines: list[str] = []
    lines.append("MEETING INSIGHTS")
    lines.append("=" * 50)
    lines.append("")

    current_category = ""
    for i in insights:
        if i.category != current_category:
            current_category = i.category
            label = current_category.replace("_", " ").title()
            lines.append(f"--- {label} ---")

        marker = "\u2757" if i.priority == 1 else "\u2022"
        lines.append(f"  {marker} {i.text}")

    lines.append("")
    lines.append(f"({len(insights)} insight{'s' if len(insights) != 1 else ''})")
    return "\n".join(lines)


def _time_insights(
    recordings: list[tuple[Path, dict, datetime]],
) -> list[Insight]:
    """Generate time-management insights."""
    insights: list[Insight] = []
    now = datetime.now()

    # This week vs last week
    this_week_start = now - timedelta(days=now.weekday())
    last_week_start = this_week_start - timedelta(days=7)

    this_week_dur = 0.0
    this_week_count = 0
    last_week_dur = 0.0
    last_week_count = 0

    for _, meta, dt in recordings:
        dur = meta.get("duration_seconds", 0)
        if dt.date() >= this_week_start.date():
            this_week_dur += dur
            this_week_count += 1
        elif dt.date() >= last_week_start.date():
            last_week_dur += dur
            last_week_count += 1

    if this_week_count > 0:
        this_h = this_week_dur / 3600
        insights.append(Insight(
            category="time_management",
            text=f"You've had {this_week_count} meeting{'s' if this_week_count != 1 else ''} "
                 f"this week ({this_h:.1f}h total).",
            priority=3,
            data={"this_week_hours": round(this_h, 1), "this_week_count": this_week_count},
        ))

    if this_week_dur > 0 and last_week_dur > 0:
        change_pct = (this_week_dur - last_week_dur) / last_week_dur * 100
        if abs(change_pct) >= 20:
            direction = "more" if change_pct > 0 else "less"
            insights.append(Insight(
                category="time_management",
                text=f"Meeting time is {abs(change_pct):.0f}% {direction} than last week.",
                priority=2 if abs(change_pct) >= 50 else 3,
                data={"change_pct": round(change_pct, 1)},
            ))

    # Work hours in meetings (assuming 40h week)
    if this_week_dur > 0:
        pct_of_week = this_week_dur / (40 * 3600) * 100
        if pct_of_week >= 50:
            insights.append(Insight(
                category="time_management",
                text=f"Meetings consume {pct_of_week:.0f}% of your work week.",
                priority=1,
                data={"pct_of_week": round(pct_of_week, 1)},
            ))

    # Longest meeting this month
    month_recs = [(p, m, d) for p, m, d in recordings
                  if d.month == now.month and d.year == now.year]
    if month_recs:
        longest = max(month_recs, key=lambda x: x[1].get("duration_seconds", 0))
        longest_dur = longest[1].get("duration_seconds", 0)
        if longest_dur >= 3600:  # Only mention if >= 1 hour
            subject = longest[1].get("meeting_subject", longest[0].name[20:])
            hrs = longest_dur / 3600
            insights.append(Insight(
                category="time_management",
                text=f"Longest meeting this month: \"{subject}\" ({hrs:.1f}h).",
                priority=3,
                data={"path": str(longest[0]), "duration_hours": round(hrs, 1)},
            ))

    return insights


def _trend_insights(
    recordings: list[tuple[Path, dict, datetime]],
) -> list[Insight]:
    """Generate trend-based insights from recurring meetings."""
    insights: list[Insight] = []

    # Group by subject to find trends
    subject_recs: dict[str, list[tuple[Path, dict, datetime]]] = defaultdict(list)
    for p, m, d in recordings:
        subject = m.get("meeting_subject", "")
        if subject:
            subject_recs[subject.lower()].append((p, m, d))

    for subject_key, recs in subject_recs.items():
        if len(recs) < 3:
            continue

        # Sort chronologically
        recs.sort(key=lambda x: x[2])

        # Check duration trend
        durations = [m.get("duration_seconds", 0) for _, m, _ in recs]
        if all(d > 0 for d in durations):
            recent_avg = sum(durations[-3:]) / 3
            older_avg = sum(durations[:-3]) / max(len(durations) - 3, 1) if len(durations) > 3 else durations[0]
            if older_avg > 0:
                change = (recent_avg - older_avg) / older_avg * 100
                if abs(change) >= 20:
                    display_subject = recs[0][1].get("meeting_subject", subject_key)
                    direction = "longer" if change > 0 else "shorter"
                    insights.append(Insight(
                        category="trends",
                        text=f"\"{display_subject}\" is getting {abs(change):.0f}% {direction} "
                             f"(avg {recent_avg / 60:.0f}m vs {older_avg / 60:.0f}m).",
                        priority=2 if abs(change) >= 30 else 3,
                        data={"subject": display_subject, "change_pct": round(change, 1)},
                    ))

    return insights


def _collaboration_insights(
    recordings: list[tuple[Path, dict, datetime]],
) -> list[Insight]:
    """Generate collaboration-related insights."""
    insights: list[Insight] = []

    # Count meetings per attendee (last 30 days)
    now = datetime.now()
    cutoff = now - timedelta(days=30)
    attendee_counts: dict[str, int] = defaultdict(int)
    attendee_time: dict[str, float] = defaultdict(float)

    for _, meta, dt in recordings:
        if dt < cutoff:
            continue
        dur = meta.get("duration_seconds", 0)
        for att in meta.get("meeting_attendees", []):
            name = att.strip()
            if name:
                attendee_counts[name] += 1
                attendee_time[name] += dur

    if attendee_counts:
        # Most frequent collaborator
        top = max(attendee_counts.items(), key=lambda x: x[1])
        if top[1] >= 3:
            time_h = attendee_time[top[0]] / 3600
            insights.append(Insight(
                category="collaboration",
                text=f"Most frequent collaborator: {top[0]} "
                     f"({top[1]} meetings, {time_h:.1f}h in the last 30 days).",
                priority=3,
                data={"name": top[0], "count": top[1], "hours": round(time_h, 1)},
            ))

    # Solo meetings (no attendees listed)
    recent = [(p, m, d) for p, m, d in recordings if d >= cutoff]
    solo = [r for r in recent if not r[1].get("meeting_attendees")]
    if len(solo) >= 3 and len(recent) > 0:
        pct = len(solo) / len(recent) * 100
        if pct >= 30:
            insights.append(Insight(
                category="collaboration",
                text=f"{len(solo)} of {len(recent)} recent recordings "
                     f"have no attendee information ({pct:.0f}%).",
                priority=3,
                data={"solo_count": len(solo), "total": len(recent)},
            ))

    return insights


def _issue_insights(
    recordings: list[tuple[Path, dict, datetime]],
) -> list[Insight]:
    """Generate insights about recording issues."""
    insights: list[Insight] = []

    # Failed recordings
    failed = [(p, m, d) for p, m, d in recordings if m.get("status") == "error"]
    if failed:
        recent_failed = [r for r in failed
                         if r[2] >= datetime.now() - timedelta(days=14)]
        if recent_failed:
            insights.append(Insight(
                category="issues",
                text=f"{len(recent_failed)} recording{'s' if len(recent_failed) != 1 else ''} "
                     f"failed processing in the last 2 weeks. "
                     f"Try re-processing from the detail view.",
                priority=1,
                data={"failed_count": len(recent_failed),
                      "paths": [str(r[0]) for r in recent_failed[:5]]},
            ))

    # Recordings without transcripts
    no_transcript = []
    for p, m, d in recordings[:20]:  # Check recent ones
        if m.get("status") == "completed" and not (p / "transcript.txt").exists():
            no_transcript.append(p)
    if no_transcript:
        insights.append(Insight(
            category="issues",
            text=f"{len(no_transcript)} completed recording{'s' if len(no_transcript) != 1 else ''} "
                 f"{'have' if len(no_transcript) != 1 else 'has'} no transcript.",
            priority=2,
            data={"count": len(no_transcript)},
        ))

    return insights


def _followup_insights(recordings_dir: Path) -> list[Insight]:
    """Generate insights about open follow-ups."""
    insights: list[Insight] = []
    try:
        from meeting_recorder.storage.followups import gather_followups
        followups = gather_followups(recordings_dir, include_completed=False)
        if len(followups) >= 3:
            insights.append(Insight(
                category="collaboration",
                text=f"You have {len(followups)} open follow-up item{'s' if len(followups) != 1 else ''} "
                     f"across your meetings.",
                priority=2,
                data={"count": len(followups)},
            ))
    except Exception:
        pass
    return insights


def _load_meta(path: Path) -> dict:
    """Load metadata from recording directory."""
    meta_path = path / "metadata.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}
