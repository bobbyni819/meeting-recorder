"""Platform abstraction layer for Meeting Recorder.

Phase 1 provides the public interfaces, OS detection, factory helpers, and
Windows adapters. Existing Windows app code remains unchanged.
"""

from meeting_recorder.platform_support import detect, factory
from meeting_recorder.platform_support.base import (
    AudioSource,
    HotkeyBackend,
    MuteDetector,
    RingBufferLike,
    ScreenRecorderBackend,
    ScreenTarget,
    TrayBackend,
)

__all__ = [
    "detect",
    "factory",
    "AudioSource",
    "HotkeyBackend",
    "MuteDetector",
    "RingBufferLike",
    "ScreenRecorderBackend",
    "ScreenTarget",
    "TrayBackend",
]

