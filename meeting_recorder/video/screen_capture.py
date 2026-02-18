"""Screen capture of meeting application windows to MP4 video."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
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

# GDI constants
_PW_RENDERFULLCONTENT = 2  # PrintWindow flag: render full content (Win 8.1+)
_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    """Win32 BITMAPINFOHEADER for reading bitmap pixels."""
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


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
        # Latest captured frame for live preview. Each frame is a new numpy array
        # (never mutated in-place), so Python's GIL guarantees atomic ref read/write.
        self._latest_frame: Optional[np.ndarray] = None
        # Pending window switch: set by switch_window(), consumed by capture loop.
        # Python GIL makes int/None reference assignment atomic.
        self._override_hwnd: Optional[int] = None

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

    def switch_window(self, hwnd: int) -> None:
        """Request the capture loop to switch to a different window.

        The switch is applied on the next frame iteration. The video output
        dimensions stay fixed at the original window's size; frames from the
        new window are resized to match if necessary.
        """
        self._override_hwnd = hwnd
        logger.info("Window switch requested: HWND %d", hwnd)

    def stop(self) -> None:
        """Stop the screen capture thread and finalize the video."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        self._latest_frame = None
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

    @staticmethod
    def _capture_printwindow(hwnd: int, width: int, height: int) -> Optional[np.ndarray]:
        """Capture window content using PrintWindow API.

        Captures only the window itself — notifications, overlays, and other
        windows on top are excluded. Returns a BGR numpy array, or None on failure.
        """
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        hdc_window = user32.GetWindowDC(hwnd)
        if not hdc_window:
            return None

        hdc_mem = None
        hbm = None
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
            if not hdc_mem:
                return None

            hbm = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
            if not hbm:
                return None

            gdi32.SelectObject(hdc_mem, hbm)

            # PW_RENDERFULLCONTENT renders the full window content (Win 8.1+)
            if not user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT):
                return None

            # Read bitmap pixels into buffer
            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = height  # positive = bottom-up DIB
            bmi.biPlanes = 1
            bmi.biBitCount = 32  # BGRA
            bmi.biCompression = 0  # BI_RGB

            buf_size = width * height * 4
            buf = (ctypes.c_char * buf_size)()
            gdi32.GetDIBits(
                hdc_mem, hbm, 0, height, buf, ctypes.byref(bmi), _DIB_RGB_COLORS
            )

            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4)
            frame = np.flipud(frame)  # bottom-up -> top-down
            return frame[:, :, :3].copy()  # BGRA -> BGR

        finally:
            if hbm:
                gdi32.DeleteObject(hbm)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_window)

    def _capture_loop(self) -> None:
        """Main capture loop: grab frames and write to video."""
        writer = None
        sct = None
        try:
            import cv2

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

            interval = 1.0 / self.fps
            frame_count = 0
            last_good_frame = None  # Cache for gap-filling dropped frames

            # current_hwnd tracks the active window (can be changed by switch_window())
            current_hwnd = hwnd

            # Determine capture method: try PrintWindow first, fall back to mss
            use_printwindow = True
            test_frame = self._capture_printwindow(current_hwnd, init_width, init_height)
            if test_frame is None or np.max(test_frame) < 5:
                logger.info(
                    "PrintWindow returned blank frame; falling back to mss region capture."
                )
                use_printwindow = False
                import mss
                sct = mss.mss()
            else:
                logger.info("Using PrintWindow for window-only capture (no overlays).")
                # Write the test frame as the first frame
                writer.write(test_frame)
                self._latest_frame = test_frame
                last_good_frame = test_frame
                frame_count = 1

            while not self._stop_event.is_set():
                frame_start = time.monotonic()

                # Check for a pending window-switch request
                pending = self._override_hwnd
                if pending is not None and pending != current_hwnd:
                    self._override_hwnd = None  # consume
                    new_rect = get_window_rect(pending)
                    if new_rect is not None:
                        current_hwnd = pending
                        new_title = get_window_title(pending)
                        logger.info(
                            "Switched capture to: '%s' (HWND %d)", new_title, current_hwnd
                        )
                        # Re-probe capture method for the new window
                        probe = self._capture_printwindow(
                            current_hwnd, new_rect[2], new_rect[3]
                        )
                        if probe is not None and np.max(probe) >= 5:
                            use_printwindow = True
                            if sct is not None:
                                sct.close()
                                sct = None
                        else:
                            use_printwindow = False
                            if sct is None:
                                import mss
                                sct = mss.mss()
                    else:
                        logger.warning(
                            "Requested HWND %d is not visible; ignoring switch.", pending
                        )

                # Get current window position/size
                rect = get_window_rect(current_hwnd)
                if rect is None:
                    # Window minimized or transiently unavailable — repeat last
                    # good frame to avoid black-frame flicker.
                    if last_good_frame is not None:
                        writer.write(last_good_frame)
                    else:
                        black = np.zeros((init_height, init_width, 3), dtype=np.uint8)
                        writer.write(black)
                    frame_count += 1
                    _sleep_remaining(frame_start, interval)
                    continue

                left, top, cur_w, cur_h = rect

                try:
                    if use_printwindow:
                        frame = self._capture_printwindow(current_hwnd, cur_w, cur_h)
                        if frame is None:
                            # PrintWindow failed — repeat last good frame instead
                            # of skipping, which causes timing gaps and flicker.
                            if last_good_frame is not None:
                                writer.write(last_good_frame)
                                frame_count += 1
                            _sleep_remaining(frame_start, interval)
                            continue
                    else:
                        monitor = {
                            "left": left,
                            "top": top,
                            "width": cur_w,
                            "height": cur_h,
                        }
                        screenshot = sct.grab(monitor)
                        frame = np.array(screenshot)[:, :, :3]  # BGRA -> BGR

                    # Resize if window changed size (keep video dimensions consistent)
                    if cur_w != init_width or cur_h != init_height:
                        frame = cv2.resize(frame, (init_width, init_height))

                    writer.write(frame)
                    self._latest_frame = frame
                    last_good_frame = frame
                    frame_count += 1
                except Exception:
                    # Capture exception — repeat last good frame to avoid gap
                    if last_good_frame is not None:
                        writer.write(last_good_frame)
                        frame_count += 1
                    logger.debug("Frame capture failed, repeating last frame", exc_info=True)

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
                "Install with: pip install opencv-python",
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
    def latest_frame(self) -> Optional[np.ndarray]:
        """The most recently captured frame (BGR), or None if not yet captured."""
        return self._latest_frame

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def _sleep_remaining(frame_start: float, interval: float) -> None:
    """Sleep for the remainder of the frame interval."""
    elapsed = time.monotonic() - frame_start
    sleep_time = interval - elapsed
    if sleep_time > 0:
        time.sleep(sleep_time)
