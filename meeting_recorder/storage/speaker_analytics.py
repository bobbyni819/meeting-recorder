"""Per-speaker analytics for meeting recordings.

Computes detailed speaking metrics from transcript.json segment data:
talk time, word count, speaking rate, turn count, silences, and cross-talk.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SpeakerStats:
    """Analytics for a single speaker in a recording."""
    name: str
    talk_seconds: float  # total speaking time
    talk_pct: float  # percentage of meeting duration
    word_count: int
    wpm: float  # words per minute of speaking time
    turn_count: int  # number of speaking turns
    avg_turn_seconds: float  # average turn duration
    longest_turn_seconds: float  # longest uninterrupted segment


@dataclass
class RecordingAnalytics:
    """Full speaker analytics for a recording."""
    duration: float  # meeting duration in seconds
    speakers: list[SpeakerStats]
    total_speech_seconds: float  # all speakers combined
    silence_seconds: float  # time with no one speaking
    silence_pct: float
    crosstalk_seconds: float  # overlapping speech
    crosstalk_pct: float
    turn_count: int  # total turns across all speakers
    avg_turn_seconds: float  # average turn duration


def analyze_speakers(
    rec_path: Path,
    meta: dict | None = None,
) -> Optional[RecordingAnalytics]:
    """Analyze per-speaker metrics for a recording.

    Args:
        rec_path: Recording directory containing transcript.json.
        meta: Optional pre-loaded metadata dict.

    Returns:
        RecordingAnalytics or None if insufficient data.
    """
    # Load transcript segments
    transcript_path = rec_path / "transcript.json"
    if not transcript_path.exists():
        return None

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            tdata = json.load(f)
    except Exception:
        return None

    segments = tdata.get("segments") or []
    if not segments:
        return None

    # Load metadata for duration and speaker map
    if meta is None:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        else:
            meta = {}

    speaker_map = meta.get("speaker_map", {})
    duration = meta.get("duration_seconds", 0)

    # If no duration, use last segment end
    if duration <= 0:
        duration = max((seg.get("end", 0) for seg in segments), default=0)
    if duration <= 0:
        return None

    # Load transcript text for word counts per speaker
    word_counts = _count_words_per_speaker(segments, speaker_map)

    # Compute per-speaker segment data
    speaker_segments: dict[str, list[tuple[float, float]]] = {}
    for seg in segments:
        raw_name = seg.get("speaker", "Unknown")
        name = speaker_map.get(raw_name, raw_name)
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        if end <= start:
            continue
        speaker_segments.setdefault(name, []).append((start, end))

    if not speaker_segments:
        return None

    # Compute per-speaker stats
    all_intervals: list[tuple[float, float]] = []
    speaker_stats: list[SpeakerStats] = []

    for name, segs in speaker_segments.items():
        segs.sort()
        talk_time = sum(e - s for s, e in segs)
        turn_count = _count_turns(segs)
        longest = max((e - s for s, e in segs), default=0)
        words = word_counts.get(name, 0)
        talk_minutes = talk_time / 60 if talk_time > 0 else 1
        wpm = words / talk_minutes if talk_minutes > 0 else 0

        speaker_stats.append(SpeakerStats(
            name=name,
            talk_seconds=round(talk_time, 1),
            talk_pct=round(talk_time / duration * 100, 1) if duration > 0 else 0,
            word_count=words,
            wpm=round(wpm, 1),
            turn_count=turn_count,
            avg_turn_seconds=round(talk_time / turn_count, 1) if turn_count > 0 else 0,
            longest_turn_seconds=round(longest, 1),
        ))

        all_intervals.extend(segs)

    # Sort speakers by talk time descending
    speaker_stats.sort(key=lambda s: -s.talk_seconds)

    # Compute silence and cross-talk from merged intervals
    total_speech, silence, crosstalk = _compute_coverage(
        all_intervals, duration, speaker_segments
    )

    total_turns = sum(s.turn_count for s in speaker_stats)

    return RecordingAnalytics(
        duration=duration,
        speakers=speaker_stats,
        total_speech_seconds=round(total_speech, 1),
        silence_seconds=round(silence, 1),
        silence_pct=round(silence / duration * 100, 1) if duration > 0 else 0,
        crosstalk_seconds=round(crosstalk, 1),
        crosstalk_pct=round(crosstalk / duration * 100, 1) if duration > 0 else 0,
        turn_count=total_turns,
        avg_turn_seconds=round(
            total_speech / total_turns, 1
        ) if total_turns > 0 else 0,
    )


def format_speaker_analytics(analytics: RecordingAnalytics) -> str:
    """Format analytics as readable text."""
    lines: list[str] = []
    lines.append("SPEAKER ANALYTICS")
    lines.append("=" * 50)
    lines.append("")

    dur_m = analytics.duration / 60
    lines.append(f"Meeting duration: {dur_m:.0f} min")
    lines.append(f"Active speech: {analytics.total_speech_seconds / 60:.1f} min "
                 f"({100 - analytics.silence_pct:.0f}%)")
    lines.append(f"Silence: {analytics.silence_seconds / 60:.1f} min "
                 f"({analytics.silence_pct:.0f}%)")
    if analytics.crosstalk_seconds > 0:
        lines.append(f"Cross-talk: {analytics.crosstalk_seconds / 60:.1f} min "
                     f"({analytics.crosstalk_pct:.0f}%)")
    lines.append(f"Total turns: {analytics.turn_count}")
    lines.append("")

    # Speaker table
    lines.append(f"{'Speaker':<20} {'Time':>8} {'%':>5} {'Words':>6} "
                 f"{'WPM':>5} {'Turns':>6}")
    lines.append("-" * 56)

    for s in analytics.speakers:
        time_str = f"{s.talk_seconds / 60:.1f}m"
        lines.append(
            f"{s.name:<20} {time_str:>8} {s.talk_pct:>4.0f}% "
            f"{s.word_count:>6} {s.wpm:>5.0f} {s.turn_count:>6}"
        )

    return "\n".join(lines)


def _count_words_per_speaker(
    segments: list[dict],
    speaker_map: dict[str, str],
) -> dict[str, int]:
    """Count words per speaker from segment text."""
    counts: dict[str, int] = {}
    for seg in segments:
        raw_name = seg.get("speaker", "Unknown")
        name = speaker_map.get(raw_name, raw_name)
        text = seg.get("text", "")
        if text:
            counts[name] = counts.get(name, 0) + len(text.split())
    return counts


def _count_turns(segments: list[tuple[float, float]], gap_threshold: float = 1.0) -> int:
    """Count speaking turns, merging segments with small gaps.

    Two consecutive segments by the same speaker with < gap_threshold seconds
    between them count as one turn.
    """
    if not segments:
        return 0

    turns = 1
    prev_end = segments[0][1]

    for start, end in segments[1:]:
        if start - prev_end > gap_threshold:
            turns += 1
        prev_end = end

    return turns


def _compute_coverage(
    all_intervals: list[tuple[float, float]],
    duration: float,
    speaker_segments: dict[str, list[tuple[float, float]]],
) -> tuple[float, float, float]:
    """Compute total speech, silence, and cross-talk durations.

    Returns:
        (total_speech_seconds, silence_seconds, crosstalk_seconds)
    """
    if not all_intervals:
        return 0.0, duration, 0.0

    # Merge all intervals to find total covered time
    sorted_intervals = sorted(all_intervals)
    merged: list[tuple[float, float]] = []
    for start, end in sorted_intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    total_speech = sum(e - s for s, e in merged)
    silence = max(0, duration - total_speech)

    # Cross-talk: time where 2+ speakers overlap
    # Build timeline events
    events: list[tuple[float, int]] = []  # (time, +1 or -1)
    for start, end in all_intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda x: (x[0], x[1]))

    crosstalk = 0.0
    active = 0
    overlap_start = 0.0

    for time, delta in events:
        if active >= 2 and time > overlap_start:
            crosstalk += time - overlap_start
        active += delta
        if active >= 2:
            overlap_start = time

    return total_speech, silence, crosstalk
