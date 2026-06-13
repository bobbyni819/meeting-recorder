"""Import a Teams/Zoom caption transcript as the authoritative record.

Teams' own meeting transcript (the "Start transcription" button → downloaded
``.vtt`` or ``.docx``) has two things our pipeline can't match on the Gemini
path: real per-speaker names (from Teams' identity) and high accuracy. This
parses the transcript into the canonical TranscriptSegment list so it can be
saved through the normal ``save_all_formats`` path — identical
transcript.json/.txt/.srt schema, just better content and named speakers.

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
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

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
_DOCX_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_SUBJECT_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_?")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LEADING_TIMESTAMP_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:_(\d{2})-(\d{2})-(\d{2})| "
    r"(\d{2})\.(\d{2})\.(\d{2}))"
)
_WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


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


def _extract_docx_paragraphs(path: Path) -> list[str]:
    """Extract non-empty Word paragraphs from a DOCX without external deps."""
    try:
        with zipfile.ZipFile(Path(path)) as docx:
            document = docx.read("word/document.xml")
        root = ET.fromstring(document)
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", _WORD_NS):
            text = "".join(
                node.text or "" for node in paragraph.findall(".//w:t", _WORD_NS)
            ).strip()
            if text:
                paragraphs.append(text)
        return paragraphs
    except Exception:
        logger.debug("Could not extract DOCX paragraphs from %s", path, exc_info=True)
        return []


def _timestamp_to_seconds(value: str) -> float:
    """Convert Teams DOCX timestamps like M:SS or HH:MM:SS to seconds."""
    parts = [int(part) for part in value.strip().split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours * 3600 + minutes * 60 + seconds)
    raise ValueError(f"Unsupported timestamp: {value}")


def parse_teams_docx(path: Path) -> list[TranscriptSegment]:
    """Parse a Teams downloaded DOCX transcript into canonical segments.

    Teams DOCX exports repeat paragraphs as ``speaker``, ``timestamp``, then
    one or more spoken-text paragraphs. Malformed groups are skipped.
    """
    paragraphs = _extract_docx_paragraphs(path)
    if not paragraphs:
        return []

    timestamp_indexes = [
        idx for idx, text in enumerate(paragraphs)
        if _DOCX_TIMESTAMP_RE.fullmatch(text.strip())
    ]
    raw: list[TranscriptSegment] = []
    for pos, idx in enumerate(timestamp_indexes):
        timestamp = paragraphs[idx].strip()
        try:
            start = _timestamp_to_seconds(timestamp)
        except ValueError:
            continue

        speaker = ""
        if idx > 0 and not _DOCX_TIMESTAMP_RE.fullmatch(paragraphs[idx - 1].strip()):
            candidate = paragraphs[idx - 1].strip()
            if _looks_like_speaker_name(candidate):
                speaker = candidate

        next_idx = (
            timestamp_indexes[pos + 1]
            if pos + 1 < len(timestamp_indexes)
            else len(paragraphs)
        )
        text_end = next_idx - 1 if next_idx < len(paragraphs) else next_idx
        text_parts: list[str] = []
        for part in paragraphs[idx + 1:text_end]:
            stripped = part.strip()
            if stripped:
                text_parts.append(stripped)
        body = re.sub(r"\s+", " ", " ".join(text_parts)).strip()
        if not body:
            continue
        raw.append(
            TranscriptSegment(start=start, end=start, text=body, speaker=speaker)
        )

    if not raw:
        return []
    raw.sort(key=lambda s: (s.start, s.end))
    for i, seg in enumerate(raw):
        if i + 1 < len(raw):
            seg.end = max(raw[i + 1].start, seg.start)
        else:
            seg.end = seg.start + 2.0
    return _merge_consecutive(raw)


def parse_transcript_file(path: Path) -> list[TranscriptSegment]:
    """Parse a Teams transcript file, dispatching DOCX separately from VTT."""
    source = Path(path)
    if source.suffix.lower() == ".docx":
        return parse_teams_docx(source)
    return parse_vtt(source)


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


def _parse_leading_timestamp(name: str) -> datetime | None:
    """Parse recording/Zoom folder timestamps anchored at the start of a name."""
    match = _LEADING_TIMESTAMP_RE.match(name)
    if not match:
        return None
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    if match.group(4) is not None:
        hour, minute, second = (int(match.group(i)) for i in (4, 5, 6))
    else:
        hour, minute, second = (int(match.group(i)) for i in (7, 8, 9))
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def find_zoom_caption_for_recording(
    recording_dir: Path,
    zoom_dir: Path | None = None,
    max_skew_minutes: float = 90.0,
) -> Path | None:
    """Find the Zoom caption whose meeting time best matches a recording."""
    try:
        recording_dir = Path(recording_dir)
        recording_start = _recording_start_time(recording_dir)
        if recording_start is None:
            return None

        max_skew_seconds = float(max_skew_minutes) * 60.0
        best_path: Path | None = None
        best_skew: float | None = None
        for caption in find_zoom_caption_files(zoom_dir):
            caption_time = _parse_leading_timestamp(caption.parent.name)
            if caption_time is None:
                caption_time = datetime.fromtimestamp(_mtime(caption))
            skew = abs((caption_time - recording_start).total_seconds())
            if skew <= max_skew_seconds and (best_skew is None or skew < best_skew):
                best_path = caption
                best_skew = skew
        return best_path
    except Exception:
        logger.debug("Could not match Zoom caption by recording time", exc_info=True)
        return None


def find_teams_transcript_files(downloads_dir: Path | None = None) -> list[Path]:
    """Find Teams transcript downloads in ``~/Downloads`` newest-first."""
    root = Path.home() / "Downloads" if downloads_dir is None else Path(downloads_dir)
    root = root.expanduser()
    if not root.is_dir():
        return []

    try:
        matches = [
            path for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in {".vtt", ".docx"}
        ]
    except OSError:
        logger.debug("Could not scan Teams transcript directory: %s", root, exc_info=True)
        return []

    return sorted(matches, key=lambda p: _mtime(p), reverse=True)


def find_teams_transcript_for_recording(
    recording_dir: Path,
    downloads_dir: Path | None = None,
    max_age_hours: float = 72.0,
) -> Path | None:
    """Find the best Teams transcript for a recording.

    Candidates are ``.vtt`` and ``.docx`` files in ``~/Downloads``. The score is
    the number of token overlaps between the candidate filename and recording
    subject plus a recency bonus from 0..1 for files modified within
    ``max_age_hours`` of the recording start. The recording subject comes from
    ``metadata.meeting_subject`` when present; otherwise it is derived from the
    recording directory name by removing the leading timestamp and Teams/Zoom
    suffixes. A candidate must have at least one overlapping token or fall
    within the recency window.
    """
    try:
        recording_dir = Path(recording_dir)
        subject = _recording_subject_for_matching(recording_dir)
        subject_tokens = _match_tokens(subject)
        recording_start = _recording_start_time(recording_dir)
        window_seconds = max(float(max_age_hours), 0.0) * 3600.0

        best_path: Path | None = None
        best_score: tuple[float, float, float] | None = None
        for candidate in find_teams_transcript_files(downloads_dir):
            overlap = len(_match_tokens(candidate.stem) & subject_tokens)
            modified = datetime.fromtimestamp(_mtime(candidate))
            skew = (
                abs((modified - recording_start).total_seconds())
                if recording_start is not None else None
            )
            recency_bonus = 0.0
            within_window = False
            if skew is not None and window_seconds > 0 and skew <= window_seconds:
                within_window = True
                recency_bonus = 1.0 - (skew / window_seconds)
            if overlap < 1 and not within_window:
                continue
            score = (overlap + recency_bonus, overlap, _mtime(candidate))
            if best_score is None or score > best_score:
                best_path = candidate
                best_score = score
        return best_path
    except Exception:
        logger.debug("Could not match Teams transcript", exc_info=True)
        return None


def _recording_subject_for_matching(recording_dir: Path) -> str:
    try:
        from meeting_recorder.storage.metadata import RecordingMetadata

        metadata = RecordingMetadata.load(recording_dir)
        subject = metadata.meeting_subject.strip() if metadata.meeting_subject else ""
        if subject:
            return subject
    except Exception:
        logger.debug(
            "Could not load recording metadata for Teams transcript matching: %s",
            recording_dir,
            exc_info=True,
        )
    return _derive_subject_from_recording_dir(recording_dir.name)


def _derive_subject_from_recording_dir(name: str) -> str:
    subject = _SUBJECT_PREFIX_RE.sub("", name)
    subject = re.sub(
        r"(?:_+Microsoft_+Teams|_+Zoom_+Meeting)$",
        "",
        subject,
        flags=re.IGNORECASE,
    )
    subject = subject.replace("_", " ")
    return re.sub(r"\s+", " ", subject).strip()


def _match_tokens(value: str) -> set[str]:
    stopwords = {
        "caption",
        "captions",
        "closed",
        "docx",
        "microsoft",
        "meeting",
        "teams",
        "transcript",
        "vtt",
        "zoom",
    }
    return {
        token for token in _TOKEN_RE.findall(value.lower())
        if len(token) >= 3 and token not in stopwords
    }


def _recording_start_time(recording_dir: Path) -> datetime | None:
    try:
        from meeting_recorder.storage.metadata import RecordingMetadata

        metadata = RecordingMetadata.load(recording_dir)
        start_time = metadata.start_time.strip() if metadata.start_time else ""
        if start_time:
            parsed = _parse_iso_datetime(start_time)
            if parsed is not None:
                return parsed
    except Exception:
        logger.debug(
            "Could not load recording metadata for Zoom caption matching: %s",
            recording_dir,
            exc_info=True,
        )
    return _parse_leading_timestamp(recording_dir.name)


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _backup_existing_transcript(recording_dir: Path) -> bool:
    """Preserve the first generated transcript before an import overwrites it."""
    try:
        recording_dir = Path(recording_dir)
        transcript_json = recording_dir / "transcript.json"
        original_json = recording_dir / "transcript.original.json"
        if not transcript_json.exists() or original_json.exists():
            return False

        for suffix in ("json", "txt", "srt"):
            source = recording_dir / f"transcript.{suffix}"
            if source.exists():
                shutil.copy2(source, recording_dir / f"transcript.original.{suffix}")
        return True
    except Exception:
        logger.debug(
            "Could not back up existing transcript in %s",
            recording_dir,
            exc_info=True,
        )
        return False


def import_vtt_to_recording(
    recording_dir: Path,
    vtt_path: Path,
    formats: list[str] | None = None,
) -> dict:
    """Replace a recording's transcript with a parsed Teams transcript file.

    Writes transcript.json/.txt/.srt (canonical schema), preserves the source
    alongside as teams_transcript.vtt/.docx, and updates metadata (speaker
    count, segment count, a transcription_source marker). Re-indexes for
    search. Returns a small summary dict.
    """
    from meeting_recorder.storage.metadata import RecordingMetadata
    from meeting_recorder.storage.transcript_formatter import save_all_formats

    recording_dir = Path(recording_dir)
    source_path = Path(vtt_path)
    backed_up_original = _backup_existing_transcript(recording_dir)
    segments = parse_transcript_file(source_path)
    if not segments:
        raise ValueError(f"No usable cues parsed from {vtt_path}")

    save_all_formats(segments, recording_dir, formats=formats)

    # Keep the original transcript next to the recording for provenance.
    try:
        source_suffix = ".docx" if source_path.suffix.lower() == ".docx" else ".vtt"
        dest = recording_dir / f"teams_transcript{source_suffix}"
        if source_path.resolve() != dest.resolve():
            if source_suffix == ".docx":
                shutil.copy2(source_path, dest)
            else:
                dest.write_text(
                    source_path.read_text(encoding="utf-8-sig", errors="replace"),
                    encoding="utf-8",
                )
    except OSError:
        logger.debug("Could not copy transcript into recording dir", exc_info=True)

    speakers = sorted({s.speaker for s in segments if s.speaker})
    try:
        metadata = RecordingMetadata.load(recording_dir)
    except FileNotFoundError:
        metadata = RecordingMetadata()
    metadata.has_transcript = True
    metadata.speaker_count = len(speakers)
    metadata.segment_count = len(segments)
    metadata.transcription_source = (
        "teams_docx" if source_path.suffix.lower() == ".docx" else "teams_vtt"
    )
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
        "Imported transcript into %s: %d segments, speakers: %s",
        recording_dir.name, len(segments), ", ".join(speakers) or "(none)",
    )
    return {
        "segments": len(segments),
        "speakers": speakers,
        "duration": segments[-1].end if segments else 0.0,
        "backed_up_original": backed_up_original,
        "original_backup": (
            "transcript.original.json" if backed_up_original else None
        ),
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
    backed_up_original = _backup_existing_transcript(recording_dir)
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
        "backed_up_original": backed_up_original,
        "original_backup": (
            "transcript.original.json" if backed_up_original else None
        ),
    }
