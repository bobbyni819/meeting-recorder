"""Format transcripts as JSON, TXT, and SRT files."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)


def save_transcript_json(segments: list[TranscriptSegment], output_path: Path) -> None:
    """Save transcript as structured JSON.

    Output format:
    {
        "segments": [
            {"start": 0.0, "end": 2.5, "speaker": "User", "text": "Hello"},
            ...
        ]
    }
    """
    data = {
        "segments": [asdict(seg) for seg in segments],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Transcript JSON saved: %s", output_path)


def save_transcript_txt(segments: list[TranscriptSegment], output_path: Path) -> None:
    """Save transcript as human-readable plain text.

    Output format:
    [00:00:00 - 00:00:02] User: Hello everyone.
    [00:00:03 - 00:00:05] Participant 1: Hi there!

    When the Gemini backend was used, ``transcript_raw.txt`` contains the
    verbatim API output which often includes context (intro, narrative,
    off-format lines) that the strict segment parser drops.  In that case
    we use the raw text as ``transcript.txt`` so no information is lost.
    The structured output still lives in ``transcript.json`` / ``.srt``.
    """
    raw_path = output_path.parent / "transcript_raw.txt"
    if raw_path.exists():
        try:
            raw_text = raw_path.read_text(encoding="utf-8").strip()
            if raw_text:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(raw_text)
                    f.write("\n")
                logger.info(
                    "Transcript TXT saved (verbatim from raw): %s", output_path
                )
                return
        except Exception:
            logger.debug("Could not read transcript_raw.txt; falling back to segments", exc_info=True)

    lines = []
    for seg in segments:
        start = _format_timestamp_txt(seg.start)
        end = _format_timestamp_txt(seg.end)
        speaker = seg.speaker or "Unknown"
        lines.append(f"[{start} - {end}] {speaker}: {seg.text}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")
    logger.info("Transcript TXT saved: %s", output_path)


def save_transcript_srt(segments: list[TranscriptSegment], output_path: Path) -> None:
    """Save transcript as SRT subtitle file.

    Output format:
    1
    00:00:00,000 --> 00:00:02,500
    [User] Hello everyone.

    2
    00:00:03,000 --> 00:00:05,200
    [Participant 1] Hi there!
    """
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_timestamp_srt(seg.start)
        end = _format_timestamp_srt(seg.end)
        speaker = seg.speaker or "Unknown"
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(f"[{speaker}] {seg.text}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Transcript SRT saved: %s", output_path)


def save_all_formats(
    segments: list[TranscriptSegment],
    recording_dir: Path,
    formats: list[str] | None = None,
) -> None:
    """Save transcript in all requested formats.

    Args:
        segments: Transcript segments to save.
        recording_dir: Directory to save files in.
        formats: List of format strings ("json", "txt", "srt").
                 Defaults to all formats.
    """
    if formats is None:
        formats = ["json", "txt", "srt"]

    savers = {
        "json": (save_transcript_json, "transcript.json"),
        "txt": (save_transcript_txt, "transcript.txt"),
        "srt": (save_transcript_srt, "transcript.srt"),
    }

    for fmt in formats:
        if fmt in savers:
            saver_fn, filename = savers[fmt]
            saver_fn(segments, recording_dir / filename)
        else:
            logger.warning("Unknown transcript format: %s", fmt)


def _format_timestamp_txt(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _format_timestamp_srt(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SRT."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
