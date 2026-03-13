"""Tests for the voice profiles management window."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from meeting_recorder.transcription.voice_profiles import VoiceProfileDB
from meeting_recorder.ui.voice_profiles_window import VoiceProfilesWindow


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_profiles.db"


@pytest.fixture
def db(db_path: Path) -> VoiceProfileDB:
    return VoiceProfileDB(db_path)


def _random_embedding(dim: int = 256) -> np.ndarray:
    """Create a random unit-norm embedding."""
    e = np.random.randn(dim).astype(np.float32)
    return e / np.linalg.norm(e)


class TestVoiceProfilesWindowConstruction:
    def test_create_window(self, db_path: Path):
        w = VoiceProfilesWindow(db_path)
        assert w._window is None

    def test_close_resets(self, db_path: Path):
        w = VoiceProfilesWindow(db_path)
        w.close()
        assert w._window is None

    def test_default_db_path(self):
        w = VoiceProfilesWindow()
        assert w._db_path is None  # will use default


class TestVoiceProfilesWindowData:
    def test_refresh_empty(self, db_path: Path):
        """Refresh with no profiles shows empty message."""
        w = VoiceProfilesWindow(db_path)
        db = w._get_db()
        profiles = db.list_profiles_detailed()
        db.close()
        assert profiles == []

    def test_refresh_with_profiles(self, db: VoiceProfileDB, db_path: Path):
        db.enroll("Alice", _random_embedding())
        db.enroll("Bob", _random_embedding())
        db.close()

        w = VoiceProfilesWindow(db_path)
        db2 = w._get_db()
        profiles = db2.list_profiles_detailed()
        db2.close()
        assert len(profiles) == 2
        names = [p["name"] for p in profiles]
        assert "Alice" in names
        assert "Bob" in names

    def test_profile_sample_count(self, db: VoiceProfileDB, db_path: Path):
        db.enroll("Alice", _random_embedding())
        db.enroll("Alice", _random_embedding())
        db.enroll("Alice", _random_embedding())
        db.close()

        w = VoiceProfilesWindow(db_path)
        db2 = w._get_db()
        profiles = db2.list_profiles_detailed()
        db2.close()
        assert profiles[0]["sample_count"] == 3

    def test_rename_profile(self, db: VoiceProfileDB, db_path: Path):
        db.enroll("OldName", _random_embedding())
        db.close()

        w = VoiceProfilesWindow(db_path)
        db2 = w._get_db()
        ok = db2.rename_profile("OldName", "NewName")
        db2.close()
        assert ok

        db3 = VoiceProfileDB(db_path)
        profiles = db3.list_profiles()
        db3.close()
        assert "NewName" in profiles
        assert "OldName" not in profiles

    def test_rename_to_existing_fails(self, db: VoiceProfileDB, db_path: Path):
        db.enroll("Alice", _random_embedding())
        db.enroll("Bob", _random_embedding())
        db.close()

        w = VoiceProfilesWindow(db_path)
        db2 = w._get_db()
        ok = db2.rename_profile("Alice", "Bob")
        db2.close()
        assert not ok

    def test_delete_profile(self, db: VoiceProfileDB, db_path: Path):
        db.enroll("Alice", _random_embedding())
        db.enroll("Bob", _random_embedding())
        db.close()

        w = VoiceProfilesWindow(db_path)
        db2 = w._get_db()
        ok = db2.delete_profile("Alice")
        profiles = db2.list_profiles()
        db2.close()
        assert ok
        assert "Alice" not in profiles
        assert "Bob" in profiles

    def test_delete_nonexistent(self, db: VoiceProfileDB, db_path: Path):
        db.close()

        w = VoiceProfilesWindow(db_path)
        db2 = w._get_db()
        ok = db2.delete_profile("Ghost")
        db2.close()
        assert not ok

    def test_profile_dates(self, db: VoiceProfileDB, db_path: Path):
        db.enroll("Alice", _random_embedding())
        db.close()

        w = VoiceProfilesWindow(db_path)
        db2 = w._get_db()
        profiles = db2.list_profiles_detailed()
        db2.close()
        assert profiles[0]["created_at"] is not None
        assert profiles[0]["updated_at"] is not None


class TestVoiceProfileDBEdgeCases:
    def test_case_insensitive_match(self, db: VoiceProfileDB):
        emb = _random_embedding()
        db.enroll("Alice", emb)
        profile = db.get_profile("alice")
        assert profile is not None
        assert profile.name == "Alice"
        db.close()

    def test_case_insensitive_enroll_update(self, db: VoiceProfileDB):
        db.enroll("Alice", _random_embedding())
        db.enroll("ALICE", _random_embedding())  # should update, not create new
        profiles = db.list_profiles()
        db.close()
        assert len(profiles) == 1

    def test_empty_db_match(self, db: VoiceProfileDB):
        result = db.match(_random_embedding())
        db.close()
        assert result is None

    def test_match_with_similar_embedding(self, db: VoiceProfileDB):
        emb = _random_embedding()
        db.enroll("Alice", emb)
        # Add small noise — should still match
        noisy = emb + np.random.randn(len(emb)).astype(np.float32) * 0.05
        result = db.match(noisy, threshold=0.75)
        db.close()
        assert result is not None
        assert result.name == "Alice"
        assert result.is_match is True

    def test_match_with_different_embedding(self, db: VoiceProfileDB):
        db.enroll("Alice", _random_embedding())
        totally_different = _random_embedding()
        result = db.match(totally_different, threshold=0.99)
        db.close()
        # Very high threshold — unlikely to match random vectors
        if result is not None:
            assert result.is_match is False
