"""macOS menu bar backend using rumps."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from meeting_recorder.platform_support.base import TrayBackend

logger = logging.getLogger(__name__)


class MacTrayBackend(TrayBackend):
    """Native macOS menu bar equivalent of the Windows tray surface."""

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
        try:
            import rumps
        except ImportError as exc:
            raise RuntimeError(
                "rumps is required for the macOS menu bar. "
                "Install with: pip install -e '.[macos]'"
            ) from exc

        self._rumps = rumps
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
        self._app = rumps.App("Meeting Recorder", title="MR")
        self._items: dict[str, object] = {}
        self._build_menu()

    def run(self) -> None:
        logger.info("Starting macOS menu bar app.")
        self._app.run()

    def stop(self) -> None:
        try:
            self._rumps.quit_application()
        except Exception:
            logger.debug("rumps quit failed", exc_info=True)

    def set_state(self, state: str, status_text: str = "") -> None:
        self._state = state
        defaults = {
            "idle": "Idle",
            "recording": "Recording...",
            "processing": "Processing...",
            "error": "Error",
        }
        self._status_text = status_text or defaults.get(state, state)
        self._app.title = {
            "idle": "MR",
            "recording": "REC",
            "processing": "...",
            "error": "!",
        }.get(state, "MR")
        self._sync_menu_state()

    def _build_menu(self) -> None:
        rumps = self._rumps
        menu = self._app.menu
        self._items["status"] = rumps.MenuItem(self._status_text, callback=None)
        self._items["show_window"] = rumps.MenuItem("Show Window", self._handle_show_main_window)
        self._items["toggle"] = rumps.MenuItem("Start Recording", self._handle_toggle_recording)
        self._items["pause"] = rumps.MenuItem("Pause / Resume", self._handle_pause)
        self._items["record_window"] = rumps.MenuItem("Record Window...", self._handle_record_window)
        self._items["import_audio"] = rumps.MenuItem("Import Audio...", self._handle_import_audio)
        self._items["auto_start"] = rumps.MenuItem(
            "Auto-Record Meetings", self._handle_toggle_auto_start
        )
        hotkeys = rumps.MenuItem("Hotkeys")
        for title in (
            f"{self._hotkey_recording}     Start/Stop Recording",
            f"{self._hotkey_pause}     Pause/Resume",
            f"{self._hotkey_mute}     Manual Mic Mute Toggle",
            f"{self._hotkey_dashboard}     Show/Hide Dashboard",
            "Zoom:  Alt+A     Mute Sync",
            "Teams: Ctrl+Shift+M  Mute Sync",
            "Webex: Ctrl+M    Mute Sync",
        ):
            hotkeys.add(rumps.MenuItem(title, callback=None))
        self._items["dashboard"] = rumps.MenuItem("Show Dashboard", self._handle_show_dashboard)
        self._items["open_recordings"] = rumps.MenuItem(
            "Open Recordings", self._handle_open_recordings
        )
        self._items["open_last"] = rumps.MenuItem(
            "Open Last Recording", self._handle_open_last_recording
        )
        self._items["recent"] = rumps.MenuItem("Recent Recordings")
        self._items["search"] = rumps.MenuItem("Search Recordings...", self._handle_search)
        self._items["settings"] = rumps.MenuItem("Settings", self._handle_settings)
        self._items["quit"] = rumps.MenuItem("Quit", self._handle_quit)

        for item in (
            self._items["status"],
            None,
            self._items["show_window"],
            None,
            self._items["toggle"],
            self._items["pause"],
            self._items["record_window"],
            self._items["import_audio"],
            self._items["auto_start"],
            None,
            hotkeys,
            None,
            self._items["dashboard"],
            None,
            self._items["open_recordings"],
            self._items["open_last"],
            self._items["recent"],
            self._items["search"],
            self._items["settings"],
            None,
            self._items["quit"],
        ):
            menu.add(item)
        self._refresh_recent_menu()
        self._sync_menu_state()

    def _sync_menu_state(self) -> None:
        self._items["status"].title = self._status_text
        self._items["toggle"].title = (
            "Stop Recording" if self._state == "recording" else "Start Recording"
        )
        self._items["toggle"].set_callback(
            self._handle_toggle_recording if self._state in ("idle", "recording") else None
        )
        self._items["pause"].set_callback(
            self._handle_pause if self._state == "recording" else None
        )
        idle_callback = self._handle_record_window if self._state == "idle" else None
        self._items["record_window"].set_callback(idle_callback)
        self._items["import_audio"].set_callback(
            self._handle_import_audio if self._state == "idle" else None
        )
        self._items["dashboard"].set_callback(
            self._handle_show_dashboard if self._state == "recording" else None
        )
        self._items["auto_start"].state = bool(self._auto_start)

    def _refresh_recent_menu(self) -> None:
        rumps = self._rumps
        recent_menu = self._items["recent"]
        recent_menu.clear()
        if not self._on_list_recent:
            recent_menu.add(rumps.MenuItem("(no recordings)", callback=None))
            return
        try:
            paths = list(self._on_list_recent() or [])[:5]
        except Exception:
            logger.exception("Failed to list recent recordings")
            recent_menu.add(rumps.MenuItem("(error loading)", callback=None))
            return
        if not paths:
            recent_menu.add(rumps.MenuItem("(no recordings)", callback=None))
            return
        for path in paths:
            title = path.name
            if len(title) > 50:
                title = title[:47] + "..."
            recent_menu.add(rumps.MenuItem(title, self._make_open_recording_handler(path)))

    def _run_callback(self, callback: Optional[Callable], *args) -> None:
        if callback is not None:
            threading.Thread(target=callback, args=args, daemon=True).start()

    def _handle_toggle_recording(self, _sender) -> None:
        if self._state == "recording":
            self._run_callback(self._on_stop)
        elif self._state == "idle":
            self._run_callback(self._on_start)

    def _handle_pause(self, _sender) -> None:
        self._run_callback(self._on_pause)

    def _handle_record_window(self, _sender) -> None:
        self._run_callback(self._on_record_window)

    def _handle_import_audio(self, _sender) -> None:
        self._run_callback(self._on_import_audio)

    def _handle_toggle_auto_start(self, _sender) -> None:
        self._auto_start = not self._auto_start
        self._items["auto_start"].state = bool(self._auto_start)
        if self._on_toggle_auto_start:
            self._on_toggle_auto_start(self._auto_start)

    def _handle_settings(self, _sender) -> None:
        self._run_callback(self._on_settings)

    def _handle_open_recordings(self, _sender) -> None:
        self._run_callback(self._on_open_recordings)

    def _handle_open_last_recording(self, _sender) -> None:
        self._run_callback(self._on_open_last_recording)

    def _handle_search(self, _sender) -> None:
        self._run_callback(self._on_search)

    def _handle_show_dashboard(self, _sender) -> None:
        self._run_callback(self._on_show_dashboard)

    def _handle_show_main_window(self, _sender) -> None:
        self._run_callback(self._on_show_main_window)

    def _make_open_recording_handler(self, path):
        def handler(_sender) -> None:
            self._run_callback(self._on_open_recording, path)

        return handler

    def _handle_quit(self, _sender) -> None:
        if self._on_quit:
            self._on_quit()
        self.stop()

