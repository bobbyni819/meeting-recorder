"""Factory functions for platform-specific backend adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from meeting_recorder.platform_support import detect
from meeting_recorder.platform_support.base import (
    AudioSource,
    HotkeyBackend,
    MuteDetector,
    ScreenRecorderBackend,
    TrayBackend,
)


@dataclass(frozen=True)
class PlatformBackends:
    """Classes that implement the current platform's capability interfaces."""

    app_audio_source: Type[AudioSource]
    desktop_audio_source: Type[AudioSource]
    mic_audio_source: Type[AudioSource]
    mute_detector: Type[MuteDetector]
    screen_recorder: Type[ScreenRecorderBackend]
    tray: Type[TrayBackend]
    hotkeys: Type[HotkeyBackend]


def get_backends() -> PlatformBackends:
    """Return backend adapter classes for the current platform.

    Imports are intentionally lazy so importing ``meeting_recorder.platform_support``
    never loads Windows-only dependencies on macOS, or future macOS-only
    dependencies on Windows.
    """
    os_name = detect.current_os()
    if os_name == "windows":
        from meeting_recorder.platform_support.windows import create_backends

        return create_backends()
    if os_name == "macos":
        try:
            from meeting_recorder.platform_support.macos import create_backends
        except ImportError as exc:
            raise NotImplementedError(
                "macOS backend dependencies are not installed. Install with: "
                "pip install -e '.[macos]'"
            ) from exc
        return create_backends()
    raise NotImplementedError(f"Platform '{os_name}' is not supported yet.")


def get_mute_detector() -> MuteDetector:
    """Construct the current platform's mute detector adapter."""
    return get_backends().mute_detector()


def get_screen_recorder() -> ScreenRecorderBackend:
    """Construct the current platform's screen recorder adapter."""
    return get_backends().screen_recorder()


def get_hotkey_backend() -> HotkeyBackend:
    """Construct the current platform's global hotkey adapter."""
    return get_backends().hotkeys()


def get_tray_backend(*args, **kwargs) -> TrayBackend:
    """Construct the current platform's tray adapter."""
    return get_backends().tray(*args, **kwargs)

