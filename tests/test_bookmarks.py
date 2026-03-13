"""Tests for recording bookmarks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.bookmarks import Bookmark, BookmarkStore


class TestBookmarkStore:
    def test_empty_store(self, tmp_path):
        store = BookmarkStore(tmp_path)
        assert len(store) == 0
        assert store.bookmarks == []

    def test_add_bookmark(self, tmp_path):
        store = BookmarkStore(tmp_path)
        bm = store.add(60.0, "Introduction")
        assert bm.timestamp == 60.0
        assert bm.label == "Introduction"
        assert bm.color == "blue"
        assert len(store) == 1

    def test_add_with_color(self, tmp_path):
        store = BookmarkStore(tmp_path)
        bm = store.add(120.0, "Action item", color="red")
        assert bm.color == "red"

    def test_add_multiple(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "First")
        store.add(20.0, "Second")
        store.add(5.0, "Before first")
        assert len(store) == 3

    def test_bookmarks_sorted(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(30.0, "Third")
        store.add(10.0, "First")
        store.add(20.0, "Second")
        labels = [b.label for b in store.bookmarks]
        assert labels == ["First", "Second", "Third"]

    def test_remove_bookmark(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "Keep")
        store.add(20.0, "Remove")
        assert store.remove(20.0) is True
        assert len(store) == 1
        assert store.bookmarks[0].label == "Keep"

    def test_remove_nonexistent(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "Only")
        assert store.remove(99.0) is False
        assert len(store) == 1

    def test_clear(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "A")
        store.add(20.0, "B")
        store.clear()
        assert len(store) == 0

    def test_update_label(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "Old label")
        assert store.update_label(10.0, "New label") is True
        assert store.bookmarks[0].label == "New label"

    def test_update_label_nonexistent(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "Only")
        assert store.update_label(99.0, "Nope") is False

    def test_find_nearest(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "A")
        store.add(30.0, "B")
        store.add(60.0, "C")
        assert store.find_nearest(12.0).label == "A"
        assert store.find_nearest(28.0).label == "B"
        assert store.find_nearest(50.0).label == "C"

    def test_find_nearest_empty(self, tmp_path):
        store = BookmarkStore(tmp_path)
        assert store.find_nearest(10.0) is None

    def test_find_nearest_exact(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.0, "Exact")
        assert store.find_nearest(10.0).label == "Exact"

    def test_persistence(self, tmp_path):
        store1 = BookmarkStore(tmp_path)
        store1.add(10.0, "Persisted")
        store1.add(20.0, "Also persisted", color="green")

        store2 = BookmarkStore(tmp_path)
        assert len(store2) == 2
        assert store2.bookmarks[0].label == "Persisted"
        assert store2.bookmarks[1].color == "green"

    def test_persistence_after_remove(self, tmp_path):
        store1 = BookmarkStore(tmp_path)
        store1.add(10.0, "Keep")
        store1.add(20.0, "Delete")
        store1.remove(20.0)

        store2 = BookmarkStore(tmp_path)
        assert len(store2) == 1
        assert store2.bookmarks[0].label == "Keep"

    def test_persistence_after_clear(self, tmp_path):
        store1 = BookmarkStore(tmp_path)
        store1.add(10.0, "Gone")
        store1.clear()

        store2 = BookmarkStore(tmp_path)
        assert len(store2) == 0

    def test_json_format(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(10.5, "Test", color="amber")
        data = json.loads((tmp_path / "bookmarks.json").read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["timestamp"] == 10.5
        assert data[0]["label"] == "Test"
        assert data[0]["color"] == "amber"

    def test_corrupt_json(self, tmp_path):
        (tmp_path / "bookmarks.json").write_text("not json", encoding="utf-8")
        store = BookmarkStore(tmp_path)
        assert len(store) == 0

    def test_missing_fields_in_json(self, tmp_path):
        data = [{"timestamp": 5.0}]
        (tmp_path / "bookmarks.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        store = BookmarkStore(tmp_path)
        assert len(store) == 1
        assert store.bookmarks[0].label == ""
        assert store.bookmarks[0].color == "blue"


class TestFormatBookmarks:
    def test_empty(self, tmp_path):
        store = BookmarkStore(tmp_path)
        text = store.format_bookmarks()
        assert "No bookmarks" in text

    def test_basic_format(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(65.0, "Speaker intro")
        store.add(3661.0, "Q&A starts")
        text = store.format_bookmarks()
        assert "BOOKMARKS" in text
        assert "01:05" in text
        assert "Speaker intro" in text
        assert "1:01:01" in text
        assert "Q&A starts" in text

    def test_format_order(self, tmp_path):
        store = BookmarkStore(tmp_path)
        store.add(120.0, "Second")
        store.add(60.0, "First")
        text = store.format_bookmarks()
        lines = text.split("\n")
        first_idx = next(i for i, l in enumerate(lines) if "First" in l)
        second_idx = next(i for i, l in enumerate(lines) if "Second" in l)
        assert first_idx < second_idx


class TestBookmarkDataclass:
    def test_defaults(self):
        b = Bookmark(timestamp=0.0, label="Start")
        assert b.color == "blue"

    def test_all_fields(self):
        b = Bookmark(timestamp=99.9, label="Important", color="red")
        assert b.timestamp == 99.9
        assert b.label == "Important"
        assert b.color == "red"
