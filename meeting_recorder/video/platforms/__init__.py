"""Platform-specific video/window backend selection.

On Windows: uses Win32 API (EnumWindows, PrintWindow, GetWindowRect).
On macOS:   stubs — to be implemented with Quartz/CoreGraphics + mss.

Consumers should import from this module instead of directly from
platform-specific files.
"""

from __future__ import annotations

import sys

PLATFORM = sys.platform

if PLATFORM == "win32":
    from meeting_recorder.video.window_finder import (
        find_window_by_pid,
        find_window_by_process_name,
        get_window_rect,
        get_window_title,
        get_hwnd_pid,
        list_visible_windows,
    )
    from meeting_recorder.video.screen_capture import ScreenCapture

elif PLATFORM == "darwin":
    from meeting_recorder.video.platforms.macos import (  # noqa: F401
        find_window_by_pid,
        find_window_by_process_name,
        get_window_rect,
        get_window_title,
        get_hwnd_pid,
        list_visible_windows,
        ScreenCapture,
    )

else:
    raise ImportError(
        f"Platform '{PLATFORM}' is not supported for video capture."
    )

__all__ = [
    "find_window_by_pid",
    "find_window_by_process_name",
    "get_window_rect",
    "get_window_title",
    "get_hwnd_pid",
    "list_visible_windows",
    "ScreenCapture",
]
