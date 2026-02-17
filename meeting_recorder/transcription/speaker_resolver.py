"""Speaker name resolution: maps 'Participant N' labels to real attendee names.

Supports three resolution strategies (in priority order):
1. Voice profile matching (cross-meeting speaker ID via embeddings)
2. Calendar-based matching (attendee list heuristics)
3. Fallback to generic Participant N labels
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)


@dataclass
class SpeakerMapping:
    """Result of speaker name resolution."""
    speaker_map: dict[str, str] = field(default_factory=dict)  # e.g. {"Participant 1": "Alice"}
    confidence: str = "none"  # "confirmed", "guessed", "none"
    method: str = ""  # description of how mapping was determined
    unmapped_speakers: list[str] = field(default_factory=list)


def resolve_speakers(
    segments: list[TranscriptSegment],
    attendees: list[str],
    organizer: str = "",
    user_name: str = "User",
) -> SpeakerMapping:
    """Resolve speaker labels to real names using calendar attendee list.

    Three strategies:
    1. Single remote speaker + single remote attendee -> "confirmed" direct match
    2. Remote speaker count == remote attendee count -> "guessed" order-based match
    3. Count mismatch -> "none", keep Participant N labels

    Args:
        segments: Transcript segments with speaker labels (e.g. "Participant 1")
        attendees: List of attendee names from calendar
        organizer: Meeting organizer name
        user_name: Current user's name

    Returns:
        SpeakerMapping with the resolved mapping and confidence.
    """
    if not attendees:
        return SpeakerMapping(confidence="none", method="no attendees provided")

    remote_speakers = _get_remote_speakers_ordered(segments, user_name)
    remote_attendees = _get_remote_attendees(attendees, organizer, user_name)

    if not remote_speakers:
        return SpeakerMapping(confidence="none", method="no remote speakers found")

    if not remote_attendees:
        return SpeakerMapping(
            confidence="none",
            method="no remote attendees after filtering",
            unmapped_speakers=remote_speakers,
        )

    # Strategy 1: Single remote speaker + single remote attendee -> confirmed
    if len(remote_speakers) == 1 and len(remote_attendees) == 1:
        speaker_map = {remote_speakers[0]: remote_attendees[0]}
        logger.info(
            "Speaker resolution: confirmed match %s -> %s",
            remote_speakers[0], remote_attendees[0],
        )
        return SpeakerMapping(
            speaker_map=speaker_map,
            confidence="confirmed",
            method="single speaker/attendee direct match",
        )

    # Strategy 2: Equal counts -> order-based guess
    if len(remote_speakers) == len(remote_attendees):
        speaker_map = dict(zip(remote_speakers, remote_attendees))
        logger.info(
            "Speaker resolution: guessed mapping for %d speakers",
            len(remote_speakers),
        )
        return SpeakerMapping(
            speaker_map=speaker_map,
            confidence="guessed",
            method=f"order-based match ({len(remote_speakers)} speakers = {len(remote_attendees)} attendees)",
        )

    # Strategy 3: Count mismatch -> no mapping
    logger.info(
        "Speaker resolution: count mismatch (%d speakers vs %d attendees)",
        len(remote_speakers), len(remote_attendees),
    )
    return SpeakerMapping(
        confidence="none",
        method=f"count mismatch ({len(remote_speakers)} speakers vs {len(remote_attendees)} attendees)",
        unmapped_speakers=remote_speakers,
    )


def apply_speaker_map(
    segments: list[TranscriptSegment],
    speaker_map: dict[str, str],
) -> None:
    """Rename segment speakers in-place using the speaker map.

    Args:
        segments: Transcript segments to modify.
        speaker_map: Mapping from current labels to new names.
    """
    for seg in segments:
        if seg.speaker in speaker_map:
            seg.speaker = speaker_map[seg.speaker]


def _get_remote_speakers_ordered(
    segments: list[TranscriptSegment],
    user_name: str,
) -> list[str]:
    """Get unique remote speaker labels ordered by first appearance.

    Excludes the user and any empty/Unknown speakers.

    Args:
        segments: Transcript segments with speaker labels.
        user_name: Current user's name to exclude.

    Returns:
        List of unique remote speaker labels in order of first appearance.
    """
    seen = set()
    ordered = []
    user_lower = user_name.lower()
    for seg in segments:
        speaker = seg.speaker
        if not speaker or speaker.lower() == user_lower or speaker == "Unknown":
            continue
        if speaker not in seen:
            seen.add(speaker)
            ordered.append(speaker)
    return ordered


def _get_remote_attendees(
    attendees: list[str],
    organizer: str,
    user_name: str,
) -> list[str]:
    """Filter attendee list to only remote attendees.

    Removes the user and organizer (case-insensitive) since the organizer
    is typically the user themselves.

    Args:
        attendees: Full attendee list from calendar.
        organizer: Meeting organizer name.
        user_name: Current user's name.

    Returns:
        Filtered list of remote attendee names.
    """
    exclude = set()
    if user_name:
        exclude.add(user_name.lower())
    if organizer:
        exclude.add(organizer.lower())

    return [a for a in attendees if a.lower() not in exclude]


def resolve_speakers_with_voice_profiles(
    segments: list[TranscriptSegment],
    audio_path: Optional[Path] = None,
    user_name: str = "User",
) -> SpeakerMapping:
    """Resolve speakers using stored voice profiles (cross-meeting speaker ID).

    For each unique non-user speaker in the transcript, extracts a voice
    embedding from their audio segments and matches it against stored profiles.

    Args:
        segments: Transcript segments with speaker labels.
        audio_path: Path to the audio file for embedding extraction.
        user_name: Current user's name to exclude.

    Returns:
        SpeakerMapping with matches found from voice profiles.
    """
    if audio_path is None:
        return SpeakerMapping(confidence="none", method="no audio path for voice profiles")

    try:
        from meeting_recorder.transcription.voice_profiles import (
            VoiceProfileDB,
            extract_embedding,
        )
    except ImportError:
        return SpeakerMapping(confidence="none", method="voice profile dependencies not available")

    remote_speakers = _get_remote_speakers_ordered(segments, user_name)
    if not remote_speakers:
        return SpeakerMapping(confidence="none", method="no remote speakers for voice matching")

    db = VoiceProfileDB()
    speaker_map: dict[str, str] = {}
    unmapped: list[str] = []

    try:
        for speaker_label in remote_speakers:
            # Find the first segment for this speaker with enough audio
            speaker_segs = [s for s in segments if s.speaker == speaker_label and (s.end - s.start) >= 1.0]
            if not speaker_segs:
                unmapped.append(speaker_label)
                continue

            seg = speaker_segs[0]
            embedding = extract_embedding(audio_path, start=seg.start, end=seg.end)
            if embedding is None:
                unmapped.append(speaker_label)
                continue

            match = db.match(embedding)
            if match and match.is_match:
                speaker_map[speaker_label] = match.name
                logger.info(
                    "Voice profile match: %s -> %s (similarity=%.3f)",
                    speaker_label, match.name, match.similarity,
                )
            else:
                unmapped.append(speaker_label)
    finally:
        db.close()

    if not speaker_map:
        return SpeakerMapping(
            confidence="none",
            method="no voice profile matches found",
            unmapped_speakers=unmapped,
        )

    confidence = "confirmed" if len(unmapped) == 0 else "guessed"
    return SpeakerMapping(
        speaker_map=speaker_map,
        confidence=confidence,
        method=f"voice profile matching ({len(speaker_map)}/{len(remote_speakers)} matched)",
        unmapped_speakers=unmapped,
    )
