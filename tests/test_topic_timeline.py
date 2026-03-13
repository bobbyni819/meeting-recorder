"""Tests for topic timeline analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.topic_timeline import (
    analyze_topic_timeline,
    format_topic_timeline,
    _extract_words,
    TopicTimeline,
    TopicSegment,
)


def _make_transcript(tmp_path: Path, segments: list[dict]) -> Path:
    rec = tmp_path / "2026-03-10_09-00-00_Team_Meeting"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "transcript.json").write_text(
        json.dumps({"segments": segments}), encoding="utf-8"
    )
    return rec


def _seg(start: float, end: float, speaker: str, text: str) -> dict:
    return {"start": start, "end": end, "speaker": speaker, "text": text}


class TestExtractWords:
    def test_basic(self):
        words = _extract_words("The database migration plan looks good")
        assert "database" in words
        assert "migration" in words
        assert "plan" in words
        assert "the" not in words

    def test_short_words_filtered(self):
        words = _extract_words("It is ok to do so")
        assert len(words) == 0  # all stop words or too short

    def test_case_insensitive(self):
        words = _extract_words("Database DATABASE database")
        assert all(w == "database" for w in words)


class TestAnalyzeTopicTimeline:
    def test_no_dir(self, tmp_path):
        assert analyze_topic_timeline(tmp_path / "nope") is None

    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert analyze_topic_timeline(rec) is None

    def test_too_few_segments(self, tmp_path):
        rec = _make_transcript(tmp_path, [
            _seg(0, 5, "A", "Hello"),
        ])
        assert analyze_topic_timeline(rec) is None

    def test_too_short(self, tmp_path):
        segs = [_seg(i * 10, (i + 1) * 10, "A", "database migration") for i in range(10)]
        rec = _make_transcript(tmp_path, segs)
        assert analyze_topic_timeline(rec) is None  # 100s < 120s

    def test_single_topic(self, tmp_path):
        # All segments about the same topic
        segs = []
        for i in range(40):
            start = i * 10
            end = start + 8
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker,
                             "database migration schema upgrade rollback plan"))
        rec = _make_transcript(tmp_path, segs)
        timeline = analyze_topic_timeline(rec)
        assert timeline is not None
        # May detect 1 or more segments depending on keyword distribution
        assert timeline.topic_count >= 1
        assert timeline.total_duration_min > 0

    def test_topic_shift(self, tmp_path):
        # First half: database discussion, second half: completely different topic
        # Use smaller windows so topic shift is cleanly detected
        segs = []
        for i in range(30):
            start = i * 10
            end = start + 8
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker,
                             "database migration schema tables indexes queries"))
        for i in range(30):
            start = 300 + i * 10
            end = start + 8
            speaker = "Carol" if i % 2 == 0 else "Dave"
            segs.append(_seg(start, end, speaker,
                             "frontend components react styling layout rendering"))
        rec = _make_transcript(tmp_path, segs)
        timeline = analyze_topic_timeline(rec, window_minutes=2.0)
        assert timeline is not None
        assert timeline.topic_count >= 2

    def test_multiple_topics(self, tmp_path):
        topics = [
            ("database migration schema upgrade rollback backup", 0),
            ("frontend components react styling layout rendering", 200),
            ("deployment kubernetes docker container orchestration", 400),
        ]
        segs = []
        for topic_text, base_time in topics:
            for i in range(15):
                start = base_time + i * 10
                end = start + 8
                speaker = "Alice" if i % 2 == 0 else "Bob"
                segs.append(_seg(start, end, speaker, topic_text))
        rec = _make_transcript(tmp_path, segs)
        timeline = analyze_topic_timeline(rec)
        assert timeline is not None
        assert timeline.topic_count >= 2  # Should detect at least 2 distinct topics

    def test_speaker_count(self, tmp_path):
        segs = []
        speakers = ["Alice", "Bob", "Carol"]
        for i in range(30):
            start = i * 10
            end = start + 8
            speaker = speakers[i % 3]
            segs.append(_seg(start, end, speaker,
                             "project planning timeline resources milestones"))
        rec = _make_transcript(tmp_path, segs)
        timeline = analyze_topic_timeline(rec)
        assert timeline is not None
        # At least one segment should have multiple speakers
        assert any(s.speaker_count >= 2 for s in timeline.segments)

    def test_custom_window_size(self, tmp_path):
        segs = []
        for i in range(60):
            start = i * 10
            end = start + 8
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker,
                             "architecture design patterns services components"))
        rec = _make_transcript(tmp_path, segs)
        t1 = analyze_topic_timeline(rec, window_minutes=2.0)
        t2 = analyze_topic_timeline(rec, window_minutes=5.0)
        assert t1 is not None
        assert t2 is not None

    def test_longest_topic(self, tmp_path):
        # Long first topic, short second
        segs = []
        for i in range(30):
            start = i * 10
            end = start + 8
            segs.append(_seg(start, end, "Alice",
                             "budget planning expenses quarterly review financial"))
        for i in range(5):
            start = 300 + i * 10
            end = start + 8
            segs.append(_seg(start, end, "Bob",
                             "hiring recruiting candidates interview process"))
        rec = _make_transcript(tmp_path, segs)
        timeline = analyze_topic_timeline(rec)
        assert timeline is not None
        assert timeline.longest_duration_min > 0

    def test_invalid_json(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.json").write_text("not json", encoding="utf-8")
        assert analyze_topic_timeline(rec) is None


class TestFormatTopicTimeline:
    def test_none(self):
        text = format_topic_timeline(None)
        assert "Not enough data" in text

    def test_basic_format(self):
        segments = [
            TopicSegment(0, 10, 10.0, ["database", "migration"], "Database, Migration", 3),
            TopicSegment(10, 20, 10.0, ["frontend", "react"], "Frontend, React", 2),
        ]
        timeline = TopicTimeline(
            segments=segments,
            total_duration_min=20.0,
            topic_count=2,
            longest_topic="Database, Migration",
            longest_duration_min=10.0,
        )
        text = format_topic_timeline(timeline)
        assert "TOPIC TIMELINE" in text
        assert "20 min" in text
        assert "2 topics" in text
        assert "Database" in text
        assert "Frontend" in text
