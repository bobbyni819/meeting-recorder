"""Manages recording directory structure and file organization."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class RecordingStore:
    """Manages the recording output directory structure.

    Each recording gets a timestamped directory:
    {base_dir}/{YYYY-MM-DD_HH-MM-SS}_{app_name}/
    """

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def create_recording_dir(self, app_name: str = "Meeting") -> Path:
        """Create a new recording directory with timestamp.

        Args:
            app_name: Name of the meeting app (e.g., "Zoom", "Teams").

        Returns:
            Path to the created directory.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Sanitize app name
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in app_name)
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
