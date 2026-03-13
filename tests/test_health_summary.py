"""Tests for recording health summary."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.health_summary import (
    analyze_health,
    format_health,
    HealthSummary,
    HealthIssue,
)


def _make_rec(base: Path, name: str, meta: dict) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


class TestAnalyzeHealth:
    def test_empty_dir(self, tmp_path):
        hs = analyze_health(tmp_path)
        assert hs.total_recordings == 0
        assert hs.score == 100

    def test_nonexistent_dir(self, tmp_path):
        hs = analyze_health(tmp_path / "nope")
        assert hs.total_recordings == 0

    def test_healthy_recordings(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{today}_09-00-00_Test", {
            "status": "completed",
            "duration_seconds": 1800,
            "quality_scores": {"overall_score": 80},
        })
        hs = analyze_health(tmp_path)
        assert hs.total_recordings == 1
        assert hs.healthy_count == 1
        assert hs.score >= 90
        assert hs.label == "healthy"

    def test_error_recordings(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{today}_09-00-00_Err", {
            "status": "error",
            "duration_seconds": 100,
        })
        hs = analyze_health(tmp_path)
        assert any(i.category == "errors" for i in hs.issues)
        assert hs.score < 100

    def test_low_quality(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{today}_09-00-00_Low", {
            "status": "completed",
            "duration_seconds": 100,
            "quality_scores": {"overall_score": 25},
        })
        hs = analyze_health(tmp_path)
        assert any(i.category == "quality" for i in hs.issues)

    def test_stale_recordings(self, tmp_path):
        old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{old}_09-00-00_Old", {
            "status": "completed", "duration_seconds": 100,
        })
        hs = analyze_health(tmp_path, max_age_warn_days=7)
        assert any(i.category == "stale" for i in hs.issues)

    def test_no_stale_if_recent(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{today}_09-00-00_New", {
            "status": "completed", "duration_seconds": 100,
        })
        hs = analyze_health(tmp_path, max_age_warn_days=7)
        assert not any(i.category == "stale" for i in hs.issues)

    def test_multiple_issues_lower_score(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{today}_09-00-00_Err1", {
            "status": "error", "duration_seconds": 100,
        })
        _make_rec(tmp_path, f"{today}_10-00-00_Low", {
            "status": "completed", "duration_seconds": 100,
            "quality_scores": {"overall_score": 20},
        })
        hs = analyze_health(tmp_path)
        assert hs.score < 80
        assert len(hs.issues) >= 2


class TestFormatHealth:
    def test_empty(self):
        hs = HealthSummary(0, 0, 0, [], 100, "healthy")
        text = format_health(hs)
        assert "No recordings" in text

    def test_healthy(self):
        hs = HealthSummary(5, 5, 0, [], 100, "healthy")
        text = format_health(hs)
        assert "RECORDING HEALTH" in text
        assert "100/100" in text
        assert "No issues" in text

    def test_with_issues(self):
        issues = [
            HealthIssue("error", "errors", "2 recordings failed", 2),
            HealthIssue("warning", "quality", "1 low quality", 1),
        ]
        hs = HealthSummary(5, 2, 2, issues, 60, "needs_attention")
        text = format_health(hs)
        assert "2 recordings failed" in text
        assert "1 low quality" in text
