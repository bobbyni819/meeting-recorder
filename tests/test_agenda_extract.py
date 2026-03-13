"""Tests for meeting agenda extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.agenda_extract import (
    extract_agenda,
    format_agenda,
    _extract_words,
    _detect_boundaries,
    _build_windows,
    _fmt_time,
    _label_topic,
    AgendaItem,
    MeetingAgenda,
)


def _make_rec(tmp_path: Path, segments: list[dict], meta: dict | None = None) -> Path:
    rec = tmp_path / "2026-03-13_09-00-00_Meeting"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "transcript.json").write_text(
        json.dumps({"segments": segments}), encoding="utf-8"
    )
    if meta:
        (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return rec


class TestExtractWords:
    def test_basic(self):
        words = _extract_words("The quick brown fox jumps over the lazy dog")
        assert "quick" in words
        assert "brown" in words
        assert "the" not in words

    def test_stop_words_removed(self):
        words = _extract_words("I think we should do this thing")
        assert "think" not in words
        assert "should" not in words

    def test_short_words_removed(self):
        words = _extract_words("Go to the AI lab")
        assert "go" not in words  # less than 3 chars
        assert "lab" in words

    def test_empty(self):
        assert _extract_words("") == []


class TestFmtTime:
    def test_minutes(self):
        assert _fmt_time(90) == "01:30"

    def test_hours(self):
        assert _fmt_time(3661) == "1:01:01"

    def test_zero(self):
        assert _fmt_time(0) == "00:00"


class TestBuildWindows:
    def test_basic(self):
        segments = [
            {"text": "Let's discuss the project timeline and deliverables", "start": 0, "end": 30},
            {"text": "The first milestone is the database migration project", "start": 30, "end": 60},
            {"text": "We need to finish the API endpoints for the project", "start": 60, "end": 90},
            {"text": "Testing should cover all edge cases in the system", "start": 90, "end": 120},
        ]
        windows = _build_windows(segments, window_size=2)
        assert len(windows) >= 1
        assert all("words" in w for w in windows)

    def test_empty(self):
        assert _build_windows([], 3) == []


class TestDetectBoundaries:
    def test_single_window(self):
        windows = [{"words": {"project": 3, "timeline": 2}}]
        boundaries = _detect_boundaries(windows)
        assert boundaries == [0]

    def test_similar_windows(self):
        windows = [
            {"words": {"project": 3, "timeline": 2, "milestone": 1}},
            {"words": {"project": 2, "timeline": 3, "deadline": 1}},
        ]
        boundaries = _detect_boundaries(windows)
        # High overlap → no boundary at window 1
        assert 0 in boundaries

    def test_different_windows(self):
        windows = [
            {"words": {"project": 3, "timeline": 2, "milestone": 1}},
            {"words": {"budget": 3, "expense": 2, "cost": 1}},
        ]
        boundaries = _detect_boundaries(windows)
        assert len(boundaries) >= 2  # boundary between the two


class TestLabelTopic:
    def test_status_update(self):
        label = _label_topic(["report", "progress"], "Let me give a status update on the project")
        assert label == "Status Update"

    def test_action_items(self):
        label = _label_topic(["items"], "We need to capture action items and follow-ups")
        assert label == "Action Items & Follow-ups"

    def test_generic(self):
        label = _label_topic(["database", "schema", "migration"], "plain text here")
        assert "Database" in label

    def test_empty(self):
        label = _label_topic([], "some text")
        assert label == "General Discussion"


class TestExtractAgenda:
    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "2026-03-13_09-00-00_Meeting"
        rec.mkdir()
        assert extract_agenda(rec) is None

    def test_too_few_segments(self, tmp_path):
        rec = _make_rec(tmp_path, [{"text": "Hello", "start": 0, "end": 10}])
        assert extract_agenda(rec) is None

    def test_basic_extraction(self, tmp_path):
        # Create segments with two distinct topics
        segments = []
        # Topic 1: project planning (0-120s)
        for i in range(10):
            segments.append({
                "text": f"The project timeline milestone deliverable schedule plan release sprint",
                "speaker": "SPEAKER_00",
                "start": i * 12,
                "end": (i + 1) * 12,
            })
        # Topic 2: budget review (120-240s)
        for i in range(10):
            segments.append({
                "text": f"The budget expense revenue quarterly financial forecast spending allocation",
                "speaker": "SPEAKER_01",
                "start": 120 + i * 12,
                "end": 120 + (i + 1) * 12,
            })

        rec = _make_rec(tmp_path, segments, meta={
            "speaker_map": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        })
        agenda = extract_agenda(rec, min_topic_duration=10)
        assert agenda is not None
        assert agenda.total_topics >= 1
        assert len(agenda.main_speakers) >= 1

    def test_speaker_mapping(self, tmp_path):
        segments = []
        for i in range(20):
            segments.append({
                "text": "discussing the architecture design pattern framework implementation strategy",
                "speaker": "SPEAKER_00",
                "start": i * 10,
                "end": (i + 1) * 10,
            })
        rec = _make_rec(tmp_path, segments, meta={
            "speaker_map": {"SPEAKER_00": "Alice"},
        })
        agenda = extract_agenda(rec, min_topic_duration=10)
        assert agenda is not None
        if agenda.items:
            assert "Alice" in agenda.items[0].speakers

    def test_min_duration_filter(self, tmp_path):
        segments = [
            {"text": "quick hello everyone", "speaker": "A", "start": 0, "end": 5},
            {"text": "brief introduction done", "speaker": "B", "start": 5, "end": 10},
            {"text": "now discussing the long topic", "speaker": "A", "start": 10, "end": 200},
        ]
        rec = _make_rec(tmp_path, segments)
        agenda = extract_agenda(rec, min_topic_duration=60)
        # Short segments should be filtered out
        if agenda:
            for item in agenda.items:
                assert item.duration_seconds >= 60


class TestFormatAgenda:
    def test_none(self):
        text = format_agenda(None)
        assert "No agenda" in text

    def test_basic_format(self):
        agenda = MeetingAgenda(
            items=[
                AgendaItem(
                    topic="Project Planning",
                    start_time=0,
                    end_time=600,
                    duration_seconds=600,
                    speakers=["Alice", "Bob"],
                    key_phrases=["project", "timeline", "milestone"],
                    segment_count=10,
                ),
                AgendaItem(
                    topic="Budget Review",
                    start_time=600,
                    end_time=1200,
                    duration_seconds=600,
                    speakers=["Bob"],
                    key_phrases=["budget", "expense"],
                    segment_count=8,
                ),
            ],
            total_topics=2,
            total_duration=1200,
            main_speakers=["Alice", "Bob"],
        )
        text = format_agenda(agenda)
        assert "MEETING AGENDA" in text
        assert "Project Planning" in text
        assert "Budget Review" in text
        assert "Alice" in text
        assert "timeline" in text
        assert "2 topics" in text

    def test_time_formatting(self):
        agenda = MeetingAgenda(
            items=[
                AgendaItem(
                    topic="Intro",
                    start_time=0,
                    end_time=300,
                    duration_seconds=300,
                    speakers=["Alice"],
                    key_phrases=["intro"],
                    segment_count=5,
                ),
            ],
            total_topics=1,
            total_duration=300,
            main_speakers=["Alice"],
        )
        text = format_agenda(agenda)
        assert "00:00" in text
        assert "05:00" in text
