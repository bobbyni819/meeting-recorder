"""Archive old recordings to save disk space.

Compresses large audio/video files in recording directories into ZIP
archives while preserving metadata, transcripts, and summaries as-is
for quick access.
"""

from __future__ import annotations

import logging
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Files to compress (large media)
_ARCHIVE_PATTERNS = {
    "*.wav", "*.mp3", "*.m4a", "*.ogg", "*.flac",
    "*.avi", "*.mp4", "*.mkv",
}

# Files to keep uncompressed (small, frequently accessed)
_KEEP_PATTERNS = {
    "metadata.json", "transcript.json", "transcript.txt",
    "summary.md", "summary.json", "notes.md",
    "action_items.json", "thumbnail.jpg",
    "report.html",
}

ARCHIVE_FILENAME = "archived_media.zip"


def archive_recording(rec_path: Path, delete_originals: bool = True) -> int:
    """Archive large media files in a recording directory.

    Creates a ZIP file containing all audio/video files, then optionally
    deletes the originals. Metadata, transcripts, and summaries are kept
    as-is for quick access.

    Args:
        rec_path: Recording directory path.
        delete_originals: If True, delete original media files after archiving.

    Returns:
        Bytes saved (original size minus archive size). Negative if archive
        is larger (shouldn't happen with media files).
    """
    if not rec_path.is_dir():
        return 0

    archive_path = rec_path / ARCHIVE_FILENAME
    if archive_path.exists():
        logger.info("Already archived: %s", rec_path.name)
        return 0

    # Find files to archive
    to_archive: list[Path] = []
    for pattern in _ARCHIVE_PATTERNS:
        to_archive.extend(rec_path.glob(pattern))

    if not to_archive:
        return 0

    # Calculate original size
    original_size = sum(f.stat().st_size for f in to_archive)
    expected_names = {f.name for f in to_archive}

    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in to_archive:
                zf.write(fpath, fpath.name)
            zf.fp.flush()
            os.fsync(zf.fp.fileno())

        with zipfile.ZipFile(archive_path, "r") as vz:
            missing = expected_names - set(vz.namelist())
            if missing:
                raise ValueError(f"archive missing entries: {sorted(missing)}")
            bad = vz.testzip()
            if bad is not None:
                raise ValueError(f"corrupt archive member: {bad}")

        archive_size = archive_path.stat().st_size
        saved = original_size - archive_size
    except Exception:
        logger.exception("Failed to archive: %s", rec_path.name)
        # Clean up partial or corrupt archive
        if archive_path.exists():
            archive_path.unlink()
        return 0

    if delete_originals:
        for fpath in to_archive:
            fpath.unlink()
        logger.info(
            "Archived %s: %d files, saved %.1f MB",
            rec_path.name, len(to_archive), saved / (1024 * 1024),
        )

    return saved


def unarchive_recording(rec_path: Path) -> bool:
    """Restore archived media files from the ZIP archive.

    Args:
        rec_path: Recording directory path.

    Returns:
        True if files were restored, False if no archive found.
    """
    archive_path = rec_path / ARCHIVE_FILENAME
    if not archive_path.exists():
        return False

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(rec_path)

        archive_path.unlink()
        logger.info("Unarchived: %s", rec_path.name)
        return True

    except Exception:
        logger.exception("Failed to unarchive: %s", rec_path.name)
        return False


def is_archived(rec_path: Path) -> bool:
    """Check if a recording has been archived."""
    return (rec_path / ARCHIVE_FILENAME).exists()


def archive_old_recordings(
    recordings_dir: Path,
    older_than_days: int = 30,
    exclude: Path | None = None,
) -> tuple[int, int]:
    """Archive recordings older than the given age.

    Args:
        recordings_dir: Base recordings directory.
        older_than_days: Archive recordings older than this many days.
        exclude: Optional directory to exclude (e.g., active recording).

    Returns:
        Tuple of (count archived, total bytes saved).
    """
    if not recordings_dir.exists():
        return 0, 0

    cutoff = datetime.now() - timedelta(days=older_than_days)
    count = 0
    total_saved = 0

    for rec_dir in sorted(recordings_dir.iterdir()):
        if not rec_dir.is_dir():
            continue
        if exclude and rec_dir == exclude:
            continue
        if is_archived(rec_dir):
            continue

        # Parse date from folder name
        name = rec_dir.name
        if len(name) < 10:
            continue
        try:
            rec_date = datetime.strptime(name[:10], "%Y-%m-%d")
        except ValueError:
            continue

        if rec_date < cutoff:
            saved = archive_recording(rec_dir)
            if saved > 0:
                count += 1
                total_saved += saved

    logger.info(
        "Archived %d recordings, saved %.1f MB",
        count, total_saved / (1024 * 1024),
    )
    return count, total_saved


def get_archive_stats(recordings_dir: Path) -> dict:
    """Get statistics about archived vs unarchived recordings.

    Returns:
        Dict with counts and sizes.
    """
    if not recordings_dir.exists():
        return {"total": 0, "archived": 0, "unarchived": 0,
                "archive_size": 0, "unarchived_size": 0}

    total = 0
    archived = 0
    archive_size = 0
    unarchived_size = 0

    for rec_dir in recordings_dir.iterdir():
        if not rec_dir.is_dir():
            continue
        if not (rec_dir / "metadata.json").exists():
            continue

        total += 1
        if is_archived(rec_dir):
            archived += 1
            ap = rec_dir / ARCHIVE_FILENAME
            archive_size += ap.stat().st_size if ap.exists() else 0
        else:
            # Sum media file sizes
            for pattern in _ARCHIVE_PATTERNS:
                for f in rec_dir.glob(pattern):
                    unarchived_size += f.stat().st_size

    return {
        "total": total,
        "archived": archived,
        "unarchived": total - archived,
        "archive_size": archive_size,
        "unarchived_size": unarchived_size,
    }
