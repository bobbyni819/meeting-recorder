"""Meeting efficiency trend analysis.

Tracks productivity, participation equity, sentiment, and ROI trends
across weeks to show whether meeting culture is improving or declining.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WeekEfficiency:
    """Efficiency metrics for a single week."""
    week_start: str  # YYYY-MM-DD
    meeting_count: int
    avg_duration_min: float
    avg_roi_score: float
    avg_participation_equity: float
    avg_sentiment: float
    total_action_items: int
    total_person_hours: float


@dataclass
class EfficiencyTrend:
    """Trend analysis across multiple weeks."""
    weeks: list[WeekEfficiency]
    overall_direction: str  # "improving", "declining", "stable"
    roi_trend: str
    participation_trend: str
    duration_trend: str  # "shorter" is better
    sparkline: str  # visual trend line


def analyze_efficiency_trend(
    recordings_dir: Path,
    weeks: int = 8,
) -> EfficiencyTrend | None:
    """Analyze meeting efficiency trends over time.

    Args:
        recordings_dir: Base recordings directory.
        weeks: Number of weeks to analyze.

    Returns:
        EfficiencyTrend or None if insufficient data.
    """
    if not recordings_dir.exists():
        return None

    now = datetime.now()
    start = now - timedelta(weeks=weeks)

    # Group recordings by week
    week_data: defaultdict[str, list[tuple[Path, dict]]] = defaultdict(list)

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        try:
            date = datetime.strptime(rec_dir.name[:10], "%Y-%m-%d")
        except ValueError:
            continue

        if date < start:
            continue

        meta = _load_meta(rec_dir)
        if meta.get("duration_seconds", 0) <= 0:
            continue

        week_start = date - timedelta(days=date.weekday())
        week_key = week_start.strftime("%Y-%m-%d")
        week_data[week_key].append((rec_dir, meta))

    if not week_data:
        return None

    # Compute per-week metrics
    week_results: list[WeekEfficiency] = []

    for week_key in sorted(week_data.keys()):
        recs = week_data[week_key]
        count = len(recs)
        durations = [m.get("duration_seconds", 0) / 60 for _, m in recs]
        avg_dur = sum(durations) / count if count > 0 else 0

        # ROI scores
        roi_scores: list[float] = []
        total_actions = 0
        total_person_hours = 0.0
        for rec_path, meta in recs:
            try:
                from meeting_recorder.storage.meeting_roi import calculate_roi
                roi = calculate_roi(rec_path, meta)
                if roi:
                    roi_scores.append(roi.roi_score)
                    total_actions += roi.action_item_count
                    total_person_hours += roi.person_hours
            except Exception:
                pass

        avg_roi = sum(roi_scores) / len(roi_scores) if roi_scores else 0

        # Participation equity
        equity_scores: list[float] = []
        for rec_path, meta in recs:
            try:
                from meeting_recorder.storage.participation import analyze_participation
                ps = analyze_participation(rec_path, meta)
                if ps:
                    equity_scores.append(ps.equity_score)
            except Exception:
                pass

        avg_equity = sum(equity_scores) / len(equity_scores) if equity_scores else 0

        # Sentiment
        sent_scores: list[float] = []
        for rec_path, _ in recs:
            try:
                from meeting_recorder.storage.sentiment import analyze_recording_sentiment
                sent = analyze_recording_sentiment(rec_path)
                if sent:
                    sent_scores.append(sent.score)
            except Exception:
                pass

        avg_sent = sum(sent_scores) / len(sent_scores) if sent_scores else 0

        week_results.append(WeekEfficiency(
            week_start=week_key,
            meeting_count=count,
            avg_duration_min=round(avg_dur, 1),
            avg_roi_score=round(avg_roi, 1),
            avg_participation_equity=round(avg_equity, 1),
            avg_sentiment=round(avg_sent, 2),
            total_action_items=total_actions,
            total_person_hours=round(total_person_hours, 1),
        ))

    if len(week_results) < 2:
        return EfficiencyTrend(
            weeks=week_results,
            overall_direction="stable",
            roi_trend="stable",
            participation_trend="stable",
            duration_trend="stable",
            sparkline="",
        )

    # Compute trends
    roi_trend = _trend_direction([w.avg_roi_score for w in week_results])
    part_trend = _trend_direction([w.avg_participation_equity for w in week_results])
    dur_values = [w.avg_duration_min for w in week_results]
    dur_trend_dir = _trend_direction(dur_values)
    # For duration, shorter is better
    dur_trend = {
        "improving": "shorter",
        "declining": "longer",
        "stable": "stable",
    }.get(dur_trend_dir, "stable")
    # But actually check: if duration is going down, that's "shorter"
    dur_trend = _duration_trend(dur_values)

    # Overall direction: weighted average of metrics
    scores = [w.avg_roi_score for w in week_results if w.avg_roi_score > 0]
    overall = _trend_direction(scores) if scores else "stable"

    # Sparkline from ROI scores
    roi_values = [w.avg_roi_score for w in week_results]
    sparkline = _sparkline(roi_values)

    return EfficiencyTrend(
        weeks=week_results,
        overall_direction=overall,
        roi_trend=roi_trend,
        participation_trend=part_trend,
        duration_trend=dur_trend,
        sparkline=sparkline,
    )


def format_efficiency_trend(trend: EfficiencyTrend) -> str:
    """Format efficiency trend as readable text."""
    if not trend.weeks:
        return "No efficiency data available."

    direction_icons = {
        "improving": "\u2197 Improving",
        "declining": "\u2198 Declining",
        "stable": "\u2192 Stable",
    }

    lines = [
        "MEETING EFFICIENCY TREND",
        "=" * 50,
        f"  Overall:        {direction_icons.get(trend.overall_direction, 'N/A')}",
        f"  ROI trend:      {direction_icons.get(trend.roi_trend, 'N/A')}",
        f"  Participation:  {direction_icons.get(trend.participation_trend, 'N/A')}",
        f"  Duration:       {trend.duration_trend}",
        "",
        f"  ROI sparkline:  {trend.sparkline}",
        "",
    ]

    # Per-week summary
    for week in trend.weeks:
        lines.append(
            f"  w/{week.week_start[5:]}  "
            f"{week.meeting_count} mtgs  "
            f"ROI {week.avg_roi_score:.0f}  "
            f"Equity {week.avg_participation_equity:.0f}  "
            f"~{week.avg_duration_min:.0f}m  "
            f"{week.total_action_items} actions"
        )

    return "\n".join(lines)


def _trend_direction(values: list[float]) -> str:
    """Determine if values are trending up, down, or stable."""
    if len(values) < 2:
        return "stable"

    mid = len(values) // 2
    first_avg = sum(values[:mid]) / max(mid, 1)
    second_avg = sum(values[mid:]) / max(len(values) - mid, 1)

    if first_avg == 0:
        return "stable"

    change = (second_avg - first_avg) / max(abs(first_avg), 1)
    if change > 0.1:
        return "improving"
    elif change < -0.1:
        return "declining"
    return "stable"


def _duration_trend(values: list[float]) -> str:
    """Determine if meeting durations are getting shorter or longer."""
    if len(values) < 2:
        return "stable"

    mid = len(values) // 2
    first_avg = sum(values[:mid]) / max(mid, 1)
    second_avg = sum(values[mid:]) / max(len(values) - mid, 1)

    if first_avg == 0:
        return "stable"

    change = (second_avg - first_avg) / first_avg
    if change > 0.1:
        return "getting longer"
    elif change < -0.1:
        return "getting shorter"
    return "stable"


_SPARK_CHARS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _sparkline(values: list[float]) -> str:
    """Render a sparkline from values."""
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    return "".join(
        _SPARK_CHARS[min(len(_SPARK_CHARS) - 1, int((v - mn) / rng * (len(_SPARK_CHARS) - 1)))]
        for v in values
    )


def _load_meta(rec_dir: Path) -> dict:
    """Load metadata from recording."""
    try:
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}
