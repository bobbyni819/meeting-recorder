"""Tests for the pop-out live-transcript window."""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from meeting_recorder.ui.live_transcript_window import LiveTranscriptWindow


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


def test_opens_as_toplevel_of_master(root):
    win = LiveTranscriptWindow(root, font_size=16)
    win.show()
    root.update()
    assert win.is_visible
    assert isinstance(win._window, tk.Toplevel)  # NOT a second tk.Tk()
    win.close()


def test_renders_pushed_text(root):
    win = LiveTranscriptWindow(root)
    win.show()
    win.update_text("[You] testing the transcript window")
    root.update()
    root.update_idletasks()
    assert "testing the transcript" in win._text.get("1.0", "end")
    win.close()


def test_font_bump_changes_size(root):
    win = LiveTranscriptWindow(root, font_size=16)
    win.show()
    root.update()
    win._bump_font(4)
    root.update()
    assert "20" in str(win._text.cget("font"))
    # Clamped at the top end
    for _ in range(40):
        win._bump_font(4)
    assert win._font_size <= 48
    win.close()


def test_hide_show_close(root):
    win = LiveTranscriptWindow(root)
    win.show()
    root.update()
    win.hide()
    assert not win.is_visible
    win.show()
    assert win.is_visible
    win.close()
    assert win._window is None


def test_update_before_show_is_noop(root):
    win = LiveTranscriptWindow(root)
    # Not shown yet — must not raise
    win.update_text("ignored")
    assert win._window is None
