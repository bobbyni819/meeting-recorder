"""Windows screen recorder adapter."""

from __future__ import annotations

from pathlib import Path

from meeting_recorder.platform_support.base import ScreenRecorderBackend, ScreenTarget


class WindowsScreenRecorder(ScreenRecorderBackend):
    """Adapter around the existing Windows ``ScreenCapture`` implementation."""

    def __init__(self):
        self._capture = None

    def start(
        self,
        target: ScreenTarget,
        output_path: Path,
        fps: float = 30.0,
        quality: int = 21,
    ) -> None:
        from meeting_recorder.video.screen_capture import ScreenCapture

        self._capture = ScreenCapture(
            pid=target.pid,
            process_name=target.process_name,
            output_path=output_path,
            fps=fps,
            encoder_preference=target.encoder_preference,
            on_window_closed=target.on_window_closed,
            quality=quality,
        )
        if target.hwnd is not None:
            self._capture.switch_window(target.hwnd)
        self._capture.start()

    def stop(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture = None

    def switch_window(self, hwnd: int) -> None:
        """Switch the active Windows capture to another HWND."""
        if self._capture is not None:
            self._capture.switch_window(hwnd)

    @property
    def latest_frame(self) -> object:
        if self._capture is None:
            return None
        return self._capture.latest_frame

    @property
    def is_running(self) -> bool:
        return bool(self._capture is not None and self._capture.is_running)

    @property
    def wrapped(self) -> object:
        """Underlying existing Windows screen capture object, if started."""
        return self._capture

