"""Tests for meeting productivity scoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.productivity import (
    ProductivityScore,
    score_productivity,
    productivity_label,
)


def _make_rec(base: Path, meta: dict = None,
              transcript_txt: str = "", transcript_json: dict = None,
              action_items: list = None) -> Path:
    rec = base / "2026-03-01_09-00-00_Test"
    rec.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        with open(rec / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)
    if transcript_txt:
        (rec / "transcript.txt").write_text(transcript_txt, encoding="utf-8")
    if transcript_json is not None:
        with open(rec / "transcript.json", "w", encoding="utf-8") as f:
            json.dump(transcript_json, f)
    if action_items is not None:
        with open(rec / "action_items.json", "w", encoding="utf-8") as f:
            json.dump(action_items, f)
    return rec


class TestScoreProductivity:
    def test_basic(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_txt="word " * 500)
        score = score_productivity(rec)
        assert score is not None
        assert 0 <= score.overall <= 100

    def test_with_action_items(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        action_items=[
                            {"description": f"Task {i}", "assignee": "", "category": ""}
                            for i in range(5)
                        ])
        score = score_productivity(rec)
        assert score.action_density > 0
        assert score.breakdown["action_items"] == 5

    def test_high_action_density(self, tmp_path):
        """Many action items in short meeting = high action density."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        action_items=[
                            {"description": f"Task {i}", "assignee": "", "category": ""}
                            for i in range(8)
                        ])
        score = score_productivity(rec)
        assert score.action_density >= 80

    def test_participation_balance(self, tmp_path):
        """Balanced speakers should score high."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_json={"segments": [
                            {"speaker": "Alice", "start": 0, "end": 300, "text": ""},
                            {"speaker": "Bob", "start": 300, "end": 600, "text": ""},
                            {"speaker": "Alice", "start": 600, "end": 900, "text": ""},
                            {"speaker": "Bob", "start": 900, "end": 1200, "text": ""},
                        ]})
        score = score_productivity(rec)
        assert score.participation >= 80

    def test_monologue_low_participation(self, tmp_path):
        """Single speaker should score low."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_json={"segments": [
                            {"speaker": "Alice", "start": 0, "end": 1800, "text": ""},
                        ]})
        score = score_productivity(rec)
        assert score.participation <= 30

    def test_imbalanced_participation(self, tmp_path):
        """One dominant speaker should score lower."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_json={"segments": [
                            {"speaker": "Alice", "start": 0, "end": 1500, "text": ""},
                            {"speaker": "Bob", "start": 1500, "end": 1800, "text": ""},
                        ]})
        score = score_productivity(rec)
        assert score.participation < 80

    def test_discussion_density(self, tmp_path):
        """Good word count should give decent density."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_txt="word " * 2000)
        score = score_productivity(rec)
        assert score.discussion_density > 50

    def test_sparse_transcript(self, tmp_path):
        """Very few words = low density."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_txt="hello world")
        score = score_productivity(rec)
        assert score.discussion_density < 30

    def test_too_short_skipped(self, tmp_path):
        """Recordings under 60 seconds should return None."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 30})
        assert score_productivity(rec) is None

    def test_no_metadata(self, tmp_path):
        """No metadata file should return None (duration 0)."""
        rec = tmp_path / "2026-03-01_09-00-00_Test"
        rec.mkdir()
        assert score_productivity(rec) is None

    def test_provided_meta(self, tmp_path):
        """Can pass meta directly instead of loading from disk."""
        rec = tmp_path / "2026-03-01_09-00-00_Test"
        rec.mkdir()
        score = score_productivity(rec, meta={"duration_seconds": 1800})
        assert score is not None

    def test_time_efficiency(self, tmp_path):
        """High speech-to-meeting ratio = high efficiency."""
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_json={"segments": [
                            {"speaker": "Alice", "start": 0, "end": 600, "text": ""},
                            {"speaker": "Bob", "start": 600, "end": 1200, "text": ""},
                        ]})
        score = score_productivity(rec)
        # 1200/1800 = 67% speech ratio
        assert score.time_efficiency > 50

    def test_breakdown_populated(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 1800},
                        transcript_txt="word " * 500,
                        action_items=[{"description": "Test", "assignee": "", "category": ""}])
        score = score_productivity(rec)
        assert "action_items" in score.breakdown
        assert "wpm" in score.breakdown
        assert "speech_ratio" in score.breakdown


class TestProductivityLabel:
    def test_labels(self):
        assert productivity_label(90) == "Highly Productive"
        assert productivity_label(70) == "Productive"
        assert productivity_label(50) == "Average"
        assert productivity_label(30) == "Low Productivity"
        assert productivity_label(10) == "Unproductive"
