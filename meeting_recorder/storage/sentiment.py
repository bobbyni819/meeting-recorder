"""Meeting sentiment analysis.

Lightweight keyword-based sentiment scoring for meeting transcripts.
Detects overall tone, emotional shifts, and contentious segments
without requiring external NLP models.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Positive sentiment indicators
_POSITIVE = frozenset({
    "agree", "agreed", "amazing", "appreciate", "awesome", "beautiful",
    "benefit", "best", "better", "celebrate", "clear", "collaborate",
    "committed", "confident", "congrats", "congratulations", "cool",
    "creative", "delighted", "efficient", "elegant", "encourage",
    "enjoy", "enthusiastic", "excellent", "excited", "exciting",
    "fantastic", "favorable", "fixed", "flexible", "friendly", "glad",
    "good", "grateful", "great", "happy", "helpful", "hopeful",
    "ideal", "impressed", "impressive", "improved", "improvement",
    "incredible", "innovative", "inspired", "interesting", "love",
    "motivated", "nice", "optimistic", "outstanding", "perfect",
    "pleased", "positive", "productive", "progress", "promising",
    "proud", "recommend", "resolved", "smooth", "solid", "solution",
    "solved", "strong", "succeed", "success", "successful", "super",
    "supportive", "terrific", "thankful", "thanks", "thrilled",
    "valuable", "well", "win", "wonderful", "worth",
})

# Negative sentiment indicators
_NEGATIVE = frozenset({
    "afraid", "angry", "annoyed", "annoying", "anxious", "bad",
    "blame", "blocked", "blocker", "boring", "broken", "bug",
    "burned", "burnout", "cancel", "challenge", "chaos", "complain",
    "complaint", "complicated", "concern", "concerned", "confusing",
    "confused", "costly", "crash", "crisis", "critical", "damage",
    "dangerous", "deadline", "delay", "delayed", "difficult",
    "disappoint", "disappointed", "disappointing", "disaster",
    "disorganized", "doubt", "error", "fail", "failed", "failing",
    "failure", "fear", "flawed", "fragile", "frustrated", "frustrating",
    "frustration", "hack", "hard", "hate", "horrible", "hurt",
    "impossible", "incident", "inefficient", "issue", "lack",
    "late", "limitation", "lose", "lost", "mess", "messy", "miss",
    "missed", "missing", "mistake", "negative", "never", "nightmare",
    "obsolete", "outage", "overdue", "overwhelmed", "painful", "panic",
    "poor", "pressure", "problem", "problematic", "reject", "rejected",
    "risky", "rushed", "sad", "scary", "serious", "severe", "slow",
    "sorry", "stale", "stalled", "stress", "stressed", "struggle",
    "stuck", "terrible", "threat", "tired", "toxic", "trouble",
    "ugly", "unable", "unclear", "unfortunately", "unhappy",
    "unstable", "upset", "urgent", "victim", "warning", "weak",
    "worry", "worried", "worse", "worst", "wrong",
})


@dataclass
class SentimentScore:
    """Sentiment analysis result."""
    positive_count: int
    negative_count: int
    total_words: int
    score: float  # -1.0 (very negative) to +1.0 (very positive)
    label: str  # "positive", "negative", "neutral", "mixed"
    top_positive: list[tuple[str, int]]  # (word, count)
    top_negative: list[tuple[str, int]]  # (word, count)


def analyze_sentiment(text: str) -> SentimentScore:
    """Analyze sentiment of text.

    Args:
        text: Text to analyze.

    Returns:
        SentimentScore with counts and label.
    """
    if not text or not text.strip():
        return SentimentScore(
            positive_count=0, negative_count=0, total_words=0,
            score=0.0, label="neutral",
            top_positive=[], top_negative=[],
        )

    words = re.findall(r"[a-zA-Z]+", text.lower())
    total = len(words)

    pos_counts: dict[str, int] = {}
    neg_counts: dict[str, int] = {}

    for word in words:
        if word in _POSITIVE:
            pos_counts[word] = pos_counts.get(word, 0) + 1
        elif word in _NEGATIVE:
            neg_counts[word] = neg_counts.get(word, 0) + 1

    pos_total = sum(pos_counts.values())
    neg_total = sum(neg_counts.values())

    # Score: normalized difference
    sentiment_total = pos_total + neg_total
    if sentiment_total > 0:
        score = (pos_total - neg_total) / sentiment_total
    else:
        score = 0.0

    # Label
    if sentiment_total == 0:
        label = "neutral"
    elif score > 0.3:
        label = "positive"
    elif score < -0.3:
        label = "negative"
    else:
        label = "mixed"

    top_pos = sorted(pos_counts.items(), key=lambda x: -x[1])[:5]
    top_neg = sorted(neg_counts.items(), key=lambda x: -x[1])[:5]

    return SentimentScore(
        positive_count=pos_total,
        negative_count=neg_total,
        total_words=total,
        score=round(score, 2),
        label=label,
        top_positive=top_pos,
        top_negative=top_neg,
    )


def analyze_recording_sentiment(rec_path: Path) -> SentimentScore | None:
    """Analyze sentiment of a recording's transcript.

    Args:
        rec_path: Path to recording directory.

    Returns:
        SentimentScore or None if no transcript.
    """
    txt_path = rec_path / "transcript.txt"
    if not txt_path.exists():
        return None
    try:
        text = txt_path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        return analyze_sentiment(text)
    except Exception:
        return None


def sentiment_emoji(score: float) -> str:
    """Return an emoji representing sentiment score."""
    if score > 0.3:
        return "\U0001f60a"  # smiling
    elif score > 0.1:
        return "\U0001f642"  # slightly smiling
    elif score < -0.3:
        return "\U0001f61f"  # worried
    elif score < -0.1:
        return "\U0001f610"  # neutral face
    return "\U0001f636"  # no mouth (truly neutral)


def format_sentiment(score: SentimentScore) -> str:
    """Format sentiment analysis as readable text."""
    lines = ["SENTIMENT ANALYSIS", "-" * 40]

    emoji = sentiment_emoji(score.score)
    bar_pos = int(max(0, score.score) * 10)
    bar_neg = int(max(0, -score.score) * 10)
    bar = "\u2591" * bar_neg + "\u2502" + "\u2588" * bar_pos
    bar = bar.rjust(11) if score.score >= 0 else bar.ljust(11)

    lines.append(f"  Tone:     {score.label.title()} {emoji}  ({score.score:+.2f})")
    lines.append(f"  Positive: {score.positive_count} words")
    lines.append(f"  Negative: {score.negative_count} words")

    if score.top_positive:
        words = ", ".join(f"{w} ({c})" for w, c in score.top_positive[:3])
        lines.append(f"  Top +ve:  {words}")

    if score.top_negative:
        words = ", ".join(f"{w} ({c})" for w, c in score.top_negative[:3])
        lines.append(f"  Top -ve:  {words}")

    return "\n".join(lines)
