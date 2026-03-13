"""Recording health summary.

Generates a quick overview of recording health across all recordings,
surfacing issues like errors, low quality, excessive meeting load,
and storage concerns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class HealthIssue:
    """A single health issue found in recordings."""
    severity: str  # "info", "warning", "error"
    category: str  # "errors", "quality", "storage", "load", "stale"
    message: str
    count: int = 1
    recording_dirs: list[str] = field(default_factory=list)


@dataclass
class HealthSummary:
    """Overall health summary across all recordings."""
    total_recordings: int
    healthy_count: int
    issue_count: int
    issues: list[HealthIssue]
    score: int  # 0-100 overall health score
    label: str  # "healthy", "good", "needs_attention", "unhealthy"


def analyze_health(
    recordings_dir: Path,
    max_age_warn_days: int = 7,
) -> HealthSummary:
    """Analyze recording health across all recordings.

    Args:
        recordings_dir: Base recordings directory.
        max_age_warn_days: Warn if no recordings in this many days.

    Returns:
        HealthSummary with issues and overall score.
    """
    issues: list[HealthIssue] = []
    total = 0
    error_count = 0
    low_quality_count = 0
    latest_date: str | None = None

    if not recordings_dir.exists():
        return HealthSummary(
            total_recordings=0, healthy_count=0, issue_count=0,
            issues=[], score=100, label="healthy",
        )

    error_dirs: list[str] = []
    low_q_dirs: list[str] = []
    total_size = 0
    weekly_hours: dict[str, float] = {}

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir():
            continue
        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue

        total += 1
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        # Track latest date
        name = rec_dir.name
        if len(name) >= 10:
            date_str = name[:10]
            if latest_date is None or date_str > latest_date:
                latest_date = date_str

        # Check for errors
        if meta.get("status") == "error":
            error_count += 1
            error_dirs.append(str(rec_dir))

        # Check quality
        qs = meta.get("quality_scores", {})
        overall = qs.get("overall_score")
        if overall is not None and overall < 40:
            low_quality_count += 1
            low_q_dirs.append(str(rec_dir))

        # Track weekly meeting hours
        dur = meta.get("duration_seconds", 0)
        if len(name) >= 10:
            try:
                d = datetime.strptime(name[:10], "%Y-%m-%d")
                week_start = d - timedelta(days=d.weekday())
                wk = week_start.strftime("%Y-%m-%d")
                weekly_hours[wk] = weekly_hours.get(wk, 0) + dur / 3600
            except ValueError:
                pass

        # Track storage
        for f in rec_dir.iterdir():
            if f.is_file():
                total_size += f.stat().st_size

    # Generate issues
    if error_count > 0:
        issues.append(HealthIssue(
            severity="error", category="errors",
            message=f"{error_count} recording(s) failed with errors",
            count=error_count,
            recording_dirs=error_dirs[:5],
        ))

    if low_quality_count > 0:
        issues.append(HealthIssue(
            severity="warning", category="quality",
            message=f"{low_quality_count} recording(s) have low quality (score < 40)",
            count=low_quality_count,
            recording_dirs=low_q_dirs[:5],
        ))

    # Check for staleness
    if latest_date and total > 0:
        try:
            last = datetime.strptime(latest_date, "%Y-%m-%d")
            days_since = (datetime.now() - last).days
            if days_since > max_age_warn_days:
                issues.append(HealthIssue(
                    severity="info", category="stale",
                    message=f"No recordings in {days_since} days (last: {latest_date})",
                ))
        except ValueError:
            pass

    # Check meeting load (>20h/week is excessive)
    if weekly_hours:
        recent_weeks = sorted(weekly_hours.keys())[-4:]
        for wk in recent_weeks:
            if weekly_hours[wk] > 20:
                issues.append(HealthIssue(
                    severity="warning", category="load",
                    message=f"Week of {wk}: {weekly_hours[wk]:.1f}h in meetings (>20h)",
                ))

    # Check storage (>10GB warning, >50GB error)
    size_gb = total_size / (1024 ** 3)
    if size_gb > 50:
        issues.append(HealthIssue(
            severity="error", category="storage",
            message=f"Recordings using {size_gb:.1f} GB of disk space",
        ))
    elif size_gb > 10:
        issues.append(HealthIssue(
            severity="warning", category="storage",
            message=f"Recordings using {size_gb:.1f} GB of disk space",
        ))

    # Calculate score
    healthy = total - error_count - low_quality_count
    penalty = 0
    for issue in issues:
        if issue.severity == "error":
            penalty += 20
        elif issue.severity == "warning":
            penalty += 10
        else:
            penalty += 2
    score = max(0, min(100, 100 - penalty))

    if score >= 90:
        label = "healthy"
    elif score >= 70:
        label = "good"
    elif score >= 50:
        label = "needs_attention"
    else:
        label = "unhealthy"

    return HealthSummary(
        total_recordings=total,
        healthy_count=healthy,
        issue_count=len(issues),
        issues=issues,
        score=score,
        label=label,
    )


def format_health(hs: HealthSummary) -> str:
    """Format health summary as readable text."""
    if hs.total_recordings == 0:
        return "No recordings found."

    severity_icons = {"error": "!!", "warning": "! ", "info": "  "}

    lines = [
        "RECORDING HEALTH",
        "=" * 40,
        f"  Score: {hs.score}/100 ({hs.label.replace('_', ' ').title()})",
        f"  Recordings: {hs.total_recordings} total, {hs.healthy_count} healthy",
        "",
    ]

    if not hs.issues:
        lines.append("  No issues found.")
    else:
        lines.append("  Issues:")
        for issue in hs.issues:
            icon = severity_icons.get(issue.severity, "  ")
            lines.append(f"  [{icon}] {issue.message}")

    return "\n".join(lines)
