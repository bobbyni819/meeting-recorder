"""macOS screen recorder backend."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from meeting_recorder.platform_support.base import ScreenRecorderBackend, ScreenTarget
from meeting_recorder.video.screen_capture import FFmpegVideoWriter

logger = logging.getLogger(__name__)


class MacScreenRecorder(ScreenRecorderBackend):
    """Record a macOS window with Quartz, falling back to full-display mss.

    This intentionally uses a simpler grab-then-write loop rather than copying
    the Windows queue/timing machinery. It is adequate for the first macOS port
    and keeps the platform-specific capture code small; missed frames are
    naturally represented by the constant-rate ffmpeg input.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._latest_frame: Optional[np.ndarray] = None
        self._target: Optional[ScreenTarget] = None
        self._output_path: Optional[Path] = None
        self._fps = 30.0
        self._quality = 21

    def start(
        self,
        target: ScreenTarget,
        output_path: Path,
        fps: float = 30.0,
        quality: int = 21,
    ) -> None:
        if self.is_running:
            return
        self._target = target
        self._output_path = Path(output_path)
        self._fps = fps
        self._quality = quality
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="macos-screen-capture",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=15.0)
            if self._thread.is_alive():
                logger.warning("macOS screen capture thread did not stop within 15s")
            self._thread = None
        self._latest_frame = None

    @property
    def latest_frame(self) -> object:
        return self._latest_frame

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _capture_loop(self) -> None:
        writer = None
        sct = None
        try:
            if self._target is None or self._output_path is None:
                return
            try:
                import cv2
            except ImportError as exc:
                raise RuntimeError(
                    "opencv-python is required for macOS screen recording"
                ) from exc

            quartz = _load_quartz()
            window_id = self._target.hwnd or _find_window_id(
                quartz, self._target.pid, self._target.process_name
            )
            use_display_fallback = window_id is None
            if use_display_fallback:
                logger.info("No target macOS window found; using full-display capture")
                sct = _open_mss()

            first = None if use_display_fallback else _grab_quartz_window(quartz, window_id)
            if first is None:
                use_display_fallback = True
                if sct is None:
                    sct = _open_mss()
                first = _grab_mss_display(sct)
            if first is None:
                logger.warning("Could not grab an initial macOS screen frame")
                if self._target.on_window_closed is not None:
                    self._target.on_window_closed()
                return

            height, width = first.shape[:2]
            writer = _create_macos_writer(
                self._output_path,
                self._fps,
                width,
                height,
                self._quality,
            )
            interval = 1.0 / self._fps if self._fps > 0 else 1.0 / 30.0
            next_frame = time.monotonic()

            while not self._stop_event.is_set():
                start = time.monotonic()
                if use_display_fallback:
                    frame = _grab_mss_display(sct)
                else:
                    frame = _grab_quartz_window(quartz, window_id)
                if frame is None and not use_display_fallback:
                    logger.info("Target macOS window is no longer capturable")
                    try:
                        sct = _open_mss()
                        use_display_fallback = True
                        frame = _grab_mss_display(sct)
                    except Exception:
                        if self._target.on_window_closed is not None:
                            self._target.on_window_closed()
                        break
                if frame is None:
                    _sleep_until(next_frame)
                    next_frame += interval
                    continue

                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                self._latest_frame = frame
                writer.write(frame)

                next_frame += interval
                # If capture fell badly behind, resync instead of sleeping on
                # a long-stale schedule.
                if start - next_frame > 2.0:
                    next_frame = time.monotonic() + interval
                _sleep_until(next_frame)
        except Exception:
            logger.exception("macOS screen capture failed")
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    logger.debug("macOS screen writer release failed", exc_info=True)
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass


def _create_macos_writer(
    output_path: Path,
    fps: float,
    width: int,
    height: int,
    quality: int,
):
    for encoder in ("h264_videotoolbox", "libx264"):
        try:
            writer = FFmpegVideoWriter(
                output_path,
                fps,
                width,
                height,
                encoder=encoder,
                quality=quality,
            )
            logger.info("macOS screen recording codec: %s (ffmpeg)", writer.encoder)
            return writer
        except Exception:
            logger.info("ffmpeg encoder %s unavailable on macOS", encoder, exc_info=True)
    raise RuntimeError("No usable macOS H.264 ffmpeg encoder found")


def _load_quartz():
    try:
        import Quartz
    except ImportError as exc:
        raise RuntimeError(
            "pyobjc-framework-Quartz is required for macOS screen recording. "
            "Install with: pip install -e '.[macos]'"
        ) from exc
    return Quartz


def _find_window_id(quartz, pid: int, process_name: str = "") -> Optional[int]:
    try:
        options = quartz.kCGWindowListOptionOnScreenOnly
        windows = quartz.CGWindowListCopyWindowInfo(options, quartz.kCGNullWindowID)
    except Exception:
        return None
    name_hint = process_name.lower()
    candidates: list[tuple[int, int]] = []
    for info in windows or []:
        try:
            owner_pid = int(info.get("kCGWindowOwnerPID", 0))
            owner_name = str(info.get("kCGWindowOwnerName", "")).lower()
            layer = int(info.get("kCGWindowLayer", 0))
            bounds = info.get("kCGWindowBounds", {}) or {}
            width = int(bounds.get("Width", 0))
            height = int(bounds.get("Height", 0))
            if layer != 0 or width <= 0 or height <= 0:
                continue
            if owner_pid == pid or (name_hint and name_hint in owner_name):
                area = width * height
                candidates.append((area, int(info.get("kCGWindowNumber"))))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _grab_quartz_window(quartz, window_id: int) -> Optional[np.ndarray]:
    try:
        image = quartz.CGWindowListCreateImage(
            quartz.CGRectNull,
            quartz.kCGWindowListOptionIncludingWindow,
            int(window_id),
            quartz.kCGWindowImageBoundsIgnoreFraming,
        )
        if image is None:
            return None
        width = int(quartz.CGImageGetWidth(image))
        height = int(quartz.CGImageGetHeight(image))
        bytes_per_row = int(quartz.CGImageGetBytesPerRow(image))
        provider = quartz.CGImageGetDataProvider(image)
        data = quartz.CGDataProviderCopyData(provider)
        raw = np.frombuffer(data, dtype=np.uint8)
        rows = raw.reshape((height, bytes_per_row))
        bgra = rows[:, : width * 4].reshape((height, width, 4))
        # Quartz window images are commonly BGRA in memory on macOS; keep BGR
        # for FFmpegVideoWriter's rawvideo bgr24 input.
        return bgra[:, :, :3].copy()
    except Exception:
        logger.debug("Quartz window grab failed for %s", window_id, exc_info=True)
        return None


def _open_mss():
    try:
        import mss
    except ImportError as exc:
        raise RuntimeError(
            "mss is required for macOS full-display fallback capture. "
            "Install with: pip install -e '.[macos]'"
        ) from exc
    return mss.mss()


def _grab_mss_display(sct) -> Optional[np.ndarray]:
    try:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        return np.asarray(shot)[:, :, :3].copy()
    except Exception:
        logger.debug("mss display grab failed", exc_info=True)
        return None


def _sleep_until(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
