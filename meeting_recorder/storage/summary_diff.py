"""Meeting summary diff.

Compares summaries between recurring meetings to highlight
what changed, new topics, resolved items, and ongoing themes.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SummaryDiff:
    """Diff between two meeting summaries."""
    rec_a_name: str
    rec_b_name: str
    new_topics: list[str]  # topics in B but not A
    dropped_topics: list[str]  # topics in A but not B
    common_topics: list[str]  # topics in both
    new_action_items: list[str]  # action items in B but not A
    resolved_items: list[str]  # action items in A but not B
    similarity: float  # 0-1 text similarity score


def diff_summaries(
    rec_path_a: Path,
    rec_path_b: Path,
) -> SummaryDiff | None:
    """Compare summaries of two recordings.

    Args:
        rec_path_a: Path to the earlier recording.
        rec_path_b: Path to the later recording.

    Returns:
        SummaryDiff or None if summaries not available.
    """
    summary_a = _read_summary(rec_path_a)
    summary_b = _read_summary(rec_path_b)

    if not summary_a and not summary_b:
        return None

    # Extract key phrases
    topics_a = _extract_topics(summary_a) if summary_a else set()
    topics_b = _extract_topics(summary_b) if summary_b else set()

    new_topics = sorted(topics_b - topics_a)
    dropped_topics = sorted(topics_a - topics_b)
    common_topics = sorted(topics_a & topics_b)

    # Extract action items
    actions_a = _extract_action_lines(summary_a) if summary_a else set()
    actions_b = _extract_action_lines(summary_b) if summary_b else set()

    new_actions = sorted(actions_b - actions_a)
    resolved = sorted(actions_a - actions_b)

    # Compute similarity
    similarity = _text_similarity(summary_a or "", summary_b or "")

    return SummaryDiff(
        rec_a_name=rec_path_a.name,
        rec_b_name=rec_path_b.name,
        new_topics=new_topics,
        dropped_topics=dropped_topics,
        common_topics=common_topics,
        new_action_items=new_actions,
        resolved_items=resolved,
        similarity=round(similarity, 2),
    )


def diff_series(
    recordings_dir: Path,
    subject_pattern: str = "",
    max_diffs: int = 5,
) -> list[SummaryDiff]:
    """Diff consecutive meetings in a series.

    Args:
        recordings_dir: Base recordings directory.
        subject_pattern: Regex pattern to match recording subjects.
        max_diffs: Maximum number of diffs to return.

    Returns:
        List of SummaryDiff objects, most recent first.
    """
    if not recordings_dir.exists():
        return []

    # Find matching recordings sorted by date
    matches: list[Path] = []
    pattern = re.compile(subject_pattern, re.IGNORECASE) if subject_pattern else None

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        if not (rec_dir / "summary.md").exists():
            continue

        if pattern:
            meta = _load_meta(rec_dir)
            subject = meta.get("meeting_subject", rec_dir.name)
            if not pattern.search(subject):
                continue

        matches.append(rec_dir)

    if len(matches) < 2:
        return []

    # Compare consecutive pairs (most recent first)
    diffs: list[SummaryDiff] = []
    for i in range(len(matches) - 1, 0, -1):
        diff = diff_summaries(matches[i - 1], matches[i])
        if diff:
            diffs.append(diff)
        if len(diffs) >= max_diffs:
            break

    return diffs


def format_diff(diff: SummaryDiff) -> str:
    """Format a summary diff as readable text."""
    lines = [
        f"SUMMARY DIFF: {diff.rec_a_name[:20]} \u2192 {diff.rec_b_name[:20]}",
        "-" * 50,
        f"  Similarity: {diff.similarity:.0%}",
        "",
    ]

    if diff.new_topics:
        lines.append("  NEW TOPICS:")
        for t in diff.new_topics:
            lines.append(f"    + {t}")
        lines.append("")

    if diff.dropped_topics:
        lines.append("  DROPPED TOPICS:")
        for t in diff.dropped_topics:
            lines.append(f"    - {t}")
        lines.append("")

    if diff.common_topics:
        lines.append("  ONGOING:")
        for t in diff.common_topics:
            lines.append(f"    = {t}")
        lines.append("")

    if diff.new_action_items:
        lines.append("  NEW ACTION ITEMS:")
        for a in diff.new_action_items:
            lines.append(f"    + {a}")
        lines.append("")

    if diff.resolved_items:
        lines.append("  RESOLVED:")
        for a in diff.resolved_items:
            lines.append(f"    \u2713 {a}")
        lines.append("")

    return "\n".join(lines)


def format_series_diffs(diffs: list[SummaryDiff]) -> str:
    """Format a series of diffs."""
    if not diffs:
        return "No consecutive meetings found to compare."
    return "\n\n".join(format_diff(d) for d in diffs)


# --- Helpers ---

# Stop words for topic extraction
_STOP = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "not", "no", "that", "this", "these", "those", "it", "its", "we",
    "they", "their", "our", "your", "he", "she", "him", "her", "his",
    "them", "us", "you", "i", "me", "my", "also", "about", "up", "out",
    "so", "if", "when", "then", "than", "each", "all", "both", "some",
    "any", "many", "more", "most", "other", "new", "just", "very",
})


def _extract_topics(text: str) -> set[str]:
    """Extract key topic phrases (2-3 word combos) from text."""
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    filtered = [w for w in words if w not in _STOP]

    # Bigrams as topics
    topics: set[str] = set()
    counts = Counter(filtered)
    for word, count in counts.most_common(20):
        if count >= 2 and len(word) >= 4:
            topics.add(word)

    # Also extract bullet-point items as phrases
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith(("-", "*", "\u2022")) and len(line) > 5:
            phrase = line.lstrip("-* \u2022").strip()
            if 10 < len(phrase) < 100:
                topics.add(phrase.lower())

    return topics


def _extract_action_lines(text: str) -> set[str]:
    """Extract action-item-like lines from text."""
    actions: set[str] = set()
    for line in text.split("\n"):
        stripped = line.strip()
        # Match checkbox items, "action item", "follow up", "todo"
        if re.match(r"^[-*]\s*\[[ x]\]", stripped, re.IGNORECASE):
            actions.add(stripped.lower())
        elif re.match(r"^[-*]\s*(action|follow|todo|task)", stripped, re.IGNORECASE):
            actions.add(stripped.lower())
    return actions


def _text_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two texts."""
    if not a or not b:
        return 0.0
    words_a = set(re.findall(r"[a-zA-Z]{3,}", a.lower()))
    words_b = set(re.findall(r"[a-zA-Z]{3,}", b.lower()))
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _read_summary(rec_path: Path) -> str | None:
    """Read summary.md from a recording."""
    summary_path = rec_path / "summary.md"
    if summary_path.exists():
        try:
            return summary_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


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
