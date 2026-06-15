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
    history = "\n".join(f"[Speaker] transcript line {i}" for i in range(300))
    win.update_text(history)
    root.update()
    root.update_idletasks()
    assert "transcript line 0" in win._text.get("1.0", "end")
    assert "transcript line 299" in win._text.get("1.0", "end")
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


def test_scroll_lock_respects_user_position(root):
    win = LiveTranscriptWindow(root)
    fake_text = _FakeText((0.1, 0.5))
    win._text = fake_text
    win._set_text("new text")
    assert fake_text.contents == "new text"
    assert fake_text.see_calls == []

    fake_text = _FakeText((0.0, 0.99))
    win._text = fake_text
    win._set_text("newer text")
    assert fake_text.contents == "newer text"
    assert fake_text.see_calls == [tk.END]


def test_reads_tail_from_live_transcript_file(root, tmp_path):
    transcript_path = tmp_path / "live_transcript.txt"
    transcript_path.write_text(
        "\n".join(f"tail line {i}" for i in range(2500)),
        encoding="utf-8",
    )

    win = LiveTranscriptWindow(
        root,
        transcript_path=transcript_path,
        transcript_pool_lines=2000,
    )
    win.show()
    root.update()
    text = win._text.get("1.0", "end")
    assert "tail line 0" not in text
    assert "tail line 500" in text
    assert "tail line 2499" in text
    win.close()


class _FakeText:
    def __init__(self, yview):
        self._yview = yview
        self.contents = ""
        self.see_calls = []

    def yview(self):
        return self._yview

    def config(self, **_kwargs):
        return None

    def delete(self, _start, _end):
        self.contents = ""

    def insert(self, _index, text):
        self.contents += text

    def see(self, index):
        self.see_calls.append(index)
