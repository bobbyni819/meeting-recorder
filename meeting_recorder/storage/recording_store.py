"""Manages recording directory structure and file organization."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class RecordingStore:
    """Manages the recording output directory structure.

    Each recording gets a timestamped directory:
    {base_dir}/{YYYY-MM-DD_HH-MM-SS}_{app_name}/
    """

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def create_recording_dir(self, app_name: str = "Meeting", meeting_subject: str = "") -> Path:
        """Create a new recording directory with timestamp.

        Args:
            app_name: Name of the meeting app (e.g., "Zoom", "Teams").
            meeting_subject: Optional meeting subject from calendar for descriptive naming.

        Returns:
            Path to the created directory.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Sanitize app name
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in app_name)

        if meeting_subject:
            # Use meeting subject for a descriptive folder name
            safe_subject = "".join(
                c if c.isalnum() or c in "._- " else "" for c in meeting_subject
            ).strip().replace(" ", "_")
            if len(safe_subject) > 60:
                safe_subject = safe_subject[:60].rstrip("_")
            dir_name = f"{timestamp}_{safe_subject}_{safe_name}"
        else:
            dir_name = f"{timestamp}_{safe_name}"

        recording_dir = self.base_dir / dir_name
        recording_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created recording directory: %s", recording_dir)
        return recording_dir

    def list_recordings(self) -> list[Path]:
        """List all recording directories, newest first."""
        if not self.base_dir.exists():
            return []
        dirs = [d for d in self.base_dir.iterdir() if d.is_dir()]
        dirs.sort(key=lambda d: d.name, reverse=True)
        return dirs

    def get_latest_recording(self) -> Path | None:
        """Get the most recent recording directory."""
        recordings = self.list_recordings()
        return recordings[0] if recordings else None

    def ensure_base_dir(self) -> None:
        """Create the base recordings directory if it doesn't exist."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(
        self,
        max_age_days: int = 0,
        max_total_gb: float = 0.0,
        exclude: Path | None = None,
    ) -> list[Path]:
        """Delete old recordings based on retention policy.

        Args:
            max_age_days: Delete recordings older than N days (0 = no age limit).
            max_total_gb: Delete oldest recordings when total exceeds N GB (0 = no limit).
            exclude: A recording directory to never delete (e.g. the active recording).

        Returns:
            List of directories that were deleted.
        """
        if max_age_days <= 0 and max_total_gb <= 0:
            return []

        recordings = self.list_recordings()  # newest first
        if not recordings:
            return []

        deleted: list[Path] = []

        # --- Age-based cleanup ---
        if max_age_days > 0:
            cutoff = datetime.now() - timedelta(days=max_age_days)
            for rec_dir in recordings:
                if exclude and rec_dir.resolve() == exclude.resolve():
                    continue
                dir_time = self._parse_dir_timestamp(rec_dir)
                if dir_time and dir_time < cutoff:
                    try:
                        shutil.rmtree(rec_dir)
                        deleted.append(rec_dir)
                        logger.info("Retention: deleted old recording %s (age > %d days)", rec_dir.name, max_age_days)
                    except Exception:
                        logger.exception("Retention: failed to delete %s", rec_dir.name)

        # --- Size-based cleanup (oldest first) ---
        if max_total_gb > 0:
            max_bytes = max_total_gb * (1024 ** 3)
            # Recalculate after age cleanup
            remaining = [d for d in self.list_recordings() if d not in deleted]
            total = sum(self._dir_size(d) for d in remaining)

            # Delete oldest until under budget
            for rec_dir in reversed(remaining):
                if total <= max_bytes:
                    break
                if exclude and rec_dir.resolve() == exclude.resolve():
                    continue
                dir_bytes = self._dir_size(rec_dir)
                try:
                    shutil.rmtree(rec_dir)
                    deleted.append(rec_dir)
                    total -= dir_bytes
                    logger.info(
                        "Retention: deleted %s to free %.1f MB (total now %.1f GB)",
                        rec_dir.name, dir_bytes / (1024 ** 2), total / (1024 ** 3),
                    )
                except Exception:
                    logger.exception("Retention: failed to delete %s", rec_dir.name)

        if deleted:
            logger.info("Retention cleanup: deleted %d recording(s).", len(deleted))
        return deleted

    @staticmethod
    def _parse_dir_timestamp(rec_dir: Path) -> datetime | None:
        """Extract timestamp from directory name (YYYY-MM-DD_HH-MM-SS_...)."""
        name = rec_dir.name
        # First 19 chars: "2026-03-06_14-30-00"
        if len(name) < 19:
            return None
        try:
            return datetime.strptime(name[:19], "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            return None

    @staticmethod
    def _dir_size(path: Path) -> int:
        """Calculate total size of a directory in bytes."""
        total = 0
        try:
            for f in path.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
        except Exception:
            pass
        return total
