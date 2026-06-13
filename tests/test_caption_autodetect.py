"""Tests for non-destructive vendor caption auto-detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from meeting_recorder.storage.metadata import RecordingMetadata
from meeting_recorder.transcription import vtt_import


def _recording(tmp_path: Path, name: str, app_name: str | None = None) -> Path:
    recording_dir = tmp_path / name
    recording_dir.mkdir()
    if app_name is not None:
        RecordingMetadata(app_name=app_name).save(recording_dir)
    return recording_dir


def test_detect_available_captions_routes_by_metadata_app_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zoom_path = tmp_path / "zoom.vtt"
    teams_path = tmp_path / "teams.docx"
    calls: list[str] = []

    def find_zoom(recording_dir: Path) -> Path:
        calls.append(f"zoom:{Path(recording_dir).name}")
        return zoom_path

    def find_teams(recording_dir: Path) -> Path:
        calls.append(f"teams:{Path(recording_dir).name}")
        return teams_path

    monkeypatch.setattr(vtt_import, "find_zoom_caption_for_recording", find_zoom)
    monkeypatch.setattr(vtt_import, "find_teams_transcript_for_recording", find_teams)

    zoom_recording = _recording(tmp_path, "recording_a", "Zoom")
    teams_recording = _recording(tmp_path, "recording_b", "Microsoft Teams")
    unknown_recording = _recording(tmp_path, "recording_c", "Browser")

    assert vtt_import.detect_available_captions(zoom_recording) == zoom_path
    assert vtt_import.detect_available_captions(teams_recording) == teams_path
    assert vtt_import.detect_available_captions(unknown_recording) is None
    assert calls == ["zoom:recording_a", "teams:recording_b"]


def test_detect_available_captions_falls_back_to_recording_dir_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zoom_path = tmp_path / "zoom.vtt"
    teams_path = tmp_path / "teams.vtt"

    monkeypatch.setattr(
        vtt_import,
        "find_zoom_caption_for_recording",
        lambda recording_dir: zoom_path,
    )
    monkeypatch.setattr(
        vtt_import,
        "find_teams_transcript_for_recording",
        lambda recording_dir: teams_path,
    )

    teams_recording = _recording(
        tmp_path,
        "2026-06-13_09-00-00_Project_Update_Microsoft_Teams",
        "",
    )
    zoom_recording = _recording(
        tmp_path,
        "2026-06-13_10-00-00_Project_Update_Zoom_Meeting",
        "",
    )

    assert vtt_import.detect_available_captions(teams_recording) == teams_path
    assert vtt_import.detect_available_captions(zoom_recording) == zoom_path


def test_detect_available_captions_never_raises_when_metadata_missing(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recording_without_metadata"
    recording_dir.mkdir()

    assert vtt_import.detect_available_captions(recording_dir) is None


def test_recording_metadata_round_trips_caption_available(
    tmp_path: Path,
) -> None:
    recording_dir = tmp_path / "recording"
    recording_dir.mkdir()
    caption_path = str((tmp_path / "Downloads" / "teams.vtt").resolve())
    metadata = RecordingMetadata(app_name="Teams", caption_available=caption_path)

    metadata.save(recording_dir)
    loaded = RecordingMetadata.load(recording_dir)

    assert loaded.caption_available == caption_path
