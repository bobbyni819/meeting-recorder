"""Tests for voice embedding speaker profiles and cross-meeting identification."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from meeting_recorder.transcription.voice_profiles import (
    EmbeddingMatch,
    SpeakerProfile,
    VoiceProfileDB,
    cosine_similarity,
)


# ---------------------------------------------------------------------------
# cosine_similarity pure-function tests
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    """Tests for the cosine_similarity helper function."""

    def test_identical_vectors_returns_one(self):
        a = np.array([1.0, 0.0, 0.0])
        assert cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors_returns_zero(self):
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors_returns_negative_one(self):
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([-1.0, 0.0, 0.0])
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.0, 2.0, 3.0])
        assert cosine_similarity(a, b) == 0.0
        # Both zero
        assert cosine_similarity(a, a) == 0.0

    def test_different_magnitude_same_direction_returns_one(self):
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([5.0, 0.0, 0.0])
        assert cosine_similarity(a, b) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Dataclass field tests
# ---------------------------------------------------------------------------

class TestDataclasses:
    """Tests for SpeakerProfile and EmbeddingMatch dataclass fields."""

    def test_speaker_profile_fields(self):
        emb = np.array([0.1, 0.2, 0.3])
        profile = SpeakerProfile(name="Alice", embedding=emb, sample_count=5)
        assert profile.name == "Alice"
        np.testing.assert_array_equal(profile.embedding, emb)
        assert profile.sample_count == 5

    def test_speaker_profile_default_sample_count(self):
        profile = SpeakerProfile(name="Bob", embedding=np.zeros(3))
        assert profile.sample_count == 1

    def test_embedding_match_fields(self):
        match = EmbeddingMatch(name="Alice", similarity=0.92, is_match=True)
        assert match.name == "Alice"
        assert match.similarity == 0.92
        assert match.is_match is True


# ---------------------------------------------------------------------------
# VoiceProfileDB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path) -> VoiceProfileDB:
    """Create a VoiceProfileDB backed by a temporary database file."""
    db_path = tmp_path / "test_profiles.db"
    database = VoiceProfileDB(db_path=db_path)
    yield database
    database.close()


# ---------------------------------------------------------------------------
# VoiceProfileDB tests
# ---------------------------------------------------------------------------

class TestVoiceProfileDB:
    """Tests for VoiceProfileDB database operations."""

    def test_creates_schema(self, db: VoiceProfileDB, tmp_path):
        """Connecting to a fresh DB creates the speaker_profiles table."""
        db._connect()
        # Verify the table exists by querying sqlite_master
        conn = sqlite3.connect(str(tmp_path / "test_profiles.db"))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='speaker_profiles'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    def test_enroll_new_profile(self, db: VoiceProfileDB):
        """Enrolling a new speaker creates a profile with sample_count=1."""
        emb = np.array([1.0, 0.0, 0.0])
        db.enroll("Alice", emb)

        profile = db.get_profile("Alice")
        assert profile is not None
        assert profile.name == "Alice"
        np.testing.assert_array_almost_equal(profile.embedding, emb)
        assert profile.sample_count == 1

    def test_enroll_updates_existing_with_running_average(self, db: VoiceProfileDB):
        """Re-enrolling the same speaker updates the embedding via running average."""
        emb1 = np.array([1.0, 0.0, 0.0])
        emb2 = np.array([0.0, 1.0, 0.0])

        db.enroll("Alice", emb1)
        db.enroll("Alice", emb2)

        profile = db.get_profile("Alice")
        assert profile is not None
        assert profile.sample_count == 2
        # Running average: (emb1 * 1 + emb2) / 2
        expected = np.array([0.5, 0.5, 0.0])
        np.testing.assert_array_almost_equal(profile.embedding, expected)

    def test_enroll_case_insensitive_name_match(self, db: VoiceProfileDB):
        """Enrolling with different case updates the existing profile."""
        emb1 = np.array([1.0, 0.0, 0.0])
        emb2 = np.array([0.0, 1.0, 0.0])

        db.enroll("Alice", emb1)
        db.enroll("alice", emb2)  # lowercase

        names = db.list_profiles()
        assert len(names) == 1  # Only one profile, not two

        profile = db.get_profile("ALICE")
        assert profile is not None
        assert profile.sample_count == 2

    def test_get_profile_returns_correct_data(self, db: VoiceProfileDB):
        """get_profile returns a SpeakerProfile with all fields populated."""
        emb = np.array([0.5, 0.3, 0.7])
        db.enroll("Bob", emb)

        profile = db.get_profile("Bob")
        assert profile is not None
        assert isinstance(profile, SpeakerProfile)
        assert profile.name == "Bob"
        assert profile.sample_count == 1
        np.testing.assert_array_almost_equal(profile.embedding, emb)

    def test_get_profile_nonexistent_returns_none(self, db: VoiceProfileDB):
        """get_profile for a name that does not exist returns None."""
        assert db.get_profile("NonExistent") is None

    def test_list_profiles_returns_sorted_names(self, db: VoiceProfileDB):
        """list_profiles returns names in alphabetical order."""
        db.enroll("Charlie", np.array([0.0, 0.0, 1.0]))
        db.enroll("Alice", np.array([1.0, 0.0, 0.0]))
        db.enroll("Bob", np.array([0.0, 1.0, 0.0]))

        assert db.list_profiles() == ["Alice", "Bob", "Charlie"]

    def test_list_profiles_empty_db(self, db: VoiceProfileDB):
        """list_profiles on a fresh database returns an empty list."""
        assert db.list_profiles() == []

    def test_delete_profile_works(self, db: VoiceProfileDB):
        """Deleting an existing profile removes it and returns True."""
        db.enroll("Alice", np.array([1.0, 0.0, 0.0]))
        assert db.delete_profile("Alice") is True
        assert db.get_profile("Alice") is None
        assert db.list_profiles() == []

    def test_delete_profile_nonexistent_returns_false(self, db: VoiceProfileDB):
        """Deleting a name that does not exist returns False."""
        assert db.delete_profile("Ghost") is False

    def test_match_finds_correct_profile(self, db: VoiceProfileDB):
        """match returns the best-matching profile above threshold."""
        alice_emb = np.array([1.0, 0.0, 0.0])
        db.enroll("Alice", alice_emb)

        # Query with the same embedding -- should be perfect match
        result = db.match(alice_emb)
        assert result is not None
        assert result.name == "Alice"
        assert result.similarity == pytest.approx(1.0)
        assert result.is_match is True

    def test_match_below_threshold_returns_is_match_false(self, db: VoiceProfileDB):
        """match returns is_match=False when best similarity is below threshold."""
        db.enroll("Alice", np.array([1.0, 0.0, 0.0]))

        # Orthogonal vector -- cosine similarity = 0.0
        result = db.match(np.array([0.0, 1.0, 0.0]), threshold=0.5)
        assert result is not None
        assert result.name == "Alice"
        assert result.similarity == pytest.approx(0.0)
        assert result.is_match is False

    def test_match_empty_db_returns_none(self, db: VoiceProfileDB):
        """match against an empty database returns None."""
        result = db.match(np.array([1.0, 0.0, 0.0]))
        assert result is None

    def test_match_picks_closest_profile(self, db: VoiceProfileDB):
        """match returns the profile with the highest similarity."""
        db.enroll("Alice", np.array([1.0, 0.0, 0.0]))
        db.enroll("Bob", np.array([0.0, 1.0, 0.0]))
        db.enroll("Charlie", np.array([0.0, 0.0, 1.0]))

        # Query is mostly aligned with Bob's direction
        query = np.array([0.1, 0.95, 0.05])
        result = db.match(query, threshold=0.5)

        assert result is not None
        assert result.name == "Bob"
        assert result.is_match is True
        assert result.similarity > 0.9

    def test_close_works(self, tmp_path):
        """close() shuts down the connection and allows reopening."""
        db_path = tmp_path / "close_test.db"
        database = VoiceProfileDB(db_path=db_path)

        # Enroll to force connection open
        database.enroll("Alice", np.array([1.0, 0.0, 0.0]))
        assert database._conn is not None

        database.close()
        assert database._conn is None

        # Can reconnect and still see data
        database2 = VoiceProfileDB(db_path=db_path)
        assert database2.list_profiles() == ["Alice"]
        database2.close()
