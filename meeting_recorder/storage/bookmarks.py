"""Recording bookmarks.

Save named timestamps within a recording for quick reference.
Bookmarks are stored as bookmarks.json in the recording directory.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Bookmark:
    """A named timestamp within a recording."""
    timestamp: float  # seconds from start
    label: str
    color: str = "blue"  # blue, green, amber, red


class BookmarkStore:
    """Manages bookmarks for a single recording."""

    def __init__(self, rec_path: Path):
        self._path = rec_path / "bookmarks.json"
        self._bookmarks: list[Bookmark] = []
        self._load()

    def _load(self) -> None:
        """Load bookmarks from disk."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._bookmarks = [
                    Bookmark(
                        timestamp=b.get("timestamp", 0),
                        label=b.get("label", ""),
                        color=b.get("color", "blue"),
                    )
                    for b in data
                ]
            except Exception:
                logger.debug("Failed to load bookmarks from %s", self._path)
                self._bookmarks = []

    def _save(self) -> None:
        """Save bookmarks to disk."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump([asdict(b) for b in self._bookmarks], f, indent=2)
        except Exception:
            logger.exception("Failed to save bookmarks to %s", self._path)

    @property
    def bookmarks(self) -> list[Bookmark]:
        """Return bookmarks sorted by timestamp."""
        return sorted(self._bookmarks, key=lambda b: b.timestamp)

    def __len__(self) -> int:
        return len(self._bookmarks)

    def add(self, timestamp: float, label: str, color: str = "blue") -> Bookmark:
        """Add a bookmark at the given timestamp.

        Args:
            timestamp: Seconds from recording start.
            label: Description of what's happening at this point.
            color: Color tag (blue, green, amber, red).

        Returns:
            The created Bookmark.
        """
        bookmark = Bookmark(timestamp=timestamp, label=label, color=color)
        self._bookmarks.append(bookmark)
        self._save()
        return bookmark

    def remove(self, timestamp: float) -> bool:
        """Remove a bookmark at the given timestamp.

        Returns True if a bookmark was removed.
        """
        before = len(self._bookmarks)
        self._bookmarks = [b for b in self._bookmarks if b.timestamp != timestamp]
        if len(self._bookmarks) < before:
            self._save()
            return True
        return False

    def clear(self) -> None:
        """Remove all bookmarks."""
        self._bookmarks = []
        self._save()

    def update_label(self, timestamp: float, new_label: str) -> bool:
        """Update the label of a bookmark.

        Returns True if a bookmark was updated.
        """
        for b in self._bookmarks:
            if b.timestamp == timestamp:
                b.label = new_label
                self._save()
                return True
        return False

    def format_bookmarks(self) -> str:
        """Format bookmarks as readable text."""
        if not self._bookmarks:
            return "No bookmarks."

        lines = ["BOOKMARKS", "-" * 40]
        for b in self.bookmarks:
            time_str = _format_timestamp(b.timestamp)
            lines.append(f"  [{time_str}]  {b.label}")
        return "\n".join(lines)

    def find_nearest(self, timestamp: float) -> Bookmark | None:
        """Find the bookmark nearest to the given timestamp."""
        if not self._bookmarks:
            return None
        return min(self._bookmarks, key=lambda b: abs(b.timestamp - timestamp))


def _format_timestamp(seconds: float) -> str:
    """Format seconds as H:MM:SS or MM:SS."""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
