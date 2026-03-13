"""Tests for the speaker map editor dialog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.ui.speaker_editor import SpeakerEditorDialog


@pytest.fixture
def rec_dir(tmp_path: Path) -> Path:
    """Create a recording directory with transcript and metadata."""
    d = tmp_path / "2026-03-12_10-00-00_TestMeeting"
    d.mkdir()
    return d


def _write_metadata(rec_dir: Path, meta: dict) -> None:
    with open(rec_dir / "metadata.json", "w") as f:
        json.dump(meta, f)


def _write_transcript_json(rec_dir: Path, segments: list[dict]) -> None:
    with open(rec_dir / "transcript.json", "w") as f:
        json.dump({"segments": segments}, f)


def _write_transcript_txt(rec_dir: Path, text: str) -> None:
    (rec_dir / "transcript.txt").write_text(text, encoding="utf-8")


class TestSpeakerEditorLoadData:
    def test_empty_recording(self, rec_dir: Path):
        dialog = SpeakerEditorDialog(rec_dir)
        meta, speakers = dialog._load_data()
        assert meta == {}
        assert speakers == []

    def test_speakers_from_transcript(self, rec_dir: Path):
        _write_metadata(rec_dir, {"status": "completed"})
        _write_transcript_json(rec_dir, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 5, "text": "Hello"},
            {"speaker": "SPEAKER_01", "start": 5, "end": 10, "text": "Hi"},
            {"speaker": "SPEAKER_00", "start": 10, "end": 15, "text": "Bye"},
        ])
        dialog = SpeakerEditorDialog(rec_dir)
        meta, speakers = dialog._load_data()
        assert speakers == ["SPEAKER_00", "SPEAKER_01"]

    def test_speakers_from_map_only(self, rec_dir: Path):
        _write_metadata(rec_dir, {
            "speaker_map": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        })
        dialog = SpeakerEditorDialog(rec_dir)
        meta, speakers = dialog._load_data()
        assert "SPEAKER_00" in speakers
        assert "SPEAKER_01" in speakers
        assert meta["speaker_map"]["SPEAKER_00"] == "Alice"

    def test_speakers_merged(self, rec_dir: Path):
        _write_metadata(rec_dir, {
            "speaker_map": {"SPEAKER_00": "Alice", "SPEAKER_02": "Charlie"},
        })
        _write_transcript_json(rec_dir, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 5, "text": "Hello"},
            {"speaker": "SPEAKER_01", "start": 5, "end": 10, "text": "Hi"},
        ])
        dialog = SpeakerEditorDialog(rec_dir)
        meta, speakers = dialog._load_data()
        # SPEAKER_00 and SPEAKER_01 from transcript, SPEAKER_02 from map
        assert len(speakers) == 3
        assert "SPEAKER_00" in speakers
        assert "SPEAKER_01" in speakers
        assert "SPEAKER_02" in speakers

    def test_preserves_order(self, rec_dir: Path):
        _write_metadata(rec_dir, {})
        _write_transcript_json(rec_dir, [
            {"speaker": "SPEAKER_02", "start": 0, "end": 5, "text": "A"},
            {"speaker": "SPEAKER_00", "start": 5, "end": 10, "text": "B"},
            {"speaker": "SPEAKER_01", "start": 10, "end": 15, "text": "C"},
        ])
        dialog = SpeakerEditorDialog(rec_dir)
        _, speakers = dialog._load_data()
        assert speakers == ["SPEAKER_02", "SPEAKER_00", "SPEAKER_01"]


class TestSpeakerEditorSave:
    def test_save_speaker_map(self, rec_dir: Path):
        _write_metadata(rec_dir, {"status": "completed"})
        _write_transcript_json(rec_dir, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 5, "text": "Hello"},
        ])

        dialog = SpeakerEditorDialog(rec_dir)
        meta, speakers = dialog._load_data()

        # Simulate editing
        new_map = {"SPEAKER_00": "Alice"}
        meta["speaker_map"] = new_map
        meta_path = rec_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        # Verify
        with open(meta_path) as f:
            saved = json.load(f)
        assert saved["speaker_map"]["SPEAKER_00"] == "Alice"

    def test_update_transcript_txt(self, rec_dir: Path):
        _write_transcript_txt(rec_dir, (
            "[00:00:00 - 00:00:05] [SPEAKER_00] Hello world\n"
            "[00:00:05 - 00:00:10] [SPEAKER_01] Hi there\n"
            "[00:00:10 - 00:00:15] [SPEAKER_00] Goodbye\n"
        ))

        dialog = SpeakerEditorDialog(rec_dir)
        dialog._update_transcript_txt({
            "SPEAKER_00": "Alice",
            "SPEAKER_01": "Bob",
        })

        result = (rec_dir / "transcript.txt").read_text(encoding="utf-8")
        assert "[Alice]" in result
        assert "[Bob]" in result
        assert "[SPEAKER_00]" not in result
        assert "[SPEAKER_01]" not in result

    def test_update_transcript_colon_format(self, rec_dir: Path):
        _write_transcript_txt(rec_dir, (
            "SPEAKER_00: Hello world\n"
            "SPEAKER_01: Hi there\n"
        ))

        dialog = SpeakerEditorDialog(rec_dir)
        dialog._update_transcript_txt({"SPEAKER_00": "Alice"})

        result = (rec_dir / "transcript.txt").read_text(encoding="utf-8")
        assert "Alice:" in result
        assert "SPEAKER_00:" not in result
        # SPEAKER_01 should remain (not in map)
        assert "SPEAKER_01:" in result

    def test_no_transcript_txt_no_error(self, rec_dir: Path):
        dialog = SpeakerEditorDialog(rec_dir)
        # Should not raise
        dialog._update_transcript_txt({"SPEAKER_00": "Alice"})

    def test_empty_map_no_update(self, rec_dir: Path):
        original = "SPEAKER_00: Hello"
        _write_transcript_txt(rec_dir, original)

        dialog = SpeakerEditorDialog(rec_dir)
        dialog._update_transcript_txt({})

        result = (rec_dir / "transcript.txt").read_text(encoding="utf-8")
        assert result == original


class TestSpeakerEditorLifecycle:
    def test_construction(self, rec_dir: Path):
        dialog = SpeakerEditorDialog(rec_dir)
        assert dialog._window is None
        assert dialog._rec_path == rec_dir

    def test_close_resets(self, rec_dir: Path):
        dialog = SpeakerEditorDialog(rec_dir)
        dialog.close()
        assert dialog._window is None

    def test_on_saved_callback(self, rec_dir: Path):
        called = []
        dialog = SpeakerEditorDialog(rec_dir, on_saved=lambda: called.append(True))
        assert dialog._on_saved is not None
