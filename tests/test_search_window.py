"""Regression tests for the search window."""

from __future__ import annotations

import inspect

from meeting_recorder.ui.search_window import SearchWindow
from meeting_recorder.ui.settings_window import SettingsWindow


class FakeVar:
    def __init__(self, value: str = ""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeTree:
    def get_children(self):
        return []

    def delete(self, *items):
        pass


class FakeIndex:
    def __init__(self):
        self.search_kwargs = None

    def search(self, **kwargs):
        self.search_kwargs = kwargs
        return []


class ImmediateThread:
    def __init__(self, target, daemon=False):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


def _make_search_window():
    window = SearchWindow.__new__(SearchWindow)
    window._window = None
    window._index = FakeIndex()
    window._results = []
    window._query_var = FakeVar()
    window._speaker_var = FakeVar()
    window._subject_var = FakeVar()
    window._attendee_var = FakeVar()
    window._date_from_var = FakeVar()
    window._date_to_var = FakeVar()
    window._sentiment_var = FakeVar()
    window._quality_var = FakeVar()
    window._status_filter_var = FakeVar("completed")
    window._status_var = FakeVar("Enter a search query or click Browse All.")
    window._tag_var = FakeVar()
    window._tree = FakeTree()
    return window


def test_post_to_ui_ignores_closed_window():
    window = SearchWindow.__new__(SearchWindow)
    window._window = None

    window._post_to_ui(lambda: None)


def test_do_search_uses_status_filter_var(monkeypatch):
    window = _make_search_window()
    monkeypatch.setattr("meeting_recorder.ui.search_window.threading.Thread", ImmediateThread)

    window._do_search()

    assert window._index.search_kwargs["status"] == "completed"


def test_settings_gemini_worker_uses_guarded_scheduler():
    source = inspect.getsource(SettingsWindow._test_gemini_key)
    worker_source = source.split("def _do_test():", 1)[1]

    assert "def _safe_after" in worker_source
    assert "self._window.after" not in worker_source
