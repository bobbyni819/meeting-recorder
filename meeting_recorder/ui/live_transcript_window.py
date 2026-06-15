"""Resizable pop-out window for reading the live transcript.

A larger, scrollable companion to the dashboard's compact transcript strip,
for reading along during a meeting. Created as a ``tk.Toplevel`` of an
existing root (the dashboard's), NOT a second ``tk.Tk()`` — two Tk roots on
separate threads corrupt each other, which is why the old standalone version
was dead code. Has a normal title bar so it is resizable, movable, and shows
the app icon in the taskbar.
"""

from __future__ import annotations

import logging
from pathlib import Path
import tkinter as tk
from typing import Optional

logger = logging.getLogger(__name__)

_BG = "#14142b"
_FG = "#e8e8f0"
_DIM = "#8888aa"
_TAIL_BYTES = 200_000
_REFRESH_MS = 2000


class LiveTranscriptWindow:
    """Large, scrollable live-transcript reader (a Toplevel of *master*)."""

    def __init__(
        self,
        master: tk.Misc,
        font_size: int = 17,
        transcript_path: str | Path | None = None,
        transcript_pool_lines: int = 2000,
    ):
        self._master = master
        self._font_size = max(10, int(font_size))
        self._transcript_path = Path(transcript_path) if transcript_path else None
        self._transcript_pool_lines = max(100, int(transcript_pool_lines))
        self._window: Optional[tk.Toplevel] = None
        self._text: Optional[tk.Text] = None
        self._visible = False
        self._refresh_after_id: Optional[str] = None
        self._last_file_text: Optional[str] = None

    def show(self) -> None:
        if self._window is not None:
            try:
                self._window.deiconify()
                self._window.lift()
                self._visible = True
                self._refresh_from_file()
                self._schedule_refresh()
                return
            except tk.TclError:
                self._window = None

        win = tk.Toplevel(self._master)
        self._window = win
        win.title("Live Transcript")
        win.configure(bg=_BG)
        win.geometry("600x380")
        win.minsize(320, 160)
        try:
            from meeting_recorder.ui.icons import app_icon_path

            ico = app_icon_path()
            if ico:
                win.iconbitmap(ico)
        except Exception:
            pass
        # Closing the window just hides it (recording keeps running).
        win.protocol("WM_DELETE_WINDOW", self.hide)

        header = tk.Frame(win, bg=_BG)
        header.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(
            header, text="Live Transcript", font=("Segoe UI", 10, "bold"),
            fg=_DIM, bg=_BG,
        ).pack(side=tk.LEFT)
        # Font size controls (A- / A+).
        tk.Button(
            header, text="A+", command=lambda: self._bump_font(2),
            font=("Segoe UI", 9), bg="#22224a", fg=_FG, bd=0, padx=8,
            activebackground="#33335a", cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(
            header, text="A-", command=lambda: self._bump_font(-2),
            font=("Segoe UI", 9), bg="#22224a", fg=_FG, bd=0, padx=8,
            activebackground="#33335a", cursor="hand2",
        ).pack(side=tk.RIGHT)

        body = tk.Frame(win, bg=_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        scrollbar = tk.Scrollbar(body)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._text = tk.Text(
            body, wrap=tk.WORD, font=("Segoe UI", self._font_size),
            fg=_FG, bg="#0d0d1a", bd=0, padx=10, pady=10,
            yscrollcommand=scrollbar.set, insertwidth=0,
            spacing1=2, spacing3=6,
        )
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._text.yview)
        self._text.insert("1.0", "Waiting for speech…")
        self._text.config(state=tk.DISABLED)
        self._visible = True
        self._refresh_from_file()
        self._schedule_refresh()

    def hide(self) -> None:
        self._cancel_refresh()
        if self._window is not None:
            try:
                self._window.withdraw()
            except tk.TclError:
                pass
        self._visible = False

    def close(self) -> None:
        """Destroy the window. Must be called on the Tk thread."""
        self._visible = False
        self._cancel_refresh()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None
            self._text = None

    def request_close(self) -> None:
        """Destroy the window from any thread (marshals onto the Tk thread)."""
        self._visible = False
        win = self._window
        if win is not None:
            try:
                win.after(0, self.close)
            except tk.TclError:
                self._window = None
                self._text = None

    def update_text(self, text: str) -> None:
        """Replace the displayed rolling transcript and scroll to the end."""
        win = self._window  # snapshot — close() may null it on another thread
        if not self._visible or win is None or self._text is None:
            return
        if self._transcript_path is not None:
            file_text = self._read_transcript_tail()
            if file_text:
                text = file_text
        try:
            win.after(0, self._set_text, text)
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def _set_text(self, text: str) -> None:
        if self._text is None:
            return
        try:
            # Only auto-scroll if the user is already near the bottom, so
            # scrolling back to re-read isn't yanked away by new text.
            at_bottom = self._text.yview()[1] > 0.92
            self._text.config(state=tk.NORMAL)
            self._text.delete("1.0", tk.END)
            self._text.insert("1.0", text)
            self._text.config(state=tk.DISABLED)
            if at_bottom:
                self._text.see(tk.END)
        except tk.TclError:
            pass

    def _bump_font(self, delta: int) -> None:
        self._font_size = max(10, min(48, self._font_size + delta))
        if self._text is not None:
            try:
                self._text.config(font=("Segoe UI", self._font_size))
            except tk.TclError:
                pass

    def _read_transcript_tail(self) -> str:
        path = self._transcript_path
        if path is None:
            return ""
        try:
            size = path.stat().st_size
            with path.open("rb") as f:
                if size > _TAIL_BYTES:
                    f.seek(size - _TAIL_BYTES)
                    f.readline()
                raw = f.read(_TAIL_BYTES)
            lines = raw.decode("utf-8", errors="replace").splitlines()
            return "\n".join(lines[-self._transcript_pool_lines:])
        except FileNotFoundError:
            return ""
        except OSError:
            logger.debug("Could not read live transcript tail", exc_info=True)
            return ""

    def _refresh_from_file(self) -> None:
        if not self._visible or self._text is None:
            return
        text = self._read_transcript_tail()
        if text and text != self._last_file_text:
            self._last_file_text = text
            self._set_text(text)

    def _schedule_refresh(self) -> None:
        if not self._visible or self._window is None or self._transcript_path is None:
            return
        self._cancel_refresh()
        try:
            self._refresh_after_id = self._window.after(
                _REFRESH_MS, self._refresh_tick,
            )
        except tk.TclError:
            self._refresh_after_id = None

    def _refresh_tick(self) -> None:
        self._refresh_after_id = None
        self._refresh_from_file()
        self._schedule_refresh()

    def _cancel_refresh(self) -> None:
        if self._refresh_after_id and self._window is not None:
            try:
                self._window.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
        self._refresh_after_id = None

    @property
    def is_visible(self) -> bool:
        return self._visible
