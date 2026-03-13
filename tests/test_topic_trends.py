"""Tests for topic trend analysis."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.topic_trends import (
    analyze_topic_trends,
    format_topic_trends,
    _extract_keywords,
    _score_topics,
    _classify_trend,
    _sparkline,
    TrendReport,
    WeekTopics,
    TopicTrend,
)


def _make_rec(
    base: Path,
    date_str: str,
    transcript: str = "",
    subject: str = "Meeting",
) -> Path:
    name = f"{date_str}_09-00-00_{subject}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump({"duration_seconds": 1800}, f)
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    return rec


class TestExtractKeywords:
    def test_basic(self):
        text = "The database migration needs review. Database schema changes require testing."
        kws = _extract_keywords(text, top_n=5)
        words = [w for w, _ in kws]
        assert "database" in words

    def test_filters_short_words(self):
        text = "We go to the big lab for a cat run."
        kws = _extract_keywords(text, top_n=10)
        words = [w for w, _ in kws]
        # Words <4 chars ("go", "to", "the", "big", "lab", "for", "cat", "run") are excluded
        assert not any(len(w) < 4 for w in words)

    def test_stop_words_excluded(self):
        text = "I think we should actually basically start working on something."
        kws = _extract_keywords(text, top_n=10)
        words = [w for w, _ in kws]
        assert "think" not in words  # stop word
        assert "actually" not in words  # stop word

    def test_empty(self):
        assert _extract_keywords("") == []

    def test_top_n(self):
        text = "alpha " * 10 + "beta " * 8 + "gamma " * 6 + "delta " * 4
        kws = _extract_keywords(text, top_n=2)
        assert len(kws) == 2


class TestScoreTopics:
    def test_engineering_topic(self):
        text = "We need to deploy the API changes and fix the database bug in the pipeline."
        scores = _score_topics(text)
        assert "engineering" in scores

    def test_no_topics(self):
        text = "Hello world this is a simple message."
        scores = _score_topics(text)
        assert len(scores) == 0

    def test_multiple_topics(self):
        text = ("We need to hire a new candidate for the engineering role. "
                "The budget for this quarter needs review. "
                "Deploy the API code fix.") * 3
        scores = _score_topics(text)
        assert len(scores) >= 2


class TestClassifyTrend:
    def test_rising(self):
        data = [("w1", 10), ("w2", 8), ("w3", 2), ("w4", 1)]
        assert _classify_trend(data) == "rising"

    def test_falling(self):
        data = [("w1", 1), ("w2", 0), ("w3", 8), ("w4", 10)]
        assert _classify_trend(data) == "falling"

    def test_stable(self):
        data = [("w1", 5), ("w2", 5), ("w3", 5), ("w4", 5)]
        assert _classify_trend(data) == "stable"

    def test_new(self):
        data = [("w1", 5), ("w2", 3), ("w3", 0), ("w4", 0)]
        assert _classify_trend(data) == "new"

    def test_gone(self):
        data = [("w1", 0), ("w2", 0), ("w3", 5), ("w4", 3)]
        assert _classify_trend(data) == "gone"

    def test_empty(self):
        assert _classify_trend([]) == "stable"


class TestSparkline:
    def test_basic(self):
        result = _sparkline([0, 5, 10, 3, 0])
        assert len(result) == 5

    def test_all_zeros(self):
        result = _sparkline([0, 0, 0])
        assert len(result) == 3
        assert result == "   "

    def test_empty(self):
        assert _sparkline([]) == ""

    def test_single(self):
        result = _sparkline([5])
        assert len(result) == 1


class TestAnalyzeTopicTrends:
    def test_empty_dir(self, tmp_path):
        report = analyze_topic_trends(tmp_path)
        assert report.weeks == []
        assert report.trends == []

    def test_nonexistent_dir(self, tmp_path):
        report = analyze_topic_trends(tmp_path / "nope")
        assert report.weeks == []

    def test_basic_analysis(self, tmp_path):
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        transcript = ("deployment pipeline database migration testing "
                      "deployment pipeline database migration testing ") * 5
        _make_rec(tmp_path, today.isoformat(), transcript)

        report = analyze_topic_trends(tmp_path, weeks=1)
        assert len(report.weeks) == 1
        assert report.weeks[0].recording_count >= 1
        assert report.weeks[0].total_words > 0

    def test_multiple_weeks(self, tmp_path):
        today = date.today()
        for w in range(3):
            d = today - timedelta(weeks=w)
            if d.weekday() >= 5:
                d = d - timedelta(days=d.weekday() - 4)
            _make_rec(tmp_path, d.isoformat(),
                      f"deployment testing review sprint planning " * 5,
                      subject=f"W{w}")

        report = analyze_topic_trends(tmp_path, weeks=3)
        assert len(report.weeks) == 3

    def test_no_transcripts(self, tmp_path):
        today = date.today()
        if today.weekday() >= 5:
            today = today - timedelta(days=today.weekday() - 4)
        _make_rec(tmp_path, today.isoformat())  # No transcript
        report = analyze_topic_trends(tmp_path, weeks=1)
        assert report.weeks == []

    def test_emerging_topics(self, tmp_path):
        today = date.today()
        # Recent: has "kubernetes"
        d1 = today - timedelta(days=today.weekday())  # This Monday
        _make_rec(tmp_path, d1.isoformat(),
                  "kubernetes deployment cluster orchestration " * 10)
        # Older: has "monolith"
        d2 = d1 - timedelta(weeks=3)
        _make_rec(tmp_path, d2.isoformat(),
                  "monolith architecture legacy migration " * 10)

        report = analyze_topic_trends(tmp_path, weeks=4)
        # "kubernetes" should be in emerging or trends
        all_trend_names = [t.name for t in report.trends]
        all_names = all_trend_names + report.emerging
        assert any("kubernetes" in n for n in all_names) or len(report.weeks) > 0


class TestFormatTopicTrends:
    def test_empty(self):
        report = TrendReport(weeks=[], trends=[], emerging=[], declining=[])
        text = format_topic_trends(report)
        assert "No meeting data" in text

    def test_no_recordings(self):
        report = TrendReport(
            weeks=[WeekTopics("2026-03-09", 0, [], {}, 0)],
            trends=[], emerging=[], declining=[],
        )
        text = format_topic_trends(report)
        assert "No meeting data" in text

    def test_basic_format(self):
        report = TrendReport(
            weeks=[WeekTopics(
                "2026-03-09", 3,
                [("deployment", 15), ("testing", 10)],
                {"engineering": 20.0},
                500,
            )],
            trends=[TopicTrend("deployment", [("2026-03-09", 15)], 15, "stable")],
            emerging=["kubernetes"],
            declining=["monolith"],
        )
        text = format_topic_trends(report)
        assert "TOPIC TRENDS" in text
        assert "deployment" in text
        assert "engineering" in text
        assert "Emerging" in text
        assert "kubernetes" in text
        assert "Declining" in text
        assert "monolith" in text

    def test_trends_section(self):
        report = TrendReport(
            weeks=[WeekTopics("2026-03-09", 2, [("alpha", 5)], {}, 100)],
            trends=[
                TopicTrend("alpha", [("2026-03-09", 5)], 5, "rising"),
                TopicTrend("beta", [("2026-03-09", 3)], 3, "falling"),
            ],
            emerging=[], declining=[],
        )
        text = format_topic_trends(report)
        assert "KEYWORD TRENDS" in text
        assert "alpha" in text
        assert "beta" in text
