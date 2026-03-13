"""Tests for meeting energy curve analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.energy_curve import (
    analyze_energy,
    format_energy_curve,
    EnergyCurve,
    EnergyWindow,
)


def _make_transcript(tmp_path: Path, segments: list[dict]) -> Path:
    rec = tmp_path / "2026-03-10_09-00-00_Team_Meeting"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "transcript.json").write_text(
        json.dumps({"segments": segments}), encoding="utf-8"
    )
    return rec


def _seg(start: float, end: float, speaker: str, text: str) -> dict:
    return {"start": start, "end": end, "speaker": speaker, "text": text}


class TestAnalyzeEnergy:
    def test_no_dir(self, tmp_path):
        assert analyze_energy(tmp_path / "nope") is None

    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert analyze_energy(rec) is None

    def test_too_few_segments(self, tmp_path):
        rec = _make_transcript(tmp_path, [
            _seg(0, 5, "A", "Hello"),
            _seg(5, 10, "B", "Hi"),
        ])
        assert analyze_energy(rec) is None

    def test_too_short_duration(self, tmp_path):
        segs = [_seg(i * 5, (i + 1) * 5, "A", "word " * 10) for i in range(10)]
        rec = _make_transcript(tmp_path, segs)
        assert analyze_energy(rec) is None  # 50 seconds < 60

    def test_basic_curve(self, tmp_path):
        # 10 minutes of segments
        segs = []
        for i in range(60):
            start = i * 10
            end = start + 8
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker, "some words here today " * 3))
        rec = _make_transcript(tmp_path, segs)
        curve = analyze_energy(rec)
        assert curve is not None
        assert len(curve.windows) >= 2
        assert curve.total_duration_min > 0
        assert curve.avg_wpm > 0
        assert curve.arc_type in (
            "front-loaded", "back-loaded", "middle-peak", "flat", "declining"
        )

    def test_peak_and_valley(self, tmp_path):
        # First 5 minutes: very active (many words, many turns)
        segs = []
        for i in range(30):
            start = i * 10
            end = start + 8
            speaker = ["Alice", "Bob", "Carol"][i % 3]
            segs.append(_seg(start, end, speaker, "lots of words here today now " * 5))

        # Next 5 minutes: quiet (few words, same speaker)
        for i in range(6):
            start = 300 + i * 50
            end = start + 40
            segs.append(_seg(start, end, "Alice", "quiet"))

        rec = _make_transcript(tmp_path, segs)
        curve = analyze_energy(rec)
        assert curve is not None
        assert curve.peak_window != curve.valley_window

    def test_front_loaded(self, tmp_path):
        segs = []
        # Heavy activity in first 5 minutes
        for i in range(40):
            start = i * 7
            end = start + 5
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker, "active discussion words " * 4))
        # Light activity in minutes 5-15
        for i in range(10):
            start = 300 + i * 60
            end = start + 30
            segs.append(_seg(start, end, "Alice", "quiet"))

        rec = _make_transcript(tmp_path, segs)
        curve = analyze_energy(rec, window_minutes=5.0)
        assert curve is not None
        # First window should have more energy than last
        assert curve.windows[0].words_per_min > curve.windows[-1].words_per_min

    def test_custom_window_size(self, tmp_path):
        segs = []
        for i in range(150):
            start = i * 10
            end = start + 8
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker, "words " * 5))
        rec = _make_transcript(tmp_path, segs)

        curve_small = analyze_energy(rec, window_minutes=2.0)
        curve_large = analyze_energy(rec, window_minutes=10.0)
        assert curve_small is not None
        assert curve_large is not None
        assert len(curve_small.windows) > len(curve_large.windows)

    def test_single_speaker(self, tmp_path):
        segs = []
        for i in range(80):
            start = i * 10
            end = start + 8
            segs.append(_seg(start, end, "Alice", "monologue words here " * 3))
        rec = _make_transcript(tmp_path, segs)
        curve = analyze_energy(rec)
        assert curve is not None
        # All windows should have speaker_count == 1
        for w in curve.windows:
            assert w.speaker_count <= 1

    def test_empty_transcript_json(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.json").write_text("{}", encoding="utf-8")
        assert analyze_energy(rec) is None

    def test_invalid_json(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "transcript.json").write_text("not json", encoding="utf-8")
        assert analyze_energy(rec) is None

    def test_trailing_empty_windows_removed(self, tmp_path):
        # All segments in first 5 minutes, recording appears 15 min long
        segs = []
        for i in range(30):
            start = i * 10
            end = start + 8
            speaker = "Alice" if i % 2 == 0 else "Bob"
            segs.append(_seg(start, end, speaker, "hello world " * 3))
        # One very late segment to extend duration
        segs.append(_seg(900, 901, "Alice", "end"))
        rec = _make_transcript(tmp_path, segs)
        curve = analyze_energy(rec)
        assert curve is not None
        # Last window should have some words
        assert curve.windows[-1].word_count > 0


class TestEnergyWindow:
    def test_fields(self):
        w = EnergyWindow(
            start_min=0, end_min=5, turn_count=10,
            word_count=500, speaker_count=3, words_per_min=100.0,
        )
        assert w.start_min == 0
        assert w.words_per_min == 100.0


class TestFormatEnergyCurve:
    def test_none(self):
        text = format_energy_curve(None)
        assert "Not enough data" in text

    def test_basic_format(self):
        windows = [
            EnergyWindow(0, 5, 8, 500, 3, 100.0),
            EnergyWindow(5, 10, 5, 300, 2, 60.0),
            EnergyWindow(10, 15, 3, 200, 2, 40.0),
        ]
        curve = EnergyCurve(
            windows=windows,
            peak_window=0,
            valley_window=2,
            arc_type="declining",
            total_duration_min=15.0,
            avg_wpm=66.7,
        )
        text = format_energy_curve(curve)
        assert "MEETING ENERGY CURVE" in text
        assert "15 min" in text
        assert "Declining" in text
        assert "peak" in text
        assert "valley" in text

    def test_flat_arc_description(self):
        windows = [
            EnergyWindow(0, 5, 5, 400, 3, 80.0),
            EnergyWindow(5, 10, 5, 400, 3, 80.0),
        ]
        curve = EnergyCurve(
            windows=windows,
            peak_window=0,
            valley_window=1,
            arc_type="flat",
            total_duration_min=10.0,
            avg_wpm=80.0,
        )
        text = format_energy_curve(curve)
        assert "Consistent engagement" in text
