"""Tests for the visual comparison window."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.ui.comparison_window import _fmt_dur


def _make_rec(
    base: Path,
    name: str,
    meta: dict,
    transcript: str = "",
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    return rec


class TestFmtDur:
    def test_seconds_only(self):
        assert _fmt_dur(45) == "0m 45s"

    def test_minutes(self):
        assert _fmt_dur(300) == "5m 00s"

    def test_hours(self):
        assert _fmt_dur(3723) == "1h 02m"

    def test_zero(self):
        assert _fmt_dur(0) == "0m 00s"


class TestComparisonData:
    """Test the comparison data structure works with comparison window."""

    def test_compare_recordings(self, tmp_path):
        """Verify compare_recordings produces valid data for the window."""
        from meeting_recorder.storage.comparison import compare_recordings

        _make_rec(tmp_path, "2026-03-01_09-00-00_Sprint_A", meta={
            "duration_seconds": 1800,
            "meeting_subject": "Sprint Planning",
            "meeting_attendees": ["Alice", "Bob"],
            "tags": ["planning", "sprint"],
        }, transcript="We need to discuss the sprint goals and plan for the next iteration")

        _make_rec(tmp_path, "2026-03-08_09-00-00_Sprint_B", meta={
            "duration_seconds": 2400,
            "meeting_subject": "Sprint Planning",
            "meeting_attendees": ["Alice", "Charlie"],
            "tags": ["planning", "review"],
        }, transcript="Let us review the sprint and plan the upcoming work")

        path_a = tmp_path / "2026-03-01_09-00-00_Sprint_A"
        path_b = tmp_path / "2026-03-08_09-00-00_Sprint_B"
        result = compare_recordings(path_a, path_b)

        assert result.name_a == "2026-03-01_09-00-00_Sprint_A"
        assert result.duration_a == 1800
        assert result.duration_b == 2400
        assert result.duration_change == pytest.approx(33.3, abs=0.5)
        assert "Alice" in result.attendees_both
        assert any("Bob" in a for a in result.attendees_only_a)
        assert any("Charlie" in a for a in result.attendees_only_b)
        assert "planning" in result.tags_both
        assert "sprint" in result.tags_only_a
        assert "review" in result.tags_only_b

    def test_empty_recordings(self, tmp_path):
        """Compare two minimal recordings."""
        from meeting_recorder.storage.comparison import compare_recordings

        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={"duration_seconds": 600})
        _make_rec(tmp_path, "2026-03-02_09-00-00_B", meta={"duration_seconds": 900})

        result = compare_recordings(
            tmp_path / "2026-03-01_09-00-00_A",
            tmp_path / "2026-03-02_09-00-00_B",
        )
        assert result.duration_a == 600
        assert result.duration_b == 900
        assert result.attendees_both == []

    def test_format_text(self, tmp_path):
        """Verify format_text output is reasonable."""
        from meeting_recorder.storage.comparison import compare_recordings

        _make_rec(tmp_path, "2026-03-01_09-00-00_A", meta={
            "duration_seconds": 1800,
            "meeting_attendees": ["Alice"],
        })
        _make_rec(tmp_path, "2026-03-08_09-00-00_B", meta={
            "duration_seconds": 2400,
            "meeting_attendees": ["Alice", "Bob"],
        })

        result = compare_recordings(
            tmp_path / "2026-03-01_09-00-00_A",
            tmp_path / "2026-03-08_09-00-00_B",
        )
        text = result.format_text()
        assert "RECORDING COMPARISON" in text
        assert "Duration" in text
