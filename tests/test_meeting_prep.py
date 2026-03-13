"""Tests for meeting preparation sheet generator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.meeting_prep import (
    generate_prep,
    format_prep,
    MeetingPrep,
    _analyze_sentiment_trend,
)


def _make_rec(
    base: Path,
    name: str,
    duration: float = 1800,
    subject: str = "",
    attendees: list[str] | None = None,
    summary: str = "",
    transcript: str = "",
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": duration,
        "meeting_subject": subject,
        "meeting_attendees": attendees or [],
        "status": "completed",
    }
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    return rec


class TestGeneratePrep:
    def test_empty_dir(self, tmp_path):
        assert generate_prep(tmp_path, "Sprint") is None

    def test_nonexistent_dir(self, tmp_path):
        assert generate_prep(tmp_path / "nope", "Sprint") is None

    def test_no_match(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Meeting",
                  subject="Daily Standup")
        assert generate_prep(tmp_path, "Sprint Review") is None

    def test_basic_prep(self, tmp_path):
        for i in range(3):
            _make_rec(
                tmp_path, f"2026-03-{10+i:02d}_09-00-00_S{i}",
                subject="Sprint Review",
                duration=1800 + i * 60,
                attendees=["Alice", "Bob"],
                summary=f"Meeting {i} summary with key points.",
            )
        prep = generate_prep(tmp_path, "Sprint Review")
        assert prep is not None
        assert prep.subject == "Sprint Review"
        assert prep.occurrence_count == 3
        assert prep.last_meeting_date == "2026-03-12"
        assert "Meeting 2 summary" in prep.last_summary
        assert "Alice" in prep.attendees
        assert "Bob" in prep.attendees

    def test_case_insensitive_match(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_S1",
                  subject="sprint review")
        prep = generate_prep(tmp_path, "Sprint Review")
        assert prep is not None

    def test_partial_match(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_S1",
                  subject="Weekly Sprint Review Meeting")
        prep = generate_prep(tmp_path, "Sprint Review")
        assert prep is not None

    def test_attendees_deduplicated(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_S1",
                  subject="Sprint", attendees=["Alice", "Bob"])
        _make_rec(tmp_path, "2026-03-11_09-00-00_S2",
                  subject="Sprint", attendees=["Alice", "Charlie"])
        prep = generate_prep(tmp_path, "Sprint")
        assert prep is not None
        assert len(prep.attendees) == 3
        names_lower = [a.lower() for a in prep.attendees]
        assert "alice" in names_lower
        assert "bob" in names_lower
        assert "charlie" in names_lower

    def test_key_stats(self, tmp_path):
        for i in range(4):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                      subject="Planning", duration=3600)
        prep = generate_prep(tmp_path, "Planning")
        assert prep is not None
        assert prep.key_stats["total_meetings"] == 4
        assert prep.key_stats["avg_duration_min"] == 60.0
        assert prep.key_stats["total_hours"] == 4.0

    def test_last_summary_used(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_S1",
                  subject="Review", summary="Old summary")
        _make_rec(tmp_path, "2026-03-11_09-00-00_S2",
                  subject="Review", summary="Latest summary")
        prep = generate_prep(tmp_path, "Review")
        assert "Latest summary" in prep.last_summary

    def test_no_summary(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_S1",
                  subject="Review")
        prep = generate_prep(tmp_path, "Review")
        assert prep.last_summary == ""


class TestFormatPrep:
    def test_basic_format(self):
        prep = MeetingPrep(
            subject="Sprint Review",
            occurrence_count=5,
            last_meeting_date="2026-03-10",
            last_summary="We discussed the sprint goals.",
            outstanding_actions=["Fix login bug", "Update docs"],
            recent_topics=["sprint", "deployment", "testing"],
            predicted_duration_min=35.0,
            duration_trend="stable",
            attendees=["Alice", "Bob"],
            sentiment_trend="improving",
            key_stats={
                "total_meetings": 5,
                "total_hours": 3.0,
                "avg_duration_min": 36.0,
            },
        )
        text = format_prep(prep)
        assert "MEETING PREP: Sprint Review" in text
        assert "Occurrence #6" in text
        assert "~35 min" in text
        assert "stable" in text
        assert "sprint goals" in text
        assert "Fix login bug" in text
        assert "sprint" in text
        assert "Alice" in text
        assert "5" in text
        assert "improving" in text

    def test_empty_prep(self):
        prep = MeetingPrep(
            subject="New Meeting",
            occurrence_count=1,
            last_meeting_date="2026-03-10",
            last_summary="",
            outstanding_actions=[],
            recent_topics=[],
            predicted_duration_min=0.0,
            duration_trend="unknown",
            attendees=[],
            sentiment_trend="unknown",
            key_stats={},
        )
        text = format_prep(prep)
        assert "MEETING PREP: New Meeting" in text
        assert "LAST MEETING SUMMARY" not in text
        assert "OUTSTANDING" not in text

    def test_long_summary_truncated(self):
        prep = MeetingPrep(
            subject="Test",
            occurrence_count=1,
            last_meeting_date="2026-03-10",
            last_summary="A" * 1000,
            outstanding_actions=[],
            recent_topics=[],
            predicted_duration_min=0.0,
            duration_trend="unknown",
            attendees=[],
            sentiment_trend="unknown",
            key_stats={},
        )
        text = format_prep(prep)
        assert "..." in text


class TestSentimentTrend:
    def test_not_enough_data(self):
        assert _analyze_sentiment_trend([]) == "unknown"

    def test_improving(self, tmp_path):
        # Create recordings with transcripts that have improving sentiment
        recs = []
        for i, text in enumerate([
            "terrible disaster failure problem crisis",
            "bad issue concern",
            "good progress improvement",
            "excellent amazing wonderful fantastic",
        ]):
            path = _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                             subject="Test", transcript=text)
            (path / "transcript.txt").write_text(text, encoding="utf-8")
            recs.append(("2026-03-{10+i:02d}", path, {}))
        result = _analyze_sentiment_trend(recs)
        assert result == "improving"

    def test_declining(self, tmp_path):
        recs = []
        for i, text in enumerate([
            "excellent amazing wonderful fantastic great",
            "good nice progress",
            "bad concern issue problem",
            "terrible disaster failure horrible nightmare",
        ]):
            path = _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                             subject="Test", transcript=text)
            (path / "transcript.txt").write_text(text, encoding="utf-8")
            recs.append((f"2026-03-{10+i:02d}", path, {}))
        result = _analyze_sentiment_trend(recs)
        assert result == "declining"

    def test_stable(self, tmp_path):
        recs = []
        for i in range(4):
            text = "good progress meeting discussed agenda items reviewed"
            path = _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                             subject="Test", transcript=text)
            (path / "transcript.txt").write_text(text, encoding="utf-8")
            recs.append((f"2026-03-{10+i:02d}", path, {}))
        result = _analyze_sentiment_trend(recs)
        assert result == "stable"
