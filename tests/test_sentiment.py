"""Tests for meeting sentiment analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.sentiment import (
    analyze_sentiment,
    analyze_recording_sentiment,
    sentiment_emoji,
    format_sentiment,
    SentimentScore,
)


class TestAnalyzeSentiment:
    def test_empty(self):
        result = analyze_sentiment("")
        assert result.label == "neutral"
        assert result.score == 0.0
        assert result.total_words == 0

    def test_positive(self):
        text = "This is amazing and great progress. Excellent work, really impressive results."
        result = analyze_sentiment(text)
        assert result.score > 0.3
        assert result.label == "positive"
        assert result.positive_count > result.negative_count

    def test_negative(self):
        text = "This is terrible and frustrating. The problem is a disaster, everything is broken."
        result = analyze_sentiment(text)
        assert result.score < -0.3
        assert result.label == "negative"
        assert result.negative_count > result.positive_count

    def test_neutral(self):
        text = "We discussed the agenda items and reviewed the schedule for next week."
        result = analyze_sentiment(text)
        assert result.label == "neutral"

    def test_mixed(self):
        text = ("Great progress on the product launch, but there are serious "
                "concerns about the deadline. The team is excited but worried "
                "about the bugs and issues we found.")
        result = analyze_sentiment(text)
        assert result.label in ("mixed", "positive", "negative")
        assert result.positive_count > 0
        assert result.negative_count > 0

    def test_top_words(self):
        text = "great great great amazing good bad terrible"
        result = analyze_sentiment(text)
        pos_words = [w for w, _ in result.top_positive]
        neg_words = [w for w, _ in result.top_negative]
        assert "great" in pos_words
        assert "terrible" in neg_words or "bad" in neg_words

    def test_score_range(self):
        # All positive
        text = "excellent amazing wonderful fantastic incredible"
        result = analyze_sentiment(text)
        assert -1.0 <= result.score <= 1.0
        assert result.score > 0.5

        # All negative
        text = "terrible horrible awful disaster nightmare"
        result = analyze_sentiment(text)
        assert -1.0 <= result.score <= 1.0
        assert result.score < -0.5

    def test_whitespace_only(self):
        result = analyze_sentiment("   \n\t  ")
        assert result.label == "neutral"

    def test_total_words(self):
        text = "The meeting was good and productive today."
        result = analyze_sentiment(text)
        assert result.total_words == 7  # "the", "meeting", "was", "good", "and", "productive", "today"


class TestAnalyzeRecordingSentiment:
    def test_with_transcript(self, tmp_path):
        (tmp_path / "transcript.txt").write_text(
            "This was an excellent meeting with great progress.",
            encoding="utf-8",
        )
        result = analyze_recording_sentiment(tmp_path)
        assert result is not None
        assert result.label == "positive"

    def test_no_transcript(self, tmp_path):
        assert analyze_recording_sentiment(tmp_path) is None

    def test_empty_transcript(self, tmp_path):
        (tmp_path / "transcript.txt").write_text("", encoding="utf-8")
        assert analyze_recording_sentiment(tmp_path) is None


class TestSentimentEmoji:
    def test_positive(self):
        assert sentiment_emoji(0.5) == "\U0001f60a"

    def test_slightly_positive(self):
        assert sentiment_emoji(0.2) == "\U0001f642"

    def test_negative(self):
        assert sentiment_emoji(-0.5) == "\U0001f61f"

    def test_slightly_negative(self):
        assert sentiment_emoji(-0.2) == "\U0001f610"

    def test_neutral(self):
        assert sentiment_emoji(0.0) == "\U0001f636"


class TestFormatSentiment:
    def test_basic(self):
        score = SentimentScore(
            positive_count=10, negative_count=3, total_words=200,
            score=0.54, label="positive",
            top_positive=[("great", 4), ("good", 3)],
            top_negative=[("issue", 2)],
        )
        text = format_sentiment(score)
        assert "SENTIMENT ANALYSIS" in text
        assert "Positive" in text
        assert "+0.54" in text
        assert "great (4)" in text
        assert "issue (2)" in text

    def test_negative_format(self):
        score = SentimentScore(
            positive_count=2, negative_count=8, total_words=100,
            score=-0.6, label="negative",
            top_positive=[], top_negative=[("problem", 3)],
        )
        text = format_sentiment(score)
        assert "Negative" in text
        assert "-0.60" in text
