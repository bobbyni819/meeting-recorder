"""Tests for meeting duration prediction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.duration_predict import (
    predict_durations,
    find_anomalies,
    format_predictions,
    _normalize_subject,
    DurationPrediction,
)


def _make_rec(
    base: Path,
    name: str,
    duration: float = 1800,
    subject: str = "",
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"duration_seconds": duration, "meeting_subject": subject}
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return rec


class TestNormalizeSubject:
    def test_basic(self):
        assert _normalize_subject("Sprint Review") == "sprint review"

    def test_strip_re(self):
        assert _normalize_subject("RE: Sprint Review") == "sprint review"
        assert _normalize_subject("FW: Sprint Review") == "sprint review"

    def test_strip_numbers(self):
        assert _normalize_subject("Sprint Review #42") == "sprint review"
        assert _normalize_subject("Sprint Review 12") == "sprint review"

    def test_strip_dates(self):
        assert _normalize_subject("Sprint Review - 2026/03/10") == "sprint review"
        assert _normalize_subject("Sprint Review 2026-03-10") == "sprint review"

    def test_strip_day_names(self):
        assert _normalize_subject("Standup - Monday") == "standup"
        assert _normalize_subject("Standup – Wed") == "standup"


class TestPredictDurations:
    def test_empty_dir(self, tmp_path):
        assert predict_durations(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert predict_durations(tmp_path / "nope") == []

    def test_not_enough_data(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Sprint",
                  duration=1800, subject="Sprint Review")
        _make_rec(tmp_path, "2026-03-17_09-00-00_Sprint",
                  duration=2400, subject="Sprint Review")
        # Only 2, need 3
        assert predict_durations(tmp_path, min_occurrences=3) == []

    def test_basic_prediction(self, tmp_path):
        for i in range(5):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_Sprint{i}",
                      duration=1800 + i * 60, subject="Sprint Review")
        preds = predict_durations(tmp_path)
        assert len(preds) == 1
        pred = preds[0]
        assert pred.subject == "sprint review"
        assert pred.sample_count == 5
        assert pred.avg_minutes > 0
        assert pred.min_minutes <= pred.avg_minutes <= pred.max_minutes

    def test_even_length_median_averages_middle_values(self, tmp_path):
        for i, minutes in enumerate([100, 200, 300, 400]):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_Sprint{i}",
                      duration=minutes * 60, subject="Sprint Review")
        preds = predict_durations(tmp_path, min_occurrences=4)
        assert preds[0].median_minutes == 250.0

    def test_odd_length_median_unchanged(self, tmp_path):
        for i, minutes in enumerate([100, 200, 300]):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_Sprint{i}",
                      duration=minutes * 60, subject="Sprint Review")
        preds = predict_durations(tmp_path)
        assert preds[0].median_minutes == 200.0

    def test_multiple_series(self, tmp_path):
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_Sprint{i}",
                      duration=1800, subject="Sprint Review")
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_14-00-00_Standup{i}",
                      duration=900, subject="Daily Standup")
        preds = predict_durations(tmp_path)
        assert len(preds) == 2

    def test_trend_detection(self, tmp_path):
        # Meetings getting longer
        for i in range(6):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                      duration=1800 + i * 600, subject="Planning")
        preds = predict_durations(tmp_path)
        assert len(preds) == 1
        assert preds[0].trend == "getting_longer"

    def test_stable_trend(self, tmp_path):
        for i in range(6):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                      duration=1800, subject="Standup")
        preds = predict_durations(tmp_path)
        assert preds[0].trend == "stable"

    def test_sorted_by_count(self, tmp_path):
        for i in range(5):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_A{i}",
                      duration=1800, subject="Frequent Meeting")
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_14-00-00_B{i}",
                      duration=1800, subject="Less Frequent")
        preds = predict_durations(tmp_path)
        assert preds[0].sample_count >= preds[1].sample_count

    def test_zero_duration_excluded(self, tmp_path):
        for i in range(3):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                      duration=1800, subject="Meeting")
        _make_rec(tmp_path, "2026-03-13_09-00-00_M3",
                  duration=0, subject="Meeting")
        preds = predict_durations(tmp_path)
        assert preds[0].sample_count == 3

    def test_subject_grouping(self, tmp_path):
        # Same meeting with different suffixes
        _make_rec(tmp_path, "2026-03-10_09-00-00_S1",
                  duration=1800, subject="Sprint Review #1")
        _make_rec(tmp_path, "2026-03-17_09-00-00_S2",
                  duration=1800, subject="Sprint Review #2")
        _make_rec(tmp_path, "2026-03-24_09-00-00_S3",
                  duration=1800, subject="Sprint Review #3")
        preds = predict_durations(tmp_path)
        assert len(preds) == 1


class TestFindAnomalies:
    def test_no_anomalies(self, tmp_path):
        for i in range(5):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                      duration=1800, subject="Standup")
        anomalies = find_anomalies(tmp_path)
        assert anomalies == []

    def test_long_anomaly(self, tmp_path):
        for i in range(4):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_M{i}",
                      duration=1800, subject="Standup")
        # One very long one
        _make_rec(tmp_path, "2026-03-14_09-00-00_MLong",
                  duration=5400, subject="Standup")  # 90 min vs ~30 min avg
        anomalies = find_anomalies(tmp_path, threshold=0.5)
        assert len(anomalies) >= 1
        assert anomalies[0].deviation_pct > 0

    def test_empty_dir(self, tmp_path):
        assert find_anomalies(tmp_path) == []


class TestFormatPredictions:
    def test_empty(self):
        text = format_predictions([])
        assert "Not enough data" in text

    def test_basic(self):
        preds = [DurationPrediction(
            subject="sprint review",
            avg_minutes=32.0, median_minutes=30.0,
            min_minutes=25.0, max_minutes=45.0,
            std_dev=5.2, sample_count=8,
            trend="stable", predicted_minutes=33.0,
        )]
        text = format_predictions(preds)
        assert "DURATION PREDICTIONS" in text
        assert "sprint review" in text
        assert "33" in text
        assert "8 meetings" in text
