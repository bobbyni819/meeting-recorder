"""Import a Teams/Zoom caption transcript as the authoritative record.

Teams' own meeting transcript (the "Start transcription" button → downloaded
``.vtt``) has two things our pipeline can't match on the Gemini path: real
per-speaker names (from Teams' identity) and high accuracy. This parses that
VTT into the canonical TranscriptSegment list so it can be saved through the
normal ``save_all_formats`` path — identical transcript.json/.txt/.srt schema,
just better content and named speakers.

VTT cue blocks look like::

    00:00:03.713 --> 00:00:08.211
    <v Faye Guo>And I have been working on the
    multi-agent project for a year,</v>

Speaker names come from the ``<v Name>...</v>`` voice tag. Cues can be out of
chronological order (overlapping speakers), so segments are sorted by start.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)

# "00:00:03.713", "00:00:03,713", or "01:02.345" (HH optional).
# Captures the two timestamps.
_CUE_TIMING_RE = re.compile(
    r"(\d{1,2}:)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*"
    r"(\d{1,2}:)?(\d{1,2}):(\d{2})[.,](\d{3})"
)
# <v Speaker Name>text</v>  (closing tag optional; name optional)
_VOICE_RE = re.compile(r"<v\s+([^>]*)>(.*?)(?:</v>)?$", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_ZOOM_SPEAKER_RE = re.compile(r"^\s*([A-Z][\w .,'-]{0,40}?):\s+(.*)$", re.DOTALL)
_ZOOM_CAPTION_FILENAMES = {
    "closed_caption.txt",
    "meeting_saved_closed_captions.txt",
}


def _ts_to_seconds(hh: str, mm: str, ss: str, mmm: str) -> float:
    hours = int(hh.rstrip(":")) if hh else 0
    return hours * 3600 + int(mm) * 60 + int(ss) + int(mmm) / 1000.0


def parse_vtt(path: Path) -> list[TranscriptSegment]:
    """Parse a VTT/SRT caption file into TranscriptSegments (sorted by start).

    Consecutive cues from the same speaker with no gap are merged so the
    output reads like turns rather than caption fragments. Never raises on
    malformed cues — they are skipped.
    """
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    # Split into blocks on blank lines; each cue is one block.
    blocks = re.split(r"\r?\n\r?\n", text)
    raw: list[TranscriptSegment] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        timing = None
        timing_idx = -1
        for i, ln in enumerate(lines):
            m = _CUE_TIMING_RE.search(ln)
            if m:
                timing = m
                timing_idx = i
                break
        if timing is None:
            continue  # header (WEBVTT), NOTE, or a stray block
        start = _ts_to_seconds(timing.group(1), timing.group(2),
                               timing.group(3), timing.group(4))
        end = _ts_to_seconds(timing.group(5), timing.group(6),
                             timing.group(7), timing.group(8))
        payload = " ".join(lines[timing_idx + 1:]).strip()
        if not payload:
            continue
        speaker = ""
        # A cue with more than one <v> span has overlapping speakers; don't
        # confidently attribute all of it to the first one — leave unnamed.
        multi_voice = payload.lower().count("<v ") > 1
        vm = _VOICE_RE.match(payload)
        if vm:
            speaker = "" if multi_voice else vm.group(1).strip()
            payload = vm.group(2)
        # Strip any remaining tags and collapse whitespace.
        body = _TAG_RE.sub("", payload)
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        if not speaker:
            speaker, body = _extract_zoom_speaker(body)
        raw.append(TranscriptSegment(
            start=start, end=max(end, start), text=body, speaker=speaker,
        ))

    raw.sort(key=lambda s: (s.start, s.end))
    return _merge_consecutive(raw)


def _extract_zoom_speaker(body: str) -> tuple[str, str]:
    """Split Zoom's inline ``Name: caption`` convention when it is name-like."""
    match = _ZOOM_SPEAKER_RE.match(body)
    if not match:
        return "", body
    speaker = match.group(1).strip()
    text = match.group(2).strip()
    if not text or not _looks_like_speaker_name(speaker):
        return "", body
    return speaker, text


def _looks_like_speaker_name(speaker: str) -> bool:
    """Keep Zoom speaker detection conservative so unlabeled captions stay empty."""
    if len(speaker) > 41:
        return False
    words = [w for w in re.split(r"\s+", speaker) if w]
    if not words or len(words) > 6:
        return False
    if any(not re.match(r"^[A-Z]", word.lstrip(".,'-")) for word in words):
        return False
    return bool(re.fullmatch(r"[A-Z][\w .,'-]{0,40}", speaker))


def _merge_consecutive(
    segments: list[TranscriptSegment], max_gap: float = 1.0,
) -> list[TranscriptSegment]:
    """Merge adjacent same-speaker cues separated by <= max_gap seconds."""
    if not segments:
        return []
    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if (
            seg.speaker == prev.speaker
            and seg.start - prev.end <= max_gap
            and seg.start >= prev.start
        ):
            prev.end = max(prev.end, seg.end)
            prev.text = f"{prev.text} {seg.text}".strip()
        else:
            merged.append(seg)
    return merged


def find_zoom_caption_files(zoom_dir: Path | None = None) -> list[Path]:
    """Find local Zoom caption files newest-first.

    Zoom stores meeting artifacts under ``~/Documents/Zoom`` by default. Missing
    or unreadable directories are treated as no results.
    """
    root = Path.home() / "Documents" / "Zoom" if zoom_dir is None else Path(zoom_dir)
    root = root.expanduser()
    if not root.is_dir():
        return []

    matches: list[Path] = []
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            if name in _ZOOM_CAPTION_FILENAMES or path.suffix.lower() == ".vtt":
                matches.append(path)
    except OSError:
        logger.debug("Could not scan Zoom caption directory: %s", root, exc_info=True)
        return []

    return sorted(matches, key=lambda p: _mtime(p), reverse=True)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def import_vtt_to_recording(
    recording_dir: Path,
    vtt_path: Path,
    formats: list[str] | None = None,
) -> dict:
    """Replace a recording's transcript with a parsed Teams/Zoom VTT.

    Writes transcript.json/.txt/.srt (canonical schema), preserves the raw
    VTT alongside as teams_transcript.vtt, and updates metadata (speaker
    count, segment count, a transcription_source marker). Re-indexes for
    search. Returns a small summary dict.
    """
    from meeting_recorder.storage.metadata import RecordingMetadata
    from meeting_recorder.storage.transcript_formatter import save_all_formats

    recording_dir = Path(recording_dir)
    segments = parse_vtt(Path(vtt_path))
    if not segments:
        raise ValueError(f"No usable cues parsed from {vtt_path}")

    save_all_formats(segments, recording_dir, formats=formats)

    # Keep the original VTT next to the recording for provenance.
    try:
        dest = recording_dir / "teams_transcript.vtt"
        if Path(vtt_path).resolve() != dest.resolve():
            dest.write_text(
                Path(vtt_path).read_text(encoding="utf-8-sig", errors="replace"),
                encoding="utf-8",
            )
    except OSError:
        logger.debug("Could not copy VTT into recording dir", exc_info=True)

    speakers = sorted({s.speaker for s in segments if s.speaker})
    try:
        metadata = RecordingMetadata.load(recording_dir)
    except FileNotFoundError:
        metadata = RecordingMetadata()
    metadata.has_transcript = True
    metadata.speaker_count = len(speakers)
    metadata.segment_count = len(segments)
    metadata.transcription_source = "teams_vtt"
    if metadata.status not in ("completed",):
        metadata.status = "completed"
    metadata.save(recording_dir)

    try:
        from meeting_recorder.search.index import RecordingIndex

        index = RecordingIndex()
        index.index_recording(recording_dir)
        index.close()
    except Exception:
        logger.debug("Re-index after VTT import failed (non-fatal)", exc_info=True)

    logger.info(
        "Imported VTT into %s: %d segments, speakers: %s",
        recording_dir.name, len(segments), ", ".join(speakers) or "(none)",
    )
    return {
        "segments": len(segments),
        "speakers": speakers,
        "duration": segments[-1].end if segments else 0.0,
    }


def import_zoom_caption_to_recording(
    recording_dir: Path,
    caption_path: Path,
    formats: list[str] | None = None,
) -> dict:
    """Replace a recording's transcript with parsed Zoom local captions.

    Writes transcript.json/.txt/.srt (canonical schema), preserves the raw
    caption file alongside as zoom_caption.txt, and updates metadata with
    transcription_source="zoom_caption". Re-indexes for search.
    """
    from meeting_recorder.storage.metadata import RecordingMetadata
    from meeting_recorder.storage.transcript_formatter import save_all_formats

    recording_dir = Path(recording_dir)
    segments = parse_vtt(Path(caption_path))
    if not segments:
        raise ValueError(f"No usable cues parsed from {caption_path}")

    save_all_formats(segments, recording_dir, formats=formats)

    try:
        dest = recording_dir / "zoom_caption.txt"
        if Path(caption_path).resolve() != dest.resolve():
            dest.write_text(
                Path(caption_path).read_text(encoding="utf-8-sig", errors="replace"),
                encoding="utf-8",
            )
    except OSError:
        logger.debug("Could not copy Zoom caption into recording dir", exc_info=True)

    speakers = sorted({s.speaker for s in segments if s.speaker})
    try:
        metadata = RecordingMetadata.load(recording_dir)
    except FileNotFoundError:
        metadata = RecordingMetadata()
    metadata.has_transcript = True
    metadata.speaker_count = len(speakers)
    metadata.segment_count = len(segments)
    metadata.transcription_source = "zoom_caption"
    if metadata.status not in ("completed",):
        metadata.status = "completed"
    metadata.save(recording_dir)

    try:
        from meeting_recorder.search.index import RecordingIndex

        index = RecordingIndex()
        index.index_recording(recording_dir)
        index.close()
    except Exception:
        logger.debug(
            "Re-index after Zoom caption import failed (non-fatal)", exc_info=True
        )

    logger.info(
        "Imported Zoom caption into %s: %d segments, speakers: %s",
        recording_dir.name, len(segments), ", ".join(speakers) or "(none)",
    )
    return {
        "segments": len(segments),
        "speakers": speakers,
        "duration": segments[-1].end if segments else 0.0,
    }
