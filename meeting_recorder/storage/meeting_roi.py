"""Meeting ROI (Return on Investment) calculator.

Estimates the value-to-cost ratio of meetings based on concrete
outputs (action items, decisions) versus time invested by attendees.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default cost per person-hour in dollars
DEFAULT_HOURLY_RATE = 75


@dataclass
class MeetingROI:
    """ROI analysis for a single recording."""
    duration_minutes: float
    attendee_count: int
    person_hours: float
    estimated_cost: float
    action_item_count: int
    decision_count: int
    output_count: int  # total concrete outputs
    cost_per_output: float  # dollars per action item/decision
    roi_score: int  # 0-100
    label: str  # "high_value", "moderate", "low_value", "no_outputs"
    recommendations: list[str]


def calculate_roi(
    rec_path: Path,
    meta: dict | None = None,
    hourly_rate: float = DEFAULT_HOURLY_RATE,
) -> MeetingROI | None:
    """Calculate ROI for a recording.

    Args:
        rec_path: Recording directory path.
        meta: Optional pre-loaded metadata.
        hourly_rate: Assumed hourly rate per attendee.

    Returns:
        MeetingROI or None if insufficient data.
    """
    if meta is None:
        meta = _load_meta(rec_path)

    duration = meta.get("duration_seconds", 0)
    if duration <= 0:
        return None

    duration_min = duration / 60
    attendees = meta.get("meeting_attendees") or []
    attendee_count = max(len(attendees), meta.get("speaker_count", 1))
    person_hours = (duration_min / 60) * attendee_count
    estimated_cost = person_hours * hourly_rate

    # Count action items
    action_count = 0
    try:
        from meeting_recorder.storage.action_items import (
            extract_action_items_for_recording,
        )
        items = extract_action_items_for_recording(rec_path, meta)
        action_count = len(items)
    except Exception:
        pass

    # Count decisions from summary
    decision_count = _count_decisions(rec_path)

    output_count = action_count + decision_count
    cost_per_output = estimated_cost / output_count if output_count > 0 else 0

    # ROI score
    # Based on outputs per person-hour
    outputs_per_person_hour = output_count / person_hours if person_hours > 0 else 0

    if output_count == 0:
        roi_score = 10
        label = "no_outputs"
    elif outputs_per_person_hour >= 2.0:
        roi_score = 90
        label = "high_value"
    elif outputs_per_person_hour >= 1.0:
        roi_score = 75
        label = "high_value"
    elif outputs_per_person_hour >= 0.5:
        roi_score = 60
        label = "moderate"
    elif outputs_per_person_hour >= 0.2:
        roi_score = 40
        label = "moderate"
    else:
        roi_score = 25
        label = "low_value"

    # Bonus for short meetings with outputs
    if duration_min <= 30 and output_count >= 2:
        roi_score = min(100, roi_score + 10)

    # Recommendations
    recommendations = _generate_recommendations(
        duration_min, attendee_count, output_count, person_hours,
    )

    return MeetingROI(
        duration_minutes=round(duration_min, 1),
        attendee_count=attendee_count,
        person_hours=round(person_hours, 1),
        estimated_cost=round(estimated_cost, 0),
        action_item_count=action_count,
        decision_count=decision_count,
        output_count=output_count,
        cost_per_output=round(cost_per_output, 0),
        roi_score=roi_score,
        label=label,
        recommendations=recommendations,
    )


def format_roi(roi: MeetingROI) -> str:
    """Format ROI analysis as readable text."""
    label_indicators = {
        "high_value": "\u2705 High Value",
        "moderate": "\U0001f7e1 Moderate",
        "low_value": "\U0001f7e0 Low Value",
        "no_outputs": "\U0001f534 No Outputs",
    }
    indicator = label_indicators.get(roi.label, roi.label)

    lines = [
        "MEETING ROI",
        "-" * 40,
        f"  Score:       {roi.roi_score}/100  {indicator}",
        f"  Investment:  {roi.person_hours}h person-hours  (~${roi.estimated_cost:,.0f})",
        f"  Outputs:     {roi.action_item_count} action items + "
        f"{roi.decision_count} decisions = {roi.output_count} total",
    ]

    if roi.output_count > 0:
        lines.append(f"  Cost/output: ~${roi.cost_per_output:,.0f}")

    if roi.recommendations:
        lines.append("")
        for rec in roi.recommendations:
            lines.append(f"  \u2192 {rec}")

    return "\n".join(lines)


def aggregate_roi(
    recordings_dir: Path,
    hourly_rate: float = DEFAULT_HOURLY_RATE,
) -> dict:
    """Compute aggregate ROI across all recordings.

    Returns dict with total_cost, total_outputs, avg_roi, best/worst meetings.
    """
    if not recordings_dir.exists():
        return {}

    rois: list[tuple[str, MeetingROI]] = []
    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        roi = calculate_roi(rec_dir, hourly_rate=hourly_rate)
        if roi:
            rois.append((rec_dir.name, roi))

    if not rois:
        return {}

    total_cost = sum(r.estimated_cost for _, r in rois)
    total_outputs = sum(r.output_count for _, r in rois)
    avg_roi = sum(r.roi_score for _, r in rois) / len(rois)

    best = max(rois, key=lambda x: x[1].roi_score)
    worst = min(rois, key=lambda x: x[1].roi_score)

    return {
        "meeting_count": len(rois),
        "total_cost": round(total_cost, 0),
        "total_outputs": total_outputs,
        "avg_roi_score": round(avg_roi, 0),
        "cost_per_output": round(total_cost / total_outputs, 0) if total_outputs > 0 else 0,
        "best_meeting": best[0][:10],
        "best_roi": best[1].roi_score,
        "worst_meeting": worst[0][:10],
        "worst_roi": worst[1].roi_score,
    }


def _count_decisions(rec_path: Path) -> int:
    """Count decision-like statements in summary."""
    summary_path = rec_path / "summary.md"
    if not summary_path.exists():
        return 0
    try:
        text = summary_path.read_text(encoding="utf-8").lower()
        import re
        # Count decision markers
        patterns = [
            r"decided\s+to",
            r"decision\s*:",
            r"agreed\s+to",
            r"will\s+proceed\s+with",
            r"approved\b",
            r"resolved\s+to",
            r"consensus\s+on",
        ]
        count = 0
        for p in patterns:
            count += len(re.findall(p, text))
        return count
    except Exception:
        return 0


def _generate_recommendations(
    duration_min: float,
    attendee_count: int,
    output_count: int,
    person_hours: float,
) -> list[str]:
    """Generate actionable recommendations."""
    recs: list[str] = []

    if output_count == 0:
        recs.append("Consider adding an agenda with expected outcomes")

    if duration_min > 60 and output_count < 3:
        recs.append("Long meeting with few outputs — could be shorter")

    if attendee_count > 6 and output_count < attendee_count:
        recs.append("Many attendees — consider smaller group or async update")

    if person_hours > 5 and output_count < 2:
        recs.append(f"{person_hours:.0f} person-hours invested — ensure clear ROI")

    if duration_min <= 30 and output_count >= 3:
        recs.append("Efficient meeting — keep this format")

    return recs[:3]


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
