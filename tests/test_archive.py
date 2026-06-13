"""Tests for recording archive/compress."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.archive import (
    archive_recording,
    unarchive_recording,
    is_archived,
    archive_old_recordings,
    get_archive_stats,
    ARCHIVE_FILENAME,
)


def _make_rec(base: Path, name: str, wav_size: int = 1000) -> Path:
    """Create a recording directory with metadata and a WAV file."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {"duration_seconds": 100, "status": "completed"}
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "transcript.txt").write_text("Alice: Hello", encoding="utf-8")
    (d / "app_audio.wav").write_bytes(b"\x00" * wav_size)
    return d


class TestArchiveRecording:
    def test_basic_archive(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        saved = archive_recording(rec)
        assert saved > 0
        assert is_archived(rec)
        assert not (rec / "app_audio.wav").exists()
        assert (rec / "metadata.json").exists()
        assert (rec / "transcript.txt").exists()

    def test_archive_creates_zip(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        archive_recording(rec)
        archive = rec / ARCHIVE_FILENAME
        assert archive.exists()
        with zipfile.ZipFile(archive) as zf:
            assert "app_audio.wav" in zf.namelist()

    def test_archive_no_media_files(self, tmp_path):
        d = tmp_path / "2026-03-01_09-00-00_Test"
        d.mkdir()
        (d / "metadata.json").write_text("{}")
        saved = archive_recording(d)
        assert saved == 0
        assert not is_archived(d)

    def test_archive_keeps_delete_false(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        archive_recording(rec, delete_originals=False)
        assert is_archived(rec)
        assert (rec / "app_audio.wav").exists()  # originals kept

    def test_archive_already_archived(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        archive_recording(rec)
        saved = archive_recording(rec)  # second time
        assert saved == 0

    def test_archive_nonexistent_dir(self, tmp_path):
        saved = archive_recording(tmp_path / "nope")
        assert saved == 0

    def test_verification_failure_keeps_originals_and_removes_archive(
        self, tmp_path, monkeypatch,
    ):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "app_audio.wav")

        saved = archive_recording(rec)

        assert saved == 0
        assert (rec / "app_audio.wav").exists()
        assert not (rec / ARCHIVE_FILENAME).exists()

    def test_archive_delete_then_unarchive_round_trips(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        original = (rec / "app_audio.wav").read_bytes()

        saved = archive_recording(rec)

        assert saved > 0
        assert not (rec / "app_audio.wav").exists()
        assert unarchive_recording(rec) is True
        assert (rec / "app_audio.wav").read_bytes() == original


class TestUnarchiveRecording:
    def test_basic_unarchive(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        archive_recording(rec)
        assert not (rec / "app_audio.wav").exists()

        restored = unarchive_recording(rec)
        assert restored is True
        assert (rec / "app_audio.wav").exists()
        assert not is_archived(rec)

    def test_unarchive_no_archive(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        assert unarchive_recording(rec) is False


class TestIsArchived:
    def test_not_archived(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        assert is_archived(rec) is False

    def test_archived(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-01_09-00-00_Test")
        archive_recording(rec)
        assert is_archived(rec) is True


class TestArchiveOldRecordings:
    def test_archives_old(self, tmp_path):
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{old_date}_09-00-00_Old")

        count, saved = archive_old_recordings(tmp_path, older_than_days=30)
        assert count == 1
        assert saved > 0

    def test_keeps_recent(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{today}_09-00-00_New")

        count, saved = archive_old_recordings(tmp_path, older_than_days=30)
        assert count == 0
        assert saved == 0

    def test_excludes_directory(self, tmp_path):
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        rec = _make_rec(tmp_path, f"{old_date}_09-00-00_Old")

        count, saved = archive_old_recordings(
            tmp_path, older_than_days=30, exclude=rec,
        )
        assert count == 0

    def test_nonexistent_dir(self, tmp_path):
        count, saved = archive_old_recordings(tmp_path / "nope")
        assert count == 0

    def test_mixed_ages(self, tmp_path):
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, f"{old_date}_09-00-00_Old")
        _make_rec(tmp_path, f"{today}_09-00-00_New")

        count, saved = archive_old_recordings(tmp_path, older_than_days=30)
        assert count == 1


class TestGetArchiveStats:
    def test_empty(self, tmp_path):
        stats = get_archive_stats(tmp_path)
        assert stats["total"] == 0

    def test_nonexistent(self, tmp_path):
        stats = get_archive_stats(tmp_path / "nope")
        assert stats["total"] == 0

    def test_mixed_state(self, tmp_path):
        rec1 = _make_rec(tmp_path, "2026-03-01_09-00-00_A", wav_size=2000)
        rec2 = _make_rec(tmp_path, "2026-03-02_09-00-00_B", wav_size=3000)
        archive_recording(rec1)

        stats = get_archive_stats(tmp_path)
        assert stats["total"] == 2
        assert stats["archived"] == 1
        assert stats["unarchived"] == 1
        assert stats["unarchived_size"] == 3000
        assert stats["archive_size"] > 0
