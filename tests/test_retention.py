"""Tests for recording retention / auto-cleanup policy."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.recording_store import RecordingStore


@pytest.fixture
def store(tmp_path: Path) -> RecordingStore:
    """Create a RecordingStore with a temp base directory."""
    base = tmp_path / "MeetingRecordings"
    base.mkdir()
    return RecordingStore(base)


def _create_recording(base: Path, name: str, size_kb: int = 10) -> Path:
    """Create a fake recording directory with a dummy file."""
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    dummy = rec / "app_audio.wav"
    dummy.write_bytes(b"\x00" * (size_kb * 1024))
    return rec


class TestRetentionAgeCleanup:
    def test_no_cleanup_when_disabled(self, store: RecordingStore):
        _create_recording(store.base_dir, "2026-03-01_10-00-00_Zoom")
        deleted = store.cleanup(max_age_days=0, max_total_gb=0.0)
        assert deleted == []
        assert (store.base_dir / "2026-03-01_10-00-00_Zoom").exists()

    def test_deletes_old_recordings(self, store: RecordingStore):
        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d_%H-%M-%S")
        new_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        _create_recording(store.base_dir, f"{old_date}_OldMeeting")
        _create_recording(store.base_dir, f"{new_date}_NewMeeting")

        deleted = store.cleanup(max_age_days=90)
        assert len(deleted) == 1
        assert "OldMeeting" in deleted[0].name
        assert not (store.base_dir / f"{old_date}_OldMeeting").exists()
        assert (store.base_dir / f"{new_date}_NewMeeting").exists()

    def test_respects_exclude(self, store: RecordingStore):
        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d_%H-%M-%S")
        old_dir = _create_recording(store.base_dir, f"{old_date}_Protected")

        deleted = store.cleanup(max_age_days=90, exclude=old_dir)
        assert deleted == []
        assert old_dir.exists()

    def test_keeps_recent_recordings(self, store: RecordingStore):
        recent_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d_%H-%M-%S")
        _create_recording(store.base_dir, f"{recent_date}_Recent")

        deleted = store.cleanup(max_age_days=90)
        assert deleted == []

    def test_deletes_multiple_old(self, store: RecordingStore):
        for i in range(5):
            old_date = (datetime.now() - timedelta(days=100 + i)).strftime("%Y-%m-%d_%H-%M-%S")
            _create_recording(store.base_dir, f"{old_date}_Meeting{i}")

        new_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        _create_recording(store.base_dir, f"{new_date}_CurrentMeeting")

        deleted = store.cleanup(max_age_days=90)
        assert len(deleted) == 5
        assert (store.base_dir / f"{new_date}_CurrentMeeting").exists()


class TestRetentionSizeCleanup:
    def test_deletes_oldest_when_over_budget(self, store: RecordingStore):
        # Create 3 recordings at 100KB each = 300KB total
        dates = []
        for i in range(3):
            d = (datetime.now() - timedelta(days=3 - i)).strftime("%Y-%m-%d_%H-%M-%S")
            dates.append(d)
            _create_recording(store.base_dir, f"{d}_Meeting{i}", size_kb=100)

        # Budget: 250KB = 0.000238 GB -> should delete oldest
        deleted = store.cleanup(max_total_gb=250 / (1024 * 1024))
        assert len(deleted) >= 1
        # Oldest should be deleted
        assert not (store.base_dir / f"{dates[0]}_Meeting0").exists()
        # Newest should remain
        assert (store.base_dir / f"{dates[2]}_Meeting2").exists()

    def test_no_deletion_when_under_budget(self, store: RecordingStore):
        d = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        _create_recording(store.base_dir, f"{d}_Small", size_kb=10)

        deleted = store.cleanup(max_total_gb=1.0)
        assert deleted == []

    def test_size_cleanup_respects_exclude(self, store: RecordingStore):
        dates = []
        for i in range(3):
            d = (datetime.now() - timedelta(days=3 - i)).strftime("%Y-%m-%d_%H-%M-%S")
            dates.append(d)
            _create_recording(store.base_dir, f"{d}_Meeting{i}", size_kb=100)

        oldest = store.base_dir / f"{dates[0]}_Meeting0"
        # Budget is tight, but exclude the oldest
        deleted = store.cleanup(max_total_gb=250 / (1024 * 1024), exclude=oldest)
        # Oldest should NOT be deleted since it's excluded
        assert oldest.exists()


class TestRetentionCombined:
    def test_age_and_size_combined(self, store: RecordingStore):
        # Old recording (should be deleted by age)
        old_date = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d_%H-%M-%S")
        _create_recording(store.base_dir, f"{old_date}_AncientMeeting", size_kb=100)

        # Recent but large recordings
        for i in range(3):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d_%H-%M-%S")
            _create_recording(store.base_dir, f"{d}_Recent{i}", size_kb=100)

        deleted = store.cleanup(max_age_days=90, max_total_gb=250 / (1024 * 1024))
        # At minimum the ancient one should be deleted by age
        assert any("AncientMeeting" in d.name for d in deleted)

    def test_empty_base_dir(self, store: RecordingStore):
        deleted = store.cleanup(max_age_days=30, max_total_gb=1.0)
        assert deleted == []


class TestDirTimestampParsing:
    def test_valid_timestamp(self):
        p = Path("2026-03-06_14-30-00_Zoom")
        result = RecordingStore._parse_dir_timestamp(p)
        assert result == datetime(2026, 3, 6, 14, 30, 0)

    def test_invalid_name(self):
        p = Path("not-a-recording")
        assert RecordingStore._parse_dir_timestamp(p) is None

    def test_short_name(self):
        p = Path("short")
        assert RecordingStore._parse_dir_timestamp(p) is None


class TestDirSize:
    def test_calculates_size(self, tmp_path: Path):
        d = tmp_path / "rec"
        d.mkdir()
        (d / "a.wav").write_bytes(b"\x00" * 1000)
        (d / "b.wav").write_bytes(b"\x00" * 2000)
        assert RecordingStore._dir_size(d) == 3000

    def test_empty_dir(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        assert RecordingStore._dir_size(d) == 0


class TestRetentionConfig:
    def test_default_config(self):
        from meeting_recorder.config import RetentionConfig
        rc = RetentionConfig()
        assert rc.enabled is False
        assert rc.max_age_days == 90
        assert rc.max_total_gb == 0.0

    def test_config_from_dict(self):
        from meeting_recorder.config import Config
        data = {"retention": {"enabled": True, "max_age_days": 30, "max_total_gb": 50.0}}
        cfg = Config._from_dict(data)
        assert cfg.retention.enabled is True
        assert cfg.retention.max_age_days == 30
        assert cfg.retention.max_total_gb == 50.0

    def test_config_roundtrip(self):
        import tempfile
        from meeting_recorder.config import Config
        import meeting_recorder.config as cfg_mod

        config = Config()
        config.retention.enabled = True
        config.retention.max_age_days = 60
        config.retention.max_total_gb = 25.0

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            orig_bundled = cfg_mod.BUNDLED_CONFIG
            orig_secrets = cfg_mod.SECRETS_FILE
            orig_config = cfg_mod.CONFIG_FILE
            orig_dir = cfg_mod.CONFIG_DIR
            cfg_mod.BUNDLED_CONFIG = tmp_dir / "config.toml"
            cfg_mod.SECRETS_FILE = tmp_dir / "secrets.toml"
            cfg_mod.CONFIG_FILE = tmp_dir / "legacy.toml"
            cfg_mod.CONFIG_DIR = tmp_dir
            config.save()
            loaded = Config.load()
            assert loaded.retention.enabled is True
            assert loaded.retention.max_age_days == 60
            assert loaded.retention.max_total_gb == 25.0
        finally:
            cfg_mod.BUNDLED_CONFIG = orig_bundled
            cfg_mod.SECRETS_FILE = orig_secrets
            cfg_mod.CONFIG_FILE = orig_config
            cfg_mod.CONFIG_DIR = orig_dir
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
