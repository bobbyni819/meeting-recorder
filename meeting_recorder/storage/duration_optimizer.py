"""Meeting duration optimizer.

Analyzes historical meeting durations by subject to suggest optimal
lengths. Identifies meetings that consistently run over or end early,
and recommends schedule adjustments.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DurationSuggestion:
    """Suggested duration for a meeting type."""
    subject: str
    avg_duration_min: float
    median_duration_min: float
    min_duration_min: float
    max_duration_min: float
    count: int
    suggested_slot_min: int  # rounded to nearest 15-min block
    confidence: str  # "high", "medium", "low"
    note: str


@dataclass
class DurationOptimizer:
    """Duration optimization report."""
    suggestions: list[DurationSuggestion]
    total_meetings: int
    total_wasted_minutes: float  # sum of unused scheduled time
    avg_overrun_minutes: float
    top_overrunners: list[tuple[str, float]]  # (subject, avg_overrun_min)
    top_underrunners: list[tuple[str, float]]  # (subject, avg_unused_min)


def analyze_duration_optimization(
    recordings_dir: Path,
    weeks: int = 12,
    scheduled_minutes: dict[str, int] | None = None,
) -> DurationOptimizer | None:
    """Analyze meeting durations and suggest optimal lengths.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.
        scheduled_minutes: Map of subject→scheduled duration in minutes.
            If not provided, assumes standard 30/60 minute slots.

    Returns:
        DurationOptimizer or None if insufficient data.
    """
    if not recordings_dir.exists():
        return None

    cutoff = date.today() - timedelta(weeks=weeks)
    subject_durations: dict[str, list[float]] = defaultdict(list)

    for rec_dir in recordings_dir.iterdir():
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

        subject = meta.get("meeting_subject", "")
        if not subject:
            subject = rec_dir.name[20:].replace("_", " ").strip()[:40] if len(rec_dir.name) > 20 else "Unknown"

        # Normalize subject
        subject = _normalize_subject(subject)
        subject_durations[subject].append(dur / 60)

    if not subject_durations:
        return None

    suggestions: list[DurationSuggestion] = []
    total_wasted = 0.0
    overrun_data: list[tuple[str, float]] = []
    underrun_data: list[tuple[str, float]] = []
    total_meetings = 0

    for subject, durations in sorted(subject_durations.items()):
        if len(durations) < 2:
            continue

        total_meetings += len(durations)
        avg = sum(durations) / len(durations)
        sorted_d = sorted(durations)
        median = sorted_d[len(sorted_d) // 2]
        min_d = sorted_d[0]
        max_d = sorted_d[-1]

        # Suggest a slot rounded to nearest 15 minutes, at ~85th percentile
        p85 = sorted_d[int(len(sorted_d) * 0.85)]
        suggested = _round_to_slot(p85)

        # Confidence based on sample size
        if len(durations) >= 10:
            confidence = "high"
        elif len(durations) >= 5:
            confidence = "medium"
        else:
            confidence = "low"

        # Calculate scheduled vs actual
        scheduled = (scheduled_minutes or {}).get(subject, _guess_scheduled(avg))
        overrun = avg - scheduled
        if overrun > 5:
            overrun_data.append((subject, round(overrun, 1)))
            note = f"Typically runs {overrun:.0f} min over scheduled {scheduled} min"
        elif overrun < -10:
            unused = abs(overrun)
            underrun_data.append((subject, round(unused, 1)))
            total_wasted += unused * len(durations)
            note = f"Typically ends {unused:.0f} min early — consider shortening"
        else:
            note = "Duration matches scheduled time well"

        suggestions.append(DurationSuggestion(
            subject=subject,
            avg_duration_min=round(avg, 1),
            median_duration_min=round(median, 1),
            min_duration_min=round(min_d, 1),
            max_duration_min=round(max_d, 1),
            count=len(durations),
            suggested_slot_min=suggested,
            confidence=confidence,
            note=note,
        ))

    if not suggestions:
        return None

    avg_overrun = (
        sum(o for _, o in overrun_data) / max(len(overrun_data), 1)
        if overrun_data else 0.0
    )

    return DurationOptimizer(
        suggestions=suggestions,
        total_meetings=total_meetings,
        total_wasted_minutes=round(total_wasted, 0),
        avg_overrun_minutes=round(avg_overrun, 1),
        top_overrunners=sorted(overrun_data, key=lambda x: -x[1])[:5],
        top_underrunners=sorted(underrun_data, key=lambda x: -x[1])[:5],
    )


def format_duration_optimizer(report: DurationOptimizer | None) -> str:
    """Format duration optimization report as readable text."""
    if report is None:
        return "Not enough data for duration optimization."

    lines = [
        "MEETING DURATION OPTIMIZER",
        "=" * 55,
        "",
        f"  Meetings analyzed:  {report.total_meetings}",
    ]

    if report.total_wasted_minutes > 0:
        lines.append(f"  Total wasted time:  {report.total_wasted_minutes:.0f} min "
                     f"({report.total_wasted_minutes / 60:.1f}h)")
    if report.avg_overrun_minutes > 0:
        lines.append(f"  Avg overrun:        {report.avg_overrun_minutes:.0f} min")
    lines.append("")

    # Suggestions
    lines.append("  Duration Suggestions")
    lines.append("  " + "-" * 50)
    for s in report.suggestions:
        conf_marker = {"high": "***", "medium": "** ", "low": "*  "}[s.confidence]
        lines.append(
            f"    {s.subject[:28]:<28}  "
            f"avg:{s.avg_duration_min:>5.0f}m  "
            f"suggest:{s.suggested_slot_min:>3}m  "
            f"n={s.count:<3} {conf_marker}"
        )
        lines.append(f"      {s.note}")
    lines.append("")

    # Overrunners
    if report.top_overrunners:
        lines.append("  Meetings That Run Over")
        lines.append("  " + "-" * 50)
        for subj, mins in report.top_overrunners:
            lines.append(f"    {subj[:30]:<30}  +{mins:.0f} min avg")
        lines.append("")

    # Underrunners
    if report.top_underrunners:
        lines.append("  Meetings That End Early")
        lines.append("  " + "-" * 50)
        for subj, mins in report.top_underrunners:
            lines.append(f"    {subj[:30]:<30}  -{mins:.0f} min avg")
        lines.append("")

    # Legend
    lines.append("  Confidence: *** high (10+)  ** medium (5-9)  * low (2-4)")

    return "\n".join(lines)


# --- Helpers ---


def _normalize_subject(subject: str) -> str:
    """Normalize meeting subject for grouping."""
    # Remove date-like suffixes, numbers, extra spaces
    subject = re.sub(r"\s*\d{4}[-/]\d{2}[-/]\d{2}\s*", "", subject)
    subject = re.sub(r"\s*#\d+\s*", "", subject)
    subject = re.sub(r"\s+", " ", subject).strip()
    return subject


def _round_to_slot(minutes: float) -> int:
    """Round to nearest 15-minute meeting slot."""
    return max(15, int(round(minutes / 15) * 15))


def _guess_scheduled(avg_minutes: float) -> int:
    """Guess the scheduled duration based on average actual duration."""
    if avg_minutes <= 20:
        return 15
    elif avg_minutes <= 35:
        return 30
    elif avg_minutes <= 50:
        return 45
    elif avg_minutes <= 70:
        return 60
    elif avg_minutes <= 100:
        return 90
    else:
        return 120
