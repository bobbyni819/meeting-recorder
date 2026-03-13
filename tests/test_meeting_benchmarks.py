"""Tests for meeting benchmarks."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.meeting_benchmarks import (
    compute_benchmarks,
    compare_to_benchmark,
    format_benchmark_comparison,
    Benchmark,
    BenchmarkComparison,
)


def _this_week(offset: int = 0) -> date:
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=offset)


def _make_rec(base: Path, d: date, subject: str, duration: int,
              speakers: int = 3, quality: int | None = None,
              action_items: list | None = None) -> Path:
    name = f"{d.isoformat()}_09-00-00_{subject.replace(' ', '_')}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": duration,
        "meeting_subject": subject,
        "speaker_count": speakers,
    }
    if quality is not None:
        meta["quality_scores"] = {"overall_score": quality}
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if action_items is not None:
        (rec / "action_items.json").write_text(json.dumps(action_items), encoding="utf-8")
    return rec


class TestComputeBenchmarks:
    def test_no_dir(self, tmp_path):
        assert compute_benchmarks(tmp_path / "nope") == {}

    def test_empty_dir(self, tmp_path):
        assert compute_benchmarks(tmp_path) == {}

    def test_basic_benchmarks(self, tmp_path):
        d = _this_week()
        for i in range(3):
            _make_rec(tmp_path, d - timedelta(days=i), "Daily Standup",
                      900, speakers=5)
        bms = compute_benchmarks(tmp_path)
        assert len(bms) >= 1

    def test_multiple_types(self, tmp_path):
        d = _this_week()
        for i in range(3):
            _make_rec(tmp_path, d - timedelta(days=i), "Daily Standup",
                      900, speakers=5)
        for i in range(3):
            _make_rec(tmp_path, d - timedelta(days=i + 3), "Sprint Planning",
                      3600, speakers=8)
        bms = compute_benchmarks(tmp_path)
        assert len(bms) >= 1

    def test_old_excluded(self, tmp_path):
        old = _this_week() - timedelta(weeks=20)
        for i in range(3):
            _make_rec(tmp_path, old + timedelta(days=i), "Old Meeting", 1800)
        bms = compute_benchmarks(tmp_path, weeks=4)
        assert len(bms) == 0


class TestCompareToSenchmark:
    def test_no_benchmarks(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "metadata.json").write_text(json.dumps({
            "duration_seconds": 1800,
        }), encoding="utf-8")
        assert compare_to_benchmark(rec, {}) is None

    def test_basic_comparison(self, tmp_path):
        bm = Benchmark(
            meeting_type="general", count=10,
            avg_duration_min=30.0, avg_speakers=4.0,
            avg_actions=3.0, avg_quality=75.0,
        )
        rec = _make_rec(tmp_path, _this_week(), "Team Meeting",
                        2400, speakers=5, quality=80,
                        action_items=[{"text": f"Action {i}"} for i in range(4)])
        comp = compare_to_benchmark(rec, {"general": bm})
        assert comp is not None
        assert comp.duration_min == 40.0
        assert comp.speakers == 5
        assert comp.action_count == 4
        assert comp.quality == 80

    def test_verdict_above(self, tmp_path):
        bm = Benchmark(
            meeting_type="general", count=10,
            avg_duration_min=60.0, avg_speakers=4.0,
            avg_actions=2.0, avg_quality=60.0,
        )
        # Shorter meeting with more actions and better quality
        rec = _make_rec(tmp_path, _this_week(), "Efficient Meeting",
                        1500, speakers=4, quality=85,
                        action_items=[{"text": f"Action {i}"} for i in range(5)])
        comp = compare_to_benchmark(rec, {"general": bm})
        assert comp is not None
        assert comp.overall_verdict == "above average"

    def test_delta_strings(self, tmp_path):
        bm = Benchmark(
            meeting_type="general", count=10,
            avg_duration_min=30.0, avg_speakers=4.0,
            avg_actions=3.0, avg_quality=None,
        )
        rec = _make_rec(tmp_path, _this_week(), "Long Meeting", 3600, speakers=4)
        comp = compare_to_benchmark(rec, {"general": bm})
        assert comp is not None
        assert "longer" in comp.duration_delta


class TestFormatBenchmarkComparison:
    def test_none(self):
        text = format_benchmark_comparison(None)
        assert "No benchmark" in text

    def test_basic(self):
        comp = BenchmarkComparison(
            recording_name="rec1",
            subject="Sprint Planning",
            meeting_type="planning",
            benchmark=Benchmark(
                meeting_type="planning", count=15,
                avg_duration_min=60.0, avg_speakers=8.0,
                avg_actions=5.0, avg_quality=75.0,
            ),
            duration_min=55.0,
            duration_delta="-5 min shorter",
            speakers=7,
            speaker_delta="typical",
            action_count=6,
            action_delta="+1 more",
            quality=80,
            quality_delta="+5",
            overall_verdict="above average",
        )
        text = format_benchmark_comparison(comp)
        assert "BENCHMARK COMPARISON" in text
        assert "Planning" in text
        assert "Above Average" in text
        assert "55 min" in text
        assert "60 min" in text
