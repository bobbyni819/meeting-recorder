"""Tests for speaker name resolution -- mapping Participant N to real attendee names."""

from __future__ import annotations

import pytest

from meeting_recorder.transcription.local_whisper import TranscriptSegment
from unittest.mock import MagicMock, patch

from meeting_recorder.transcription.speaker_resolver import (
    SpeakerMapping,
    resolve_speakers,
    resolve_speakers_with_voice_profiles,
    apply_speaker_map,
    _get_remote_speakers_ordered,
    _get_remote_attendees,
)


# ---------------------------------------------------------------------------
# Helper to build segments quickly
# ---------------------------------------------------------------------------

def _seg(speaker: str, text: str = "hello", start: float = 0.0, end: float = 1.0) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, speaker=speaker)


# ---------------------------------------------------------------------------
# SpeakerMapping dataclass defaults
# ---------------------------------------------------------------------------

class TestSpeakerMappingDefaults:
    """Test that the SpeakerMapping dataclass has sensible defaults."""

    def test_default_values(self):
        mapping = SpeakerMapping()
        assert mapping.speaker_map == {}
        assert mapping.confidence == "none"
        assert mapping.method == ""
        assert mapping.unmapped_speakers == []


# ---------------------------------------------------------------------------
# _get_remote_speakers_ordered
# ---------------------------------------------------------------------------

class TestGetRemoteSpeakersOrdered:
    """Test extraction and ordering of remote speaker labels."""

    def test_excludes_user_case_insensitive(self):
        segments = [
            _seg("user", start=0, end=1),
            _seg("User", start=1, end=2),
            _seg("USER", start=2, end=3),
            _seg("Participant 1", start=3, end=4),
        ]
        result = _get_remote_speakers_ordered(segments, user_name="User")
        assert result == ["Participant 1"]

    def test_excludes_unknown(self):
        segments = [
            _seg("Unknown", start=0, end=1),
            _seg("Participant 1", start=1, end=2),
        ]
        result = _get_remote_speakers_ordered(segments, user_name="User")
        assert result == ["Participant 1"]

    def test_excludes_empty_speaker(self):
        segments = [
            _seg("", start=0, end=1),
            _seg("Participant 1", start=1, end=2),
        ]
        result = _get_remote_speakers_ordered(segments, user_name="User")
        assert result == ["Participant 1"]

    def test_preserves_first_appearance_order(self):
        segments = [
            _seg("Participant 2", start=0, end=1),
            _seg("Participant 1", start=1, end=2),
            _seg("Participant 3", start=2, end=3),
            _seg("Participant 2", start=3, end=4),  # duplicate
            _seg("Participant 1", start=4, end=5),  # duplicate
        ]
        result = _get_remote_speakers_ordered(segments, user_name="User")
        assert result == ["Participant 2", "Participant 1", "Participant 3"]

    def test_empty_segments(self):
        result = _get_remote_speakers_ordered([], user_name="User")
        assert result == []


# ---------------------------------------------------------------------------
# _get_remote_attendees
# ---------------------------------------------------------------------------

class TestGetRemoteAttendees:
    """Test attendee list filtering."""

    def test_excludes_user(self):
        attendees = ["Alice", "Bob", "User"]
        result = _get_remote_attendees(attendees, organizer="", user_name="User")
        assert result == ["Alice", "Bob"]

    def test_excludes_organizer(self):
        attendees = ["Alice", "Bob", "Charlie"]
        result = _get_remote_attendees(attendees, organizer="Charlie", user_name="User")
        assert result == ["Alice", "Bob"]

    def test_case_insensitive_exclusion(self):
        attendees = ["alice", "BOB", "user"]
        result = _get_remote_attendees(attendees, organizer="ALICE", user_name="User")
        assert result == ["BOB"]

    def test_empty_organizer_does_not_exclude_extra(self):
        attendees = ["Alice", "Bob"]
        result = _get_remote_attendees(attendees, organizer="", user_name="User")
        assert result == ["Alice", "Bob"]

    def test_empty_attendees(self):
        result = _get_remote_attendees([], organizer="Org", user_name="User")
        assert result == []


# ---------------------------------------------------------------------------
# resolve_speakers
# ---------------------------------------------------------------------------

class TestResolveSpeakers:
    """Test the main speaker resolution logic."""

    def test_single_speaker_single_attendee_confirmed(self):
        """1 remote speaker + 1 remote attendee -> confirmed match."""
        segments = [
            _seg("User", start=0, end=2),
            _seg("Participant 1", start=2, end=5),
        ]
        result = resolve_speakers(
            segments, attendees=["User", "Alice"], organizer="", user_name="User",
        )
        assert result.confidence == "confirmed"
        assert result.speaker_map == {"Participant 1": "Alice"}
        assert result.method == "single speaker/attendee direct match"

    def test_two_speakers_two_attendees_guessed(self):
        """2 remote speakers + 2 remote attendees -> guessed order match."""
        segments = [
            _seg("Participant 1", start=0, end=2),
            _seg("Participant 2", start=2, end=4),
        ]
        result = resolve_speakers(
            segments, attendees=["User", "Alice", "Bob"],
            organizer="", user_name="User",
        )
        assert result.confidence == "guessed"
        assert result.speaker_map == {"Participant 1": "Alice", "Participant 2": "Bob"}
        assert "order-based" in result.method

    def test_three_speakers_three_attendees_guessed(self):
        """3 remote speakers + 3 remote attendees -> guessed order match."""
        segments = [
            _seg("Participant 1", start=0, end=1),
            _seg("Participant 2", start=1, end=2),
            _seg("Participant 3", start=2, end=3),
        ]
        result = resolve_speakers(
            segments, attendees=["User", "Alice", "Bob", "Charlie"],
            organizer="", user_name="User",
        )
        assert result.confidence == "guessed"
        assert result.speaker_map == {
            "Participant 1": "Alice",
            "Participant 2": "Bob",
            "Participant 3": "Charlie",
        }

    def test_count_mismatch_2_speakers_3_attendees(self):
        """2 speakers but 3 attendees -> no mapping."""
        segments = [
            _seg("Participant 1", start=0, end=1),
            _seg("Participant 2", start=1, end=2),
        ]
        result = resolve_speakers(
            segments, attendees=["User", "Alice", "Bob", "Charlie"],
            organizer="", user_name="User",
        )
        assert result.confidence == "none"
        assert "mismatch" in result.method
        assert result.unmapped_speakers == ["Participant 1", "Participant 2"]

    def test_count_mismatch_3_speakers_2_attendees(self):
        """3 speakers but 2 attendees -> no mapping."""
        segments = [
            _seg("Participant 1", start=0, end=1),
            _seg("Participant 2", start=1, end=2),
            _seg("Participant 3", start=2, end=3),
        ]
        result = resolve_speakers(
            segments, attendees=["User", "Alice", "Bob"],
            organizer="", user_name="User",
        )
        assert result.confidence == "none"
        assert "mismatch" in result.method
        assert result.unmapped_speakers == ["Participant 1", "Participant 2", "Participant 3"]

    def test_no_attendees_returns_none(self):
        """No attendees provided at all -> none confidence."""
        segments = [_seg("Participant 1")]
        result = resolve_speakers(segments, attendees=[], user_name="User")
        assert result.confidence == "none"
        assert result.method == "no attendees provided"

    def test_empty_segments_no_remote_speakers(self):
        """Empty segment list -> no remote speakers found."""
        result = resolve_speakers(
            segments=[], attendees=["Alice", "Bob"], user_name="User",
        )
        assert result.confidence == "none"
        assert result.method == "no remote speakers found"

    def test_all_segments_from_user_no_remote_speakers(self):
        """All segments belong to user -> no remote speakers."""
        segments = [
            _seg("User", start=0, end=2),
            _seg("User", start=3, end=5),
        ]
        result = resolve_speakers(
            segments, attendees=["User", "Alice"], user_name="User",
        )
        assert result.confidence == "none"
        assert result.method == "no remote speakers found"

    def test_no_remote_attendees_after_filtering(self):
        """All attendees are user/organizer -> no remote attendees after filtering."""
        segments = [
            _seg("Participant 1", start=0, end=2),
        ]
        result = resolve_speakers(
            segments, attendees=["User", "Bob"],
            organizer="Bob", user_name="User",
        )
        assert result.confidence == "none"
        assert result.method == "no remote attendees after filtering"
        assert result.unmapped_speakers == ["Participant 1"]


# ---------------------------------------------------------------------------
# apply_speaker_map
# ---------------------------------------------------------------------------

class TestApplySpeakerMap:
    """Test in-place renaming of segment speakers."""

    def test_renames_correctly(self):
        segments = [
            _seg("Participant 1", text="hello", start=0, end=1),
            _seg("Participant 2", text="world", start=1, end=2),
        ]
        apply_speaker_map(segments, {"Participant 1": "Alice", "Participant 2": "Bob"})
        assert segments[0].speaker == "Alice"
        assert segments[1].speaker == "Bob"

    def test_leaves_unmapped_speakers_unchanged(self):
        segments = [
            _seg("Participant 1", start=0, end=1),
            _seg("Participant 3", start=1, end=2),
        ]
        apply_speaker_map(segments, {"Participant 1": "Alice"})
        assert segments[0].speaker == "Alice"
        assert segments[1].speaker == "Participant 3"  # unchanged

    def test_empty_map_is_noop(self):
        segments = [
            _seg("Participant 1", start=0, end=1),
            _seg("User", start=1, end=2),
        ]
        original_speakers = [s.speaker for s in segments]
        apply_speaker_map(segments, {})
        assert [s.speaker for s in segments] == original_speakers

    def test_empty_segments_no_error(self):
        apply_speaker_map([], {"Participant 1": "Alice"})  # should not raise


# ---------------------------------------------------------------------------
# Voice profile resolution
# ---------------------------------------------------------------------------

class TestResolveWithVoiceProfiles:
    """Test voice profile based speaker resolution."""

    def test_no_audio_path_returns_none(self):
        segments = [_seg("Participant 1")]
        result = resolve_speakers_with_voice_profiles(segments, audio_path=None)
        assert result.confidence == "none"

    def test_no_remote_speakers_returns_none(self):
        segments = [_seg("User")]
        result = resolve_speakers_with_voice_profiles(
            segments, audio_path=None, user_name="User"
        )
        assert result.confidence == "none"

    @patch("meeting_recorder.transcription.voice_profiles.extract_embedding")
    @patch("meeting_recorder.transcription.voice_profiles.VoiceProfileDB")
    def test_successful_match(self, mock_db_cls, mock_extract):
        import numpy as np
        from pathlib import Path
        from meeting_recorder.transcription.voice_profiles import EmbeddingMatch

        segments = [
            _seg("Participant 1", start=0.0, end=2.0),
            _seg("User", start=2.0, end=3.0),
        ]

        fake_embedding = np.array([0.1, 0.2, 0.3])
        mock_extract.return_value = fake_embedding

        mock_db = MagicMock()
        mock_db.match.return_value = EmbeddingMatch(
            name="Alice", similarity=0.9, is_match=True
        )
        mock_db_cls.return_value = mock_db

        result = resolve_speakers_with_voice_profiles(
            segments, audio_path=Path("test.wav"), user_name="User"
        )

        assert result.speaker_map == {"Participant 1": "Alice"}
        assert result.confidence == "confirmed"
        mock_db.close.assert_called_once()

    @patch("meeting_recorder.transcription.voice_profiles.extract_embedding")
    @patch("meeting_recorder.transcription.voice_profiles.VoiceProfileDB")
    def test_no_match_above_threshold(self, mock_db_cls, mock_extract):
        import numpy as np
        from pathlib import Path

        segments = [_seg("Participant 1", start=0.0, end=2.0)]

        mock_extract.return_value = np.array([0.1, 0.2, 0.3])

        mock_db = MagicMock()
        mock_db.match.return_value = MagicMock(
            name="Alice", similarity=0.3, is_match=False
        )
        mock_db_cls.return_value = mock_db

        result = resolve_speakers_with_voice_profiles(
            segments, audio_path=Path("test.wav"), user_name="User"
        )

        assert result.speaker_map == {}
        assert result.confidence == "none"
        assert "Participant 1" in result.unmapped_speakers

    @patch("meeting_recorder.transcription.voice_profiles.extract_embedding")
    @patch("meeting_recorder.transcription.voice_profiles.VoiceProfileDB")
    def test_partial_match(self, mock_db_cls, mock_extract):
        import numpy as np
        from pathlib import Path
        from meeting_recorder.transcription.voice_profiles import EmbeddingMatch

        segments = [
            _seg("Participant 1", start=0.0, end=2.0),
            _seg("Participant 2", start=2.0, end=4.0),
        ]

        # First speaker matches, second doesn't
        mock_extract.side_effect = [np.array([0.1, 0.2]), np.array([0.3, 0.4])]

        mock_db = MagicMock()
        mock_db.match.side_effect = [
            EmbeddingMatch(name="Alice", similarity=0.9, is_match=True),
            EmbeddingMatch(name="Bob", similarity=0.3, is_match=False),
        ]
        mock_db_cls.return_value = mock_db

        result = resolve_speakers_with_voice_profiles(
            segments, audio_path=Path("test.wav"), user_name="User"
        )

        assert result.speaker_map == {"Participant 1": "Alice"}
        assert result.confidence == "guessed"  # partial match
        assert "Participant 2" in result.unmapped_speakers

    @patch("meeting_recorder.transcription.voice_profiles.extract_embedding")
    @patch("meeting_recorder.transcription.voice_profiles.VoiceProfileDB")
    def test_segment_too_short_skipped(self, mock_db_cls, mock_extract):
        from pathlib import Path

        # Only segment is 0.5s which is < 1.0s minimum
        segments = [_seg("Participant 1", start=0.0, end=0.5)]

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        result = resolve_speakers_with_voice_profiles(
            segments, audio_path=Path("test.wav"), user_name="User"
        )

        assert result.confidence == "none"
        mock_extract.assert_not_called()
