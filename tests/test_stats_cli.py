"""Tests for the stats CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.stats_cli import compute_stats, format_stats, _fmt_duration


def _make_rec(base: Path, name: str, meta: dict) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return d


class TestFmtDuration:
    def test_minutes_only(self):
        assert _fmt_duration(300) == "5m"

    def test_hours_and_minutes(self):
        assert _fmt_duration(3720) == "1h 02m"

    def test_zero(self):
        assert _fmt_duration(0) == "0m"


class TestComputeStats:
    def test_empty_dir(self, tmp_path):
        stats = compute_stats(tmp_path)
        assert stats.get("total_recordings", 0) == 0

    def test_nonexistent_dir(self, tmp_path):
        stats = compute_stats(tmp_path / "nope")
        assert stats == {}

    def test_basic_stats(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Test", {
            "duration_seconds": 1800,
            "app_name": "Zoom",
            "status": "completed",
        })
        _make_rec(tmp_path, "2026-03-11_14-00-00_Meeting", {
            "duration_seconds": 3600,
            "app_name": "Teams",
            "status": "completed",
        })

        stats = compute_stats(tmp_path)
        assert stats["total_recordings"] == 2
        assert stats["total_duration"] == 5400
        assert stats["completed"] == 2
        assert stats["app_counts"]["Zoom"] == 1
        assert stats["app_counts"]["Teams"] == 1

    def test_quality_average(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A", {
            "duration_seconds": 100,
            "quality_scores": {"overall_score": 80},
        })
        _make_rec(tmp_path, "2026-03-10_10-00-00_B", {
            "duration_seconds": 100,
            "quality_scores": {"overall_score": 60},
        })
        stats = compute_stats(tmp_path)
        assert stats["avg_quality"] == 70

    def test_tags(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A", {
            "duration_seconds": 100,
            "tags": ["standup", "team"],
        })
        _make_rec(tmp_path, "2026-03-10_10-00-00_B", {
            "duration_seconds": 100,
            "tags": ["standup"],
        })
        stats = compute_stats(tmp_path)
        assert stats["tag_counts"]["standup"] == 2
        assert stats["tag_counts"]["team"] == 1

    def test_errors_counted(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A", {
            "duration_seconds": 100, "status": "error",
        })
        stats = compute_stats(tmp_path)
        assert stats["errors"] == 1

    def test_speaker_times_from_transcript(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_A", {
            "duration_seconds": 100,
        })
        data = {
            "segments": [
                {"speaker": "Alice", "start": 0, "end": 30, "text": "hi"},
                {"speaker": "Bob", "start": 30, "end": 50, "text": "hi"},
            ]
        }
        (rec / "transcript.json").write_text(json.dumps(data), encoding="utf-8")
        stats = compute_stats(tmp_path)
        assert stats["speaker_times"]["Alice"] == 30.0
        assert stats["speaker_times"]["Bob"] == 20.0


class TestFormatStats:
    def test_empty(self):
        text = format_stats({})
        assert "No recordings" in text

    def test_basic_format(self):
        stats = {
            "total_recordings": 10,
            "total_duration": 36000,
            "avg_duration": 3600,
            "completed": 9,
            "errors": 1,
            "avg_quality": 75,
            "this_week_time": 7200,
            "speaker_times": {"Alice": 1800, "Bob": 900},
            "app_counts": {"Zoom": 7, "Teams": 3},
            "weekly_duration": {"2026-03-03": 18000, "2026-03-10": 7200},
            "tag_counts": {"standup": 5},
        }
        text = format_stats(stats)
        assert "MEETING STATISTICS" in text
        assert "10" in text
        assert "Alice" in text
        assert "Zoom" in text
        assert "standup" in text

    def test_no_errors_hidden(self):
        stats = {
            "total_recordings": 1,
            "total_duration": 100,
            "avg_duration": 100,
            "completed": 1,
            "errors": 0,
            "avg_quality": None,
            "this_week_time": 0,
            "speaker_times": {},
            "app_counts": {},
            "weekly_duration": {},
            "tag_counts": {},
        }
        text = format_stats(stats)
        assert "Errors" not in text
