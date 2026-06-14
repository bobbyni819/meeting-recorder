"""Regression tests for the ask window."""

from __future__ import annotations

import tkinter as tk

import pytest

from meeting_recorder.search.ask import AskResult
from meeting_recorder.ui.ask_window import AskWindow


class ImmediateThread:
    def __init__(self, target, daemon=False):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def test_ask_window_builds(root):
    window = AskWindow()
    window.show(root)
    root.update()

    assert isinstance(window._window, tk.Toplevel)
    assert window._question_text is not None
    assert window._result_text is not None
    assert window._ask_button is not None

    window.close()


def test_post_to_ui_noops_when_window_is_gone():
    class GoneWindow:
        after_called = False

        def winfo_exists(self):
            return False

        def after(self, delay, callback):
            self.after_called = True

    gone = GoneWindow()
    window = AskWindow.__new__(AskWindow)
    window._window = gone
    called = []

    window._post_to_ui(lambda: called.append(True))

    assert called == []
    assert not gone.after_called


def test_do_ask_uses_worker_and_displays_result(root, monkeypatch):
    calls = []

    def fake_ask_meetings(question, *, top_k=5, config=None):
        calls.append((question, top_k, config))
        return AskResult(answer="The answer is in the notes.", sources=[], used_recordings=0)

    monkeypatch.setattr("meeting_recorder.ui.ask_window.ask_meetings", fake_ask_meetings)
    monkeypatch.setattr("meeting_recorder.ui.ask_window.threading.Thread", ImmediateThread)

    window = AskWindow()
    window.show(root)
    window._question_text.insert("1.0", "What did we decide?")
    window._top_k_var.set(7)

    window._do_ask()
    root.update()

    assert calls == [("What did we decide?", 7, None)]
    assert "The answer is in the notes." in window._result_text.get("1.0", tk.END)
    assert str(window._ask_button.cget("state")) != tk.DISABLED

    window.close()


def test_do_ask_handles_missing_api_key(root, monkeypatch):
    def fake_ask_meetings(question, *, top_k=5, config=None):
        raise ValueError("missing api key")

    monkeypatch.setattr("meeting_recorder.ui.ask_window.ask_meetings", fake_ask_meetings)
    monkeypatch.setattr("meeting_recorder.ui.ask_window.threading.Thread", ImmediateThread)

    window = AskWindow()
    window.show(root)
    window._question_text.insert("1.0", "What needs follow-up?")

    window._do_ask()
    root.update()

    text = window._result_text.get("1.0", tk.END)
    assert "Set your Gemini API key in Settings \u2192 Transcription" in text
    assert str(window._ask_button.cget("state")) != tk.DISABLED

    window.close()
