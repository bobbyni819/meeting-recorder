"""Meeting agenda extraction from transcripts.

Analyzes transcript segments to identify topic transitions and build
a structured agenda showing what was discussed, when, and by whom.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Stop words for topic extraction
STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "ought",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "she",
    "it", "they", "them", "their", "this", "that", "these", "those",
    "and", "but", "or", "so", "if", "then", "than", "when", "while",
    "for", "to", "of", "in", "on", "at", "by", "with", "from", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "not", "no", "nor", "just", "also", "very", "really", "actually",
    "like", "know", "think", "right", "yeah", "yes", "okay", "ok",
    "well", "going", "get", "got", "thing", "things", "one", "way",
    "what", "how", "which", "where", "who", "whom", "why",
    "let", "lets", "make", "take", "put", "say", "said", "come",
    "here", "there", "some", "any", "all", "each", "every", "both",
    "up", "out", "off", "over", "down", "back", "still", "even",
})

# Minimum words per segment for topic extraction
MIN_SEGMENT_WORDS = 5


@dataclass
class AgendaItem:
    """A single topic discussed in the meeting."""
    topic: str
    start_time: float  # seconds
    end_time: float  # seconds
    duration_seconds: float
    speakers: list[str]
    key_phrases: list[str]
    segment_count: int


@dataclass
class MeetingAgenda:
    """Extracted agenda from a recording."""
    items: list[AgendaItem]
    total_topics: int
    total_duration: float
    main_speakers: list[str]


def extract_agenda(
    rec_path: Path,
    meta: dict | None = None,
    window_size: int = 5,
    min_topic_duration: float = 30.0,
) -> MeetingAgenda | None:
    """Extract a structured agenda from a recording's transcript.

    Groups transcript segments into topic clusters based on vocabulary
    shifts, then labels each cluster with its key phrases.

    Args:
        rec_path: Path to recording directory.
        meta: Pre-loaded metadata (loaded from file if None).
        window_size: Number of segments to group for topic detection.
        min_topic_duration: Minimum seconds for a topic to be included.

    Returns:
        MeetingAgenda or None if no transcript.
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
    if len(segments) < 2:
        return None

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

    # Build windows of segments
    windows = _build_windows(segments, window_size)
    if not windows:
        return None

    # Detect topic boundaries via vocabulary shift
    boundaries = _detect_boundaries(windows)

    # Group segments into topics
    topics = _group_topics(segments, boundaries, speaker_map, min_topic_duration)

    if not topics:
        return None

    # Collect main speakers across all topics
    all_speakers: Counter = Counter()
    for topic in topics:
        for spk in topic.speakers:
            all_speakers[spk] += 1

    return MeetingAgenda(
        items=topics,
        total_topics=len(topics),
        total_duration=sum(t.duration_seconds for t in topics),
        main_speakers=[s for s, _ in all_speakers.most_common(5)],
    )


def format_agenda(agenda: MeetingAgenda | None) -> str:
    """Format extracted agenda as readable text."""
    if agenda is None:
        return "No agenda could be extracted from this recording."

    lines = [
        "MEETING AGENDA",
        "=" * 50,
        f"  {agenda.total_topics} topics  |  "
        f"{_fmt_time(agenda.total_duration)} total  |  "
        f"Speakers: {', '.join(agenda.main_speakers[:3])}",
        "",
    ]

    for i, item in enumerate(agenda.items, 1):
        time_range = f"{_fmt_time(item.start_time)} - {_fmt_time(item.end_time)}"
        dur = f"({_fmt_time(item.duration_seconds)})"
        lines.append(f"  {i}. {item.topic}")
        lines.append(f"     {time_range}  {dur}")
        if item.speakers:
            lines.append(f"     Speakers: {', '.join(item.speakers[:3])}")
        if item.key_phrases:
            lines.append(f"     Keywords: {', '.join(item.key_phrases[:5])}")
        lines.append("")

    return "\n".join(lines)


# --- Helpers ---


def _fmt_time(seconds: float) -> str:
    """Format seconds as MM:SS or H:MM:SS."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _extract_words(text: str) -> list[str]:
    """Extract meaningful words from text."""
    words = re.findall(r"[a-z]{3,}", text.lower())
    return [w for w in words if w not in STOP_WORDS]


def _build_windows(segments: list[dict], window_size: int) -> list[dict]:
    """Group segments into overlapping windows with word counts."""
    windows = []
    for i in range(0, len(segments), max(window_size // 2, 1)):
        chunk = segments[i:i + window_size]
        if not chunk:
            break
        text = " ".join(seg.get("text", "") for seg in chunk)
        words = _extract_words(text)
        if len(words) < MIN_SEGMENT_WORDS:
            continue
        windows.append({
            "start_idx": i,
            "end_idx": min(i + window_size, len(segments)),
            "start_time": chunk[0].get("start", 0),
            "end_time": chunk[-1].get("end", 0),
            "words": Counter(words),
            "text": text,
        })
    return windows


def _detect_boundaries(windows: list[dict]) -> list[int]:
    """Detect topic boundaries via vocabulary shift between windows.

    Returns list of window indices where topics change.
    """
    if len(windows) < 2:
        return [0]

    boundaries = [0]
    for i in range(1, len(windows)):
        prev_words = windows[i - 1]["words"]
        curr_words = windows[i]["words"]

        # Jaccard distance
        all_words = set(prev_words.keys()) | set(curr_words.keys())
        if not all_words:
            continue
        shared = set(prev_words.keys()) & set(curr_words.keys())
        jaccard = len(shared) / len(all_words) if all_words else 1.0

        # Low overlap = topic shift
        if jaccard < 0.3:
            boundaries.append(i)

    return boundaries


def _group_topics(
    segments: list[dict],
    boundaries: list[int],
    speaker_map: dict,
    min_duration: float,
) -> list[AgendaItem]:
    """Group segments between boundaries into agenda items."""
    # Map boundary window indices to segment indices
    # Each boundary is a window start, but we need segment ranges
    # For simplicity, use time-based grouping from boundary windows

    all_text = " ".join(seg.get("text", "") for seg in segments)
    total_segments = len(segments)

    # Split segments at boundary points
    groups: list[list[dict]] = []
    if not boundaries:
        groups.append(segments)
    else:
        # Convert boundaries to approximate segment indices
        seg_boundaries = []
        segs_per_window = max(total_segments // max(len(boundaries) * 2, 1), 1)
        for b in boundaries:
            idx = min(b * segs_per_window, total_segments)
            seg_boundaries.append(idx)

        for i, start in enumerate(seg_boundaries):
            end = seg_boundaries[i + 1] if i + 1 < len(seg_boundaries) else total_segments
            group = segments[start:end]
            if group:
                groups.append(group)

    topics: list[AgendaItem] = []
    for group in groups:
        if not group:
            continue

        start = group[0].get("start", 0)
        end = group[-1].get("end", 0)
        duration = end - start

        if duration < min_duration:
            continue

        # Extract key phrases
        text = " ".join(seg.get("text", "") for seg in group)
        words = _extract_words(text)
        word_counts = Counter(words)
        key_phrases = [w for w, _ in word_counts.most_common(8)]

        # Label the topic from top phrases
        topic_label = _label_topic(key_phrases, text)

        # Speakers in this group
        speakers: list[str] = []
        seen: set[str] = set()
        for seg in group:
            spk = seg.get("speaker", "Unknown")
            spk = speaker_map.get(spk, spk)
            if spk not in seen:
                seen.add(spk)
                speakers.append(spk)

        topics.append(AgendaItem(
            topic=topic_label,
            start_time=start,
            end_time=end,
            duration_seconds=round(duration, 1),
            speakers=speakers[:5],
            key_phrases=key_phrases[:5],
            segment_count=len(group),
        ))

    return topics


def _label_topic(key_phrases: list[str], text: str) -> str:
    """Generate a human-readable topic label from key phrases."""
    if not key_phrases:
        return "General Discussion"

    # Use top 2-3 phrases, capitalized
    label_words = key_phrases[:3]
    label = " / ".join(w.title() for w in label_words)

    # Look for common meeting patterns
    text_lower = text.lower()
    patterns = [
        (r"\b(status|update|progress)\b", "Status Update"),
        (r"\b(action items?|follow.?ups?|next steps?)\b", "Action Items & Follow-ups"),
        (r"\b(demo|presentation|walkthrough)\b", "Demo / Presentation"),
        (r"\b(design|architecture|approach)\b", "Design Discussion"),
        (r"\b(bug|issue|problem|fix)\b", "Issue Discussion"),
        (r"\b(review|feedback|approval)\b", "Review & Feedback"),
        (r"\b(sprint|planning|roadmap|timeline)\b", "Planning"),
        (r"\b(question|clarif|explain)\b", "Q&A"),
        (r"\b(intro|welcome|agenda)\b", "Introduction"),
        (r"\b(wrap.?up|summary|closing|bye)\b", "Wrap-up"),
    ]

    for pattern, name in patterns:
        if re.search(pattern, text_lower):
            return name

    return label
