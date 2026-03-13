"""Topic timeline analysis.

Segments a meeting transcript into topical sections by detecting topic shifts
based on keyword clustering in time windows. Shows when different topics
were discussed and how long each topic took.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Common stop words to ignore
_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "that", "this", "was", "are",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "not", "no", "so", "if",
    "then", "than", "just", "about", "up", "out", "all", "also", "as",
    "been", "being", "get", "got", "goes", "going", "into", "its", "let",
    "like", "make", "made", "more", "much", "need", "new", "now", "one",
    "only", "our", "over", "own", "put", "say", "said", "see", "she",
    "some", "still", "take", "them", "there", "they", "thing", "think",
    "those", "time", "too", "two", "use", "very", "want", "way", "we",
    "well", "what", "when", "where", "which", "who", "why", "how",
    "you", "your", "I", "me", "my", "he", "him", "his", "her", "us",
    "don't", "doesn't", "didn't", "won't", "can't", "couldn't", "shouldn't",
    "yeah", "yes", "okay", "ok", "um", "uh", "right", "actually",
    "basically", "really", "know", "gonna", "wanna", "gotta",
})


@dataclass
class TopicSegment:
    """A topic segment within the meeting."""
    start_min: float
    end_min: float
    duration_min: float
    keywords: list[str]  # top keywords defining this topic
    label: str  # auto-generated topic label
    speaker_count: int


@dataclass
class TopicTimeline:
    """Topic timeline for an entire recording."""
    segments: list[TopicSegment]
    total_duration_min: float
    topic_count: int
    longest_topic: str
    longest_duration_min: float


def _extract_words(text: str) -> list[str]:
    """Extract meaningful words from text."""
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [w for w in words if w not in _STOP_WORDS]


def analyze_topic_timeline(
    rec_path: Path,
    window_minutes: float = 3.0,
    min_keyword_freq: int = 2,
) -> TopicTimeline | None:
    """Analyze topic timeline of a recording.

    Args:
        rec_path: Recording directory.
        window_minutes: Size of analysis windows in minutes.
        min_keyword_freq: Minimum frequency for a keyword to count.

    Returns:
        TopicTimeline or None if insufficient data.
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
    if len(segments) < 5:
        return None

    max_end = max((s.get("end", 0) for s in segments), default=0)
    if max_end < 120:  # less than 2 minutes
        return None

    total_min = max_end / 60.0
    window_sec = window_minutes * 60.0

    # Build per-window keyword profiles
    num_windows = max(1, int(total_min / window_minutes) + 1)
    window_profiles: list[tuple[Counter, set[str], float, float]] = []

    for i in range(num_windows):
        w_start = i * window_sec
        w_end = (i + 1) * window_sec
        word_counter: Counter = Counter()
        speakers: set[str] = set()

        for seg in segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            if seg_end <= w_start or seg_start >= w_end:
                continue

            text = seg.get("text", "")
            words = _extract_words(text)
            word_counter.update(words)
            speaker = seg.get("speaker", "")
            if speaker:
                speakers.add(speaker)

        window_profiles.append((word_counter, speakers, w_start, w_end))

    # Remove trailing empty windows
    while window_profiles and not window_profiles[-1][0]:
        window_profiles.pop()

    if len(window_profiles) < 2:
        return None

    # Detect topic shifts by comparing adjacent window keyword profiles
    topic_segments: list[TopicSegment] = []
    current_start = window_profiles[0][2]
    current_words: Counter = Counter()
    current_speakers: set[str] = set()
    prev_window_words: Counter = Counter()

    for i, (words, speakers, w_start, w_end) in enumerate(window_profiles):
        if not current_words:
            current_words = Counter(words)
            prev_window_words = Counter(words)
            current_speakers = set(speakers)
            continue

        # Compare against previous window (not accumulated) to catch transitions
        prev_top = set(w for w, _ in prev_window_words.most_common(6))
        curr_top = set(w for w, _ in words.most_common(6))

        if prev_top and curr_top:
            jaccard = len(prev_top & curr_top) / len(prev_top | curr_top)
        else:
            jaccard = 0.0

        # Topic shift if similarity is low
        if jaccard < 0.35 and current_words:
            # Save current topic segment
            top_kw = [w for w, c in current_words.most_common(5) if c >= min_keyword_freq]
            if not top_kw:
                top_kw = [w for w, _ in current_words.most_common(3)]

            end_min = w_start / 60.0
            start_min = current_start / 60.0
            topic_segments.append(TopicSegment(
                start_min=round(start_min, 1),
                end_min=round(end_min, 1),
                duration_min=round(end_min - start_min, 1),
                keywords=top_kw[:5],
                label=", ".join(top_kw[:3]).title() if top_kw else "Discussion",
                speaker_count=len(current_speakers),
            ))

            # Start new topic
            current_start = w_start
            current_words = Counter(words)
            current_speakers = set(speakers)
        else:
            current_words.update(words)
            current_speakers |= speakers
        prev_window_words = Counter(words)

    # Save final topic segment
    if current_words:
        top_kw = [w for w, c in current_words.most_common(5) if c >= min_keyword_freq]
        if not top_kw:
            top_kw = [w for w, _ in current_words.most_common(3)]

        end_min = window_profiles[-1][3] / 60.0
        start_min = current_start / 60.0
        topic_segments.append(TopicSegment(
            start_min=round(start_min, 1),
            end_min=round(min(end_min, total_min), 1),
            duration_min=round(min(end_min, total_min) - start_min, 1),
            keywords=top_kw[:5],
            label=", ".join(top_kw[:3]).title() if top_kw else "Discussion",
            speaker_count=len(current_speakers),
        ))

    if not topic_segments:
        return None

    longest = max(topic_segments, key=lambda t: t.duration_min)

    return TopicTimeline(
        segments=topic_segments,
        total_duration_min=round(total_min, 1),
        topic_count=len(topic_segments),
        longest_topic=longest.label,
        longest_duration_min=longest.duration_min,
    )


def format_topic_timeline(timeline: TopicTimeline | None) -> str:
    """Format topic timeline as readable text."""
    if timeline is None:
        return "Not enough data for topic timeline."

    lines = [
        "TOPIC TIMELINE",
        "-" * 40,
        f"  Duration: {timeline.total_duration_min:.0f} min  |  {timeline.topic_count} topics",
        "",
    ]

    for i, seg in enumerate(timeline.segments):
        marker = "\u25b6" if seg == max(timeline.segments, key=lambda s: s.duration_min) else " "
        lines.append(
            f"  {marker} {seg.start_min:5.0f}-{seg.end_min:>4.0f}m  "
            f"{seg.label:<30}  {seg.duration_min:.0f} min  "
            f"{seg.speaker_count}sp"
        )
        if seg.keywords:
            lines.append(f"              [{', '.join(seg.keywords)}]")

    lines.append("")
    lines.append(f"  Longest topic: {timeline.longest_topic} ({timeline.longest_duration_min:.0f} min)")

    return "\n".join(lines)
