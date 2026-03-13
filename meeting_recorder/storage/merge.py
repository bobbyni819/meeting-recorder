"""Merge multiple recordings into a single combined transcript."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def merge_transcripts(
    recording_dirs: list[Path],
    output_dir: Path,
) -> Path:
    """Merge transcripts from multiple recordings into a single output.

    Creates a new directory with combined transcript.txt, summary.md,
    and metadata.json. Audio files are NOT merged (only text content).

    Args:
        recording_dirs: List of recording directories to merge, in order.
        output_dir: Base directory to create the merged recording in.

    Returns:
        Path to the new merged recording directory.

    Raises:
        ValueError: If fewer than 2 recordings provided.
    """
    if len(recording_dirs) < 2:
        raise ValueError("Need at least 2 recordings to merge")

    # Sort by folder name (chronological)
    sorted_dirs = sorted(recording_dirs, key=lambda p: p.name)

    # Determine merged recording name
    first_name = sorted_dirs[0].name
    last_name = sorted_dirs[-1].name
    date_str = first_name[:10] if len(first_name) >= 10 else "merged"

    # Try to extract subject from first recording
    first_meta = _load_meta(sorted_dirs[0])
    subject = first_meta.get("meeting_subject", "")
    if not subject and len(first_name) > 20:
        subject = first_name[20:].replace("_", " ").strip()
    merged_name = f"{date_str}_merged_{subject.replace(' ', '_')}" if subject else f"{date_str}_merged"

    # Create output directory
    merged_dir = output_dir / merged_name
    merged_dir.mkdir(parents=True, exist_ok=True)

    # Merge transcripts
    combined_transcript = _merge_text_files(sorted_dirs, "transcript.txt")
    if combined_transcript:
        (merged_dir / "transcript.txt").write_text(
            combined_transcript, encoding="utf-8")

    # Merge summaries
    combined_summary = _merge_summaries(sorted_dirs)
    if combined_summary:
        (merged_dir / "summary.md").write_text(
            combined_summary, encoding="utf-8")

    # Merge notes
    combined_notes = _merge_text_files(sorted_dirs, "notes.md")
    if combined_notes:
        (merged_dir / "notes.md").write_text(
            combined_notes, encoding="utf-8")

    # Merge transcript.json (if all have it)
    combined_json = _merge_transcript_json(sorted_dirs)
    if combined_json:
        with open(merged_dir / "transcript.json", "w", encoding="utf-8") as f:
            json.dump(combined_json, f, indent=2, ensure_ascii=False)

    # Build merged metadata
    merged_meta = _build_merged_metadata(sorted_dirs, merged_dir)
    with open(merged_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(merged_meta, f, indent=2, ensure_ascii=False)

    logger.info("Merged %d recordings into %s", len(sorted_dirs), merged_dir)
    return merged_dir


def _load_meta(rec_path: Path) -> dict:
    """Load metadata from a recording directory."""
    meta_path = rec_path / "metadata.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _merge_text_files(dirs: list[Path], filename: str) -> str:
    """Merge text files with section headers between them."""
    parts: list[str] = []
    for d in dirs:
        fpath = d / filename
        if fpath.exists():
            try:
                text = fpath.read_text(encoding="utf-8").strip()
                if text:
                    # Add a header with the recording name
                    name = d.name
                    date = name[:10] if len(name) >= 10 else name
                    time = name[11:19].replace("-", ":") if len(name) >= 19 else ""
                    header = f"--- {date} {time} ---"
                    parts.append(f"{header}\n\n{text}")
            except Exception:
                logger.warning("Failed to read %s from %s", filename, d)
    return "\n\n".join(parts)


def _merge_summaries(dirs: list[Path]) -> str:
    """Merge summary files with clear section separation."""
    parts: list[str] = []
    for d in dirs:
        fpath = d / "summary.md"
        if fpath.exists():
            try:
                text = fpath.read_text(encoding="utf-8").strip()
                if text:
                    meta = _load_meta(d)
                    subject = meta.get("meeting_subject", d.name)
                    parts.append(f"## Part: {subject}\n\n{text}")
            except Exception:
                pass
    if not parts:
        return ""
    return "# Combined Summary\n\n" + "\n\n---\n\n".join(parts)


def _merge_transcript_json(dirs: list[Path]) -> dict | None:
    """Merge transcript.json files, adjusting timestamps."""
    all_segments: list[dict] = []
    time_offset = 0.0

    for d in dirs:
        json_path = d / "transcript.json"
        if not json_path.exists():
            return None  # Only merge JSON if ALL have it

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None

        segments = data.get("segments", [])
        max_end = 0.0
        for seg in segments:
            adjusted = dict(seg)
            adjusted["start"] = seg.get("start", 0.0) + time_offset
            adjusted["end"] = seg.get("end", 0.0) + time_offset
            max_end = max(max_end, adjusted["end"])
            # Tag with source recording
            adjusted["source"] = d.name
            all_segments.append(adjusted)

        # Add a small gap between recordings
        time_offset = max_end + 2.0

    return {"segments": all_segments}


def _build_merged_metadata(dirs: list[Path], merged_dir: Path) -> dict:
    """Build metadata for the merged recording."""
    all_meta = [_load_meta(d) for d in dirs]

    # Total duration
    total_duration = sum(m.get("duration_seconds", 0) for m in all_meta)

    # Collect unique attendees
    all_attendees: list[str] = []
    seen_attendees: set[str] = set()
    for m in all_meta:
        for att in m.get("meeting_attendees", []):
            if att.lower() not in seen_attendees:
                all_attendees.append(att)
                seen_attendees.add(att.lower())

    # Total speakers
    total_speakers = max((m.get("speaker_count", 0) for m in all_meta), default=0)

    # Merge tags
    all_tags: list[str] = []
    seen_tags: set[str] = set()
    for m in all_meta:
        for tag in m.get("tags", []):
            if tag.lower() not in seen_tags:
                all_tags.append(tag)
                seen_tags.add(tag.lower())
    all_tags.append("merged")

    # Subject from first recording
    subject = all_meta[0].get("meeting_subject", "") if all_meta else ""
    app_name = all_meta[0].get("app_name", "") if all_meta else ""
    organizer = all_meta[0].get("meeting_organizer", "") if all_meta else ""

    # Source recordings list
    sources = [d.name for d in dirs]

    return {
        "meeting_subject": subject,
        "app_name": app_name,
        "status": "completed",
        "duration_seconds": total_duration,
        "speaker_count": total_speakers,
        "meeting_attendees": all_attendees,
        "meeting_organizer": organizer,
        "tags": all_tags,
        "merged_from": sources,
        "has_summary": bool((merged_dir / "summary.md").exists()),
    }
