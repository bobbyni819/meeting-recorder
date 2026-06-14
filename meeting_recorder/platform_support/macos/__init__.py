"""macOS adapters for the platform abstraction layer."""

from __future__ import annotations

from meeting_recorder.platform_support.factory import PlatformBackends
from meeting_recorder.platform_support.macos.audio import (
    MacMicAudioSource,
    MacSystemAudioSource,
)
from meeting_recorder.platform_support.macos.hotkeys import MacHotkeyBackend
from meeting_recorder.platform_support.macos.mute import MacMuteDetector
from meeting_recorder.platform_support.macos.screen import MacScreenRecorder
from meeting_recorder.platform_support.macos.tray import MacTrayBackend


def create_backends() -> PlatformBackends:
    """Return the macOS backend adapter classes."""
    return PlatformBackends(
        app_audio_source=MacSystemAudioSource,
        desktop_audio_source=MacSystemAudioSource,
        mic_audio_source=MacMicAudioSource,
        mute_detector=MacMuteDetector,
        screen_recorder=MacScreenRecorder,
        tray=MacTrayBackend,
        hotkeys=MacHotkeyBackend,
    )


__all__ = [
    "MacMicAudioSource",
    "MacSystemAudioSource",
    "MacMuteDetector",
    "MacScreenRecorder",
    "MacTrayBackend",
    "MacHotkeyBackend",
    "create_backends",
]

