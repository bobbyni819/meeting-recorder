"""Silence gap analysis.

Detects significant silence gaps in recordings from transcript segment
timing. Reports gap locations, durations, and context (who spoke before/after).
Useful for identifying awkward pauses, technical issues, or topic transitions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum gap to report (seconds)
MIN_GAP_SECONDS = 5.0


@dataclass
class SilenceGap:
    """A detected silence gap."""
    start_seconds: float
    duration_seconds: float
    speaker_before: str
    speaker_after: str
    context_before: str  # last few words before gap
    context_after: str  # first few words after gap


@dataclass
class SilenceReport:
    """Silence analysis for a recording."""
    total_gaps: int
    total_silence_seconds: float
    silence_percentage: float
    longest_gap: SilenceGap | None
    avg_gap_seconds: float
    gaps: list[SilenceGap]


def analyze_silence_gaps(
    rec_path: Path,
    min_gap: float = MIN_GAP_SECONDS,
) -> SilenceReport | None:
    """Analyze silence gaps in a recording.

    Args:
        rec_path: Recording directory.
        min_gap: Minimum gap duration in seconds to report.

    Returns:
        SilenceReport or None if insufficient data.
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
    if len(segments) < 3:
        return None

    max_end = max((s.get("end", 0) for s in segments), default=0)
    if max_end < 30:
        return None

    # Sort segments by start time
    sorted_segs = sorted(segments, key=lambda s: s.get("start", 0))

    gaps: list[SilenceGap] = []

    for i in range(1, len(sorted_segs)):
        prev = sorted_segs[i - 1]
        curr = sorted_segs[i]

        prev_end = prev.get("end", 0)
        curr_start = curr.get("start", 0)

        gap_duration = curr_start - prev_end
        if gap_duration >= min_gap:
            # Get context
            prev_text = prev.get("text", "").strip()
            curr_text = curr.get("text", "").strip()
            context_before = prev_text[-50:] if len(prev_text) > 50 else prev_text
            context_after = curr_text[:50] if len(curr_text) > 50 else curr_text

            gaps.append(SilenceGap(
                start_seconds=round(prev_end, 1),
                duration_seconds=round(gap_duration, 1),
                speaker_before=prev.get("speaker", ""),
                speaker_after=curr.get("speaker", ""),
                context_before=context_before,
                context_after=context_after,
            ))

    total_silence = sum(g.duration_seconds for g in gaps)
    silence_pct = (total_silence / max_end * 100) if max_end > 0 else 0
    longest = max(gaps, key=lambda g: g.duration_seconds) if gaps else None
    avg_gap = total_silence / len(gaps) if gaps else 0

    return SilenceReport(
        total_gaps=len(gaps),
        total_silence_seconds=round(total_silence, 1),
        silence_percentage=round(silence_pct, 1),
        longest_gap=longest,
        avg_gap_seconds=round(avg_gap, 1),
        gaps=gaps,
    )


def format_silence_report(report: SilenceReport | None) -> str:
    """Format silence report as readable text."""
    if report is None:
        return "Not enough data for silence analysis."

    lines = [
        "SILENCE GAP ANALYSIS",
        "-" * 40,
        f"  Gaps found:     {report.total_gaps}",
        f"  Total silence:  {report.total_silence_seconds:.0f}s ({report.silence_percentage:.1f}%)",
        f"  Average gap:    {report.avg_gap_seconds:.1f}s",
        "",
    ]

    if report.total_gaps == 0:
        lines.append("  No significant silence gaps detected.")
        return "\n".join(lines)

    if report.longest_gap:
        g = report.longest_gap
        time_min = int(g.start_seconds // 60)
        time_sec = int(g.start_seconds % 60)
        lines.append(f"  Longest gap: {g.duration_seconds:.0f}s at {time_min}:{time_sec:02d}")
        if g.speaker_before:
            lines.append(f"    {g.speaker_before}: ...{g.context_before}")
        if g.speaker_after:
            lines.append(f"    {g.speaker_after}: {g.context_after}...")
        lines.append("")

    # List notable gaps
    sorted_gaps = sorted(report.gaps, key=lambda g: -g.duration_seconds)
    if len(sorted_gaps) > 1:
        lines.append("  Top Gaps")
        for g in sorted_gaps[:8]:
            time_min = int(g.start_seconds // 60)
            time_sec = int(g.start_seconds % 60)
            lines.append(f"    {time_min:2d}:{time_sec:02d}  {g.duration_seconds:5.1f}s  "
                         f"{g.speaker_before or '?'} -> {g.speaker_after or '?'}")

    # Assessment
    lines.append("")
    if report.silence_percentage > 15:
        lines.append("  High silence ratio — may indicate technical issues or disengagement.")
    elif report.silence_percentage > 5:
        lines.append("  Moderate silence — natural pauses for thought and topic transitions.")
    else:
        lines.append("  Low silence — active and engaged discussion throughout.")

    return "\n".join(lines)
