"""Operating-system detection for platform backend selection."""

from __future__ import annotations

import sys


def current_os() -> str:
    """Return the current operating system as ``windows``, ``macos``, or ``linux``.

    Unknown Python platform strings are returned unchanged so callers can include
    the exact unsupported platform in diagnostics.
    """
    platform = sys.platform
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    if platform.startswith("linux"):
        return "linux"
    return platform


def is_windows() -> bool:
    """Return True when running on Windows."""
    return current_os() == "windows"


def is_macos() -> bool:
    """Return True when running on macOS."""
    return current_os() == "macos"

