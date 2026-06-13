"""Meeting duration prediction.

Uses historical recording data to predict expected duration for
recurring meetings, identify meetings that tend to run long,
and detect duration anomalies.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DurationPrediction:
    """Duration prediction for a meeting series."""
    subject: str
    avg_minutes: float
    median_minutes: float
    min_minutes: float
    max_minutes: float
    std_dev: float
    sample_count: int
    trend: str  # "getting_longer", "getting_shorter", "stable"
    predicted_minutes: float  # weighted recent average


@dataclass
class DurationAnomaly:
    """A meeting that ran significantly longer or shorter than expected."""
    folder: str
    subject: str
    actual_minutes: float
    expected_minutes: float
    deviation_pct: float  # how far from expected (positive = longer)


def predict_durations(
    recordings_dir: Path,
    min_occurrences: int = 3,
) -> list[DurationPrediction]:
    """Predict durations for recurring meeting series.

    Args:
        recordings_dir: Base recordings directory.
        min_occurrences: Minimum meetings to form a prediction.

    Returns:
        List of DurationPrediction, sorted by sample count desc.
    """
    if not recordings_dir.exists():
        return []

    # Group durations by normalized subject
    series: defaultdict[str, list[tuple[str, float]]] = defaultdict(list)

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue

        meta = _load_meta(rec_dir)
        dur = meta.get("duration_seconds", 0)
        if dur <= 0:
            continue

        subject = meta.get("meeting_subject", "")
        if not subject:
            subject = rec_dir.name[20:].replace("_", " ").strip() if len(rec_dir.name) > 20 else ""
        if not subject:
            continue

        normalized = _normalize_subject(subject)
        if normalized:
            series[normalized].append((rec_dir.name, dur / 60))

    # Build predictions for series with enough data
    predictions: list[DurationPrediction] = []
    for subject, entries in series.items():
        if len(entries) < min_occurrences:
            continue

        durations = [d for _, d in entries]
        avg = sum(durations) / len(durations)
        sorted_durs = sorted(durations)
        median = statistics.median(durations)
        std = (sum((d - avg) ** 2 for d in durations) / len(durations)) ** 0.5

        # Trend: compare first half to second half
        mid = len(durations) // 2
        first_half_avg = sum(durations[:mid]) / max(mid, 1)
        second_half_avg = sum(durations[mid:]) / max(len(durations) - mid, 1)

        if first_half_avg > 0:
            change = (second_half_avg - first_half_avg) / first_half_avg
            if change > 0.15:
                trend = "getting_longer"
            elif change < -0.15:
                trend = "getting_shorter"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Predicted: weighted average favoring recent
        weights = [1 + i * 0.5 for i in range(len(durations))]
        predicted = sum(d * w for d, w in zip(durations, weights)) / sum(weights)

        predictions.append(DurationPrediction(
            subject=subject,
            avg_minutes=round(avg, 1),
            median_minutes=round(median, 1),
            min_minutes=round(sorted_durs[0], 1),
            max_minutes=round(sorted_durs[-1], 1),
            std_dev=round(std, 1),
            sample_count=len(entries),
            trend=trend,
            predicted_minutes=round(predicted, 1),
        ))

    predictions.sort(key=lambda p: -p.sample_count)
    return predictions


def find_anomalies(
    recordings_dir: Path,
    threshold: float = 0.5,
    max_results: int = 10,
) -> list[DurationAnomaly]:
    """Find meetings that deviated significantly from their series average.

    Args:
        recordings_dir: Base recordings directory.
        threshold: Minimum deviation ratio to report (0.5 = 50%).
        max_results: Maximum anomalies to return.

    Returns:
        List of DurationAnomaly, sorted by deviation desc.
    """
    predictions = predict_durations(recordings_dir, min_occurrences=3)
    pred_map = {p.subject: p for p in predictions}

    if not pred_map:
        return []

    anomalies: list[DurationAnomaly] = []

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue

        meta = _load_meta(rec_dir)
        dur = meta.get("duration_seconds", 0)
        if dur <= 0:
            continue

        subject = meta.get("meeting_subject", "")
        if not subject:
            subject = rec_dir.name[20:].replace("_", " ").strip() if len(rec_dir.name) > 20 else ""

        normalized = _normalize_subject(subject)
        pred = pred_map.get(normalized)
        if not pred:
            continue

        actual_min = dur / 60
        expected = pred.avg_minutes
        if expected > 0:
            deviation = (actual_min - expected) / expected
            if abs(deviation) >= threshold:
                anomalies.append(DurationAnomaly(
                    folder=rec_dir.name,
                    subject=subject,
                    actual_minutes=round(actual_min, 1),
                    expected_minutes=round(expected, 1),
                    deviation_pct=round(deviation * 100, 1),
                ))

    anomalies.sort(key=lambda a: -abs(a.deviation_pct))
    return anomalies[:max_results]


def format_predictions(predictions: list[DurationPrediction]) -> str:
    """Format duration predictions as readable text."""
    if not predictions:
        return "Not enough data for duration predictions (need 3+ occurrences)."

    lines = ["DURATION PREDICTIONS", "=" * 50, ""]

    for pred in predictions[:10]:
        trend_arrow = {
            "getting_longer": "\u2197",
            "getting_shorter": "\u2198",
            "stable": "\u2192",
        }.get(pred.trend, "")

        lines.append(f"  {pred.subject}")
        lines.append(f"    Predicted: {pred.predicted_minutes:.0f} min  "
                     f"(avg {pred.avg_minutes:.0f}, "
                     f"range {pred.min_minutes:.0f}-{pred.max_minutes:.0f}) "
                     f"{trend_arrow}")
        lines.append(f"    Based on {pred.sample_count} meetings, "
                     f"\u00b1{pred.std_dev:.0f} min std dev")
        lines.append("")

    return "\n".join(lines)


def _normalize_subject(subject: str) -> str:
    """Normalize a meeting subject for grouping."""
    s = subject.lower().strip()
    # Strip RE:/FW: prefixes
    s = re.sub(r"^(re|fw|fwd)\s*:\s*", "", s)
    # Strip trailing dates/numbers
    s = re.sub(r"\s*[-–]?\s*\d{4}[-/]\d{1,2}[-/]\d{1,4}\s*$", "", s)
    s = re.sub(r"\s*[-/]\s*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\s*$", "", s)
    s = re.sub(r"\s*#?\d+\s*$", "", s)
    # Strip week/day references
    s = re.sub(r"\s*[-–]\s*(mon|tue|wed|thu|fri|sat|sun|week|wk)\w*\s*$", "", s, flags=re.IGNORECASE)
    return s.strip()


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
