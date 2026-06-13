"""Tests for participation equity analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.participation import (
    analyze_participation,
    format_participation,
    _gini_coefficient,
    ParticipationScore,
)


def _make_rec(
    base: Path,
    name: str,
    segments: list[dict] | None = None,
    speaker_map: dict | None = None,
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"speaker_map": speaker_map or {}}
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if segments is not None:
        with open(rec / "transcript.json", "w", encoding="utf-8") as f:
            json.dump({"segments": segments}, f)
    return rec


class TestGiniCoefficient:
    def test_perfect_equality(self):
        # All equal
        assert _gini_coefficient([25, 25, 25, 25]) == pytest.approx(0.0, abs=0.01)

    def test_perfect_inequality(self):
        # One person has everything
        gini = _gini_coefficient([0, 0, 0, 100])
        assert gini > 0.7

    def test_moderate(self):
        gini = _gini_coefficient([10, 20, 30, 40])
        assert 0.1 < gini < 0.5

    def test_single_value(self):
        assert _gini_coefficient([100]) == 0.0

    def test_empty(self):
        assert _gini_coefficient([]) == 0.0

    def test_two_equal(self):
        assert _gini_coefficient([50, 50]) == pytest.approx(0.0, abs=0.01)

    def test_two_unequal(self):
        gini = _gini_coefficient([10, 90])
        assert gini > 0.3


class TestAnalyzeParticipation:
    def test_no_transcript(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test")
        assert analyze_participation(rec) is None

    def test_single_speaker(self, tmp_path):
        segments = [
            {"speaker": "A", "text": "Hello", "start": 0, "end": 60},
        ]
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        segments=segments)
        assert analyze_participation(rec) is None

    def test_null_segments_do_not_raise(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test")
        (rec / "transcript.json").write_text(
            json.dumps({"segments": None}), encoding="utf-8"
        )
        assert analyze_participation(rec) is None

    def test_balanced(self, tmp_path):
        segments = [
            {"speaker": "A", "text": "Hello", "start": 0, "end": 30},
            {"speaker": "B", "text": "Hi", "start": 30, "end": 60},
            {"speaker": "C", "text": "Hey", "start": 60, "end": 90},
        ]
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        segments=segments)
        ps = analyze_participation(rec)
        assert ps is not None
        assert ps.label == "balanced"
        assert ps.equity_score > 80
        assert ps.speaker_count == 3

    def test_dominated(self, tmp_path):
        segments = [
            {"speaker": "A", "text": "blah", "start": 0, "end": 100},
            {"speaker": "B", "text": "ok", "start": 100, "end": 105},
            {"speaker": "C", "text": "yes", "start": 105, "end": 108},
        ]
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        segments=segments)
        ps = analyze_participation(rec)
        assert ps is not None
        assert ps.dominant_speaker == "A"
        assert ps.dominant_pct > 80
        assert ps.label in ("dominated", "monologue")
        assert ps.equity_score < 50

    def test_speaker_map_applied(self, tmp_path):
        segments = [
            {"speaker": "SPEAKER_00", "text": "blah", "start": 0, "end": 50},
            {"speaker": "SPEAKER_01", "text": "ok", "start": 50, "end": 100},
        ]
        rec = _make_rec(
            tmp_path, "2026-03-10_09-00-00_Test",
            segments=segments,
            speaker_map={"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        )
        ps = analyze_participation(rec)
        assert ps is not None
        names = [s for s, _ in ps.speaker_shares]
        assert "Alice" in names
        assert "Bob" in names

    def test_shares_sorted_desc(self, tmp_path):
        segments = [
            {"speaker": "A", "text": "x", "start": 0, "end": 10},
            {"speaker": "B", "text": "x", "start": 10, "end": 50},
            {"speaker": "C", "text": "x", "start": 50, "end": 60},
        ]
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        segments=segments)
        ps = analyze_participation(rec)
        assert ps is not None
        pcts = [p for _, p in ps.speaker_shares]
        assert pcts == sorted(pcts, reverse=True)

    def test_quietest_speaker(self, tmp_path):
        segments = [
            {"speaker": "A", "text": "x", "start": 0, "end": 80},
            {"speaker": "B", "text": "x", "start": 80, "end": 85},
        ]
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        segments=segments)
        ps = analyze_participation(rec)
        assert ps is not None
        assert ps.quietest_speaker == "B"

    def test_with_meta_param(self, tmp_path):
        segments = [
            {"speaker": "X", "text": "x", "start": 0, "end": 30},
            {"speaker": "Y", "text": "x", "start": 30, "end": 60},
        ]
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        segments=segments)
        meta = {"speaker_map": {"X": "Alice", "Y": "Bob"}}
        ps = analyze_participation(rec, meta=meta)
        assert ps is not None
        names = [s for s, _ in ps.speaker_shares]
        assert "Alice" in names


class TestFormatParticipation:
    def test_balanced_format(self):
        ps = ParticipationScore(
            equity_score=90,
            gini_coefficient=0.08,
            speaker_count=3,
            dominant_speaker="Alice",
            dominant_pct=38.0,
            quietest_speaker="Charlie",
            quietest_pct=28.0,
            speaker_shares=[("Alice", 38.0), ("Bob", 34.0), ("Charlie", 28.0)],
            label="balanced",
        )
        text = format_participation(ps)
        assert "PARTICIPATION EQUITY" in text
        assert "90/100" in text
        assert "Balanced" in text
        assert "Alice" in text
        assert "38.0%" in text

    def test_dominated_format(self):
        ps = ParticipationScore(
            equity_score=35,
            gini_coefficient=0.52,
            speaker_count=3,
            dominant_speaker="Alice",
            dominant_pct=85.0,
            quietest_speaker="Charlie",
            quietest_pct=3.0,
            speaker_shares=[("Alice", 85.0), ("Bob", 12.0), ("Charlie", 3.0)],
            label="dominated",
        )
        text = format_participation(ps)
        assert "dominated" in text.lower()
        assert "Alice dominated" in text

    def test_monologue_format(self):
        ps = ParticipationScore(
            equity_score=15,
            gini_coefficient=0.7,
            speaker_count=2,
            dominant_speaker="Bob",
            dominant_pct=95.0,
            quietest_speaker="Alice",
            quietest_pct=5.0,
            speaker_shares=[("Bob", 95.0), ("Alice", 5.0)],
            label="monologue",
        )
        text = format_participation(ps)
        assert "Monologue" in text
        assert "Bob dominated" in text
