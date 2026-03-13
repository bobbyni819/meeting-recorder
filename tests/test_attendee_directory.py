"""Tests for attendee directory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.attendee_directory import (
    AttendeeProfile,
    build_directory,
    find_meetings_with,
    format_directory,
)


def _make_rec(base: Path, name: str, meta: dict) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return rec


class TestBuildDirectory:
    def test_basic(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint", meta={
            "meeting_attendees": ["Alice", "Bob"],
            "duration_seconds": 1800,
            "meeting_subject": "Sprint Planning",
        })
        profiles = build_directory(tmp_path)
        assert len(profiles) == 2
        names = {p.name for p in profiles}
        assert "Alice" in names
        assert "Bob" in names

    def test_meeting_count(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Alice", "Bob"],
        })
        _make_rec(tmp_path, "2026-03-08_09-00-00_B", meta={
            "meeting_attendees": ["Alice", "Charlie"],
        })
        profiles = build_directory(tmp_path)
        alice = next(p for p in profiles if p.name == "Alice")
        assert alice.meeting_count == 2

    def test_sorted_by_count(self, tmp_path):
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_M{i}", meta={
                "meeting_attendees": ["Alice", "Bob"],
            })
        _make_rec(tmp_path, "2026-03-10_09-00-00_M3", meta={
            "meeting_attendees": ["Charlie"],
        })
        profiles = build_directory(tmp_path)
        assert profiles[0].meeting_count >= profiles[-1].meeting_count

    def test_date_range(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Alice"],
        })
        _make_rec(tmp_path, "2026-03-15_09-00-00_B", meta={
            "meeting_attendees": ["Alice"],
        })
        profiles = build_directory(tmp_path)
        alice = profiles[0]
        assert alice.first_seen == "2026-03-01"
        assert alice.last_seen == "2026-03-15"

    def test_common_subjects(self, tmp_path):
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{i+1:02d}_09-00-00_Sprint_{i}", meta={
                "meeting_attendees": ["Alice"],
                "meeting_subject": "Sprint Planning",
            })
        _make_rec(tmp_path, "2026-03-10_09-00-00_Budget", meta={
            "meeting_attendees": ["Alice"],
            "meeting_subject": "Budget Review",
        })
        profiles = build_directory(tmp_path)
        alice = profiles[0]
        assert "Sprint Planning" in alice.common_subjects

    def test_total_minutes(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Alice"],
            "duration_seconds": 1800,
        })
        _make_rec(tmp_path, "2026-03-02_09-00-00_B", meta={
            "meeting_attendees": ["Alice"],
            "duration_seconds": 2400,
        })
        profiles = build_directory(tmp_path)
        alice = profiles[0]
        assert alice.total_minutes == 70.0  # 30 + 40

    def test_empty_dir(self, tmp_path):
        assert build_directory(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert build_directory(tmp_path / "noexist") == []

    def test_no_attendees(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_subject": "Solo",
        })
        assert build_directory(tmp_path) == []

    def test_case_insensitive(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Alice Smith"],
        })
        _make_rec(tmp_path, "2026-03-02_09-00-00_B", meta={
            "meeting_attendees": ["alice smith"],
        })
        profiles = build_directory(tmp_path)
        assert len(profiles) == 1
        assert profiles[0].meeting_count == 2


class TestFindMeetingsWith:
    def test_finds_by_attendee(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Alice", "Bob"],
        })
        _make_rec(tmp_path, "2026-03-02_09-00-00_B", meta={
            "meeting_attendees": ["Charlie"],
        })
        results = find_meetings_with(tmp_path, "Alice")
        assert len(results) == 1

    def test_partial_match(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Alice Smith"],
        })
        results = find_meetings_with(tmp_path, "alice")
        assert len(results) == 1

    def test_finds_by_organizer(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_organizer": "Alice",
        })
        results = find_meetings_with(tmp_path, "Alice")
        assert len(results) == 1

    def test_finds_by_speaker(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "speaker_map": {"SPEAKER_00": "Alice"},
        })
        results = find_meetings_with(tmp_path, "Alice")
        assert len(results) == 1

    def test_no_results(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Bob"],
        })
        results = find_meetings_with(tmp_path, "Alice")
        assert len(results) == 0

    def test_sorted_newest_first(self, tmp_path):
        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "meeting_attendees": ["Alice"],
        })
        _make_rec(tmp_path, "2026-03-10_09-00-00_B", meta={
            "meeting_attendees": ["Alice"],
        })
        results = find_meetings_with(tmp_path, "Alice")
        assert results[0][0].name > results[1][0].name


class TestFormatDirectory:
    def test_empty(self):
        assert format_directory([]) == "No attendees found."

    def test_basic_format(self):
        profiles = [
            AttendeeProfile(
                name="Alice",
                meeting_count=5,
                total_minutes=150,
                first_seen="2026-01-01",
                last_seen="2026-03-01",
                common_subjects=["Sprint Planning"],
                common_apps=["Teams"],
                recordings=[],
            ),
        ]
        text = format_directory(profiles)
        assert "ATTENDEE DIRECTORY" in text
        assert "Alice" in text
        assert "5 meetings" in text
        assert "Sprint Planning" in text

    def test_max_entries(self):
        profiles = [
            AttendeeProfile(
                name=f"Person{i}",
                meeting_count=1,
                total_minutes=30,
                first_seen="2026-03-01",
                last_seen="2026-03-01",
                common_subjects=[],
                common_apps=[],
                recordings=[],
            )
            for i in range(30)
        ]
        text = format_directory(profiles, max_entries=5)
        # Should only contain first 5
        assert "Person0" in text
        assert "Person4" in text
        assert "Person5" not in text
