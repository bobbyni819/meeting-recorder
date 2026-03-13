"""Tests for meeting engagement score."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.engagement_score import (
    compute_engagement,
    format_engagement,
    EngagementScore,
)


def _make_rec(base: Path, meta: dict,
              transcript_txt: str = "",
              action_items: list | None = None,
              decisions: list | None = None,
              summary: str = "",
              segments: list | None = None) -> Path:
    rec = base / "2026-03-13_09-00-00_Test"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if transcript_txt:
        (rec / "transcript.txt").write_text(transcript_txt, encoding="utf-8")
    if action_items is not None:
        (rec / "action_items.json").write_text(json.dumps(action_items), encoding="utf-8")
    if decisions is not None:
        (rec / "decisions.json").write_text(
            json.dumps({"decisions": decisions}), encoding="utf-8"
        )
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    if segments is not None:
        (rec / "transcript.json").write_text(
            json.dumps({"segments": segments}), encoding="utf-8"
        )
    return rec


class TestComputeEngagement:
    def test_no_metadata(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert compute_engagement(rec) is None

    def test_too_short(self, tmp_path):
        rec = _make_rec(tmp_path, {"duration_seconds": 30})
        assert compute_engagement(rec) is None

    def test_basic_score(self, tmp_path):
        rec = _make_rec(tmp_path, {"duration_seconds": 1800, "speaker_count": 3})
        score = compute_engagement(rec)
        assert score is not None
        assert 0 <= score.overall <= 100
        assert score.label in ("Highly Engaged", "Engaged", "Moderate", "Low Engagement", "Disengaged")

    def test_high_engagement(self, tmp_path):
        rec = _make_rec(
            tmp_path,
            {"duration_seconds": 3600, "speaker_count": 5,
             "quality_scores": {"overall_score": 90}},
            transcript_txt="Great progress on the project. Excellent teamwork and collaboration. Amazing results.",
            action_items=[{"text": f"Action {i}"} for i in range(5)],
            decisions=[{"description": f"Decision {i}"} for i in range(3)],
            segments=[
                {"speaker": "A", "start": 0, "end": 600},
                {"speaker": "B", "start": 600, "end": 1200},
                {"speaker": "C", "start": 1200, "end": 1800},
            ],
        )
        score = compute_engagement(rec)
        assert score is not None
        assert score.overall >= 60
        assert score.output > 50

    def test_low_engagement(self, tmp_path):
        rec = _make_rec(
            tmp_path,
            {"duration_seconds": 1800, "speaker_count": 1},
            transcript_txt="This is terrible and frustrating. Nothing works.",
        )
        score = compute_engagement(rec)
        assert score is not None
        assert score.overall < 50

    def test_action_items_boost_output(self, tmp_path):
        rec = _make_rec(
            tmp_path,
            {"duration_seconds": 1800},
            action_items=[{"text": f"Do {i}"} for i in range(4)],
        )
        score = compute_engagement(rec)
        assert score is not None
        assert score.output >= 60

    def test_decisions_boost_output(self, tmp_path):
        rec = _make_rec(
            tmp_path,
            {"duration_seconds": 1800},
            decisions=[{"description": f"Decision {i}"} for i in range(3)],
        )
        score = compute_engagement(rec)
        assert score is not None
        assert score.output >= 60

    def test_summary_gives_some_output(self, tmp_path):
        rec = _make_rec(
            tmp_path,
            {"duration_seconds": 1800},
            summary="# Summary\n- Discussed project timeline\n- Reviewed designs",
        )
        score = compute_engagement(rec)
        assert score is not None
        assert score.output == 20  # base for having summary

    def test_quality_score_from_metadata(self, tmp_path):
        rec = _make_rec(
            tmp_path,
            {"duration_seconds": 1800, "quality_scores": {"overall_score": 85}},
        )
        score = compute_engagement(rec)
        assert score is not None
        assert score.quality == 85

    def test_balanced_speakers_high_participation(self, tmp_path):
        rec = _make_rec(
            tmp_path,
            {"duration_seconds": 1800},
            segments=[
                {"speaker": "A", "start": 0, "end": 300},
                {"speaker": "B", "start": 300, "end": 600},
                {"speaker": "C", "start": 600, "end": 900},
            ],
        )
        score = compute_engagement(rec)
        assert score is not None
        assert score.participation > 80

    def test_preloaded_meta(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        meta = {"duration_seconds": 1800, "speaker_count": 3}
        score = compute_engagement(rec, meta=meta)
        assert score is not None

    def test_breakdown_keys(self, tmp_path):
        rec = _make_rec(tmp_path, {"duration_seconds": 1800})
        score = compute_engagement(rec)
        assert score is not None
        assert "participation" in score.breakdown
        assert "output" in score.breakdown
        assert "tone" in score.breakdown
        assert "quality" in score.breakdown

    def test_label_ranges(self, tmp_path):
        # Just verify the labels make sense
        labels = {
            95: "Highly Engaged",
            70: "Engaged",
            50: "Moderate",
            30: "Low Engagement",
            10: "Disengaged",
        }
        for expected_overall, expected_label in labels.items():
            score = EngagementScore(
                overall=expected_overall,
                participation=50, output=50, tone=50, quality=50,
                label=expected_label, breakdown={},
            )
            assert score.label == expected_label


class TestFormatEngagement:
    def test_none(self):
        text = format_engagement(None)
        assert "Unable" in text

    def test_basic(self):
        score = EngagementScore(
            overall=75, participation=80, output=70, tone=65, quality=85,
            label="Engaged",
            breakdown={
                "participation": "Balance: 80/100, 4 speakers",
                "output": "3 actions, 2 decisions",
                "tone": "Positive (+0.35)",
                "quality": "85/100",
            },
        )
        text = format_engagement(score)
        assert "ENGAGEMENT SCORE" in text
        assert "75/100" in text
        assert "Engaged" in text
        assert "80/100" in text
        assert "3 actions" in text
