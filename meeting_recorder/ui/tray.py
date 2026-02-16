"""System tray icon and menu using pystray."""

from __future__ import annotations

import logging
import threading
from typing import Optional, Callable

import pystray
from pystray import MenuItem as Item

from meeting_recorder.ui.icons import (
    create_idle_icon,
    create_recording_icon,
    create_processing_icon,
    create_error_icon,
)

logger = logging.getLogger(__name__)


class TrayIcon:
    """System tray icon with menu for controlling the recorder.

    States:
    - idle: Gray mic icon, menu shows "Start Recording"
    - recording: Red circle, menu shows "Stop Recording"
    - processing: Blue dots, menu shows "Processing..."
    - error: Yellow warning
    """

    def __init__(
        self,
        on_start: Optional[Callable] = None,
        on_stop: Optional[Callable] = None,
        on_quit: Optional[Callable] = None,
        on_settings: Optional[Callable] = None,
        on_open_recordings: Optional[Callable] = None,
    ):
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_quit = on_quit
        self._on_settings = on_settings
        self._on_open_recordings = on_open_recordings
        self._state = "idle"
        self._status_text = "Idle"
        self._icon: Optional[pystray.Icon] = None
        self._icons = {
            "idle": create_idle_icon(),
            "recording": create_recording_icon(),
            "processing": create_processing_icon(),
            "error": create_error_icon(),
        }

    def run(self) -> None:
        """Create and run the system tray icon. Blocks until quit."""
        self._icon = pystray.Icon(
            name="meeting_recorder",
            icon=self._icons["idle"],
            title="Meeting Recorder",
            menu=pystray.Menu(
                Item(
                    lambda _: self._status_text,
                    None,
                    enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                Item(
                    lambda _: "Stop Recording" if self._state == "recording" else "Start Recording",
                    self._on_toggle_recording,
                    enabled=lambda _: self._state in ("idle", "recording"),
                ),
                pystray.Menu.SEPARATOR,
                Item("Hotkeys", pystray.Menu(
                    Item("Ctrl+Shift+R     Start/Stop Recording", None, enabled=False),
                    Item("Ctrl+Shift+U     Manual Mic Mute Toggle", None, enabled=False),
                    pystray.Menu.SEPARATOR,
                    Item("Zoom:  Alt+A     Mute Sync", None, enabled=False),
                    Item("Teams: Ctrl+Shift+M  Mute Sync", None, enabled=False),
                    Item("Webex: Ctrl+M    Mute Sync", None, enabled=False),
                )),
                pystray.Menu.SEPARATOR,
                Item("Open Recordings", self._handle_open_recordings),
                Item("Settings", self._handle_settings),
                pystray.Menu.SEPARATOR,
                Item("Quit", self._handle_quit),
            ),
        )
        logger.info("Starting system tray icon.")
        self._icon.run()

    def set_state(self, state: str, status_text: str = "") -> None:
        """Update the tray icon state.

        Args:
            state: One of "idle", "recording", "processing", "error".
            status_text: Optional status text shown in menu.
        """
        self._state = state
        if status_text:
            self._status_text = status_text
        else:
            defaults = {
                "idle": "Idle",
                "recording": "Recording...",
                "processing": "Processing...",
                "error": "Error",
            }
            self._status_text = defaults.get(state, state)

        if self._icon is not None:
            self._icon.icon = self._icons.get(state, self._icons["idle"])
            self._icon.title = f"Meeting Recorder - {self._status_text}"
            # Force menu update
            self._icon.update_menu()

    def stop(self) -> None:
        """Stop the tray icon."""
        if self._icon is not None:
            self._icon.stop()

    def _on_toggle_recording(self, icon, item) -> None:
        """Handle start/stop recording toggle."""
        if self._state == "recording":
            if self._on_stop:
                threading.Thread(target=self._on_stop, daemon=True).start()
        elif self._state == "idle":
            if self._on_start:
                threading.Thread(target=self._on_start, daemon=True).start()

    def _handle_settings(self, icon, item) -> None:
        """Handle settings menu click."""
        if self._on_settings:
            threading.Thread(target=self._on_settings, daemon=True).start()

    def _handle_open_recordings(self, icon, item) -> None:
        """Handle open recordings folder click."""
        if self._on_open_recordings:
            threading.Thread(target=self._on_open_recordings, daemon=True).start()

    def _handle_quit(self, icon, item) -> None:
        """Handle quit menu click."""
        if self._on_quit:
            self._on_quit()
        self.stop()
