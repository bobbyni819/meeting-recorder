"""Tests for collaboration analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.collaboration import (
    analyze_collaboration,
    format_collaboration,
    CollaborationReport,
    CollaboratorPair,
)


def _make_rec(
    base: Path,
    name: str,
    attendees: list[str] | None = None,
    organizer: str = "",
    duration: float = 1800,
    subject: str = "",
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": duration,
        "meeting_attendees": attendees or [],
        "meeting_organizer": organizer,
        "meeting_subject": subject or name,
    }
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return rec


class TestAnalyzeCollaboration:
    def test_empty_dir(self, tmp_path):
        assert analyze_collaboration(tmp_path) is None

    def test_nonexistent_dir(self, tmp_path):
        assert analyze_collaboration(tmp_path / "nope") is None

    def test_no_attendees(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Empty")
        assert analyze_collaboration(tmp_path) is None

    def test_basic_pair(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Meeting",
                  attendees=["Alice", "Bob"], duration=3600)
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert report.total_people == 2
        assert report.total_meetings_analyzed == 1
        assert len(report.top_pairs) == 1
        assert report.top_pairs[0].meeting_count == 1
        assert report.top_pairs[0].total_hours == pytest.approx(1.0, abs=0.1)

    def test_multiple_meetings_same_pair(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                  attendees=["Alice", "Bob"])
        _make_rec(tmp_path, "2026-03-11_09-00-00_B",
                  attendees=["Alice", "Bob"])
        _make_rec(tmp_path, "2026-03-12_09-00-00_C",
                  attendees=["Alice", "Bob"])
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert report.top_pairs[0].meeting_count == 3

    def test_multiple_pairs(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                  attendees=["Alice", "Bob", "Charlie"])
        report = analyze_collaboration(tmp_path)
        assert report is not None
        # 3 people = 3 pairs: A-B, A-C, B-C
        assert len(report.top_pairs) == 3

    def test_organizer_included(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                  attendees=["Bob"], organizer="Alice")
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert report.total_people == 2
        assert len(report.top_pairs) == 1

    def test_organizer_not_duplicated(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                  attendees=["Alice", "Bob"], organizer="Alice")
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert report.total_people == 2

    def test_solo_meetings(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Solo",
                  attendees=["Alice"])
        _make_rec(tmp_path, "2026-03-11_09-00-00_Pair",
                  attendees=["Alice", "Bob"])
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert report.solo_meetings == 1

    def test_most_connected(self, tmp_path):
        # Alice meets everyone
        _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                  attendees=["Alice", "Bob"])
        _make_rec(tmp_path, "2026-03-11_09-00-00_B",
                  attendees=["Alice", "Charlie"])
        _make_rec(tmp_path, "2026-03-12_09-00-00_C",
                  attendees=["Alice", "Dave"])
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert report.most_connected == "Alice"
        assert report.most_connected_count == 3

    def test_avg_attendees(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                  attendees=["Alice", "Bob"])  # 2
        _make_rec(tmp_path, "2026-03-11_09-00-00_B",
                  attendees=["Alice", "Bob", "Charlie", "Dave"])  # 4
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert report.avg_attendees == pytest.approx(3.0, abs=0.1)

    def test_top_n_limit(self, tmp_path):
        # Create many different pairs
        for i in range(20):
            _make_rec(tmp_path, f"2026-03-{10+i//5:02d}_{9+i%5:02d}-00-00_M{i}",
                      attendees=[f"Person{i}", f"Person{i+20}"])
        report = analyze_collaboration(tmp_path, top_n=5)
        assert report is not None
        assert len(report.top_pairs) <= 5

    def test_subjects_tracked(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                  attendees=["Alice", "Bob"], subject="Sprint Review")
        report = analyze_collaboration(tmp_path)
        assert report is not None
        assert "Sprint Review" in report.top_pairs[0].subjects


class TestFormatCollaboration:
    def test_none(self):
        text = format_collaboration(None)
        assert "No meeting data" in text

    def test_basic_format(self):
        report = CollaborationReport(
            top_pairs=[CollaboratorPair(
                person_a="Alice", person_b="Bob",
                meeting_count=5, total_hours=3.5,
                subjects=["Sprint", "Standup"],
            )],
            total_people=4,
            total_meetings_analyzed=10,
            most_connected="Alice",
            most_connected_count=3,
            solo_meetings=2,
            avg_attendees=3.5,
        )
        text = format_collaboration(report)
        assert "COLLABORATION ANALYSIS" in text
        assert "Alice" in text
        assert "Bob" in text
        assert "5 meetings" in text
        assert "3.5h" in text
        assert "FREQUENT PAIRS" in text
        assert "Most connected" in text

    def test_solo_shown(self):
        report = CollaborationReport(
            top_pairs=[], total_people=1,
            total_meetings_analyzed=3, most_connected="Solo",
            most_connected_count=0, solo_meetings=3, avg_attendees=1.0,
        )
        text = format_collaboration(report)
        assert "Solo meetings" in text
