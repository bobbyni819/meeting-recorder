"""rumps-based macOS menu bar application."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from meeting_recorder.config import Config
from meeting_recorder.platform_support import factory
from meeting_recorder.platform_support.base import HotkeyBackend
from meeting_recorder.platform_support.macos.orchestrator import MacRecordingSession

if sys.platform == "darwin":
    import rumps
else:  # Keep Windows imports isolated from macOS-only dependencies.
    rumps = None

logger = logging.getLogger(__name__)

_BaseApp = rumps.App if rumps is not None else object


class MacMenubarApp(_BaseApp):
    """Minimal native macOS menu bar app for Meeting Recorder."""

    def __init__(self):
        if rumps is None:
            raise RuntimeError(
                "rumps is required for the macOS menu bar. "
                "Install with: pip install -e '.[macos]'"
            )

        super().__init__("Meeting Recorder", title="MR", quit_button=None)
        self.config = Config.load()
        self.session = MacRecordingSession()
        self._state_lock = threading.Lock()
        self._recording = False
        self._busy = False
        self._hotkeys: Optional[HotkeyBackend] = None
        self._hotkey_handle = None

        self._toggle_item = rumps.MenuItem(
            "Start Recording",
            callback=self._on_toggle_recording,
        )
        self._open_folder_item = rumps.MenuItem(
            "Open Recordings Folder",
            callback=self._on_open_recordings_folder,
        )
        self._quit_item = rumps.MenuItem("Quit", callback=self._on_quit)

        self.menu = [
            self._toggle_item,
            None,
            self._open_folder_item,
            None,
            self._quit_item,
        ]
        self._set_idle_state()
        self._register_hotkeys()

    def _register_hotkeys(self) -> None:
        try:
            self._hotkeys = factory.get_hotkey_backend()
            combo = self.config.hotkey.toggle_recording
            self._hotkey_handle = self._hotkeys.register(combo, self._toggle_from_hotkey)
            self._hotkeys.start()
            logger.info("Registered macOS recording hotkey: %s", combo)
        except Exception:
            self._hotkeys = None
            self._hotkey_handle = None
            logger.exception("macOS global hotkey registration failed")

    def _toggle_from_hotkey(self) -> None:
        threading.Thread(
            target=self._toggle_recording,
            name="macos-hotkey-toggle",
            daemon=True,
        ).start()

    def _on_toggle_recording(self, _sender) -> None:
        threading.Thread(
            target=self._toggle_recording,
            name="macos-menu-toggle",
            daemon=True,
        ).start()

    def _toggle_recording(self) -> None:
        with self._state_lock:
            if self._busy:
                return
            self._busy = True
            should_stop = self._recording

        try:
            if should_stop:
                self._stop_recording()
            else:
                self._start_recording()
        finally:
            with self._state_lock:
                self._busy = False

    def _start_recording(self) -> None:
        try:
            recording_dir = self.session.start(subject="")
        except Exception as exc:
            logger.exception("Failed to start macOS recording")
            self._set_idle_state()
            rumps.notification(
                "Meeting Recorder",
                "Recording failed to start",
                str(exc),
            )
            return

        with self._state_lock:
            self._recording = True
        self._set_recording_state()
        logger.info("macOS recording active: %s", recording_dir)

    def _stop_recording(self) -> None:
        try:
            recording_dir = self.session.stop()
        except Exception as exc:
            logger.exception("Failed to stop macOS recording cleanly")
            rumps.notification(
                "Meeting Recorder",
                "Recording stop failed",
                str(exc),
            )
            recording_dir = None

        with self._state_lock:
            self._recording = False
        self._set_idle_state()
        if recording_dir is not None:
            logger.info("macOS recording saved: %s", recording_dir)

    def _on_open_recordings_folder(self, _sender) -> None:
        threading.Thread(
            target=self._open_recordings_folder,
            name="macos-open-recordings",
            daemon=True,
        ).start()

    def _open_recordings_folder(self) -> None:
        try:
            path = Path(Config.load().output_dir)
            path.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(path)], check=False)
        except Exception:
            logger.exception("Failed to open macOS recordings folder")

    def _on_quit(self, _sender) -> None:
        threading.Thread(
            target=self._quit,
            name="macos-quit",
            daemon=True,
        ).start()

    def _quit(self) -> None:
        with self._state_lock:
            recording = self._recording
        if recording:
            self._stop_recording()
        self._unregister_hotkeys()
        rumps.quit_application()

    def _unregister_hotkeys(self) -> None:
        hotkeys = self._hotkeys
        self._hotkeys = None
        if hotkeys is not None:
            try:
                if self._hotkey_handle is not None:
                    hotkeys.unregister(self._hotkey_handle)
                hotkeys.stop()
            except Exception:
                logger.debug("macOS hotkey cleanup failed", exc_info=True)
        self._hotkey_handle = None

    def _set_recording_state(self) -> None:
        self.title = "REC"
        self._toggle_item.title = "Stop Recording"

    def _set_idle_state(self) -> None:
        self.title = "MR"
        self._toggle_item.title = "Start Recording"
