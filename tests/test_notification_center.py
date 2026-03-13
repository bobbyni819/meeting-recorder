"""Tests for the notification center store and window."""

from __future__ import annotations

import pytest

from meeting_recorder.ui.notification_center import (
    Notification,
    NotificationStore,
    NotificationWindow,
    LEVEL_ICON,
    LEVEL_COLOR,
)


class TestNotification:
    def test_default_timestamp(self):
        n = Notification(level="info", message="test")
        assert n.timestamp  # auto-set
        assert ":" in n.timestamp  # HH:MM:SS format

    def test_custom_timestamp(self):
        n = Notification(level="warn", message="x", timestamp="12:34:56")
        assert n.timestamp == "12:34:56"

    def test_source(self):
        n = Notification(level="error", message="x", source="health")
        assert n.source == "health"

    def test_default_source_empty(self):
        n = Notification(level="info", message="x")
        assert n.source == ""


class TestNotificationStore:
    def test_empty(self):
        store = NotificationStore()
        assert len(store) == 0
        assert store.entries == []
        assert store.unread_count == 0

    def test_add(self):
        store = NotificationStore()
        store.add("info", "Hello")
        assert len(store) == 1
        assert store.entries[0].message == "Hello"
        assert store.entries[0].level == "info"

    def test_unread_count(self):
        store = NotificationStore()
        store.add("warn", "a")
        store.add("error", "b")
        assert store.unread_count == 2

    def test_mark_read(self):
        store = NotificationStore()
        store.add("info", "a")
        store.add("info", "b")
        assert store.unread_count == 2
        store.mark_read()
        assert store.unread_count == 0

    def test_clear(self):
        store = NotificationStore()
        store.add("info", "a")
        store.add("warn", "b")
        store.clear()
        assert len(store) == 0
        assert store.unread_count == 0

    def test_max_entries(self):
        store = NotificationStore(max_entries=5)
        for i in range(10):
            store.add("info", f"msg {i}")
        assert len(store) == 5
        # Oldest should be trimmed
        assert store.entries[0].message == "msg 5"

    def test_entries_returns_copy(self):
        store = NotificationStore()
        store.add("info", "a")
        entries = store.entries
        entries.clear()
        assert len(store) == 1  # original not affected

    def test_source_stored(self):
        store = NotificationStore()
        store.add("warn", "test", source="health")
        assert store.entries[0].source == "health"

    def test_multiple_levels(self):
        store = NotificationStore()
        store.add("info", "i")
        store.add("warn", "w")
        store.add("error", "e")
        store.add("success", "s")
        assert len(store) == 4
        assert store.entries[0].level == "info"
        assert store.entries[1].level == "warn"
        assert store.entries[2].level == "error"
        assert store.entries[3].level == "success"

    def test_mark_read_then_add_resets_count(self):
        store = NotificationStore()
        store.add("info", "a")
        store.mark_read()
        assert store.unread_count == 0
        store.add("warn", "b")
        assert store.unread_count == 1


class TestNotificationWindowLifecycle:
    def test_construction(self):
        store = NotificationStore()
        nw = NotificationWindow(store)
        assert nw._window is None

    def test_close_resets(self):
        store = NotificationStore()
        nw = NotificationWindow(store)
        nw.close()
        assert nw._window is None

    def test_store_reference(self):
        store = NotificationStore()
        nw = NotificationWindow(store)
        assert nw._store is store


class TestLevelConstants:
    def test_icons_defined(self):
        for level in ("info", "warn", "error", "success"):
            assert level in LEVEL_ICON
            assert LEVEL_ICON[level]

    def test_colors_defined(self):
        for level in ("info", "warn", "error", "success"):
            assert level in LEVEL_COLOR
            assert LEVEL_COLOR[level].startswith("#")
