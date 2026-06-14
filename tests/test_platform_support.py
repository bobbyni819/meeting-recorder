from __future__ import annotations

import sys

from meeting_recorder.platform_support import (
    AudioSource,
    HotkeyBackend,
    MuteDetector,
    ScreenRecorderBackend,
    TrayBackend,
    detect,
    factory,
)


def test_detect_windows() -> None:
    assert detect.current_os() == "windows"
    assert detect.is_windows() is True
    assert detect.is_macos() is False


def test_get_backends_is_windows_only_and_lazy() -> None:
    sys.modules.pop("meeting_recorder.platform_support.macos", None)

    import meeting_recorder.platform_support as platform_support

    backends = platform_support.factory.get_backends()

    assert "meeting_recorder.platform_support.macos" not in sys.modules
    assert issubclass(backends.app_audio_source, AudioSource)
    assert issubclass(backends.desktop_audio_source, AudioSource)
    assert issubclass(backends.mic_audio_source, AudioSource)
    assert issubclass(backends.mute_detector, MuteDetector)
    assert issubclass(backends.screen_recorder, ScreenRecorderBackend)
    assert issubclass(backends.tray, TrayBackend)
    assert issubclass(backends.hotkeys, HotkeyBackend)


def test_windows_factory_constructs_safe_adapters() -> None:
    mute_detector = factory.get_mute_detector()
    screen_recorder = factory.get_screen_recorder()

    assert isinstance(mute_detector, MuteDetector)
    assert hasattr(mute_detector, "read_mute_state")
    assert isinstance(screen_recorder, ScreenRecorderBackend)
    assert hasattr(screen_recorder, "start")
    assert hasattr(screen_recorder, "stop")
    assert screen_recorder.latest_frame is None
    assert screen_recorder.is_running is False

