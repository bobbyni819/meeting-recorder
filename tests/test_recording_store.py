"""Tests for RecordingStore directory management."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from meeting_recorder.storage.recording_store import RecordingStore


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------

class TestRecordingStoreCreate:
    """Test create_recording_dir()."""

    def test_creates_directory(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        recording_dir = store.create_recording_dir("Zoom")
        assert recording_dir.exists()
        assert recording_dir.is_dir()

    def test_directory_name_format(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        recording_dir = store.create_recording_dir("Zoom")
        name = recording_dir.name
        # Expected format: YYYY-MM-DD_HH-MM-SS_Zoom
        pattern = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_Zoom$"
        assert re.match(pattern, name), f"Directory name '{name}' does not match expected pattern"

    def test_sanitizes_app_name(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        recording_dir = store.create_recording_dir("Microsoft Teams (work)")
        name = recording_dir.name
        # Special characters should be replaced with underscores
        assert "(" not in name
        assert ")" not in name
        assert " " not in name

    def test_creates_parent_directories(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c"
        store = RecordingStore(deep_path)
        recording_dir = store.create_recording_dir("Zoom")
        assert recording_dir.exists()
        assert deep_path.exists()

    def test_multiple_recordings(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        dir1 = store.create_recording_dir("Zoom")
        time.sleep(1.1)  # Ensure different timestamps
        dir2 = store.create_recording_dir("Zoom")
        assert dir1 != dir2
        assert dir1.exists()
        assert dir2.exists()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class TestRecordingStoreListing:
    """Test list_recordings() and get_latest_recording()."""

    def test_list_recordings_empty(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        assert store.list_recordings() == []

    def test_list_recordings_nonexistent_dir(self, tmp_path: Path):
        store = RecordingStore(tmp_path / "nonexistent")
        assert store.list_recordings() == []

    def test_list_recordings_returns_dirs(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        store.create_recording_dir("Zoom")
        store.create_recording_dir("Teams")
        recordings = store.list_recordings()
        assert len(recordings) >= 2
        for r in recordings:
            assert r.is_dir()

    def test_list_recordings_newest_first(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        dir1 = store.create_recording_dir("First")
        time.sleep(1.1)
        dir2 = store.create_recording_dir("Second")
        recordings = store.list_recordings()
        # Newest first
        assert recordings[0].name >= recordings[-1].name

    def test_get_latest_recording_empty(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        assert store.get_latest_recording() is None

    def test_get_latest_recording(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        store.create_recording_dir("Old")
        time.sleep(1.1)
        latest = store.create_recording_dir("New")
        result = store.get_latest_recording()
        assert result == latest


# ---------------------------------------------------------------------------
# ensure_base_dir
# ---------------------------------------------------------------------------

class TestEnsureBaseDir:
    """Test ensure_base_dir()."""

    def test_creates_base_dir(self, tmp_path: Path):
        base = tmp_path / "new_base"
        store = RecordingStore(base)
        assert not base.exists()
        store.ensure_base_dir()
        assert base.exists()

    def test_idempotent(self, base_recordings_dir: Path):
        store = RecordingStore(base_recordings_dir)
        store.ensure_base_dir()
        store.ensure_base_dir()  # should not raise
        assert base_recordings_dir.exists()
