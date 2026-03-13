"""Speaker interruption analysis.

Detects when speakers overlap or cut each other off based on transcript
segment timing. Reports interruption frequency, who interrupts whom,
and overall meeting flow quality.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Overlap threshold in seconds — segments overlapping by more than this
# count as an interruption
OVERLAP_THRESHOLD = 0.5

# Short gap threshold — if speaker changes within this many seconds,
# it may be a quick interruption rather than natural turn-taking
QUICK_TAKEOVER_THRESHOLD = 0.3


@dataclass
class Interruption:
    """A detected interruption event."""
    time_seconds: float
    interrupter: str
    interrupted: str
    overlap_seconds: float


@dataclass
class InterruptionReport:
    """Interruption analysis for a recording."""
    total_interruptions: int
    interruptions_per_minute: float
    interrupter_counts: dict[str, int]  # speaker -> how many times they interrupted
    interrupted_counts: dict[str, int]  # speaker -> how many times they were interrupted
    top_interrupter: str
    most_interrupted: str
    flow_score: int  # 0-100, higher = fewer interruptions
    pairs: dict[str, int]  # "A -> B" -> count
    interruptions: list[Interruption]


def analyze_interruptions(
    rec_path: Path,
    overlap_threshold: float = OVERLAP_THRESHOLD,
) -> InterruptionReport | None:
    """Analyze speaker interruptions in a recording.

    Args:
        rec_path: Recording directory.
        overlap_threshold: Minimum overlap seconds to count as interruption.

    Returns:
        InterruptionReport or None if insufficient data.
    """
    transcript_path = rec_path / "transcript.json"
    if not transcript_path.exists():
        return None

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            tdata = json.load(f)
    except Exception:
        return None

    segments = tdata.get("segments", [])
    if len(segments) < 4:
        return None

    # Need at least 2 speakers
    speakers = set(s.get("speaker", "") for s in segments)
    speakers.discard("")
    if len(speakers) < 2:
        return None

    # Find total duration
    max_end = max((s.get("end", 0) for s in segments), default=0)
    if max_end < 30:
        return None

    total_min = max_end / 60.0

    interruptions: list[Interruption] = []
    interrupter_counts: dict[str, int] = {}
    interrupted_counts: dict[str, int] = {}
    pair_counts: dict[str, int] = {}

    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]

        prev_speaker = prev.get("speaker", "")
        curr_speaker = curr.get("speaker", "")

        if not prev_speaker or not curr_speaker:
            continue
        if prev_speaker == curr_speaker:
            continue

        prev_end = prev.get("end", 0)
        curr_start = curr.get("start", 0)

        # Check for overlap (current starts before previous ends)
        overlap = prev_end - curr_start
        if overlap >= overlap_threshold:
            interruptions.append(Interruption(
                time_seconds=curr_start,
                interrupter=curr_speaker,
                interrupted=prev_speaker,
                overlap_seconds=round(overlap, 2),
            ))
            interrupter_counts[curr_speaker] = interrupter_counts.get(curr_speaker, 0) + 1
            interrupted_counts[prev_speaker] = interrupted_counts.get(prev_speaker, 0) + 1
            pair_key = f"{curr_speaker} -> {prev_speaker}"
            pair_counts[pair_key] = pair_counts.get(pair_key, 0) + 1

    if not interruptions:
        # Still return a report with 0 interruptions for a clean meeting
        return InterruptionReport(
            total_interruptions=0,
            interruptions_per_minute=0.0,
            interrupter_counts={},
            interrupted_counts={},
            top_interrupter="",
            most_interrupted="",
            flow_score=100,
            pairs={},
            interruptions=[],
        )

    top_interrupter = max(interrupter_counts.items(), key=lambda x: x[1])[0] if interrupter_counts else ""
    most_interrupted = max(interrupted_counts.items(), key=lambda x: x[1])[0] if interrupted_counts else ""

    ipm = len(interruptions) / total_min if total_min > 0 else 0

    # Flow score: 100 = no interruptions, 0 = heavily interrupted
    # Scale: 0 ipm = 100, 2+ ipm = 0
    flow_score = max(0, min(100, int(100 - ipm * 50)))

    return InterruptionReport(
        total_interruptions=len(interruptions),
        interruptions_per_minute=round(ipm, 2),
        interrupter_counts=interrupter_counts,
        interrupted_counts=interrupted_counts,
        top_interrupter=top_interrupter,
        most_interrupted=most_interrupted,
        flow_score=flow_score,
        pairs=pair_counts,
        interruptions=interruptions,
    )


def format_interruption_report(report: InterruptionReport | None) -> str:
    """Format interruption report as readable text."""
    if report is None:
        return "Not enough data for interruption analysis."

    lines = [
        "INTERRUPTION ANALYSIS",
        "-" * 40,
        f"  Total interruptions: {report.total_interruptions}",
        f"  Per minute:          {report.interruptions_per_minute:.1f}",
        f"  Flow score:          {report.flow_score}/100",
        "",
    ]

    if report.total_interruptions == 0:
        lines.append("  No interruptions detected — excellent flow!")
        return "\n".join(lines)

    # Who interrupts most
    if report.interrupter_counts:
        lines.append("  Interruptions By Speaker")
        for name, count in sorted(report.interrupter_counts.items(), key=lambda x: -x[1]):
            lines.append(f"    {name:<20} {count} interruptions")
        lines.append("")

    # Who gets interrupted most
    if report.interrupted_counts:
        lines.append("  Most Interrupted")
        for name, count in sorted(report.interrupted_counts.items(), key=lambda x: -x[1]):
            lines.append(f"    {name:<20} {count} times")
        lines.append("")

    # Top pairs
    if report.pairs:
        lines.append("  Interruption Pairs")
        for pair, count in sorted(report.pairs.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"    {pair:<30} {count}x")
        lines.append("")

    # Flow assessment
    if report.flow_score >= 80:
        lines.append("  Good conversational flow with minimal interruptions.")
    elif report.flow_score >= 50:
        lines.append("  Moderate interruptions — consider structured turn-taking.")
    else:
        lines.append("  High interruption rate — meeting may benefit from a facilitator.")

    return "\n".join(lines)
