"""Find and track meeting application windows by process ID."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from typing import Optional

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# Enable DPI awareness so GetWindowRect returns real pixel coordinates
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass

# Callback type for EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)


def find_window_by_pid(pid: int) -> Optional[int]:
    """Find the main visible window handle for a process ID.

    Returns the HWND of the largest visible window belonging to the process,
    or None if no suitable window is found.
    """
    candidates = []

    def _enum_callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        # Get the PID that owns this window
        found_pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found_pid))
        if found_pid.value != pid:
            return True
        # Must have a non-empty title
        title_len = user32.GetWindowTextLengthW(hwnd)
        if title_len <= 0:
            return True
        candidates.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)

    if not candidates:
        return None

    # Pick the largest window (most likely the main meeting window)
    best_hwnd = None
    best_area = 0
    for hwnd in candidates:
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        area = w * h
        if area > best_area:
            best_area = area
            best_hwnd = hwnd
    return best_hwnd


def find_window_by_process_name(process_name: str) -> Optional[int]:
    """Fallback: find window by scanning all processes with the given name.

    Useful when the specific PID doesn't own a window (e.g., Zoom spawns
    the meeting window under a child process).
    """
    import psutil

    target_pids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"].lower() == process_name.lower():
                target_pids.add(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    best_hwnd = None
    best_area = 0

    for pid in target_pids:
        hwnd = find_window_by_pid(pid)
        if hwnd is None:
            continue
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area > best_area:
            best_area = area
            best_hwnd = hwnd

    return best_hwnd


def get_window_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """Get window bounding box as (left, top, width, height).

    Returns None if the window is minimized or invalid.
    """
    if user32.IsIconic(hwnd):  # minimized
        return None

    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None

    left = rect.left
    top = rect.top
    width = rect.right - rect.left
    height = rect.bottom - rect.top

    if width <= 0 or height <= 0:
        return None

    return (left, top, width, height)


def get_window_title(hwnd: int) -> str:
    """Get the window title text."""
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return buf.value


def get_hwnd_pid(hwnd: int) -> Optional[int]:
    """Return the PID that owns the given window handle, or None."""
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value if pid.value else None


def list_visible_windows(
    min_width: int = 200,
    min_height: int = 150,
) -> list[tuple[int, str, int, str]]:
    """Enumerate all visible top-level windows with non-empty titles.

    Returns a list of (hwnd, title, pid, process_name) tuples, sorted
    alphabetically by title. Excludes untitled windows and windows
    smaller than the minimum size thresholds.

    Args:
        min_width: Minimum window width in pixels (default 200).
        min_height: Minimum window height in pixels (default 150).
    """
    import psutil

    results: list[tuple[int, str, int]] = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title_len = user32.GetWindowTextLengthW(hwnd)
        if title_len <= 0:
            return True
        buf = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, buf, title_len + 1)
        title = buf.value.strip()
        if not title:
            return True
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < min_width or h < min_height:
            return True
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((hwnd, title, pid.value))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)

    # Resolve process names via psutil (batch lookup)
    pid_to_name: dict[int, str] = {}
    unique_pids = {pid for _, _, pid in results}
    for pid in unique_pids:
        try:
            pid_to_name[pid] = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pid_to_name[pid] = "unknown"

    enriched = [
        (hwnd, title, pid, pid_to_name.get(pid, "unknown"))
        for hwnd, title, pid in results
    ]
    enriched.sort(key=lambda x: x[1].lower())
    return enriched
