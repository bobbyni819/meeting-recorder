"""Meeting energy curve analysis.

Analyzes how meeting engagement changes over the course of a recording
by measuring speaking rate, turn frequency, and sentiment shifts in
time windows. Identifies energy peaks, valleys, and the overall arc.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class EnergyWindow:
    """Energy metrics for a time window."""
    start_min: float
    end_min: float
    turn_count: int  # number of speaker changes
    word_count: int
    speaker_count: int  # unique speakers in window
    words_per_min: float


@dataclass
class EnergyCurve:
    """Energy analysis for an entire recording."""
    windows: list[EnergyWindow]
    peak_window: int  # index of highest energy window
    valley_window: int  # index of lowest energy window
    arc_type: str  # "front-loaded", "back-loaded", "middle-peak", "flat", "declining"
    total_duration_min: float
    avg_wpm: float


def analyze_energy(
    rec_path: Path,
    window_minutes: float = 5.0,
) -> EnergyCurve | None:
    """Analyze energy curve of a recording.

    Args:
        rec_path: Recording directory.
        window_minutes: Size of each analysis window in minutes.

    Returns:
        EnergyCurve or None if insufficient data.
    """
    transcript_path = rec_path / "transcript.json"
    if not transcript_path.exists():
        return None

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            tdata = json.load(f)
    except Exception:
        return None

    segments = tdata.get("segments") or []
    if len(segments) < 5:
        return None

    # Find total duration
    max_end = max((s.get("end", 0) for s in segments), default=0)
    if max_end < 60:  # less than 1 minute
        return None

    total_min = max_end / 60.0
    window_sec = window_minutes * 60

    # Build windows
    windows: list[EnergyWindow] = []
    num_windows = max(1, int(total_min / window_minutes) + 1)

    for i in range(num_windows):
        w_start = i * window_sec
        w_end = (i + 1) * window_sec

        turns = 0
        words = 0
        speakers_in_window: set[str] = set()
        prev_speaker = ""

        for seg in segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            speaker = seg.get("speaker", "")

            # Check overlap with window
            if seg_end <= w_start or seg_start >= w_end:
                continue

            if speaker != prev_speaker:
                turns += 1
                prev_speaker = speaker

            speakers_in_window.add(speaker)

            # Count words in text
            text = seg.get("text", "")
            words += len(re.findall(r"\w+", text))

        window_dur_min = window_minutes
        wpm = words / window_dur_min if window_dur_min > 0 else 0

        windows.append(EnergyWindow(
            start_min=round(w_start / 60, 1),
            end_min=round(w_end / 60, 1),
            turn_count=turns,
            word_count=words,
            speaker_count=len(speakers_in_window),
            words_per_min=round(wpm, 1),
        ))

    # Remove trailing empty windows
    while windows and windows[-1].word_count == 0:
        windows.pop()

    if len(windows) < 2:
        return None

    # Find peak and valley
    energy_scores = [w.words_per_min + w.turn_count * 5 for w in windows]
    peak_idx = energy_scores.index(max(energy_scores))
    valley_idx = energy_scores.index(min(energy_scores))

    # Determine arc type
    first_third = sum(energy_scores[:len(energy_scores) // 3 + 1])
    last_third = sum(energy_scores[2 * len(energy_scores) // 3:])
    middle_third = sum(energy_scores[len(energy_scores) // 3:2 * len(energy_scores) // 3 + 1])

    max_third = max(first_third, middle_third, last_third)
    if max_third == 0:
        arc_type = "flat"
    elif abs(first_third - last_third) < max_third * 0.1:
        arc_type = "flat"
    elif first_third == max_third and first_third > middle_third * 1.2:
        arc_type = "front-loaded"
    elif last_third == max_third and last_third > middle_third * 1.2:
        arc_type = "back-loaded"
    elif middle_third == max_third:
        arc_type = "middle-peak"
    elif first_third > last_third * 1.3:
        arc_type = "declining"
    else:
        arc_type = "flat"

    total_words = sum(w.word_count for w in windows)
    avg_wpm = total_words / total_min if total_min > 0 else 0

    return EnergyCurve(
        windows=windows,
        peak_window=peak_idx,
        valley_window=valley_idx,
        arc_type=arc_type,
        total_duration_min=round(total_min, 1),
        avg_wpm=round(avg_wpm, 1),
    )


def format_energy_curve(curve: EnergyCurve | None) -> str:
    """Format energy curve as readable text with ASCII chart."""
    if curve is None:
        return "Not enough data for energy analysis."

    lines = [
        "MEETING ENERGY CURVE",
        "-" * 40,
        f"  Duration: {curve.total_duration_min:.0f} min  |  Avg WPM: {curve.avg_wpm:.0f}",
        f"  Arc: {curve.arc_type.replace('-', ' ').title()}",
        "",
    ]

    # ASCII energy chart
    max_wpm = max(w.words_per_min for w in curve.windows) if curve.windows else 1
    if max_wpm == 0:
        max_wpm = 1

    lines.append("  Energy Over Time")
    lines.append("  " + "-" * 40)

    for i, w in enumerate(curve.windows):
        bar_len = int((w.words_per_min / max_wpm) * 20)
        bar = "\u2588" * bar_len

        marker = ""
        if i == curve.peak_window:
            marker = " \u25b2 peak"
        elif i == curve.valley_window:
            marker = " \u25bc valley"

        lines.append(
            f"    {w.start_min:5.0f}-{w.end_min:>4.0f}m  {bar:<20}  "
            f"{w.words_per_min:>5.0f} wpm  {w.turn_count:>2}t{marker}"
        )

    lines.append("")

    arc_descriptions = {
        "front-loaded": "Most activity at the start — typical for standups and status updates",
        "back-loaded": "Energy builds toward the end — common in brainstorms and workshops",
        "middle-peak": "Highest engagement in the middle — typical planning and review pattern",
        "declining": "Energy declines over time — consider shorter meetings",
        "flat": "Consistent engagement throughout — well-balanced meeting",
    }
    lines.append(f"  {arc_descriptions.get(curve.arc_type, '')}")

    return "\n".join(lines)
