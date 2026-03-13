"""Tests for the speaker timeline visualization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.ui.timeline_view import (
    SPEAKER_COLORS,
    TimelineWindow,
    load_timeline_data,
)


@pytest.fixture
def rec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "2026-03-12_10-00-00_Test"
    d.mkdir()
    return d


def _write_transcript(rec_dir: Path, segments: list[dict]) -> None:
    with open(rec_dir / "transcript.json", "w") as f:
        json.dump({"segments": segments}, f)


def _write_metadata(rec_dir: Path, meta: dict) -> None:
    with open(rec_dir / "metadata.json", "w") as f:
        json.dump(meta, f)


class TestLoadTimelineData:
    def test_no_transcript(self, rec_dir: Path):
        assert load_timeline_data(rec_dir) is None

    def test_empty_segments(self, rec_dir: Path):
        _write_transcript(rec_dir, [])
        assert load_timeline_data(rec_dir) is None

    def test_basic_timeline(self, rec_dir: Path):
        _write_metadata(rec_dir, {"duration_seconds": 60})
        _write_transcript(rec_dir, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 20, "text": "Hello"},
            {"speaker": "SPEAKER_01", "start": 20, "end": 40, "text": "Hi"},
            {"speaker": "SPEAKER_00", "start": 40, "end": 60, "text": "Bye"},
        ])
        data = load_timeline_data(rec_dir)
        assert data is not None
        assert data["duration"] == 60
        assert len(data["speakers"]) == 2
        assert data["speakers"][0]["id"] == "SPEAKER_00"
        assert data["speakers"][1]["id"] == "SPEAKER_01"

    def test_speaker_order_by_appearance(self, rec_dir: Path):
        _write_metadata(rec_dir, {"duration_seconds": 30})
        _write_transcript(rec_dir, [
            {"speaker": "SPEAKER_02", "start": 0, "end": 10, "text": "A"},
            {"speaker": "SPEAKER_00", "start": 10, "end": 20, "text": "B"},
            {"speaker": "SPEAKER_01", "start": 20, "end": 30, "text": "C"},
        ])
        data = load_timeline_data(rec_dir)
        ids = [s["id"] for s in data["speakers"]]
        assert ids == ["SPEAKER_02", "SPEAKER_00", "SPEAKER_01"]

    def test_speaker_map_applied(self, rec_dir: Path):
        _write_metadata(rec_dir, {
            "duration_seconds": 30,
            "speaker_map": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        })
        _write_transcript(rec_dir, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 15, "text": "A"},
            {"speaker": "SPEAKER_01", "start": 15, "end": 30, "text": "B"},
        ])
        data = load_timeline_data(rec_dir)
        names = [s["name"] for s in data["speakers"]]
        assert names == ["Alice", "Bob"]

    def test_unmapped_speaker_keeps_id(self, rec_dir: Path):
        _write_metadata(rec_dir, {"duration_seconds": 30})
        _write_transcript(rec_dir, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 30, "text": "A"},
        ])
        data = load_timeline_data(rec_dir)
        assert data["speakers"][0]["name"] == "SPEAKER_00"

    def test_duration_from_segments_if_no_metadata(self, rec_dir: Path):
        _write_transcript(rec_dir, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 45, "text": "A"},
        ])
        data = load_timeline_data(rec_dir)
        assert data["duration"] == 45

    def test_colors_assigned(self, rec_dir: Path):
        _write_metadata(rec_dir, {"duration_seconds": 30})
        segs = [{"speaker": f"S{i}", "start": i * 3, "end": i * 3 + 2, "text": "X"} for i in range(5)]
        _write_transcript(rec_dir, segs)
        data = load_timeline_data(rec_dir)
        colors = [s["color"] for s in data["speakers"]]
        assert len(set(colors)) == 5  # all unique for 5 speakers
        for c in colors:
            assert c in SPEAKER_COLORS

    def test_segments_collected(self, rec_dir: Path):
        _write_metadata(rec_dir, {"duration_seconds": 60})
        _write_transcript(rec_dir, [
            {"speaker": "A", "start": 0, "end": 10, "text": "X"},
            {"speaker": "A", "start": 20, "end": 30, "text": "Y"},
            {"speaker": "A", "start": 40, "end": 50, "text": "Z"},
        ])
        data = load_timeline_data(rec_dir)
        assert len(data["speakers"][0]["segments"]) == 3

    def test_invalid_segments_skipped(self, rec_dir: Path):
        _write_metadata(rec_dir, {"duration_seconds": 30})
        _write_transcript(rec_dir, [
            {"speaker": "A", "start": 10, "end": 5, "text": "reversed"},
            {"speaker": "A", "start": 0, "end": 10, "text": "good"},
        ])
        data = load_timeline_data(rec_dir)
        assert len(data["speakers"][0]["segments"]) == 1

    def test_many_speakers_color_wrap(self, rec_dir: Path):
        _write_metadata(rec_dir, {"duration_seconds": 100})
        segs = [{"speaker": f"S{i}", "start": i, "end": i + 1, "text": "X"}
                for i in range(15)]
        _write_transcript(rec_dir, segs)
        data = load_timeline_data(rec_dir)
        assert len(data["speakers"]) == 15
        # Colors should wrap around (only 10 defined)
        assert data["speakers"][10]["color"] == data["speakers"][0]["color"]

    def test_corrupt_transcript_returns_none(self, rec_dir: Path):
        (rec_dir / "transcript.json").write_text("not json")
        assert load_timeline_data(rec_dir) is None


class TestTimelineWindowLifecycle:
    def test_construction(self, rec_dir: Path):
        tw = TimelineWindow(rec_dir)
        assert tw._window is None

    def test_close_resets(self, rec_dir: Path):
        tw = TimelineWindow(rec_dir)
        tw.close()
        assert tw._window is None

    def test_show_no_data_returns(self, rec_dir: Path):
        """Show with no transcript data does nothing."""
        tw = TimelineWindow(rec_dir)
        tw.show()  # No parent, no data — should return without creating window
        assert tw._window is None
