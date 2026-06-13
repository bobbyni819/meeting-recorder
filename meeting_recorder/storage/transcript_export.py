"""Export transcripts in multiple formats from recording directories.

Reads transcript.json and produces SRT, TXT, or VTT output without
requiring TranscriptSegment objects.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def export_transcript(rec_path: Path, fmt: str = "txt") -> str:
    """Export transcript in the given format.

    Args:
        rec_path: Recording directory.
        fmt: Output format ("txt", "srt", "vtt").

    Returns:
        Formatted transcript string, or empty string on error.
    """
    transcript_json = rec_path / "transcript.json"
    if not transcript_json.exists():
        # Fall back to plain transcript.txt
        txt = rec_path / "transcript.txt"
        if txt.exists():
            return txt.read_text(encoding="utf-8")
        return ""

    try:
        with open(transcript_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""

    segments = data.get("segments") or []
    if not segments:
        return ""

    if fmt == "srt":
        return _to_srt(segments)
    elif fmt == "vtt":
        return _to_vtt(segments)
    else:
        return _to_txt(segments)


def _to_txt(segments: list[dict]) -> str:
    """Format as timestamped plain text."""
    lines = []
    for seg in segments:
        start = _fmt_ts(seg.get("start", 0))
        end = _fmt_ts(seg.get("end", 0))
        speaker = seg.get("speaker", "")
        text = seg.get("text", "")
        if speaker:
            lines.append(f"[{start} - {end}] {speaker}: {text}")
        else:
            lines.append(f"[{start} - {end}] {text}")
    return "\n".join(lines)


def _to_srt(segments: list[dict]) -> str:
    """Format as SRT subtitles."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _fmt_srt_ts(seg.get("start", 0))
        end = _fmt_srt_ts(seg.get("end", 0))
        speaker = seg.get("speaker", "")
        text = seg.get("text", "")
        prefix = f"[{speaker}] " if speaker else ""
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(f"{prefix}{text}")
        lines.append("")
    return "\n".join(lines)


def _to_vtt(segments: list[dict]) -> str:
    """Format as WebVTT subtitles."""
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _fmt_vtt_ts(seg.get("start", 0))
        end = _fmt_vtt_ts(seg.get("end", 0))
        speaker = seg.get("speaker", "")
        text = seg.get("text", "")
        prefix = f"<v {speaker}>" if speaker else ""
        lines.append(f"{start} --> {end}")
        lines.append(f"{prefix}{text}")
        lines.append("")
    return "\n".join(lines)


def _fmt_ts(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_srt_ts(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_ts(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
