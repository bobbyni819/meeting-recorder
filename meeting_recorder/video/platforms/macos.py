"""macOS video/window backend stubs.

To implement macOS window operations:
- Window enumeration: CGWindowListCopyWindowInfo (via Quartz/CoreGraphics)
- Window titles: kCGWindowName from CGWindowListCopyWindowInfo
- Window geometry: kCGWindowBounds from CGWindowListCopyWindowInfo
- Screen capture: mss (already cross-platform) or ScreenCaptureKit
- PID lookup: kCGWindowOwnerPID from CGWindowListCopyWindowInfo

Install Quartz bindings: pip install pyobjc-framework-Quartz
"""

from __future__ import annotations

from typing import Optional

_NOT_IMPL = "macOS video backend not yet implemented. See video/platforms/macos.py"


def find_window_by_pid(pid: int) -> Optional[int]:
    """Find a window handle for a given PID on macOS.

    Use CGWindowListCopyWindowInfo with kCGWindowListOptionOnScreenOnly,
    filter by kCGWindowOwnerPID == pid.
    Returns the CGWindowID (int) or None.
    """
    raise NotImplementedError(_NOT_IMPL)


def find_window_by_process_name(process_name: str) -> Optional[int]:
    """Find a window handle by process name on macOS."""
    raise NotImplementedError(_NOT_IMPL)


def get_window_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """Get window rectangle (left, top, right, bottom) on macOS.

    Use CGWindowListCopyWindowInfo, find window by ID,
    read kCGWindowBounds dict (X, Y, Width, Height).
    """
    raise NotImplementedError(_NOT_IMPL)


def get_window_title(hwnd: int) -> str:
    """Get window title on macOS.

    Use CGWindowListCopyWindowInfo, find window by ID,
    read kCGWindowName.
    """
    raise NotImplementedError(_NOT_IMPL)


def get_hwnd_pid(hwnd: int) -> Optional[int]:
    """Get PID from window handle on macOS.

    Use CGWindowListCopyWindowInfo, find window by ID,
    read kCGWindowOwnerPID.
    """
    raise NotImplementedError(_NOT_IMPL)


def list_visible_windows(
    min_width: int = 200,
    min_height: int = 150,
    exclude_pids: set | None = None,
) -> list[dict]:
    """List visible windows on macOS.

    Use CGWindowListCopyWindowInfo with kCGWindowListOptionOnScreenOnly
    and kCGWindowListExcludeDesktopElements.

    Returns list of dicts with: hwnd, pid, title, process_name, rect.
    """
    raise NotImplementedError(_NOT_IMPL)


class ScreenCapture:
    """Screen capture stub for macOS.

    macOS options:
    - mss: already cross-platform, captures screen region. Works out of
      the box but captures the full region (including overlapping windows).
    - ScreenCaptureKit (macOS 12.3+): SCStream can capture a specific
      window without overlaps, similar to Win32 PrintWindow.
    - CGWindowListCreateImage: capture a specific window by CGWindowID.
    """

    def __init__(self, hwnd: int, output_path, fps: float = 30.0,
                 on_frame=None):
        raise NotImplementedError(_NOT_IMPL)

    def start(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    def stop(self) -> None:
        raise NotImplementedError(_NOT_IMPL)

    def switch_window(self, hwnd: int) -> None:
        raise NotImplementedError(_NOT_IMPL)

    @property
    def is_running(self) -> bool:
        return False
