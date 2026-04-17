"""Sync mic recording mute state with the meeting app's mute shortcut.

Hooks the meeting app's mute keyboard shortcut (e.g., Alt+A for Zoom,
Ctrl+Shift+M for Teams) and toggles an internal mute flag. When muted,
the mic capture thread writes silence instead of real audio.

This works by detecting when the user presses the mute shortcut while
the meeting app window is in the foreground. Since the hook doesn't
suppress the keystroke, the meeting app also receives it and mutes/unmutes
simultaneously.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# Mute shortcuts per meeting app
APP_MUTE_SHORTCUTS = {
    "zoom": "alt+a",
    "teams": "ctrl+shift+m",
    "webex": "ctrl+m",
}


class MuteSync:
    """Detects meeting app mute/unmute and syncs with mic recording.

    Hooks the meeting app's mute keyboard shortcut. When the user presses
    the shortcut while the meeting app is in the foreground, the internal
    mute state toggles. The mic capture thread checks is_muted to decide
    whether to write real audio or silence.
    """

    def __init__(
        self,
        app_key: str,
        target_pids: set[int],
        start_muted: bool = False,
        on_mute_changed: Optional[callable] = None,
    ):
        self._app_key = app_key.lower()
        self._target_pids = target_pids
        self._muted = start_muted
        self._on_mute_changed = on_mute_changed
        self._lock = threading.Lock()
        self._started = False
        self._manual_hotkey: str = ""
        # Registry poller: watches the Windows mic-usage registry so
        # mouse clicks on Zoom's mute button (which don't trigger the
        # hotkey hook) are still picked up within ~1 second.
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()

    @property
    def is_muted(self) -> bool:
        with self._lock:
            return self._muted

    def toggle(self) -> None:
        """Manually toggle mute state (for resync or manual control)."""
        with self._lock:
            self._muted = not self._muted
            muted = self._muted
            state = "MUTED" if muted else "UNMUTED"
        logger.info("Mic mute toggled: %s", state)
        self._fire_mute_changed(muted)

    def _fire_mute_changed(self, is_muted: bool) -> None:
        """Notify listener of mute state change."""
        if self._on_mute_changed is not None:
            try:
                self._on_mute_changed(is_muted)
            except Exception:
                logger.debug("on_mute_changed callback error", exc_info=True)

    def start(self, manual_hotkey: str = "ctrl+shift+u") -> None:
        """Register keyboard hooks for mute sync and manual toggle."""
        if self._started:
            return

        try:
            import keyboard

            # Hook the meeting app's mute shortcut (e.g., Alt+A for Zoom)
            shortcut = APP_MUTE_SHORTCUTS.get(self._app_key)
            if shortcut:
                keyboard.add_hotkey(
                    shortcut,
                    self._on_mute_shortcut_pressed,
                    suppress=False,
                    trigger_on_release=False,
                )
                logger.info(
                    "Mute sync: monitoring '%s' for %s",
                    shortcut,
                    self._app_key,
                )
            else:
                logger.warning(
                    "No mute shortcut known for app '%s'.", self._app_key,
                )

            # Register manual toggle hotkey (always available)
            keyboard.add_hotkey(
                manual_hotkey,
                self._on_manual_toggle,
                suppress=False,
            )
            logger.info(
                "Mute sync: manual toggle hotkey registered: %s", manual_hotkey,
            )

            state = "MUTED" if self._muted else "UNMUTED"
            logger.info("Mute sync started (initial state: %s)", state)
            self._started = True
            self._manual_hotkey = manual_hotkey
        except Exception:
            logger.exception("Failed to register mute sync hotkeys")

        # Start registry-polling fallback: detects mute toggles from
        # mouse clicks on the meeting app's mute button (which don't
        # trigger our hotkey hook).
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_registry_loop,
            name="mute-registry-poller",
            daemon=True,
        )
        self._poll_thread.start()

    def stop(self) -> None:
        """Unregister keyboard hooks."""
        if not self._started:
            return
        try:
            import keyboard

            shortcut = APP_MUTE_SHORTCUTS.get(self._app_key)
            if shortcut:
                keyboard.remove_hotkey(shortcut)
            if self._manual_hotkey:
                keyboard.remove_hotkey(self._manual_hotkey)
        except Exception:
            logger.debug("Failed to remove mute sync hotkeys", exc_info=True)
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        self._started = False

    def _poll_registry_loop(self) -> None:
        """Poll Windows registry for meeting-app mic usage changes.

        The ``LastUsedTimeStop`` value under
        ``HKCU\\...\\CapabilityAccessManager\\ConsentStore\\microphone``
        is 0 while an app is actively using the mic and nonzero otherwise.
        This works regardless of how the user toggled mute (hotkey or
        mouse click on the app's button).
        """
        interval = 1.0  # seconds between polls
        # Find any live PID from the target set to query
        consecutive_errors = 0

        while not self._poll_stop.is_set():
            try:
                detected = self._detect_via_any_pid()
                if detected is not None:
                    with self._lock:
                        changed = detected != self._muted
                        if changed:
                            self._muted = detected
                            state = "MUTED" if detected else "UNMUTED"
                    if changed:
                        logger.info("Mute sync: registry detected %s", state)
                        self._fire_mute_changed(detected)
                    consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                if consecutive_errors == 1:
                    logger.debug("Registry mute polling failed", exc_info=True)
                if consecutive_errors > 10:
                    # Registry not accessible; back off to avoid log spam
                    interval = 10.0

            self._poll_stop.wait(interval)

    def _detect_via_any_pid(self) -> Optional[bool]:
        """Try detect_initial_mute_state against each target PID.

        Meeting apps often spawn child processes; the mic-owning PID
        may not be the top-level PID we were given. Try them all and
        return the first conclusive answer.
        """
        import psutil

        # Include target_pids plus their children (WebView2, renderer, etc.)
        pids_to_try: set[int] = set(self._target_pids)
        for pid in list(self._target_pids):
            try:
                for child in psutil.Process(pid).children(recursive=True):
                    pids_to_try.add(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        for pid in pids_to_try:
            result = detect_initial_mute_state(pid)
            if result is not None:
                return result
        return None

    def _on_mute_shortcut_pressed(self) -> None:
        """Called when the mute shortcut is pressed. Only toggles if
        the meeting app window is currently in the foreground."""
        if not self._is_meeting_app_focused():
            return

        with self._lock:
            self._muted = not self._muted
            muted = self._muted
            state = "MUTED" if muted else "UNMUTED"
        logger.info("Mute sync: detected %s shortcut -> %s", self._app_key, state)
        self._fire_mute_changed(muted)

    def _on_manual_toggle(self) -> None:
        """Called when the manual toggle hotkey is pressed (works anywhere)."""
        with self._lock:
            self._muted = not self._muted
            muted = self._muted
            state = "MUTED" if muted else "UNMUTED"
        logger.info("Mute sync: manual toggle -> %s", state)
        self._fire_mute_changed(muted)

    def _is_meeting_app_focused(self) -> bool:
        """Check if the foreground window belongs to the meeting app."""
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return pid.value in self._target_pids
        except Exception:
            return False


def get_all_pids_for_process(process_name: str) -> set[int]:
    """Get all PIDs for a given process name (e.g., 'zoom.exe')."""
    import psutil

    pids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == process_name.lower():
                pids.add(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def detect_initial_mute_state(pid: int) -> Optional[bool]:
    """Detect whether the meeting app is currently muted via registry.

    Checks the Windows CapabilityAccessManager registry for microphone
    usage by the process. If the mic was recently released (LastUsedTimeStop
    > 0), the app is likely muted. If the mic is actively in use
    (LastUsedTimeStop == 0), the app is likely unmuted.

    Args:
        pid: Process ID of the meeting app.

    Returns:
        False if unmuted (mic in use), True if muted (mic not in use),
        None if detection failed.
    """
    import winreg

    import psutil

    try:
        exe_path = psutil.Process(pid).exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        logger.debug("Could not get exe path for PID %d", pid)
        return None

    # Convert exe path to registry format: replace \ and / with #
    exe_registry = exe_path.replace("\\", "#").replace("/", "#")

    base_key_path = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion"
        r"\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"
    )

    try:
        base_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_key_path)
    except OSError:
        logger.debug("Registry key not found: HKCU\\%s", base_key_path)
        return None

    try:
        idx = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(base_key, idx)
            except OSError:
                break
            idx += 1

            if exe_registry.lower() not in subkey_name.lower():
                continue

            try:
                subkey = winreg.OpenKey(base_key, subkey_name)
                try:
                    value, _ = winreg.QueryValueEx(subkey, "LastUsedTimeStop")
                    if value == 0:
                        logger.info(
                            "Registry mute detection: PID %d (%s) mic IN USE "
                            "(unmuted)",
                            pid, exe_path,
                        )
                        return False
                    else:
                        logger.info(
                            "Registry mute detection: PID %d (%s) mic NOT in "
                            "use (muted)",
                            pid, exe_path,
                        )
                        return True
                finally:
                    winreg.CloseKey(subkey)
            except OSError:
                logger.debug(
                    "Could not read LastUsedTimeStop from subkey %s",
                    subkey_name,
                )
                continue
    finally:
        winreg.CloseKey(base_key)

    logger.debug(
        "No matching registry subkey for exe %s (registry format: %s)",
        exe_path, exe_registry,
    )
    return None
