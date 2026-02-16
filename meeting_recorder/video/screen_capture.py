"""Screen capture of meeting application windows to MP4 video."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from meeting_recorder.video.window_finder import (
    find_window_by_pid,
    find_window_by_process_name,
    get_window_rect,
    get_window_title,
)

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Captures the meeting application window to an MP4 video file.

    Finds the window by PID, tracks its position each frame, and writes
    frames at a configurable FPS using OpenCV's VideoWriter.
    """

    def __init__(
        self,
        pid: int,
        process_name: str,
        output_path: Path,
        fps: float = 5.0,
    ):
        self.pid = pid
        self.process_name = process_name
        self.output_path = str(output_path)
        self.fps = fps
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the screen capture thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, name="screen-capture", daemon=True
        )
        self._thread.start()
        logger.info("Screen capture started for PID %d", self.pid)

    def stop(self) -> None:
        """Stop the screen capture thread and finalize the video."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        logger.info("Screen capture stopped.")

    def _find_hwnd(self) -> Optional[int]:
        """Find the window handle, trying PID first then process name."""
        hwnd = find_window_by_pid(self.pid)
        if hwnd is not None:
            return hwnd
        # Fallback: Zoom/Teams may spawn the meeting window under a child process
        logger.info(
            "No window found for PID %d, searching by process name: %s",
            self.pid,
            self.process_name,
        )
        return find_window_by_process_name(self.process_name)

    def _capture_loop(self) -> None:
        """Main capture loop: grab frames and write to video."""
        writer = None
        sct = None
        try:
            import cv2
            import mss

            # Find the target window
            hwnd = self._find_hwnd()
            if hwnd is None:
                logger.warning(
                    "Could not find window for PID %d (%s). Screen recording disabled.",
                    self.pid,
                    self.process_name,
                )
                return

            title = get_window_title(hwnd)
            logger.info("Capturing window: '%s' (HWND %d)", title, hwnd)

            # Get initial window dimensions
            rect = get_window_rect(hwnd)
            if rect is None:
                logger.warning("Window is minimized or invalid. Screen recording disabled.")
                return

            _, _, init_width, init_height = rect

            # Initialize video writer
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(
                self.output_path, fourcc, self.fps, (init_width, init_height)
            )
            if not writer.isOpened():
                logger.error("Failed to open video writer for %s", self.output_path)
                return

            logger.info(
                "Screen capture recording: %dx%d @ %.0f FPS -> %s",
                init_width,
                init_height,
                self.fps,
                self.output_path,
            )

            sct = mss.mss()
            interval = 1.0 / self.fps
            frame_count = 0

            while not self._stop_event.is_set():
                frame_start = time.monotonic()

                # Get current window position (tracks window movement)
                rect = get_window_rect(hwnd)
                if rect is None:
                    # Window minimized — write a black frame to keep timing
                    black = np.zeros((init_height, init_width, 3), dtype=np.uint8)
                    writer.write(black)
                    frame_count += 1
                    _sleep_remaining(frame_start, interval)
                    continue

                left, top, cur_w, cur_h = rect

                # Capture the window region
                monitor = {
                    "left": left,
                    "top": top,
                    "width": cur_w,
                    "height": cur_h,
                }
                try:
                    screenshot = sct.grab(monitor)
                    frame = np.array(screenshot)
                    # mss returns BGRA on Windows, OpenCV needs BGR
                    frame = frame[:, :, :3]

                    # Resize if window changed size (keep video dimensions consistent)
                    if cur_w != init_width or cur_h != init_height:
                        frame = cv2.resize(frame, (init_width, init_height))

                    writer.write(frame)
                    frame_count += 1
                except Exception:
                    logger.debug("Frame capture failed, skipping frame", exc_info=True)

                _sleep_remaining(frame_start, interval)

            logger.info(
                "Screen capture complete: %d frames (%.1fs at %.0f FPS)",
                frame_count,
                frame_count / self.fps if self.fps > 0 else 0,
                self.fps,
            )

        except ImportError as e:
            logger.error(
                "Screen capture dependencies not installed: %s. "
                "Install with: pip install mss opencv-python",
                e,
            )
        except Exception:
            logger.exception("Screen capture error")
        finally:
            if writer is not None:
                writer.release()
            if sct is not None:
                sct.close()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def _sleep_remaining(frame_start: float, interval: float) -> None:
    """Sleep for the remainder of the frame interval."""
    elapsed = time.monotonic() - frame_start
    sleep_time = interval - elapsed
    if sleep_time > 0:
        time.sleep(sleep_time)
