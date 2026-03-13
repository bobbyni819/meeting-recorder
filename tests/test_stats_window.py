"""Tests for the cross-recording statistics window."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from meeting_recorder.ui.stats_window import StatsWindow


@pytest.fixture
def recordings_dir(tmp_path: Path) -> Path:
    """Create a recordings directory."""
    d = tmp_path / "MeetingRecordings"
    d.mkdir()
    return d


def _make_recording(
    base: Path,
    name: str,
    duration: float = 300,
    status: str = "completed",
    app_name: str = "Zoom",
    quality_score: int | None = None,
    speaker_segments: list[dict] | None = None,
    speaker_map: dict | None = None,
    attendees: list[str] | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Create a recording directory with metadata and optional transcript."""
    rec = base / name
    rec.mkdir()
    meta = {
        "duration_seconds": duration,
        "status": status,
        "app_name": app_name,
    }
    if quality_score is not None:
        meta["quality_scores"] = {"overall_score": quality_score}
    if speaker_map:
        meta["speaker_map"] = speaker_map
    if attendees is not None:
        meta["meeting_attendees"] = attendees
    if tags is not None:
        meta["tags"] = tags
    with open(rec / "metadata.json", "w") as f:
        json.dump(meta, f)
    if speaker_segments:
        with open(rec / "transcript.json", "w") as f:
            json.dump({"segments": speaker_segments}, f)
    return rec


class TestComputeStats:
    def test_empty_dir(self, recordings_dir: Path):
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["total_recordings"] == 0
        assert stats["total_duration"] == 0

    def test_nonexistent_dir(self, tmp_path: Path):
        sw = StatsWindow(tmp_path / "nope")
        stats = sw._compute_stats()
        assert stats == {}

    def test_single_recording(self, recordings_dir: Path):
        _make_recording(recordings_dir, "2026-03-10_10-00-00_Test", duration=600)
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["total_recordings"] == 1
        assert stats["total_duration"] == 600
        assert stats["avg_duration"] == 600
        assert stats["completed"] == 1
        assert stats["errors"] == 0

    def test_multiple_recordings(self, recordings_dir: Path):
        _make_recording(recordings_dir, "2026-03-10_10-00-00_A", duration=300, status="completed")
        _make_recording(recordings_dir, "2026-03-11_10-00-00_B", duration=600, status="completed")
        _make_recording(recordings_dir, "2026-03-12_10-00-00_C", duration=900, status="error")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["total_recordings"] == 3
        assert stats["total_duration"] == 1800
        assert stats["avg_duration"] == 600
        assert stats["completed"] == 2
        assert stats["errors"] == 1

    def test_app_counts(self, recordings_dir: Path):
        _make_recording(recordings_dir, "rec1", app_name="Zoom")
        _make_recording(recordings_dir, "rec2", app_name="Zoom")
        _make_recording(recordings_dir, "rec3", app_name="Teams")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["app_counts"]["Zoom"] == 2
        assert stats["app_counts"]["Teams"] == 1

    def test_quality_average(self, recordings_dir: Path):
        _make_recording(recordings_dir, "rec1", quality_score=80)
        _make_recording(recordings_dir, "rec2", quality_score=90)
        _make_recording(recordings_dir, "rec3")  # no quality
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["avg_quality"] == 85

    def test_quality_none_when_no_scores(self, recordings_dir: Path):
        _make_recording(recordings_dir, "rec1")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["avg_quality"] is None

    def test_speaker_times(self, recordings_dir: Path):
        segments = [
            {"speaker": "SPEAKER_00", "start": 0, "end": 60, "text": "Hello"},
            {"speaker": "SPEAKER_01", "start": 60, "end": 120, "text": "World"},
            {"speaker": "SPEAKER_00", "start": 120, "end": 150, "text": "Bye"},
        ]
        _make_recording(
            recordings_dir, "rec1",
            speaker_segments=segments,
            speaker_map={"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        )
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["speaker_times"]["Alice"] == 90
        assert stats["speaker_times"]["Bob"] == 60

    def test_speaker_times_no_map(self, recordings_dir: Path):
        segments = [
            {"speaker": "SPEAKER_00", "start": 0, "end": 30, "text": "Hi"},
        ]
        _make_recording(recordings_dir, "rec1", speaker_segments=segments)
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["speaker_times"]["SPEAKER_00"] == 30

    def test_weekly_duration(self, recordings_dir: Path):
        # Create recordings on specific dates
        _make_recording(recordings_dir, "2026-03-09_10-00-00_Mon", duration=3600)
        _make_recording(recordings_dir, "2026-03-10_10-00-00_Tue", duration=1800)
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert len(stats["weekly_duration"]) >= 1
        # Both should be in the same week (Mon Mar 9 and Tue Mar 10)
        assert sum(stats["weekly_duration"].values()) == 5400

    def test_skips_non_dirs(self, recordings_dir: Path):
        (recordings_dir / "random_file.txt").write_text("ignore me")
        _make_recording(recordings_dir, "rec1", duration=100)
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["total_recordings"] == 1

    def test_skips_dirs_without_metadata(self, recordings_dir: Path):
        (recordings_dir / "empty_dir").mkdir()
        _make_recording(recordings_dir, "rec1", duration=100)
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["total_recordings"] == 1

    def test_corrupt_metadata_skipped(self, recordings_dir: Path):
        _make_recording(recordings_dir, "good_rec", duration=200)
        bad = recordings_dir / "bad_rec"
        bad.mkdir()
        (bad / "metadata.json").write_text("not json")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["total_recordings"] == 1

    def test_corrupt_transcript_skipped(self, recordings_dir: Path):
        rec = _make_recording(recordings_dir, "rec1", duration=200)
        (rec / "transcript.json").write_text("bad json")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        # Should still count the recording
        assert stats["total_recordings"] == 1
        assert stats["speaker_times"] == {}


    def test_attendee_frequency(self, recordings_dir: Path):
        _make_recording(
            recordings_dir, "rec1",
            attendees=["Alice", "Bob"], duration=600)
        _make_recording(
            recordings_dir, "rec2",
            attendees=["Alice", "Charlie"], duration=300)
        _make_recording(
            recordings_dir, "rec3",
            attendees=["Alice"], duration=900)
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["attendee_counts"]["Alice"] == 3
        assert stats["attendee_counts"]["Bob"] == 1
        assert stats["attendee_counts"]["Charlie"] == 1
        # Alice total time = 600 + 300 + 900 = 1800
        assert stats["attendee_time"]["Alice"] == 1800

    def test_hour_distribution(self, recordings_dir: Path):
        _make_recording(recordings_dir, "2026-03-10_09-00-00_Morning")
        _make_recording(recordings_dir, "2026-03-10_09-30-00_Morning2")
        _make_recording(recordings_dir, "2026-03-10_14-00-00_Afternoon")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["hour_counts"][9] == 2
        assert stats["hour_counts"][14] == 1

    def test_day_of_week_distribution(self, recordings_dir: Path):
        # 2026-03-09 is Monday, 2026-03-10 is Tuesday
        _make_recording(recordings_dir, "2026-03-09_10-00-00_Mon")
        _make_recording(recordings_dir, "2026-03-10_10-00-00_Tue")
        _make_recording(recordings_dir, "2026-03-10_14-00-00_Tue2")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["day_counts"][0] == 1  # Monday
        assert stats["day_counts"][1] == 2  # Tuesday

    def test_tag_frequency(self, recordings_dir: Path):
        _make_recording(recordings_dir, "rec1", tags=["engineering", "standup"])
        _make_recording(recordings_dir, "rec2", tags=["engineering", "planning"])
        _make_recording(recordings_dir, "rec3", tags=["standup"])
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["tag_counts"]["engineering"] == 2
        assert stats["tag_counts"]["standup"] == 2
        assert stats["tag_counts"]["planning"] == 1

    def test_empty_attendees(self, recordings_dir: Path):
        _make_recording(recordings_dir, "rec1")
        sw = StatsWindow(recordings_dir)
        stats = sw._compute_stats()
        assert stats["attendee_counts"] == {}


class TestStatsWindowLifecycle:
    def test_close_resets_window(self, recordings_dir: Path):
        sw = StatsWindow(recordings_dir)
        assert sw._window is None
        sw.close()  # Should not error
        assert sw._window is None

    def test_show_reentrant_without_parent(self, recordings_dir: Path):
        """StatsWindow.show() without parent would create Tk — we just test construction."""
        sw = StatsWindow(recordings_dir)
        assert sw._recordings_dir == recordings_dir
