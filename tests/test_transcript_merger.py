"""Tests for transcript merger -- speaker label merging and segment ordering."""

from __future__ import annotations

import pytest

from meeting_recorder.transcription.local_whisper import TranscriptSegment
from meeting_recorder.transcription.diarization import SpeakerSegment
from meeting_recorder.transcription.transcript_merger import (
    merge_transcript_with_speakers,
    merge_user_and_app_transcripts,
    _find_best_speaker,
    _overlap_duration,
    _merge_adjacent,
    _rename_speakers,
)


# ---------------------------------------------------------------------------
# _overlap_duration
# ---------------------------------------------------------------------------

class TestOverlapDuration:
    """Test the overlap calculation between two intervals."""

    def test_full_overlap(self):
        assert _overlap_duration(0, 10, 0, 10) == 10.0

    def test_partial_overlap(self):
        assert _overlap_duration(0, 5, 3, 8) == 2.0

    def test_no_overlap(self):
        assert _overlap_duration(0, 2, 3, 5) == 0.0

    def test_contained(self):
        assert _overlap_duration(2, 4, 0, 10) == 2.0

    def test_touching_edges(self):
        assert _overlap_duration(0, 5, 5, 10) == 0.0

    def test_reversed_order(self):
        assert _overlap_duration(3, 8, 0, 5) == 2.0


# ---------------------------------------------------------------------------
# _find_best_speaker
# ---------------------------------------------------------------------------

class TestFindBestSpeaker:
    """Test assigning best speaker based on overlap."""

    def test_single_speaker(self):
        tseg = TranscriptSegment(start=0.0, end=5.0, text="hello")
        speaker_segments = [
            SpeakerSegment(start=0.0, end=10.0, speaker="SPEAKER_00"),
        ]
        assert _find_best_speaker(tseg, speaker_segments) == "SPEAKER_00"

    def test_multiple_speakers_picks_best(self):
        tseg = TranscriptSegment(start=2.0, end=6.0, text="hello")
        speaker_segments = [
            SpeakerSegment(start=0.0, end=3.0, speaker="SPEAKER_00"),  # 1s overlap
            SpeakerSegment(start=3.0, end=8.0, speaker="SPEAKER_01"),  # 3s overlap
        ]
        assert _find_best_speaker(tseg, speaker_segments) == "SPEAKER_01"

    def test_no_overlap_returns_unknown(self):
        tseg = TranscriptSegment(start=10.0, end=12.0, text="hello")
        speaker_segments = [
            SpeakerSegment(start=0.0, end=5.0, speaker="SPEAKER_00"),
        ]
        assert _find_best_speaker(tseg, speaker_segments) == "Unknown"

    def test_empty_speaker_segments(self):
        tseg = TranscriptSegment(start=0.0, end=2.0, text="hello")
        assert _find_best_speaker(tseg, []) == "Unknown"


# ---------------------------------------------------------------------------
# _rename_speakers
# ---------------------------------------------------------------------------

class TestRenameSpeakers:
    """Test the speaker rename logic."""

    def test_renames_speakers_sequentially(self):
        segments = [
            TranscriptSegment(start=0, end=1, text="a", speaker="SPEAKER_00"),
            TranscriptSegment(start=1, end=2, text="b", speaker="SPEAKER_01"),
            TranscriptSegment(start=2, end=3, text="c", speaker="SPEAKER_00"),
        ]
        _rename_speakers(segments, user_name="User")
        assert segments[0].speaker == "Participant 1"
        assert segments[1].speaker == "Participant 2"
        assert segments[2].speaker == "Participant 1"

    def test_unknown_speaker_not_renamed(self):
        segments = [
            TranscriptSegment(start=0, end=1, text="a", speaker="Unknown"),
        ]
        _rename_speakers(segments, user_name="User")
        assert segments[0].speaker == "Unknown"

    def test_empty_segments(self):
        segments = []
        _rename_speakers(segments, user_name="User")  # should not raise


# ---------------------------------------------------------------------------
# _merge_adjacent
# ---------------------------------------------------------------------------

class TestMergeAdjacent:
    """Test merging adjacent segments from the same speaker."""

    def test_merge_adjacent_same_speaker(self):
        segments = [
            TranscriptSegment(start=0, end=1, text="Hello", speaker="User"),
            TranscriptSegment(start=1.5, end=3, text="how are you?", speaker="User"),
        ]
        merged = _merge_adjacent(segments, gap_threshold=1.0)
        assert len(merged) == 1
        assert merged[0].text == "Hello how are you?"
        assert merged[0].start == 0
        assert merged[0].end == 3

    def test_no_merge_different_speakers(self):
        segments = [
            TranscriptSegment(start=0, end=1, text="Hello", speaker="User"),
            TranscriptSegment(start=1.5, end=3, text="Hi", speaker="Participant 1"),
        ]
        merged = _merge_adjacent(segments, gap_threshold=1.0)
        assert len(merged) == 2

    def test_no_merge_large_gap(self):
        segments = [
            TranscriptSegment(start=0, end=1, text="Hello", speaker="User"),
            TranscriptSegment(start=5, end=7, text="world", speaker="User"),
        ]
        merged = _merge_adjacent(segments, gap_threshold=1.0)
        assert len(merged) == 2

    def test_merge_chain(self):
        """Three consecutive segments from same speaker should all merge."""
        segments = [
            TranscriptSegment(start=0, end=1, text="A", speaker="User"),
            TranscriptSegment(start=1.2, end=2, text="B", speaker="User"),
            TranscriptSegment(start=2.3, end=3, text="C", speaker="User"),
        ]
        merged = _merge_adjacent(segments, gap_threshold=1.0)
        assert len(merged) == 1
        assert merged[0].text == "A B C"

    def test_empty_segments(self):
        assert _merge_adjacent([]) == []

    def test_single_segment(self):
        segments = [TranscriptSegment(start=0, end=1, text="solo", speaker="User")]
        merged = _merge_adjacent(segments)
        assert len(merged) == 1


# ---------------------------------------------------------------------------
# merge_transcript_with_speakers
# ---------------------------------------------------------------------------

class TestMergeTranscriptWithSpeakers:
    """Test full merge of transcript segments with diarization output."""

    def test_assigns_speakers(self):
        transcript_segments = [
            TranscriptSegment(start=0, end=3, text="Hello"),
            TranscriptSegment(start=4, end=7, text="Hi there"),
        ]
        speaker_segments = [
            SpeakerSegment(start=0, end=3.5, speaker="SPEAKER_00"),
            SpeakerSegment(start=3.5, end=8, speaker="SPEAKER_01"),
        ]

        result = merge_transcript_with_speakers(
            transcript_segments, speaker_segments, user_name="User",
        )
        # Speakers should be renamed to Participant N
        assert result[0].speaker == "Participant 1"
        assert result[1].speaker == "Participant 2"

    def test_no_speaker_segments_returns_unchanged(self):
        transcript_segments = [
            TranscriptSegment(start=0, end=2, text="test", speaker="original"),
        ]
        result = merge_transcript_with_speakers(transcript_segments, [], user_name="User")
        assert result[0].speaker == "original"

    def test_single_speaker(self):
        transcript_segments = [
            TranscriptSegment(start=0, end=2, text="A"),
            TranscriptSegment(start=3, end=5, text="B"),
        ]
        speaker_segments = [
            SpeakerSegment(start=0, end=10, speaker="SPEAKER_00"),
        ]
        result = merge_transcript_with_speakers(
            transcript_segments, speaker_segments, user_name="User",
        )
        # Both should get the same speaker label
        assert result[0].speaker == result[1].speaker


# ---------------------------------------------------------------------------
# merge_user_and_app_transcripts
# ---------------------------------------------------------------------------

class TestMergeUserAndAppTranscripts:
    """Test merging user (mic) and app (remote) transcripts."""

    def test_chronological_ordering(self):
        user_segs = [
            TranscriptSegment(start=0, end=2, text="I said this"),
        ]
        app_segs = [
            TranscriptSegment(start=1, end=3, text="Remote said this", speaker="Participant 1"),
        ]
        merged = merge_user_and_app_transcripts(user_segs, app_segs, user_name="Alice")
        # Should be sorted by start time
        assert merged[0].start <= merged[-1].start

    def test_user_segments_labeled(self):
        user_segs = [
            TranscriptSegment(start=0, end=2, text="Hello"),
        ]
        app_segs = [
            TranscriptSegment(start=3, end=5, text="Hi", speaker="Remote"),
        ]
        merged = merge_user_and_app_transcripts(user_segs, app_segs, user_name="Bob")
        user_seg = [s for s in merged if s.text == "Hello"][0]
        assert user_seg.speaker == "Bob"

    def test_empty_user_segments(self):
        app_segs = [
            TranscriptSegment(start=0, end=2, text="Solo remote", speaker="Remote"),
        ]
        merged = merge_user_and_app_transcripts([], app_segs, user_name="User")
        assert len(merged) == 1

    def test_empty_app_segments(self):
        user_segs = [
            TranscriptSegment(start=0, end=2, text="Solo user"),
        ]
        merged = merge_user_and_app_transcripts(user_segs, [], user_name="User")
        assert len(merged) == 1
        assert merged[0].speaker == "User"

    def test_adjacent_same_speaker_merged(self):
        """Adjacent segments from the same speaker should be merged."""
        user_segs = [
            TranscriptSegment(start=0, end=1, text="Part 1"),
            TranscriptSegment(start=1.2, end=2, text="Part 2"),
        ]
        merged = merge_user_and_app_transcripts(user_segs, [], user_name="User")
        assert len(merged) == 1
        assert "Part 1" in merged[0].text
        assert "Part 2" in merged[0].text

    def test_interleaved_speakers(self):
        user_segs = [
            TranscriptSegment(start=0, end=2, text="User Q1"),
            TranscriptSegment(start=5, end=7, text="User Q2"),
        ]
        app_segs = [
            TranscriptSegment(start=3, end=4.5, text="Remote A1", speaker="Remote"),
        ]
        merged = merge_user_and_app_transcripts(user_segs, app_segs, user_name="Alice")

        # Verify chronological order
        for i in range(len(merged) - 1):
            assert merged[i].start <= merged[i + 1].start
