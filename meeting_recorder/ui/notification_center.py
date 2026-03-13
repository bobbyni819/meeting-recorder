"""Notification center — collects and displays system alerts and warnings.

Maintains a chronological log of events (health warnings, status changes)
that can be viewed in a scrollable window.
"""

from __future__ import annotations

import logging
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL, BG_CARD,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    GREEN, AMBER, RED_DOT,
)

logger = logging.getLogger(__name__)

LEVEL_ICON = {"info": "\u2139", "warn": "\u26a0", "error": "\u2717", "success": "\u2713"}
LEVEL_COLOR = {"info": "#3498db", "warn": AMBER, "error": RED_DOT, "success": GREEN}


@dataclass
class Notification:
    """A single notification entry."""
    level: str  # "info", "warn", "error", "success"
    message: str
    timestamp: str = ""
    source: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%H:%M:%S")


class NotificationStore:
    """Thread-safe store for notification entries."""

    def __init__(self, max_entries: int = 200):
        self._entries: list[Notification] = []
        self._max = max_entries
        self._unread_count: int = 0

    def add(self, level: str, message: str, source: str = "") -> None:
        """Add a notification. Thread-safe (append is atomic in CPython)."""
        entry = Notification(level=level, message=message, source=source)
        self._entries.append(entry)
        self._unread_count += 1
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]

    @property
    def entries(self) -> list[Notification]:
        return list(self._entries)

    @property
    def unread_count(self) -> int:
        return self._unread_count

    def mark_read(self) -> None:
        self._unread_count = 0

    def clear(self) -> None:
        self._entries.clear()
        self._unread_count = 0

    def __len__(self) -> int:
        return len(self._entries)


class NotificationWindow:
    """Window displaying the notification log."""

    def __init__(self, store: NotificationStore):
        self._store = store
        self._window: Optional[tk.Toplevel] = None

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show or raise the notification window."""
        if self._window is not None:
            try:
                self._window.lift()
                self._refresh()
                return
            except tk.TclError:
                self._window = None

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("Notifications")
        self._window.geometry("520x400")
        self._window.configure(bg=BG_COLOR)
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        # Header
        header = tk.Frame(self._window, bg=BG_HEADER, height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="Notifications",
            font=("Segoe UI", 11, "bold"), fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(padx=16, pady=8, side=tk.LEFT)

        self._count_label = tk.Label(
            header, text="",
            font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_HEADER,
        )
        self._count_label.pack(side=tk.RIGHT, padx=16, pady=8)

        # Scrollable area
        container = tk.Frame(self._window, bg=BG_COLOR)
        container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(container, bg=BG_COLOR, highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        self._list_frame = tk.Frame(canvas, bg=BG_COLOR)

        self._list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._list_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self._canvas = canvas

        # Bottom buttons
        btn_frame = tk.Frame(self._window, bg=BG_HEADER, height=40)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        btn_frame.pack_propagate(False)

        clear_btn = tk.Label(
            btn_frame, text="  Clear All  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg="#0f3460", cursor="hand2", padx=8, pady=4,
        )
        clear_btn.pack(side=tk.RIGHT, padx=12, pady=6)
        clear_btn.bind("<Button-1>", lambda e: self._clear_all())
        clear_btn.bind("<Enter>", lambda e: clear_btn.configure(fg=TEXT_BRIGHT))
        clear_btn.bind("<Leave>", lambda e: clear_btn.configure(fg=TEXT_DIM))

        close_btn = tk.Label(
            btn_frame, text="  Close  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg="#0f3460", cursor="hand2", padx=8, pady=4,
        )
        close_btn.pack(side=tk.RIGHT, padx=4, pady=6)
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=TEXT_BRIGHT))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=TEXT_DIM))

        self._refresh()
        self._store.mark_read()

    def close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None

    def _refresh(self) -> None:
        """Rebuild the notification list."""
        if not self._window or not self._list_frame:
            return

        for w in self._list_frame.winfo_children():
            w.destroy()

        entries = self._store.entries
        self._count_label.configure(text=f"{len(entries)} event{'s' if len(entries) != 1 else ''}")

        if not entries:
            tk.Label(
                self._list_frame, text="No notifications yet.",
                font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_COLOR,
            ).pack(padx=20, pady=40)
            return

        # Show newest first
        for entry in reversed(entries):
            self._build_entry(entry)

        self._store.mark_read()

    def _build_entry(self, entry: Notification) -> None:
        """Build a single notification row."""
        row = tk.Frame(self._list_frame, bg=BG_PANEL)
        row.pack(fill=tk.X, padx=8, pady=2)

        icon = LEVEL_ICON.get(entry.level, "?")
        color = LEVEL_COLOR.get(entry.level, TEXT_DIM)

        # Icon + timestamp
        left = tk.Frame(row, bg=BG_PANEL)
        left.pack(side=tk.LEFT, padx=(8, 4), pady=4)

        tk.Label(
            left, text=icon,
            font=("Segoe UI", 10), fg=color, bg=BG_PANEL,
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Label(
            left, text=entry.timestamp,
            font=("Segoe UI", 7), fg=TEXT_DIM, bg=BG_PANEL,
        ).pack(side=tk.LEFT, padx=(0, 8))

        # Message
        tk.Label(
            row, text=entry.message,
            font=("Segoe UI", 8), fg=TEXT_COLOR, bg=BG_PANEL,
            anchor=tk.W, wraplength=420, justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=4)

        # Source tag
        if entry.source:
            tk.Label(
                row, text=entry.source,
                font=("Segoe UI", 7), fg=TEXT_DIM, bg=BG_PANEL,
            ).pack(side=tk.RIGHT, padx=8, pady=4)

    def _clear_all(self) -> None:
        """Clear all notifications."""
        self._store.clear()
        self._refresh()
