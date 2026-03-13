"""Tests for recurring meeting analysis."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from meeting_recorder.storage.recurring import (
    MeetingInstance,
    RecurringSeries,
    find_recurring_meetings,
    _normalize_subject,
    _parse_folder_date,
    _subjects_match,
)


def _make_rec(base: Path, name: str, **kwargs) -> Path:
    """Create a minimal recording directory."""
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = kwargs.pop("meta", {})
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return rec


class TestParseDate:
    def test_standard_format(self):
        dt = _parse_folder_date("2026-03-06_14-30-00_Teams")
        assert dt == datetime(2026, 3, 6, 14, 30, 0)

    def test_with_subject(self):
        dt = _parse_folder_date("2026-01-15_09-00-00_Sprint_Planning_Teams")
        assert dt == datetime(2026, 1, 15, 9, 0, 0)

    def test_invalid(self):
        assert _parse_folder_date("invalid_folder") is None

    def test_partial(self):
        assert _parse_folder_date("2026-03") is None


class TestNormalizeSubject:
    def test_basic(self):
        assert _normalize_subject("Sprint Planning") == "sprint planning"

    def test_re_prefix(self):
        assert _normalize_subject("RE: Sprint Planning") == "sprint planning"

    def test_fw_prefix(self):
        assert _normalize_subject("FW: Budget Review") == "budget review"

    def test_trailing_date(self):
        assert _normalize_subject("Sprint Planning (2026-03-01)") == "sprint planning"

    def test_trailing_number(self):
        assert _normalize_subject("Sprint Planning 23") == "sprint planning"

    def test_trailing_hash_number(self):
        assert _normalize_subject("Sprint Planning #23") == "sprint planning"

    def test_trailing_month(self):
        assert _normalize_subject("Budget Review - March 1") == "budget review"

    def test_whitespace(self):
        assert _normalize_subject("  Sprint   Planning  ") == "sprint planning"


class TestSubjectsMatch:
    def test_exact(self):
        assert _subjects_match("sprint planning", "sprint planning")

    def test_containment(self):
        assert _subjects_match("sprint", "sprint planning")
        assert _subjects_match("sprint planning", "sprint")

    def test_word_overlap(self):
        assert _subjects_match("sprint planning review", "sprint planning session")

    def test_no_match(self):
        assert not _subjects_match("budget review", "sprint planning")

    def test_empty(self):
        assert not _subjects_match("", "sprint planning")


class TestMeetingInstance:
    def test_create(self, tmp_path):
        inst = MeetingInstance(
            path=tmp_path,
            date=datetime(2026, 3, 6, 14, 0),
            duration=1800,
            attendees=["Alice", "Bob"],
            subject="Sprint Planning",
            speaker_count=2,
            quality=85,
            tags=["engineering"],
        )
        assert inst.duration == 1800
        assert len(inst.attendees) == 2


class TestRecurringSeries:
    def _make_series(self, n: int = 4) -> RecurringSeries:
        instances = []
        for i in range(n):
            instances.append(MeetingInstance(
                path=Path(f"/rec_{i}"),
                date=datetime(2026, 3, 1 + i * 7, 9, 0),
                duration=1800 + i * 60,
                attendees=["Alice", "Bob"] + (["Charlie"] if i % 2 == 0 else []),
                subject="Sprint Planning",
                speaker_count=2,
                quality=80 + i,
                tags=["engineering"],
            ))
        return RecurringSeries(subject="Sprint Planning", instances=instances)

    def test_count(self):
        s = self._make_series(4)
        assert s.count == 4

    def test_avg_duration(self):
        s = self._make_series(4)
        # 1800, 1860, 1920, 1980 → avg 1890
        assert s.avg_duration == 1890.0

    def test_core_attendees(self):
        s = self._make_series(4)
        core = s.core_attendees
        assert "alice" in core
        assert "bob" in core
        # Charlie only in 2 of 4 = 50%, below 60% threshold
        assert "charlie" not in core

    def test_all_attendees(self):
        s = self._make_series(4)
        all_att = [a.lower() for a in s.all_attendees]
        assert "alice" in all_att
        assert "bob" in all_att
        assert "charlie" in all_att

    def test_avg_attendee_count(self):
        s = self._make_series(4)
        # 3, 2, 3, 2 → avg 2.5
        assert s.avg_attendee_count == 2.5

    def test_date_range(self):
        s = self._make_series(4)
        first, last = s.date_range
        assert first.day == 1
        assert last.day == 22

    def test_avg_interval_days(self):
        s = self._make_series(4)
        assert s.avg_interval_days == 7.0

    def test_frequency_label_weekly(self):
        s = self._make_series(4)
        assert s.frequency_label == "weekly"

    def test_frequency_label_daily(self):
        instances = [
            MeetingInstance(
                path=Path(f"/rec_{i}"),
                date=datetime(2026, 3, 1 + i, 9, 0),
                duration=900,
                attendees=["Alice"],
                subject="Standup",
                speaker_count=1,
                quality=None,
                tags=[],
            )
            for i in range(5)
        ]
        s = RecurringSeries(subject="Standup", instances=instances)
        assert s.frequency_label == "daily"

    def test_frequency_label_one_time(self):
        s = RecurringSeries(subject="Test", instances=[
            MeetingInstance(
                path=Path("/rec_0"),
                date=datetime(2026, 3, 1, 9, 0),
                duration=1800,
                attendees=[],
                subject="Test",
                speaker_count=0,
                quality=None,
                tags=[],
            )
        ])
        assert s.frequency_label == "one-time"

    def test_duration_trend(self):
        s = self._make_series(4)
        # Meetings getting slightly longer, should be positive
        assert s.duration_trend > 0

    def test_duration_trend_single(self):
        s = RecurringSeries(subject="Test", instances=[
            MeetingInstance(
                path=Path("/rec_0"),
                date=datetime(2026, 3, 1, 9, 0),
                duration=1800,
                attendees=[],
                subject="Test",
                speaker_count=0,
                quality=None,
                tags=[],
            )
        ])
        assert s.duration_trend == 0.0

    def test_format_summary(self):
        s = self._make_series(4)
        text = s.format_summary()
        assert "RECURRING MEETING" in text
        assert "Sprint Planning" in text
        assert "weekly" in text
        assert "alice" in text.lower()

    def test_empty_series(self):
        s = RecurringSeries(subject="Empty", instances=[])
        assert s.count == 0
        assert s.avg_duration == 0
        assert s.core_attendees == []
        assert s.avg_attendee_count == 0


class TestFindRecurringMeetings:
    def test_finds_recurring(self, tmp_path):
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_Sprint", meta={
                "meeting_subject": "Sprint Planning",
                "duration_seconds": 1800,
                "meeting_attendees": ["Alice", "Bob"],
            })
        results = find_recurring_meetings(tmp_path)
        assert len(results) == 1
        assert results[0].count == 3
        assert results[0].subject == "Sprint Planning"

    def test_groups_similar_subjects(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint", meta={
            "meeting_subject": "Sprint Planning",
        })
        _make_rec(tmp_path, "2026-03-08_09-00-00_Sprint", meta={
            "meeting_subject": "Sprint Planning #23",
        })
        _make_rec(tmp_path, "2026-03-15_09-00-00_Sprint", meta={
            "meeting_subject": "RE: Sprint Planning",
        })
        results = find_recurring_meetings(tmp_path)
        assert len(results) == 1
        assert results[0].count == 3

    def test_separates_different_meetings(self, tmp_path):
        for i in range(2):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_Sprint_{i}", meta={
                "meeting_subject": "Sprint Planning",
            })
        for i in range(2):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_14-00-00_Budget_{i}", meta={
                "meeting_subject": "Budget Review",
            })
        results = find_recurring_meetings(tmp_path)
        assert len(results) == 2
        subjects = {r.subject for r in results}
        assert "Sprint Planning" in subjects
        assert "Budget Review" in subjects

    def test_min_occurrences(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_OneOff", meta={
            "meeting_subject": "One Off Meeting",
        })
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_Sprint_{i}", meta={
                "meeting_subject": "Sprint Planning",
            })
        results = find_recurring_meetings(tmp_path, min_occurrences=2)
        assert len(results) == 1
        assert results[0].subject == "Sprint Planning"

    def test_empty_dir(self, tmp_path):
        results = find_recurring_meetings(tmp_path)
        assert results == []

    def test_nonexistent_dir(self, tmp_path):
        results = find_recurring_meetings(tmp_path / "noexist")
        assert results == []

    def test_no_subject(self, tmp_path):
        """Recordings without subjects are skipped."""
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_NoSubj_{i}", meta={
                "duration_seconds": 1800,
            })
        results = find_recurring_meetings(tmp_path)
        assert results == []

    def test_sorted_by_count(self, tmp_path):
        for i in range(5):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_Daily_{i}", meta={
                "meeting_subject": "Daily Standup",
            })
        for i in range(2):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_14-00-00_Weekly_{i}", meta={
                "meeting_subject": "Weekly Sync",
            })
        results = find_recurring_meetings(tmp_path)
        assert results[0].count >= results[1].count

    def test_chronological_instances(self, tmp_path):
        """Instances within a series should be sorted chronologically."""
        _make_rec(tmp_path, "2026-03-15_09-00-00_Sprint_c", meta={
            "meeting_subject": "Sprint Planning",
        })
        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint_a", meta={
            "meeting_subject": "Sprint Planning",
        })
        _make_rec(tmp_path, "2026-03-08_09-00-00_Sprint_b", meta={
            "meeting_subject": "Sprint Planning",
        })
        results = find_recurring_meetings(tmp_path)
        assert results[0].instances[0].date < results[0].instances[-1].date

    def test_corrupt_metadata(self, tmp_path):
        """Corrupt metadata files are skipped gracefully."""
        rec = tmp_path / "2026-03-01_09-00-00_Corrupt"
        rec.mkdir()
        (rec / "metadata.json").write_text("bad json", encoding="utf-8")
        _make_rec(tmp_path, "2026-03-08_09-00-00_Good", meta={
            "meeting_subject": "Sprint Planning",
        })
        _make_rec(tmp_path, "2026-03-15_09-00-00_Good2", meta={
            "meeting_subject": "Sprint Planning",
        })
        results = find_recurring_meetings(tmp_path)
        assert len(results) == 1
        assert results[0].count == 2
