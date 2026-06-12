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
import ntpath
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# Declare 64-bit-safe handle types. Window handles are pointers; left
# untyped (default c_int) they overflow with "int too long to convert" once
# another library (uiautomation, for mute detection) sets .restype on a
# shared user32 function. See the same fix in video/screen_capture.py.
try:
    import ctypes.wintypes as _wintypes

    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetForegroundWindow.argtypes = []
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = _wintypes.DWORD
except Exception:  # pragma: no cover - non-Windows / stubbed ctypes
    pass

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
        use_uia_detection: bool = True,
        use_registry_fallback: bool = True,
        privacy_first: bool = True,
        remute_grace_seconds: float = 12.0,
        clock: Optional[callable] = None,
    ):
        self._app_key = app_key.lower()
        self._target_pids = target_pids
        self._muted = start_muted
        # Privacy-first: only ever record the mic when positively confirmed
        # unmuted. Auto-unmute happens ONLY on a conclusive UIA "unmuted"
        # read (never on the flawed registry signal), and if the mute button
        # stays unreadable (toolbar hidden) for longer than the grace period
        # while unmuted, re-mute — so the recorder can never keep capturing
        # the room once it loses sight of your actual mute state.
        self._privacy_first = privacy_first
        self._remute_grace = remute_grace_seconds
        self._clock = clock or time.monotonic
        self._last_uia_ts = self._clock()
        self._on_mute_changed = on_mute_changed
        self._lock = threading.Lock()
        self._started = False
        self._manual_hotkey: str = ""
        # Sticky once the user takes manual control (dashboard button or
        # manual hotkey). The detection poller stops overriding so the
        # user can mute the recording independently of the meeting app —
        # e.g. to silence a noisy environment without muting Zoom.
        # Cleared by resume_auto_sync() (dashboard right-click).
        self._manual_override = False
        # Detection backends. UIA reads the actual mute-button state from
        # the meeting window and always wins when conclusive; the registry
        # mic-usage signal is only a fallback because Zoom/Teams keep the
        # mic device open while soft-muted (registry says "unmuted" all
        # meeting).
        self._use_uia_detection = use_uia_detection
        self._use_registry_fallback = use_registry_fallback
        # Last conclusive UIA reading this recording. While set, the
        # poller holds the current state through inconclusive polls
        # (toolbar hidden, window minimized) instead of flapping to the
        # registry signal.
        self._last_uia_state: Optional[bool] = None
        # Detection poller: watches the meeting app's mute state so
        # mouse clicks on Zoom's mute button (which don't trigger the
        # hotkey hook) are still picked up within ~1 second.
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        # Background thread that re-detects after resume_auto_sync (joined in
        # tests; the UIA walk must not run on the dashboard's Tk thread).
        self._resume_thread: Optional[threading.Thread] = None

    @property
    def is_muted(self) -> bool:
        with self._lock:
            return self._muted

    def toggle(self) -> None:
        """Manually toggle mute state (for resync or manual control)."""
        with self._lock:
            self._muted = not self._muted
            self._manual_override = True
            muted = self._muted
            state = "MUTED" if muted else "UNMUTED"
        logger.info("Mic mute toggled: %s (manual override on)", state)
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

        # Start detection polling: detects mute toggles from mouse
        # clicks on the meeting app's mute button (which don't trigger
        # our hotkey hook). UIA-first, registry fallback.
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_detection_loop,
            name="mute-state-poller",
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

    def resume_auto_sync(self) -> None:
        """Clear the manual override and immediately re-run detection once.

        Hands mute control back to auto-detection after a manual
        dashboard/hotkey correction (which is otherwise sticky for the
        rest of the recording).
        """
        with self._lock:
            self._manual_override = False
            # Restart the privacy-first blind grace window from NOW. Otherwise
            # the timer inherits a stale _last_uia_ts frozen during the
            # override period and could re-mute the user instantly on resume.
            self._last_uia_ts = self._clock()
        logger.info("Mute sync: manual override cleared (auto-sync resumed)")
        # Run detection off the UI thread — the UIA tree walk can take up to
        # ~1s and resume_auto_sync is called from the dashboard (Tk) thread.
        self._resume_thread = threading.Thread(
            target=self._resume_detect, name="mute-resume-detect", daemon=True,
        )
        self._resume_thread.start()

    def _resume_detect(self) -> None:
        try:
            self._run_detection_cycle(apply_held=True)
        except Exception:
            logger.debug("Mute re-detection after resume failed", exc_info=True)

    def _poll_detection_loop(self) -> None:
        """Poll the meeting app's mute state while not manually overridden.

        Detection order per cycle (see _run_detection_cycle):
        UIA mute-button state first, held UIA state second, registry
        mic-usage signal last. Backs off to a slow interval after
        repeated errors and restores the fast interval on recovery.
        """
        poll_interval = 1.0  # seconds between polls
        backoff_interval = 10.0  # after repeated errors, avoid log spam
        interval = poll_interval
        consecutive_errors = 0

        while not self._poll_stop.is_set():
            try:
                with self._lock:
                    overridden = self._manual_override
                if overridden:
                    self._poll_stop.wait(interval)
                    continue
                self._run_detection_cycle(apply_held=False)
                # Successful poll (even if inconclusive): undo any backoff.
                consecutive_errors = 0
                interval = poll_interval
            except Exception:
                consecutive_errors += 1
                if consecutive_errors == 1:
                    logger.debug("Mute state polling failed", exc_info=True)
                if consecutive_errors > 10:
                    interval = backoff_interval

            self._poll_stop.wait(interval)

    def _run_detection_cycle(self, apply_held: bool) -> None:
        """Run one detection pass and apply the result (override-gated).

        Order:
        1. UIA: read the actual mute-button state from the meeting
           window. Conclusive UIA always wins over registry.
        2. Held UIA state: once UIA has been conclusive this recording,
           inconclusive polls hold the current state rather than
           flapping to the registry signal. Only re-applied when
           ``apply_held`` is True (resume_auto_sync); the periodic
           poller must not undo hotkey blind-toggles.
        3. Registry mic-usage fallback (legacy behavior), only when UIA
           never concluded and the constructor flag allows.
        """
        # 1. UIA — the real mute-button state. Conclusive read always wins.
        uia = self._detect_via_uia() if self._use_uia_detection else None
        if uia is not None:
            with self._lock:
                self._last_uia_state = uia
                self._last_uia_ts = self._clock()
            self._apply_detected_state(uia, "uia")
            return

        # 2. UIA is blind (toolbar hidden / window minimized).
        if self._privacy_first:
            self._privacy_blind_cycle()
            return

        # Legacy (non-privacy) behavior: hold the last conclusive UIA state
        # through inconclusive polls; only re-apply on resume_auto_sync.
        with self._lock:
            held = self._last_uia_state
        if held is not None:
            if apply_held:
                self._apply_detected_state(held, "uia-held")
            return
        if self._use_registry_fallback:
            reg = self._detect_via_any_pid()
            if reg is not None:
                self._apply_detected_state(reg, "registry")

    def _privacy_blind_cycle(self) -> None:
        """Privacy-first handling when the mute button can't be read.

        Never trusts the registry to UNMUTE (it can't see soft-mute). If we
        are currently unmuted and have been unable to confirm it for longer
        than the grace period, re-mute — the recorder must not keep capturing
        the room once it loses sight of your real mute state.
        """
        with self._lock:
            muted_now = self._muted
            overridden = self._manual_override
            blind_for = self._clock() - self._last_uia_ts
        if overridden:
            return  # manual control wins — never auto-mute/unmute
        if not muted_now and blind_for >= self._remute_grace:
            self._apply_detected_state(True, "privacy-remute")
            return
        # Registry may only *mute* (e.g. the app released the mic / call
        # ended), never unmute, in privacy-first mode.
        if self._use_registry_fallback:
            reg = self._detect_via_any_pid()
            if reg is True:
                self._apply_detected_state(True, "registry-mute")

    def _apply_detected_state(self, detected: bool, source: str) -> None:
        """Apply an auto-detected mute state unless manually overridden."""
        with self._lock:
            changed = detected != self._muted and not self._manual_override
            if changed:
                self._muted = detected
        if changed:
            logger.info(
                "Mute sync: %s detected %s",
                source, "MUTED" if detected else "UNMUTED",
            )
            self._fire_mute_changed(detected)

    def _detect_via_uia(self) -> Optional[bool]:
        """Read the mute-button state from the meeting app's UIA tree."""
        try:
            from meeting_recorder.audio.uia_mute_detector import detect_mute_state
        except Exception:
            return None
        return detect_mute_state(self._expanded_target_pids())

    def _expanded_target_pids(self) -> set[int]:
        """Target PIDs plus their children (WebView2, renderer, etc.).

        Meeting apps often spawn child processes; the window- or
        mic-owning PID may not be the top-level PID we were given.
        """
        import psutil

        pids: set[int] = set(self._target_pids)
        for pid in list(self._target_pids):
            try:
                for child in psutil.Process(pid).children(recursive=True):
                    pids.add(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return pids

    def _detect_via_any_pid(self) -> Optional[bool]:
        """Try detect_initial_mute_state against each target PID.

        Returns the first conclusive answer across targets and their
        child processes.
        """
        for pid in self._expanded_target_pids():
            result = detect_initial_mute_state(pid, include_packaged=True)
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
            self._manual_override = True
            muted = self._muted
            state = "MUTED" if muted else "UNMUTED"
        logger.info("Mute sync: manual toggle -> %s (manual override on)", state)
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


_MIC_CONSENT_KEY_PATH = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion"
    r"\CapabilityAccessManager\ConsentStore\microphone"
)


def detect_initial_mute_state(
    pid: int, include_packaged: bool = False,
) -> Optional[bool]:
    """Detect whether the meeting app is currently muted via registry.

    Checks the Windows CapabilityAccessManager registry for microphone
    usage by the process. If the mic was recently released (LastUsedTimeStop
    > 0), the app is likely muted. If the mic is actively in use
    (LastUsedTimeStop == 0), the app is likely unmuted.

    Scans the ``NonPackaged`` subtree (classic exes like Zoom) first,
    then — only when ``include_packaged`` — packaged-app keys (MSIX,
    e.g. new Teams ``MSTeams_*``).

    ``include_packaged`` defaults to False because the initial-state call
    must stay conservative: Teams holds the mic device open while
    soft-muted, so the packaged signal would flip the safe MUTED default
    to UNMUTED at recording start and capture mic audio from t=0. The
    poller opts in (UIA-gated) where a wrong read self-corrects.

    Args:
        pid: Process ID of the meeting app.
        include_packaged: Also scan MSIX packaged-app consent keys.

    Returns:
        False if unmuted (mic in use), True if muted (mic not in use),
        None if detection failed.
    """
    import psutil

    try:
        exe_path = psutil.Process(pid).exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        logger.debug("Could not get exe path for PID %d", pid)
        return None

    result = _detect_nonpackaged_mute_state(pid, exe_path)
    if result is not None:
        return result
    if not include_packaged:
        return None
    return _detect_packaged_mute_state(pid, exe_path)


def _detect_nonpackaged_mute_state(pid: int, exe_path: str) -> Optional[bool]:
    """Scan the NonPackaged consent subtree (classic Win32 exes)."""
    import winreg

    # Convert exe path to registry format: replace \ and / with #
    exe_registry = exe_path.replace("\\", "#").replace("/", "#")

    base_key_path = _MIC_CONSENT_KEY_PATH + r"\NonPackaged"

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
                    value = _query_last_used_stop(subkey)
                finally:
                    winreg.CloseKey(subkey)
            except OSError:
                logger.debug(
                    "Could not open subkey %s", subkey_name,
                )
                continue
            if value is None:
                logger.debug(
                    "Could not read LastUsedTimeStop from subkey %s",
                    subkey_name,
                )
                continue
            if value == 0:
                logger.info(
                    "Registry mute detection: PID %d (%s) mic IN USE "
                    "(unmuted)",
                    pid, exe_path,
                )
                return False
            logger.info(
                "Registry mute detection: PID %d (%s) mic NOT in "
                "use (muted)",
                pid, exe_path,
            )
            return True
    finally:
        winreg.CloseKey(base_key)

    logger.debug(
        "No matching registry subkey for exe %s (registry format: %s)",
        exe_path, exe_registry,
    )
    return None


def _detect_packaged_mute_state(pid: int, exe_path: str) -> Optional[bool]:
    """Scan packaged-app (MSIX) consent keys for mic usage.

    Packaged apps — e.g. new Teams, package family ``MSTeams_...`` —
    do not appear under ``NonPackaged``; their keys sit directly under
    ``ConsentStore\\microphone`` named by package family name, with
    ``LastUsedTimeStop`` on the key itself or on its subkeys. The exe
    name is matched against the family name with separators stripped
    (``ms-teams`` -> ``msteams`` matches ``MSTeams_8wekyb3d8bbwe``).
    """
    import winreg

    exe_name = ntpath.splitext(ntpath.basename(exe_path))[0]
    norm_exe = re.sub(r"[^a-z0-9]", "", exe_name.lower())
    if not norm_exe:
        return None

    try:
        base_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_CONSENT_KEY_PATH)
    except OSError:
        logger.debug("Registry key not found: HKCU\\%s", _MIC_CONSENT_KEY_PATH)
        return None

    try:
        idx = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(base_key, idx)
            except OSError:
                break
            idx += 1

            if subkey_name == "NonPackaged":
                continue
            # Family names look like "MSTeams_8wekyb3d8bbwe".
            family = subkey_name.split("_", 1)[0]
            norm_family = re.sub(r"[^a-z0-9]", "", family.lower())
            if not norm_family:
                continue
            if norm_exe not in norm_family and norm_family not in norm_exe:
                continue

            try:
                subkey = winreg.OpenKey(base_key, subkey_name)
            except OSError:
                continue
            try:
                value = _query_last_used_stop(subkey)
                if value is None:
                    value = _query_last_used_stop_in_children(subkey)
            finally:
                winreg.CloseKey(subkey)
            if value is None:
                continue
            muted = value != 0
            logger.info(
                "Registry mute detection (packaged %s): PID %d (%s) mic %s",
                subkey_name, pid, exe_path,
                "NOT in use (muted)" if muted else "IN USE (unmuted)",
            )
            return muted
    finally:
        winreg.CloseKey(base_key)

    logger.debug("No matching packaged consent key for exe %s", exe_path)
    return None


def _query_last_used_stop(key) -> Optional[int]:
    """Read the LastUsedTimeStop value from an open registry key."""
    import winreg

    try:
        value, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
        return value
    except OSError:
        return None


def _query_last_used_stop_in_children(key) -> Optional[int]:
    """Read LastUsedTimeStop from the first child subkey that has it."""
    import winreg

    idx = 0
    while True:
        try:
            child_name = winreg.EnumKey(key, idx)
        except OSError:
            return None
        idx += 1
        try:
            child = winreg.OpenKey(key, child_name)
        except OSError:
            continue
        try:
            value = _query_last_used_stop(child)
        finally:
            winreg.CloseKey(child)
        if value is not None:
            return value
