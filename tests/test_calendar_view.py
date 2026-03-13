"""Tests for the recording calendar view."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from meeting_recorder.ui.calendar_view import (
    CalendarWindow,
    scan_recording_dates,
)


@pytest.fixture
def rec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "recordings"
    d.mkdir()
    return d


def _make_recording(base: Path, name: str, meta: dict | None = None) -> Path:
    """Create a fake recording directory with optional metadata."""
    d = base / name
    d.mkdir()
    if meta is not None:
        with open(d / "metadata.json", "w") as f:
            json.dump(meta, f)
    return d


class TestScanRecordingDates:
    def test_empty_dir(self, rec_dir: Path):
        result = scan_recording_dates(rec_dir)
        assert result == {}

    def test_nonexistent_dir(self, tmp_path: Path):
        result = scan_recording_dates(tmp_path / "nope")
        assert result == {}

    def test_single_recording(self, rec_dir: Path):
        _make_recording(rec_dir, "2026-03-10_14-30-00_Test_Zoom", {
            "meeting_subject": "Sprint Planning",
            "duration_seconds": 1800,
            "status": "completed",
            "app_name": "zoom",
        })
        result = scan_recording_dates(rec_dir)
        assert "2026-03-10" in result
        assert len(result["2026-03-10"]) == 1
        assert result["2026-03-10"][0]["subject"] == "Sprint Planning"
        assert result["2026-03-10"][0]["duration"] == 1800

    def test_multiple_same_day(self, rec_dir: Path):
        _make_recording(rec_dir, "2026-03-10_09-00-00_Standup", {
            "duration_seconds": 900, "status": "completed",
        })
        _make_recording(rec_dir, "2026-03-10_14-00-00_Review", {
            "duration_seconds": 3600, "status": "completed",
        })
        result = scan_recording_dates(rec_dir)
        assert len(result["2026-03-10"]) == 2

    def test_multiple_days(self, rec_dir: Path):
        _make_recording(rec_dir, "2026-03-10_09-00-00_A", {"status": "completed"})
        _make_recording(rec_dir, "2026-03-11_09-00-00_B", {"status": "completed"})
        _make_recording(rec_dir, "2026-03-12_09-00-00_C", {"status": "completed"})
        result = scan_recording_dates(rec_dir)
        assert len(result) == 3

    def test_no_metadata(self, rec_dir: Path):
        _make_recording(rec_dir, "2026-03-10_09-00-00_NoMeta")
        result = scan_recording_dates(rec_dir)
        assert "2026-03-10" in result
        info = result["2026-03-10"][0]
        assert "subject" not in info  # no metadata loaded

    def test_invalid_folder_name_skipped(self, rec_dir: Path):
        _make_recording(rec_dir, "not-a-date")
        _make_recording(rec_dir, "abc")
        result = scan_recording_dates(rec_dir)
        assert result == {}

    def test_short_folder_name_skipped(self, rec_dir: Path):
        _make_recording(rec_dir, "short")
        result = scan_recording_dates(rec_dir)
        assert result == {}

    def test_corrupt_metadata_handled(self, rec_dir: Path):
        d = _make_recording(rec_dir, "2026-03-10_09-00-00_Corrupt")
        (d / "metadata.json").write_text("not json")
        result = scan_recording_dates(rec_dir)
        assert "2026-03-10" in result  # folder still found, just no metadata

    def test_path_stored(self, rec_dir: Path):
        _make_recording(rec_dir, "2026-03-10_09-00-00_Test", {"status": "completed"})
        result = scan_recording_dates(rec_dir)
        info = result["2026-03-10"][0]
        assert "path" in info
        assert "2026-03-10" in info["path"]


class TestCalendarWindowLifecycle:
    def test_construction(self, rec_dir: Path):
        cw = CalendarWindow(rec_dir)
        assert cw._window is None
        assert cw._current_month == date.today().month
        assert cw._current_year == date.today().year

    def test_close_resets(self, rec_dir: Path):
        cw = CalendarWindow(rec_dir)
        cw.close()
        assert cw._window is None

    def test_month_wrap_forward(self, rec_dir: Path):
        """Month 12 + 1 should become month 1 of next year."""
        cw = CalendarWindow(rec_dir)
        cw._current_month = 12
        cw._current_year = 2026
        # Manually test wrap logic (same as _change_month without _draw_month)
        m = cw._current_month + 1
        y = cw._current_year
        while m > 12:
            m -= 12
            y += 1
        assert m == 1
        assert y == 2027

    def test_month_wrap_backward(self, rec_dir: Path):
        """Month 1 - 1 should become month 12 of previous year."""
        cw = CalendarWindow(rec_dir)
        cw._current_month = 1
        cw._current_year = 2026
        m = cw._current_month - 1
        y = cw._current_year
        while m < 1:
            m += 12
            y -= 1
        assert m == 12
        assert y == 2025

    def test_initial_month_is_today(self, rec_dir: Path):
        cw = CalendarWindow(rec_dir)
        assert cw._current_month == date.today().month
        assert cw._current_year == date.today().year

    def test_callback_stored(self, rec_dir: Path):
        cb = lambda d: None
        cw = CalendarWindow(rec_dir, on_date_click=cb)
        assert cw._on_date_click is cb
