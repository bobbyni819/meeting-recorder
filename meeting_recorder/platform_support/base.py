"""Small platform capability interfaces used by Meeting Recorder.

These abstractions describe the platform-specific surface the app needs while
preserving the existing Windows implementations. Audio sources produce
16 kHz, mono, int16 PCM chunks through the same ring-buffer contract used by
the current capture pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol


class RingBufferLike(Protocol):
    """Minimal ring-buffer API used by audio capture sources."""

    def put(self, chunk: bytes) -> None:
        """Append one PCM chunk to the buffer."""

    def get(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Return the oldest chunk, or None if no chunk arrives before timeout."""


class AudioSource(ABC):
    """Audio capture source that writes normalized PCM chunks to a ring buffer."""

    @abstractmethod
    def start(self) -> None:
        """Start producing audio chunks."""

    @abstractmethod
    def stop(self) -> None:
        """Stop producing audio chunks."""

    @abstractmethod
    def close(self) -> None:
        """Release any resources held by the source."""

    @abstractmethod
    def get_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Return one 16 kHz mono int16 PCM chunk from the source buffer."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether the source is actively capturing."""


class MuteDetector(ABC):
    """Read the soft-mute state from a meeting app when the platform allows it."""

    @abstractmethod
    def read_mute_state(
        self,
        app_key: str,
        target_pids: Optional[set[int]] = None,
    ) -> Optional[bool]:
        """Return True for muted, False for unmuted, or None if inconclusive."""


@dataclass(frozen=True)
class ScreenTarget:
    """Target metadata needed to start a platform screen recorder."""

    pid: int
    process_name: str = ""
    hwnd: Optional[int] = None
    encoder_preference: str = "nvenc"
    on_window_closed: Optional[Callable[[], None]] = None


class ScreenRecorderBackend(ABC):
    """Video recorder for a meeting window or process-owned window."""

    @abstractmethod
    def start(
        self,
        target: ScreenTarget,
        output_path: Path,
        fps: float = 30.0,
        quality: int = 21,
    ) -> None:
        """Start recording *target* to *output_path*."""

    @abstractmethod
    def stop(self) -> None:
        """Stop recording and finalize the output."""

    @property
    @abstractmethod
    def latest_frame(self) -> object:
        """Most recent preview frame, or None before capture starts."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether the recorder is actively capturing."""


class TrayBackend(ABC):
    """System tray icon backend."""

    @abstractmethod
    def run(self) -> None:
        """Run the tray icon event loop."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the tray icon event loop."""

    @abstractmethod
    def set_state(self, state: str, status_text: str = "") -> None:
        """Update tray state and status text."""


class HotkeyBackend(ABC):
    """Global hotkey registration backend."""

    @abstractmethod
    def register(self, combo: str, callback: Callable[[], None]) -> object:
        """Register *callback* for *combo* and return a backend handle."""

    @abstractmethod
    def unregister(self, handle_or_combo: object) -> None:
        """Unregister a previously registered hotkey."""

    @abstractmethod
    def start(self) -> None:
        """Start the backend if it has an explicit event loop."""

    @abstractmethod
    def stop(self) -> None:
        """Unregister all hotkeys and stop the backend."""

