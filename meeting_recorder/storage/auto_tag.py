"""Automatic tag suggestion based on transcript content."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path


# Common English stop words to exclude from keyword extraction
_STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "aren't", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can",
    "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does",
    "doesn't", "doing", "don't", "down", "during", "each", "few", "for",
    "from", "further", "get", "gets", "getting", "got", "had", "hadn't",
    "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll",
    "he's", "her", "here", "here's", "hers", "herself", "him", "himself",
    "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in",
    "into", "is", "isn't", "it", "it's", "its", "itself", "just", "know",
    "let", "let's", "like", "make", "me", "might", "more", "most", "mustn't",
    "my", "myself", "need", "no", "nor", "not", "of", "off", "oh", "ok",
    "okay", "on", "once", "one", "only", "or", "other", "ought", "our",
    "ours", "ourselves", "out", "over", "own", "put", "really", "right",
    "said", "same", "say", "says", "see", "shall", "shan't", "she", "she'd",
    "she'll", "she's", "should", "shouldn't", "so", "some", "such", "sure",
    "take", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd",
    "they'll", "they're", "they've", "thing", "things", "think", "this",
    "those", "through", "to", "too", "um", "uh", "under", "until", "up",
    "us", "very", "want", "was", "wasn't", "way", "we", "we'd", "we'll",
    "we're", "we've", "well", "were", "weren't", "what", "what's", "when",
    "when's", "where", "where's", "which", "while", "who", "who's", "whom",
    "why", "why's", "will", "with", "won't", "would", "wouldn't", "yeah",
    "yes", "yet", "you", "you'd", "you'll", "you're", "you've", "your",
    "yours", "yourself", "yourselves", "going", "gonna", "gotta", "kind",
    "also", "actually", "basically", "something", "someone", "everything",
    "everyone", "anything", "anyone", "nothing", "already", "still",
    "even", "much", "many", "come", "came", "back", "good", "great",
    "look", "looking", "looks", "work", "working", "works", "worked",
    "time", "times", "now", "new", "first", "last", "next", "here",
    "there", "where", "when", "what", "which", "been", "being",
    "done", "doing", "made", "went", "goes", "give", "given", "gave",
    "told", "tell", "keep", "start", "started",
})

# Topic categories with associated keywords
_TOPIC_PATTERNS: dict[str, list[str]] = {
    "budget": ["budget", "cost", "spending", "expense", "revenue", "profit",
               "financial", "funding", "invoice", "billing"],
    "hiring": ["hire", "hiring", "candidate", "interview", "recruit",
               "onboarding", "position", "role", "resume", "applicant"],
    "product": ["feature", "product", "roadmap", "release", "launch",
                "mvp", "prototype", "backlog", "sprint", "epic"],
    "engineering": ["code", "deploy", "deployment", "api", "database",
                    "server", "bug", "fix", "pull request", "merge",
                    "pipeline", "infrastructure", "architecture"],
    "design": ["design", "mockup", "wireframe", "figma", "ui", "ux",
               "prototype", "layout", "typography", "accessibility"],
    "marketing": ["marketing", "campaign", "seo", "analytics", "conversion",
                  "brand", "content", "social media", "audience", "engagement"],
    "sales": ["sales", "deal", "pipeline", "prospect", "lead", "quota",
              "revenue", "client", "contract", "proposal"],
    "legal": ["legal", "compliance", "regulation", "contract", "policy",
              "privacy", "gdpr", "terms", "liability", "intellectual property"],
    "research": ["research", "study", "data", "analysis", "experiment",
                 "hypothesis", "findings", "methodology", "survey", "results"],
    "planning": ["plan", "planning", "strategy", "goal", "objective",
                 "milestone", "timeline", "deadline", "priority", "quarter"],
    "standup": ["standup", "stand-up", "blocker", "blocked", "yesterday",
                "today", "tomorrow", "progress", "update"],
    "retrospective": ["retro", "retrospective", "went well", "improve",
                      "action item", "feedback", "lessons learned"],
    "onboarding": ["onboarding", "training", "orientation", "documentation",
                   "setup", "getting started", "walkthrough"],
    "incident": ["incident", "outage", "downtime", "postmortem", "root cause",
                 "escalation", "alert", "monitoring", "recovery"],
    "customer": ["customer", "user", "feedback", "support", "ticket",
                 "complaint", "satisfaction", "nps", "churn"],
}


def suggest_tags(
    transcript: str,
    existing_tags: list[str] | None = None,
    max_tags: int = 5,
) -> list[str]:
    """Suggest tags based on transcript content.

    Uses two strategies:
    1. Topic matching: checks for domain-specific keyword patterns
    2. Keyword extraction: finds frequently used significant words

    Args:
        transcript: The transcript text to analyze.
        existing_tags: Tags already applied (will be excluded from suggestions).
        max_tags: Maximum number of tags to suggest.

    Returns:
        List of suggested tag strings, most relevant first.
    """
    if not transcript or len(transcript.strip()) < 50:
        return []

    existing = set(t.lower() for t in (existing_tags or []))
    text_lower = transcript.lower()

    suggestions: list[tuple[str, float]] = []

    # Strategy 1: Topic pattern matching
    for topic, keywords in _TOPIC_PATTERNS.items():
        if topic.lower() in existing:
            continue
        score = 0.0
        for kw in keywords:
            count = text_lower.count(kw)
            if count > 0:
                # Weight by keyword length (longer = more specific = higher weight)
                score += count * (1 + len(kw) / 10)
        if score >= 3.0:  # Minimum threshold
            suggestions.append((topic, score))

    # Strategy 2: Extract top keywords (bigrams and single words)
    keywords = _extract_keywords(transcript)
    for word, count in keywords:
        if word.lower() in existing:
            continue
        # Don't duplicate topic tags
        if any(word.lower() == s[0].lower() for s in suggestions):
            continue
        suggestions.append((word, count))

    # Sort by score descending, take top N
    suggestions.sort(key=lambda x: -x[1])
    return [tag for tag, _score in suggestions[:max_tags]]


def suggest_tags_for_recording(rec_path: Path, meta: dict | None = None) -> list[str]:
    """Suggest tags for a recording directory.

    Args:
        rec_path: Path to the recording directory.
        meta: Pre-loaded metadata (optional).

    Returns:
        List of suggested tags.
    """
    import json

    if meta is None:
        meta = {}
        try:
            meta_path = rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
        except Exception:
            pass

    existing_tags = meta.get("tags", [])

    # Read transcript
    transcript = ""
    try:
        txt_path = rec_path / "transcript.txt"
        if txt_path.exists():
            transcript = txt_path.read_text(encoding="utf-8")
    except Exception:
        pass

    # Also consider summary
    try:
        summary_path = rec_path / "summary.md"
        if summary_path.exists():
            transcript += "\n" + summary_path.read_text(encoding="utf-8")
    except Exception:
        pass

    return suggest_tags(transcript, existing_tags)


def _extract_keywords(text: str, top_n: int = 10) -> list[tuple[str, int]]:
    """Extract top keywords from text using frequency analysis.

    Returns (word, count) tuples sorted by count descending.
    """
    # Tokenize: split on non-alphanumeric, keep words 3+ chars
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())

    # Filter stop words
    filtered = [w for w in words if w not in _STOP_WORDS]

    # Count frequencies
    counter = Counter(filtered)

    # Also extract common bigrams
    bigrams: list[str] = []
    for i in range(len(filtered) - 1):
        bigram = f"{filtered[i]} {filtered[i + 1]}"
        bigrams.append(bigram)
    bigram_counter = Counter(bigrams)

    # Merge: bigrams get extra weight
    combined: dict[str, float] = {}
    for word, count in counter.most_common(50):
        if count >= 3:  # Minimum 3 occurrences
            combined[word] = count
    for bigram, count in bigram_counter.most_common(20):
        if count >= 2:  # Minimum 2 occurrences for bigrams
            combined[bigram] = count * 2  # Weight bigrams higher

    # Sort and return top N
    sorted_keywords = sorted(combined.items(), key=lambda x: -x[1])
    return sorted_keywords[:top_n]
