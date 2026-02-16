"""Merge transcription segments with speaker diarization labels."""

from __future__ import annotations

import logging
from meeting_recorder.transcription.local_whisper import TranscriptSegment
from meeting_recorder.transcription.diarization import SpeakerSegment

logger = logging.getLogger(__name__)


def merge_transcript_with_speakers(
    transcript_segments: list[TranscriptSegment],
    speaker_segments: list[SpeakerSegment],
    user_name: str = "User",
) -> list[TranscriptSegment]:
    """Merge transcript segments with speaker labels from diarization.

    Each transcript segment is assigned the speaker who has the most
    overlap with it during the diarization output.

    Args:
        transcript_segments: Segments from whisper transcription.
        speaker_segments: Segments from speaker diarization.
        user_name: Name to use for the user (from mic track).

    Returns:
        Transcript segments with speaker fields populated.
    """
    if not speaker_segments:
        logger.warning("No speaker segments provided. Skipping speaker assignment.")
        return transcript_segments

    for tseg in transcript_segments:
        best_speaker = _find_best_speaker(tseg, speaker_segments)
        tseg.speaker = best_speaker

    # Rename generic speaker labels to friendly names
    _rename_speakers(transcript_segments, user_name)

    logger.info("Merged %d transcript segments with speaker labels.", len(transcript_segments))
    return transcript_segments


def merge_user_and_app_transcripts(
    user_segments: list[TranscriptSegment],
    app_segments: list[TranscriptSegment],
    user_name: str = "User",
) -> list[TranscriptSegment]:
    """Merge user (mic) and app (remote) transcript segments chronologically.

    User segments are labeled with user_name. App segments keep their
    diarization-assigned speaker labels.

    Args:
        user_segments: Transcript segments from mic audio (the user).
        app_segments: Transcript segments from app audio (remote participants).
        user_name: Name label for the user.

    Returns:
        Merged and chronologically sorted transcript segments.
    """
    for seg in user_segments:
        seg.speaker = user_name

    merged = user_segments + app_segments
    merged.sort(key=lambda s: s.start)

    # Merge adjacent segments from the same speaker
    merged = _merge_adjacent(merged)

    return merged


def _find_best_speaker(
    tseg: TranscriptSegment,
    speaker_segments: list[SpeakerSegment],
) -> str:
    """Find the speaker with the most overlap for a transcript segment."""
    best_speaker = "Unknown"
    best_overlap = 0.0

    for sseg in speaker_segments:
        overlap = _overlap_duration(tseg.start, tseg.end, sseg.start, sseg.end)
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = sseg.speaker

    return best_speaker


def _overlap_duration(s1: float, e1: float, s2: float, e2: float) -> float:
    """Calculate the overlap duration between two time intervals."""
    overlap_start = max(s1, s2)
    overlap_end = min(e1, e2)
    return max(0.0, overlap_end - overlap_start)


def _rename_speakers(segments: list[TranscriptSegment], user_name: str) -> None:
    """Rename SPEAKER_XX labels to Participant 1, Participant 2, etc."""
    speaker_map: dict[str, str] = {}
    counter = 1

    for seg in segments:
        if seg.speaker not in speaker_map and seg.speaker != "Unknown":
            speaker_map[seg.speaker] = f"Participant {counter}"
            counter += 1
        if seg.speaker in speaker_map:
            seg.speaker = speaker_map[seg.speaker]


def _merge_adjacent(
    segments: list[TranscriptSegment],
    gap_threshold: float = 1.0,
) -> list[TranscriptSegment]:
    """Merge adjacent segments from the same speaker if gap < threshold."""
    if not segments:
        return segments

    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg.speaker == prev.speaker and (seg.start - prev.end) < gap_threshold:
            prev.end = seg.end
            prev.text = prev.text + " " + seg.text
        else:
            merged.append(seg)

    return merged
