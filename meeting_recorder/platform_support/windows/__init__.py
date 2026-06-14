"""Windows adapters for the platform abstraction layer."""

from __future__ import annotations

from meeting_recorder.platform_support.factory import PlatformBackends
from meeting_recorder.platform_support.windows.audio import (
    WindowsAppAudioSource,
    WindowsDesktopAudioSource,
    WindowsMicAudioSource,
)
from meeting_recorder.platform_support.windows.mute import WindowsMuteDetector
from meeting_recorder.platform_support.windows.screen import WindowsScreenRecorder
from meeting_recorder.platform_support.windows.ui import WindowsHotkeyBackend, WindowsTrayBackend


def create_backends() -> PlatformBackends:
    """Return the Windows backend adapter classes."""
    return PlatformBackends(
        app_audio_source=WindowsAppAudioSource,
        desktop_audio_source=WindowsDesktopAudioSource,
        mic_audio_source=WindowsMicAudioSource,
        mute_detector=WindowsMuteDetector,
        screen_recorder=WindowsScreenRecorder,
        tray=WindowsTrayBackend,
        hotkeys=WindowsHotkeyBackend,
    )


__all__ = [
    "WindowsAppAudioSource",
    "WindowsDesktopAudioSource",
    "WindowsMicAudioSource",
    "WindowsMuteDetector",
    "WindowsScreenRecorder",
    "WindowsTrayBackend",
    "WindowsHotkeyBackend",
    "create_backends",
]

