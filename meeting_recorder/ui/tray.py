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
        on_search: Optional[Callable] = None,
        on_show_dashboard: Optional[Callable] = None,
        on_record_window: Optional[Callable] = None,
        on_toggle_auto_start: Optional[Callable] = None,
        on_pause: Optional[Callable] = None,
        on_open_last_recording: Optional[Callable] = None,
        on_list_recent: Optional[Callable] = None,
        on_open_recording: Optional[Callable] = None,
        on_show_main_window: Optional[Callable] = None,
        on_import_audio: Optional[Callable] = None,
        auto_start: bool = False,
        hotkey_recording: str = "ctrl+shift+r",
        hotkey_mute: str = "ctrl+shift+u",
        hotkey_dashboard: str = "ctrl+shift+d",
        hotkey_pause: str = "ctrl+shift+p",
    ):
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_quit = on_quit
        self._on_settings = on_settings
        self._on_open_recordings = on_open_recordings
        self._on_search = on_search
        self._on_show_dashboard = on_show_dashboard
        self._on_record_window = on_record_window
        self._on_toggle_auto_start = on_toggle_auto_start
        self._on_pause = on_pause
        self._on_open_last_recording = on_open_last_recording
        self._on_list_recent = on_list_recent
        self._on_open_recording = on_open_recording
        self._on_show_main_window = on_show_main_window
        self._on_import_audio = on_import_audio
        self._auto_start = auto_start
        self._hotkey_recording = hotkey_recording
        self._hotkey_mute = hotkey_mute
        self._hotkey_dashboard = hotkey_dashboard
        self._hotkey_pause = hotkey_pause
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
                    "Show Window",
                    self._handle_show_main_window,
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
                Item(
                    lambda _: "Stop Recording" if self._state == "recording" else "Start Recording",
                    self._on_toggle_recording,
                    enabled=lambda _: self._state in ("idle", "recording"),
                ),
                Item(
                    "Pause / Resume",
                    self._handle_pause,
                    enabled=lambda _: self._state == "recording",
                ),
                Item(
                    "Record Window...",
                    self._handle_record_window,
                    enabled=lambda _: self._state == "idle",
                ),
                Item(
                    "Import Audio...",
                    self._handle_import_audio,
                    enabled=lambda _: self._state == "idle",
                ),
                Item(
                    "Auto-Record Meetings",
                    self._handle_toggle_auto_start,
                    checked=lambda _: self._auto_start,
                ),
                pystray.Menu.SEPARATOR,
                Item("Hotkeys", pystray.Menu(
                    Item(f"{self._hotkey_recording}     Start/Stop Recording", None, enabled=False),
                    Item(f"{self._hotkey_pause}     Pause/Resume", None, enabled=False),
                    Item(f"{self._hotkey_mute}     Manual Mic Mute Toggle", None, enabled=False),
                    Item(f"{self._hotkey_dashboard}     Show/Hide Dashboard", None, enabled=False),
                    pystray.Menu.SEPARATOR,
                    Item("Zoom:  Alt+A     Mute Sync", None, enabled=False),
                    Item("Teams: Ctrl+Shift+M  Mute Sync", None, enabled=False),
                    Item("Webex: Ctrl+M    Mute Sync", None, enabled=False),
                )),
                pystray.Menu.SEPARATOR,
                Item(
                    "Show Dashboard",
                    self._handle_show_dashboard,
                    enabled=lambda _: self._state == "recording",
                ),
                pystray.Menu.SEPARATOR,
                Item("Open Recordings", self._handle_open_recordings),
                Item("Open Last Recording", self._handle_open_last_recording),
                Item("Recent Recordings", pystray.Menu(self._build_recent_items)),
                Item("Search Recordings...", self._handle_search),
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
            # Windows caps the tray tooltip (Shell_NotifyIcon szTip) at 128
            # chars; a long meeting title would otherwise raise on every
            # update. Truncate with an ellipsis well under the limit.
            title = f"Meeting Recorder - {self._status_text}"
            if len(title) > 120:
                title = title[:117] + "..."
            self._icon.title = title
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

    def _handle_pause(self, icon, item) -> None:
        """Handle 'Pause / Resume' menu click."""
        if self._on_pause:
            threading.Thread(target=self._on_pause, daemon=True).start()

    def _handle_record_window(self, icon, item) -> None:
        """Handle 'Record Window...' menu click."""
        if self._on_record_window:
            threading.Thread(target=self._on_record_window, daemon=True).start()

    def _handle_import_audio(self, icon, item) -> None:
        """Handle 'Import Audio...' menu click."""
        if self._on_import_audio:
            threading.Thread(target=self._on_import_audio, daemon=True).start()

    def _handle_toggle_auto_start(self, icon, item) -> None:
        """Handle 'Auto-Record Meetings' toggle."""
        self._auto_start = not self._auto_start
        if self._on_toggle_auto_start:
            self._on_toggle_auto_start(self._auto_start)

    def _handle_settings(self, icon, item) -> None:
        """Handle settings menu click."""
        if self._on_settings:
            threading.Thread(target=self._on_settings, daemon=True).start()

    def _handle_open_recordings(self, icon, item) -> None:
        """Handle open recordings folder click."""
        if self._on_open_recordings:
            threading.Thread(target=self._on_open_recordings, daemon=True).start()

    def _handle_open_last_recording(self, icon, item) -> None:
        """Handle 'Open Last Recording' menu click."""
        if self._on_open_last_recording:
            threading.Thread(target=self._on_open_last_recording, daemon=True).start()

    def _handle_search(self, icon, item) -> None:
        """Handle search recordings menu click."""
        if self._on_search:
            threading.Thread(target=self._on_search, daemon=True).start()

    def _build_recent_items(self) -> list:
        """Dynamically build recent recordings submenu items."""
        if not self._on_list_recent:
            return [Item("(no recordings)", None, enabled=False)]
        try:
            recent = self._on_list_recent()
            if not recent:
                return [Item("(no recordings)", None, enabled=False)]
            items = []
            for path in recent[:5]:
                name = path.name
                # Shorten long names for menu readability
                if len(name) > 50:
                    name = name[:47] + "..."
                # Capture path in closure
                items.append(Item(name, self._make_open_recording_handler(path)))
            return items
        except Exception:
            logger.exception("Failed to list recent recordings")
            return [Item("(error loading)", None, enabled=False)]

    def _make_open_recording_handler(self, path):
        """Create a menu click handler that opens a specific recording directory."""
        def handler(icon, item):
            if self._on_open_recording:
                threading.Thread(
                    target=self._on_open_recording, args=(path,), daemon=True,
                ).start()
        return handler

    def _handle_show_main_window(self, icon, item) -> None:
        """Handle 'Show Window' menu click."""
        if self._on_show_main_window:
            threading.Thread(target=self._on_show_main_window, daemon=True).start()

    def _handle_show_dashboard(self, icon, item) -> None:
        """Handle show dashboard menu click."""
        if self._on_show_dashboard:
            threading.Thread(target=self._on_show_dashboard, daemon=True).start()

    def _handle_quit(self, icon, item) -> None:
        """Handle quit menu click."""
        if self._on_quit:
            self._on_quit()
        self.stop()
