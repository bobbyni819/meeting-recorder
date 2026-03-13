"""Word frequency analysis for meeting transcripts.

Extracts top keywords, generates frequency data suitable for
word cloud rendering, and identifies distinctive terms per speaker.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Common stop words to exclude
_STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "could", "did",
    "do", "does", "doing", "down", "during", "each", "few", "for", "from",
    "further", "get", "gets", "getting", "go", "going", "got", "had", "has",
    "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "if", "in", "into", "is", "it", "its",
    "itself", "just", "know", "let", "like", "make", "me", "might", "more",
    "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on",
    "once", "one", "only", "or", "other", "our", "ours", "ourselves", "out",
    "over", "own", "put", "re", "right", "said", "same", "say", "says",
    "she", "should", "so", "some", "such", "take", "than", "that", "the",
    "their", "theirs", "them", "themselves", "then", "there", "these",
    "they", "think", "this", "those", "through", "to", "too", "under",
    "until", "up", "us", "use", "very", "want", "was", "we", "well",
    "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "would", "yeah", "yes", "yet", "you", "your",
    "yours", "yourself", "yourselves", "gonna", "gotta", "wanna",
    "okay", "ok", "oh", "um", "uh", "ah", "hmm", "huh", "actually",
    "really", "thing", "things", "something", "kind", "sort", "way",
    "also", "much", "even", "still", "already", "back", "need",
    "see", "look", "come", "came", "went", "done", "made",
})


@dataclass
class WordFrequency:
    """Word frequency analysis result."""
    top_words: list[tuple[str, int]]  # (word, count), sorted by count desc
    total_words: int
    unique_words: int
    avg_word_length: float
    speaker_keywords: dict[str, list[tuple[str, int]]]  # per-speaker top words


def analyze_word_frequency(
    rec_path: Path,
    top_n: int = 30,
    min_word_length: int = 3,
) -> WordFrequency | None:
    """Analyze word frequency in a recording's transcript.

    Args:
        rec_path: Recording directory path.
        top_n: Number of top words to return.
        min_word_length: Minimum word length to include.

    Returns:
        WordFrequency or None if no transcript.
    """
    transcript_path = rec_path / "transcript.txt"
    if not transcript_path.exists():
        return None

    try:
        text = transcript_path.read_text(encoding="utf-8")
    except Exception:
        return None

    if not text.strip():
        return None

    # Overall word frequency
    words = _extract_words(text, min_word_length)
    if not words:
        return None

    counter = Counter(words)
    total = len(words)
    unique = len(counter)
    avg_len = sum(len(w) for w in words) / total if total > 0 else 0

    # Per-speaker analysis from transcript.json
    speaker_keywords: dict[str, list[tuple[str, int]]] = {}
    transcript_json = rec_path / "transcript.json"
    if transcript_json.exists():
        try:
            with open(transcript_json, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            # Load speaker map
            meta = _load_meta(rec_path)
            smap = meta.get("speaker_map", {})

            speaker_texts: dict[str, list[str]] = {}
            for seg in tdata.get("segments", []):
                spk = seg.get("speaker", "Unknown")
                spk = smap.get(spk, spk)
                seg_text = seg.get("text", "")
                if seg_text:
                    speaker_texts.setdefault(spk, []).append(seg_text)

            for spk, texts in speaker_texts.items():
                spk_words = _extract_words(" ".join(texts), min_word_length)
                spk_counter = Counter(spk_words)
                # Find words distinctive to this speaker
                # (higher relative frequency compared to overall)
                distinctive: list[tuple[str, int, float]] = []
                for word, count in spk_counter.most_common(50):
                    overall_freq = counter.get(word, 1) / total
                    spk_total = len(spk_words)
                    spk_freq = count / spk_total if spk_total > 0 else 0
                    ratio = spk_freq / overall_freq if overall_freq > 0 else 0
                    if count >= 2:
                        distinctive.append((word, count, ratio))

                # Sort by distinctiveness, take top
                distinctive.sort(key=lambda x: (-x[2], -x[1]))
                speaker_keywords[spk] = [(w, c) for w, c, _ in distinctive[:10]]

        except Exception:
            pass

    return WordFrequency(
        top_words=counter.most_common(top_n),
        total_words=total,
        unique_words=unique,
        avg_word_length=round(avg_len, 1),
        speaker_keywords=speaker_keywords,
    )


def format_word_frequency(wf: WordFrequency) -> str:
    """Format word frequency as readable text."""
    lines = [
        "WORD FREQUENCY",
        "-" * 40,
        f"  Total words: {wf.total_words:,}  |  "
        f"Unique: {wf.unique_words:,}  |  "
        f"Avg length: {wf.avg_word_length}",
        "",
    ]

    if wf.top_words:
        max_count = wf.top_words[0][1] if wf.top_words else 1
        for word, count in wf.top_words[:20]:
            bar_len = int(20 * count / max_count) if max_count > 0 else 0
            bar = "\u2588" * bar_len
            lines.append(f"  {word:<16} {count:>4}  {bar}")
        lines.append("")

    if wf.speaker_keywords:
        lines.append("DISTINCTIVE WORDS PER SPEAKER")
        lines.append("-" * 40)
        for spk, keywords in sorted(wf.speaker_keywords.items()):
            if keywords:
                words_str = ", ".join(f"{w} ({c})" for w, c in keywords[:5])
                lines.append(f"  {spk}: {words_str}")
        lines.append("")

    return "\n".join(lines)


def _extract_words(text: str, min_length: int = 3) -> list[str]:
    """Extract meaningful words from text."""
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return [w for w in words if len(w) >= min_length and w not in _STOP_WORDS]


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
