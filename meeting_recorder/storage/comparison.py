"""Compare two recordings to highlight differences and commonalities.

Useful for recurring meetings: shows who was new/missing, duration changes,
topic overlap, and key differences between two recordings.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RecordingComparison:
    """Result of comparing two recordings."""

    # Basic info
    name_a: str
    name_b: str
    date_a: str
    date_b: str

    # Duration
    duration_a: float  # seconds
    duration_b: float
    duration_change: float  # percentage change

    # Attendees
    attendees_both: list[str]  # present in both
    attendees_only_a: list[str]  # only in first
    attendees_only_b: list[str]  # only in second

    # Tags
    tags_both: list[str]
    tags_only_a: list[str]
    tags_only_b: list[str]

    # Topics (extracted from transcript keywords)
    common_topics: list[str]
    new_topics: list[str]  # in B but not A
    dropped_topics: list[str]  # in A but not B

    # Quality
    quality_a: int | None
    quality_b: int | None

    # Speaker counts
    speakers_a: int
    speakers_b: int

    # Subject
    subject_a: str
    subject_b: str

    def format_text(self) -> str:
        """Format comparison as readable text."""
        lines: list[str] = []

        lines.append("RECORDING COMPARISON")
        lines.append("=" * 50)
        lines.append("")
        lines.append(f"A: {self.name_a}")
        lines.append(f"B: {self.name_b}")
        lines.append("")

        # Duration
        dur_a = _fmt_duration(self.duration_a)
        dur_b = _fmt_duration(self.duration_b)
        change_str = f"{self.duration_change:+.0f}%" if self.duration_change else "same"
        lines.append(f"Duration: {dur_a} -> {dur_b} ({change_str})")
        lines.append("")

        # Attendees
        if self.attendees_both or self.attendees_only_a or self.attendees_only_b:
            lines.append("Attendees:")
            for att in self.attendees_both:
                lines.append(f"  = {att}")
            for att in self.attendees_only_a:
                lines.append(f"  - {att} (left)")
            for att in self.attendees_only_b:
                lines.append(f"  + {att} (new)")
            lines.append("")

        # Topics
        if self.common_topics or self.new_topics or self.dropped_topics:
            lines.append("Topics:")
            for t in self.common_topics[:5]:
                lines.append(f"  = {t}")
            for t in self.dropped_topics[:5]:
                lines.append(f"  - {t}")
            for t in self.new_topics[:5]:
                lines.append(f"  + {t}")
            lines.append("")

        # Tags
        if self.tags_both or self.tags_only_a or self.tags_only_b:
            lines.append("Tags:")
            for t in self.tags_both:
                lines.append(f"  = {t}")
            for t in self.tags_only_a:
                lines.append(f"  - {t}")
            for t in self.tags_only_b:
                lines.append(f"  + {t}")
            lines.append("")

        # Quality
        if self.quality_a is not None or self.quality_b is not None:
            qa = str(self.quality_a) if self.quality_a is not None else "n/a"
            qb = str(self.quality_b) if self.quality_b is not None else "n/a"
            lines.append(f"Quality: {qa} -> {qb}")

        return "\n".join(lines)


def compare_recordings(
    path_a: Path,
    path_b: Path,
) -> RecordingComparison:
    """Compare two recording directories.

    Args:
        path_a: First (earlier) recording directory.
        path_b: Second (later) recording directory.

    Returns:
        RecordingComparison with detailed diff.
    """
    meta_a = _load_meta(path_a)
    meta_b = _load_meta(path_b)

    # Duration
    dur_a = meta_a.get("duration_seconds", 0)
    dur_b = meta_b.get("duration_seconds", 0)
    dur_change = ((dur_b - dur_a) / dur_a * 100) if dur_a > 0 else 0

    # Attendees (case-insensitive comparison)
    att_a = set(a.strip().lower() for a in (meta_a.get("meeting_attendees") or []))
    att_b = set(a.strip().lower() for a in (meta_b.get("meeting_attendees") or []))
    att_a_orig = {a.strip().lower(): a.strip() for a in (meta_a.get("meeting_attendees") or [])}
    att_b_orig = {a.strip().lower(): a.strip() for a in (meta_b.get("meeting_attendees") or [])}

    both_att = att_a & att_b
    only_a_att = att_a - att_b
    only_b_att = att_b - att_a

    # Tags
    tags_a = set(meta_a.get("tags") or [])
    tags_b = set(meta_b.get("tags") or [])

    # Topics from transcripts
    topics_a = _extract_topics(_read_transcript(path_a))
    topics_b = _extract_topics(_read_transcript(path_b))

    common_topics = sorted(topics_a & topics_b)
    new_topics = sorted(topics_b - topics_a)
    dropped_topics = sorted(topics_a - topics_b)

    # Quality
    qa = meta_a.get("quality_scores", {}).get("overall_score")
    qb = meta_b.get("quality_scores", {}).get("overall_score")

    # Dates
    name_a = path_a.name
    name_b = path_b.name
    date_a = name_a[:10] if len(name_a) >= 10 else ""
    date_b = name_b[:10] if len(name_b) >= 10 else ""

    return RecordingComparison(
        name_a=name_a,
        name_b=name_b,
        date_a=date_a,
        date_b=date_b,
        duration_a=dur_a,
        duration_b=dur_b,
        duration_change=dur_change,
        attendees_both=sorted(att_a_orig.get(k, k) for k in both_att),
        attendees_only_a=sorted(att_a_orig.get(k, k) for k in only_a_att),
        attendees_only_b=sorted(att_b_orig.get(k, k) for k in only_b_att),
        tags_both=sorted(tags_a & tags_b),
        tags_only_a=sorted(tags_a - tags_b),
        tags_only_b=sorted(tags_b - tags_a),
        common_topics=common_topics,
        new_topics=new_topics,
        dropped_topics=dropped_topics,
        quality_a=qa,
        quality_b=qb,
        speakers_a=meta_a.get("speaker_count", 0),
        speakers_b=meta_b.get("speaker_count", 0),
        subject_a=meta_a.get("meeting_subject", ""),
        subject_b=meta_b.get("meeting_subject", ""),
    )


def find_similar_recordings(
    target: Path,
    recordings_dir: Path,
    max_results: int = 5,
) -> list[tuple[Path, float]]:
    """Find recordings similar to the target.

    Similarity based on subject, attendees, tags, and app.

    Args:
        target: The recording to find similar ones for.
        recordings_dir: Base recordings directory.
        max_results: Maximum number of results.

    Returns:
        List of (recording_path, similarity_score) sorted by score descending.
    """
    target_meta = _load_meta(target)
    target_subject = target_meta.get("meeting_subject", "").lower()
    target_attendees = set(
        a.strip().lower() for a in (target_meta.get("meeting_attendees") or [])
    )
    target_tags = set(target_meta.get("tags") or [])
    target_app = target_meta.get("app_name", "").lower()
    target_organizer = target_meta.get("meeting_organizer", "").lower()

    if not recordings_dir.exists():
        return []

    scored: list[tuple[Path, float]] = []

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir() or rec_dir == target:
            continue
        meta_path = rec_dir / "metadata.json"
        if not meta_path.exists():
            continue

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        score = 0.0

        # Subject match (highest weight)
        other_subject = meta.get("meeting_subject", "").lower()
        if target_subject and other_subject:
            if target_subject == other_subject:
                score += 40
            elif target_subject in other_subject or other_subject in target_subject:
                score += 20
            else:
                # Word overlap
                words_t = set(target_subject.split())
                words_o = set(other_subject.split())
                if words_t & words_o:
                    overlap = len(words_t & words_o) / max(len(words_t | words_o), 1)
                    score += overlap * 30

        # Attendee overlap
        other_attendees = set(
            a.strip().lower() for a in (meta.get("meeting_attendees") or [])
        )
        if target_attendees and other_attendees:
            overlap = len(target_attendees & other_attendees)
            total = len(target_attendees | other_attendees)
            score += (overlap / max(total, 1)) * 25

        # Same organizer
        other_organizer = meta.get("meeting_organizer", "").lower()
        if target_organizer and target_organizer == other_organizer:
            score += 10

        # Tag overlap
        other_tags = set(meta.get("tags") or [])
        if target_tags and other_tags:
            overlap = len(target_tags & other_tags)
            total = len(target_tags | other_tags)
            score += (overlap / max(total, 1)) * 15

        # Same app
        other_app = meta.get("app_name", "").lower()
        if target_app and target_app == other_app:
            score += 5

        if score > 5:
            scored.append((rec_dir, score))

    scored.sort(key=lambda x: -x[1])
    return scored[:max_results]


def _load_meta(path: Path) -> dict:
    """Load metadata from recording directory."""
    meta_path = path / "metadata.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _read_transcript(path: Path) -> str:
    """Read transcript text from recording."""
    txt_path = path / "transcript.txt"
    if txt_path.exists():
        try:
            return txt_path.read_text(encoding="utf-8")
        except Exception:
            pass
    return ""


def _extract_topics(text: str, min_freq: int = 2, top_n: int = 15) -> set[str]:
    """Extract topic keywords from transcript text."""
    if not text or len(text) < 50:
        return set()

    # Simple word frequency extraction
    import re
    words = re.findall(r'\b[a-z]{4,}\b', text.lower())
    counter = Counter(words)

    # Filter stop words
    _stop = frozenset({
        "that", "this", "with", "from", "have", "been", "were", "will",
        "would", "could", "should", "about", "their", "there", "which",
        "when", "what", "them", "than", "also", "just", "some", "more",
        "like", "into", "very", "then", "only", "over", "such", "much",
        "before", "after", "being", "going", "doing", "each", "make",
        "know", "think", "want", "need", "look", "take", "come", "back",
        "really", "something", "anything", "everything", "everyone",
        "people", "thing", "things", "yeah", "okay", "right",
    })

    topics = set()
    for word, count in counter.most_common(top_n * 3):
        if count >= min_freq and word not in _stop:
            topics.add(word)
            if len(topics) >= top_n:
                break

    return topics


def _fmt_duration(seconds: float) -> str:
    """Format seconds as short duration string."""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"
