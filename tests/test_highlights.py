"""Tests for transcript highlights."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.highlights import Highlight, HighlightStore


class TestHighlightStore:
    def test_empty_store(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        assert len(store) == 0
        assert store.highlights == []

    def test_add_highlight(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        h = store.add("important text", 100, 114)
        assert h.text == "important text"
        assert h.start_offset == 100
        assert h.end_offset == 114
        assert len(store) == 1

    def test_add_with_note(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        h = store.add("budget discussion", 50, 67, note="Key decision")
        assert h.note == "Key decision"

    def test_add_with_color(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        h = store.add("text", 0, 4, color="green")
        assert h.color == "green"

    def test_persistence(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        store.add("first", 0, 5)
        store.add("second", 10, 16, note="Important")

        # Reload from disk
        store2 = HighlightStore(tmp_path)
        assert len(store2) == 2
        assert store2.highlights[0].text == "first"
        assert store2.highlights[1].note == "Important"

    def test_remove_highlight(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        store.add("first", 0, 5)
        store.add("second", 10, 16)
        assert store.remove(0)
        assert len(store) == 1
        assert store.highlights[0].text == "second"

    def test_remove_invalid_index(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        store.add("text", 0, 4)
        assert not store.remove(5)
        assert not store.remove(-1)
        assert len(store) == 1

    def test_clear(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        store.add("a", 0, 1)
        store.add("b", 2, 3)
        store.clear()
        assert len(store) == 0
        # Check persistence
        store2 = HighlightStore(tmp_path)
        assert len(store2) == 0

    def test_update_note(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        store.add("text", 0, 4, note="old")
        assert store.update_note(0, "new")
        assert store.highlights[0].note == "new"

    def test_update_note_invalid(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        assert not store.update_note(0, "text")

    def test_format_highlights(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        store.add("first quote", 0, 11)
        store.add("second quote", 20, 32, note="Key point")
        text = store.format_highlights()
        assert "HIGHLIGHTS" in text
        assert '"first quote"' in text
        assert '"second quote"' in text
        assert "Key point" in text

    def test_format_empty(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        assert store.format_highlights() == ""

    def test_created_at_populated(self, tmp_path: Path):
        store = HighlightStore(tmp_path)
        h = store.add("text", 0, 4)
        assert h.created_at  # Should be non-empty ISO timestamp

    def test_corrupted_file(self, tmp_path: Path):
        (tmp_path / "highlights.json").write_text("bad json", encoding="utf-8")
        store = HighlightStore(tmp_path)
        assert len(store) == 0  # Graceful fallback

    def test_highlights_immutable(self, tmp_path: Path):
        """highlights property should return a copy."""
        store = HighlightStore(tmp_path)
        store.add("text", 0, 4)
        result = store.highlights
        result.clear()
        assert len(store) == 1  # Original unaffected
