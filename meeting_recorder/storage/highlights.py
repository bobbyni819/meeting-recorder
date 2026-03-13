"""Manage transcript highlights and annotations.

Stores user-highlighted passages from transcripts with optional notes.
Persists as highlights.json in the recording directory.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Highlight:
    """A single highlighted passage in a transcript."""
    text: str  # The highlighted text
    start_offset: int  # Character offset in transcript
    end_offset: int  # End character offset
    note: str = ""  # Optional user note
    color: str = "yellow"  # Highlight color name
    created_at: str = ""  # ISO timestamp

    def to_dict(self) -> dict:
        return asdict(self)


class HighlightStore:
    """Manages highlights for a single recording."""

    def __init__(self, rec_path: Path):
        self._path = rec_path / "highlights.json"
        self._highlights: list[Highlight] = []
        self._load()

    def _load(self) -> None:
        """Load highlights from disk."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._highlights = [Highlight(**h) for h in data]
            except Exception:
                logger.warning("Failed to load highlights from %s", self._path)
                self._highlights = []

    def _save(self) -> None:
        """Save highlights to disk."""
        try:
            data = [h.to_dict() for h in self._highlights]
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to save highlights to %s", self._path)

    @property
    def highlights(self) -> list[Highlight]:
        return list(self._highlights)

    def add(
        self,
        text: str,
        start_offset: int,
        end_offset: int,
        note: str = "",
        color: str = "yellow",
    ) -> Highlight:
        """Add a new highlight."""
        from datetime import datetime
        h = Highlight(
            text=text,
            start_offset=start_offset,
            end_offset=end_offset,
            note=note,
            color=color,
            created_at=datetime.now().isoformat(),
        )
        self._highlights.append(h)
        self._save()
        return h

    def remove(self, index: int) -> bool:
        """Remove a highlight by index."""
        if 0 <= index < len(self._highlights):
            self._highlights.pop(index)
            self._save()
            return True
        return False

    def clear(self) -> None:
        """Remove all highlights."""
        self._highlights.clear()
        self._save()

    def update_note(self, index: int, note: str) -> bool:
        """Update the note on a highlight."""
        if 0 <= index < len(self._highlights):
            self._highlights[index].note = note
            self._save()
            return True
        return False

    def format_highlights(self) -> str:
        """Format all highlights as readable text for clipboard."""
        if not self._highlights:
            return ""
        lines: list[str] = ["HIGHLIGHTS", "=" * 40, ""]
        for i, h in enumerate(self._highlights, 1):
            lines.append(f'{i}. "{h.text}"')
            if h.note:
                lines.append(f"   Note: {h.note}")
            lines.append("")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._highlights)
