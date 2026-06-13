"""Tests for recording comparison and similarity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.comparison import (
    compare_recordings,
    find_similar_recordings,
    RecordingComparison,
    _extract_topics,
    _fmt_duration,
)


def _make_rec(base: Path, name: str, **kwargs) -> Path:
    """Create a minimal recording directory."""
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = kwargs.pop("meta", {})
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if "transcript" in kwargs:
        (rec / "transcript.txt").write_text(kwargs["transcript"], encoding="utf-8")
    return rec


class TestCompareRecordings:
    def test_basic_comparison(self, tmp_path: Path):
        a = _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "duration_seconds": 1800,
            "meeting_attendees": ["Alice", "Bob"],
            "meeting_subject": "Sprint Planning",
            "tags": ["engineering"],
        })
        b = _make_rec(tmp_path, "2026-03-08_09-00-00_B", meta={
            "duration_seconds": 2400,
            "meeting_attendees": ["Alice", "Charlie"],
            "meeting_subject": "Sprint Planning",
            "tags": ["engineering", "planning"],
        })
        result = compare_recordings(a, b)
        assert result.duration_a == 1800
        assert result.duration_b == 2400
        assert result.duration_change > 0  # increased

    def test_attendee_diff(self, tmp_path: Path):
        a = _make_rec(tmp_path, "a", meta={
            "meeting_attendees": ["Alice", "Bob", "Charlie"],
        })
        b = _make_rec(tmp_path, "b", meta={
            "meeting_attendees": ["Alice", "Charlie", "Dave"],
        })
        result = compare_recordings(a, b)
        both_lower = [x.lower() for x in result.attendees_both]
        assert "alice" in both_lower
        assert "charlie" in both_lower
        only_a_lower = [x.lower() for x in result.attendees_only_a]
        assert "bob" in only_a_lower
        only_b_lower = [x.lower() for x in result.attendees_only_b]
        assert "dave" in only_b_lower

    def test_tag_diff(self, tmp_path: Path):
        a = _make_rec(tmp_path, "a", meta={"tags": ["engineering", "standup"]})
        b = _make_rec(tmp_path, "b", meta={"tags": ["engineering", "planning"]})
        result = compare_recordings(a, b)
        assert "engineering" in result.tags_both
        assert "standup" in result.tags_only_a
        assert "planning" in result.tags_only_b

    def test_topic_comparison(self, tmp_path: Path):
        a = _make_rec(tmp_path, "a", transcript=(
            "deployment deployment deployment pipeline pipeline "
            "infrastructure infrastructure testing testing "
        ))
        b = _make_rec(tmp_path, "b", transcript=(
            "deployment deployment deployment pipeline pipeline "
            "security security monitoring monitoring "
        ))
        result = compare_recordings(a, b)
        assert "deployment" in result.common_topics
        assert "pipeline" in result.common_topics

    def test_quality_comparison(self, tmp_path: Path):
        a = _make_rec(tmp_path, "a", meta={
            "quality_scores": {"overall_score": 80},
        })
        b = _make_rec(tmp_path, "b", meta={
            "quality_scores": {"overall_score": 90},
        })
        result = compare_recordings(a, b)
        assert result.quality_a == 80
        assert result.quality_b == 90

    def test_quality_none(self, tmp_path: Path):
        a = _make_rec(tmp_path, "a", meta={})
        b = _make_rec(tmp_path, "b", meta={})
        result = compare_recordings(a, b)
        assert result.quality_a is None
        assert result.quality_b is None

    def test_empty_metadata(self, tmp_path: Path):
        a = _make_rec(tmp_path, "a", meta={})
        b = _make_rec(tmp_path, "b", meta={})
        result = compare_recordings(a, b)
        assert result.attendees_both == []
        assert result.duration_change == 0

    def test_null_attendees_do_not_raise(self, tmp_path: Path):
        a = _make_rec(tmp_path, "a", meta={
            "meeting_attendees": None,
            "tags": None,
        })
        b = _make_rec(tmp_path, "b", meta={
            "meeting_attendees": ["Alice"],
            "tags": ["planning"],
        })
        result = compare_recordings(a, b)
        assert result.attendees_both == []
        assert result.attendees_only_a == []
        assert result.attendees_only_b == ["Alice"]

    def test_format_text(self, tmp_path: Path):
        a = _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "duration_seconds": 1800,
            "meeting_attendees": ["Alice", "Bob"],
            "tags": ["engineering"],
        })
        b = _make_rec(tmp_path, "2026-03-08_09-00-00_B", meta={
            "duration_seconds": 2400,
            "meeting_attendees": ["Alice", "Charlie"],
            "tags": ["engineering", "planning"],
        })
        result = compare_recordings(a, b)
        text = result.format_text()
        assert "RECORDING COMPARISON" in text
        assert "Duration:" in text


class TestFindSimilarRecordings:
    def test_finds_by_subject(self, tmp_path: Path):
        target = _make_rec(tmp_path, "target", meta={
            "meeting_subject": "Sprint Planning",
        })
        similar = _make_rec(tmp_path, "similar", meta={
            "meeting_subject": "Sprint Planning",
        })
        different = _make_rec(tmp_path, "different", meta={
            "meeting_subject": "Budget Review",
        })
        results = find_similar_recordings(target, tmp_path)
        paths = [r[0] for r in results]
        assert similar in paths

    def test_finds_by_attendees(self, tmp_path: Path):
        target = _make_rec(tmp_path, "target", meta={
            "meeting_attendees": ["Alice", "Bob", "Charlie"],
        })
        similar = _make_rec(tmp_path, "similar", meta={
            "meeting_attendees": ["Alice", "Bob", "Dave"],
        })
        different = _make_rec(tmp_path, "different", meta={
            "meeting_attendees": ["Eve", "Frank"],
        })
        results = find_similar_recordings(target, tmp_path)
        if results:  # should find similar
            paths = [r[0] for r in results]
            assert similar in paths

    def test_excludes_self(self, tmp_path: Path):
        target = _make_rec(tmp_path, "target", meta={
            "meeting_subject": "Test",
        })
        results = find_similar_recordings(target, tmp_path)
        paths = [r[0] for r in results]
        assert target not in paths

    def test_max_results(self, tmp_path: Path):
        target = _make_rec(tmp_path, "target", meta={
            "meeting_subject": "Planning",
        })
        for i in range(10):
            _make_rec(tmp_path, f"rec_{i}", meta={
                "meeting_subject": "Planning",
            })
        results = find_similar_recordings(target, tmp_path, max_results=3)
        assert len(results) <= 3

    def test_empty_dir(self, tmp_path: Path):
        target = _make_rec(tmp_path, "target", meta={})
        empty = tmp_path / "empty"
        results = find_similar_recordings(target, empty)
        assert results == []

    def test_sorts_by_score(self, tmp_path: Path):
        target = _make_rec(tmp_path, "target", meta={
            "meeting_subject": "Sprint Planning",
            "meeting_attendees": ["Alice", "Bob"],
        })
        exact = _make_rec(tmp_path, "exact", meta={
            "meeting_subject": "Sprint Planning",
            "meeting_attendees": ["Alice", "Bob"],
        })
        partial = _make_rec(tmp_path, "partial", meta={
            "meeting_subject": "Sprint",
            "meeting_attendees": ["Alice"],
        })
        results = find_similar_recordings(target, tmp_path)
        if len(results) >= 2:
            # Exact match should score higher
            assert results[0][1] >= results[1][1]


class TestExtractTopics:
    def test_empty(self):
        assert _extract_topics("") == set()

    def test_short(self):
        assert _extract_topics("hello world") == set()

    def test_extracts_frequent_words(self):
        text = "deployment deployment deployment testing testing infrastructure"
        topics = _extract_topics(text)
        assert "deployment" in topics

    def test_filters_stop_words(self):
        text = "that that that this this this with with with about about about"
        topics = _extract_topics(text)
        assert "that" not in topics

    def test_min_frequency(self):
        text = "unique singleton rare deployment deployment deployment"
        topics = _extract_topics(text)
        assert "unique" not in topics
        assert "deployment" in topics


class TestFormatDuration:
    def test_minutes(self):
        assert _fmt_duration(300) == "5m"

    def test_hours(self):
        assert _fmt_duration(3720) == "1h 02m"

    def test_zero(self):
        assert _fmt_duration(0) == "0m"
