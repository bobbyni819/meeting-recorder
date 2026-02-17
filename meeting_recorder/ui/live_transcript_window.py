"""Floating overlay window for live transcription preview."""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Optional

logger = logging.getLogger(__name__)


class LiveTranscriptWindow:
    """Small floating window that displays live transcription text.

    Shows the most recent preview transcript, updated in real-time
    during recording. Semi-transparent, always-on-top, positioned
    at the bottom-right of the screen.
    """

    def __init__(self):
        self._window: Optional[tk.Tk] = None
        self._text_var: Optional[tk.StringVar] = None
        self._is_visible = False

    def show(self) -> None:
        """Show the live transcript overlay window."""
        if self._window is not None:
            try:
                self._window.deiconify()
                self._is_visible = True
                return
            except tk.TclError:
                self._window = None

        self._window = tk.Tk()
        self._window.title("Live Transcript")
        self._window.attributes("-topmost", True)
        self._window.attributes("-alpha", 0.85)
        self._window.overrideredirect(True)  # No title bar

        # Position at bottom-right of screen
        screen_w = self._window.winfo_screenwidth()
        screen_h = self._window.winfo_screenheight()
        win_w, win_h = 400, 120
        x = screen_w - win_w - 20
        y = screen_h - win_h - 80  # Above taskbar
        self._window.geometry(f"{win_w}x{win_h}+{x}+{y}")

        # Dark background
        self._window.configure(bg="#1a1a2e")

        # Header
        header = tk.Label(
            self._window,
            text="Live Transcript",
            font=("Segoe UI", 9, "bold"),
            fg="#888888",
            bg="#1a1a2e",
            anchor=tk.W,
        )
        header.pack(fill=tk.X, padx=10, pady=(8, 2))

        # Transcript text
        self._text_var = tk.StringVar(value="Waiting for speech...")
        label = tk.Label(
            self._window,
            textvariable=self._text_var,
            font=("Segoe UI", 11),
            fg="#e0e0e0",
            bg="#1a1a2e",
            wraplength=380,
            justify=tk.LEFT,
            anchor=tk.NW,
        )
        label.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        # Allow dragging the window
        self._window.bind("<Button-1>", self._start_drag)
        self._window.bind("<B1-Motion>", self._do_drag)

        # Right-click to close
        self._window.bind("<Button-3>", lambda e: self.hide())

        self._is_visible = True
        # Don't call mainloop here - the window updates via update_text calls

    def hide(self) -> None:
        """Hide the overlay window."""
        if self._window is not None:
            try:
                self._window.withdraw()
            except tk.TclError:
                pass
        self._is_visible = False

    def close(self) -> None:
        """Destroy the overlay window."""
        self._is_visible = False
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None

    def update_text(self, text: str) -> None:
        """Update the displayed transcript text (thread-safe).

        Args:
            text: New transcript text to display.
        """
        if self._window is not None and self._text_var is not None and self._is_visible:
            try:
                # Truncate to last ~200 chars for readability
                if len(text) > 200:
                    text = "..." + text[-197:]
                self._window.after(0, self._text_var.set, text)
            except tk.TclError:
                pass

    @property
    def is_visible(self) -> bool:
        return self._is_visible

    def _start_drag(self, event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event) -> None:
        if self._window:
            x = self._window.winfo_x() + event.x - self._drag_x
            y = self._window.winfo_y() + event.y - self._drag_y
            self._window.geometry(f"+{x}+{y}")
