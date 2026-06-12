"""Screen capture of meeting application windows to MP4 video."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import queue
import re
import subprocess
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
_DIB_RGB_COLORS = 0

# When the tracked window stays minimized this long, fall back to capturing
# the whole monitor instead of a frozen last frame. This covers the Zoom /
# Teams screen-share case, where the meeting window is minimized while the
# user presents — without this, the recorded video would freeze for the
# entire share. Audio capture (by PID) is unaffected either way.
_SHARE_FALLBACK_SECONDS = 3.0

# Frame hand-off queue between the grab loop and the encode thread. Small on
# purpose: when the encoder falls behind we drop the oldest frame (the writer
# rebuilds timing from timestamps), so a deep queue only adds latency.
_FRAME_QUEUE_SIZE = 4

# Max duplicate frames written per consumed frame when catching up missed
# slots. Bounds the encode cost of a single pathological grab stall; any
# remaining deficit carries over to the next frames.
_MAX_DUP_PER_FRAME = 30

# If the timeline deficit ever exceeds this many seconds, jump the timeline
# forward instead of smearing thousands of duplicate frames (e.g. the
# monotonic clock leapt, or the producer was wedged for minutes).
_RESYNC_DEFICIT_SECONDS = 10.0

# Probed ffmpeg encoder choice, cached for the process lifetime (probing
# spawns 1-2 ffmpeg subprocesses). Only successful probes are cached so a
# transient failure can recover on the next recording.
_ffmpeg_encoder_cache: Optional[str] = None
_ffmpeg_probe_lock = threading.Lock()


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


def _parse_encoder_names(encoders_output: str) -> set[str]:
    """Extract video encoder names from ``ffmpeg -encoders`` output."""
    names: set[str] = set()
    for line in encoders_output.splitlines():
        m = re.match(r"\s*V\S{5}\s+(\S+)", line)
        if m and m.group(1) != "=":
            names.add(m.group(1))
    return names


def _test_encode(ffmpeg_exe: str, encoder: str) -> bool:
    """Verify an encoder actually initializes (e.g. NVENC needs a GPU).

    ``-encoders`` lists everything compiled in, so h264_nvenc shows up even
    on machines without an NVIDIA GPU — a 2-frame test encode is the only
    reliable check.
    """
    try:
        result = subprocess.run(
            [
                ffmpeg_exe, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=black:s=256x256:r=30:d=0.2",
                "-frames:v", "2", "-c:v", encoder, "-f", "null", "-",
            ],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        logger.debug("Encoder test for %s failed", encoder, exc_info=True)
        return False


def _probe_best_encoder(ffmpeg_exe: str) -> str:
    """Pick the best available H.264 encoder, preferring NVENC.

    Cached per process after the first successful probe. Raises on failure
    so the caller can fall back to cv2.VideoWriter.
    """
    global _ffmpeg_encoder_cache
    with _ffmpeg_probe_lock:
        if _ffmpeg_encoder_cache is not None:
            return _ffmpeg_encoder_cache
        result = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg -encoders failed (exit code {result.returncode})"
            )
        available = _parse_encoder_names(result.stdout)
        choice: Optional[str] = None
        if "h264_nvenc" in available and _test_encode(ffmpeg_exe, "h264_nvenc"):
            choice = "h264_nvenc"
        elif "libx264" in available:
            choice = "libx264"
        if choice is None:
            raise RuntimeError("no usable H.264 encoder in ffmpeg build")
        _ffmpeg_encoder_cache = choice
        return choice


class FFmpegVideoWriter:
    """Video writer backed by an ffmpeg subprocess (h264_nvenc / libx264).

    Reads rawvideo bgr24 frames on stdin and writes an H.264 MP4. Duck-types
    the minimal cv2.VideoWriter interface used here: ``write()``,
    ``release()``, ``isOpened()``. Unlike cv2, ``write()`` raises when the
    ffmpeg process has died so callers can fail over.
    """

    def __init__(
        self,
        output_path: Path | str,
        fps: float,
        width: int,
        height: int,
        encoder: Optional[str] = None,
    ):
        import imageio_ffmpeg

        self._exe = imageio_ffmpeg.get_ffmpeg_exe()
        self.encoder = encoder or _probe_best_encoder(self._exe)
        self._frame_bytes = width * height * 3

        args = [
            self._exe, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "-",
            "-an",
        ]
        if width % 2 or height % 2:
            # H.264 yuv420p requires even dimensions; crop a 1px edge
            args += ["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2:0:0"]
        args += ["-c:v", self.encoder]
        if self.encoder == "h264_nvenc":
            args += ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
        else:
            args += ["-preset", "veryfast", "-crf", "23"]
        args += ["-pix_fmt", "yuv420p", str(output_path)]

        # stderr -> DEVNULL: ffmpeg writes progress continuously; an unread
        # PIPE would fill its buffer and deadlock the encoder.
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._open = True
        if self._proc.poll() is not None:
            self._open = False
            raise RuntimeError(
                f"ffmpeg exited immediately (code {self._proc.returncode})"
            )

    def isOpened(self) -> bool:
        return self._open and self._proc.poll() is None

    def write(self, frame: np.ndarray) -> None:
        """Write one BGR frame. Raises RuntimeError if ffmpeg has died."""
        if not self._open:
            raise RuntimeError("ffmpeg writer is closed")
        data = frame.tobytes()
        if len(data) != self._frame_bytes:
            raise ValueError(
                f"frame size mismatch: got {len(data)} bytes, "
                f"expected {self._frame_bytes}"
            )
        try:
            self._proc.stdin.write(data)
        except (BrokenPipeError, OSError) as e:
            self._open = False
            raise RuntimeError(
                f"ffmpeg process died (exit code {self._proc.poll()})"
            ) from e

    def release(self) -> None:
        """Close stdin and wait for ffmpeg to finalize the container."""
        self._open = False
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg did not finalize within 10s; killing it")
            self._proc.kill()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass


class ScreenCapture:
    """Captures the meeting application window to an MP4 video file.

    Finds the window by PID and tracks its position each frame. Grabbing and
    encoding run in separate threads connected by a small frame queue; the
    encode side fills missed frame slots with duplicates so the container
    timeline matches wall-clock time even when grabs or encodes overrun.
    """

    def __init__(
        self,
        pid: int,
        process_name: str,
        output_path: Path,
        fps: float = 30.0,
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
        # Pause flag: when True, frames are captured for preview but not written to video.
        # Set externally by CaptureManager.
        self.paused: bool = False
        # Grab -> encode hand-off (created per-recording in _capture_loop).
        # Items are (frame, timestamp, resync) tuples; None is the stop sentinel.
        self._frame_queue: Optional[queue.Queue] = None
        self._dropped_frames = 0
        # Set when the writer thread gave up (no working video writer left);
        # the grab loop keeps running for live preview only.
        self._writer_failed = False
        # Set on pause -> resume so the writer realigns its timeline instead
        # of back-filling the paused gap with duplicate frames.
        self._pending_resync = False
        # Writer currently owned by the writer thread (for diagnostics only).
        self._active_writer = None
        self._writer_size: tuple[int, int] = (0, 0)

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
            # Generous timeout: the writer thread must drain its queue and
            # the encoder must flush/finalize the container before exit.
            self._thread.join(timeout=15.0)
            if self._thread.is_alive():
                logger.warning("Screen capture thread did not exit within 15s")
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
        if width <= 0 or height <= 0:
            return None

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Validate window handle before GDI operations
        if not user32.IsWindow(hwnd):
            return None

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

            if not gdi32.SelectObject(hdc_mem, hbm):
                return None

            # PW_RENDERFULLCONTENT renders the full window content (Win 8.1+)
            if not user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT):
                return None

            # Read bitmap pixels into buffer
            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height  # negative = top-down DIB (no flip needed)
            bmi.biPlanes = 1
            bmi.biBitCount = 32  # BGRA
            bmi.biCompression = 0  # BI_RGB

            buf_size = width * height * 4
            buf = (ctypes.c_char * buf_size)()
            gdi32.GetDIBits(
                hdc_mem, hbm, 0, height, buf, ctypes.byref(bmi), _DIB_RGB_COLORS
            )

            # buf is a stack-allocated ctypes array freed when this function
            # returns; both conversion paths below copy the data out before
            # returning (cvtColor allocates a new array; the slice is copied).
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4)
            try:
                import cv2
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            except ImportError:
                return frame[:, :, :3].copy()  # BGRA -> BGR

        finally:
            if hbm:
                gdi32.DeleteObject(hbm)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_window)

    def _create_writer(self, cv2_module, width: int, height: int):
        """Create the best available video writer for this recording.

        Prefers the ffmpeg subprocess writer (h264_nvenc, then libx264);
        any failure — imageio_ffmpeg missing, probe failure, process dead at
        startup — falls back to the original cv2.VideoWriter path. Returns
        None only when no writer could be opened at all.
        """
        try:
            w = FFmpegVideoWriter(self.output_path, self.fps, width, height)
            logger.info("Screen recording codec: %s (ffmpeg)", w.encoder)
            return w
        except Exception as e:
            logger.info(
                "FFmpeg writer unavailable (%s); falling back to cv2.VideoWriter.",
                e,
            )
        # cv2 path — try H.264 first (3-5x smaller), fall back to mp4v
        for codec in ("avc1", "mp4v"):
            fourcc = cv2_module.VideoWriter_fourcc(*codec)
            w = cv2_module.VideoWriter(
                self.output_path, fourcc, self.fps, (width, height)
            )
            if w.isOpened():
                logger.info("Screen recording codec: %s (cv2)", codec)
                return w
            w.release()
        return None

    def _failover_to_cv2(self, old_writer, cv2_module):
        """Replace a dead ffmpeg writer with cv2.VideoWriter mid-recording.

        Returns the new writer, or None when no failover is possible (e.g.
        the failed writer already was cv2). The partially-written ffmpeg
        output is overwritten — losing the video so far beats losing the
        rest of the meeting.
        """
        try:
            old_writer.release()
        except Exception:
            pass
        if not isinstance(old_writer, FFmpegVideoWriter):
            return None
        width, height = self._writer_size
        try:
            for codec in ("avc1", "mp4v"):
                fourcc = cv2_module.VideoWriter_fourcc(*codec)
                w = cv2_module.VideoWriter(
                    self.output_path, fourcc, self.fps, (width, height)
                )
                if w.isOpened():
                    logger.warning(
                        "ffmpeg writer died — failing over to cv2 (%s) for the "
                        "remainder; video before this point may be lost",
                        codec,
                    )
                    return w
                w.release()
        except Exception:
            logger.exception("cv2 failover failed")
        return None

    def _submit_frame(self, frame: np.ndarray, ts: float) -> None:
        """Hand a frame to the writer thread unless paused or writer is gone.

        When the queue is full, drops the oldest queued frame: the writer
        rebuilds timing from timestamps, so a dropped frame becomes a
        duplicate of its neighbour rather than a timeline gap.
        """
        q = self._frame_queue
        if self.paused or self._writer_failed or q is None:
            return
        resync = self._pending_resync
        self._pending_resync = False
        item = (frame, ts, resync)
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            dropped = q.get_nowait()
            self._dropped_frames += 1
            if dropped is not None and dropped[2]:
                item = (frame, ts, True)  # carry the dropped resync marker
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            # Writer thread raced us refilling the queue; extremely unlikely.
            self._dropped_frames += 1
            self._pending_resync = self._pending_resync or item[2]
        if self._dropped_frames % 100 == 1:
            logger.warning(
                "Encoder falling behind: %d frames dropped from queue so far",
                self._dropped_frames,
            )

    def _writer_loop(self, vid_writer, cv2_module) -> None:
        """Consume frames from the queue and encode them (writer thread).

        Maintains a frame-slot timeline derived from capture timestamps:
        when grabbing or encoding overruns, the current frame is written
        extra times to fill the missed slots so the container duration
        matches wall-clock time — this is what keeps video in sync with
        audio. On writer failure, attempts a cv2 failover; if that also
        fails, video stops but audio capture is unaffected.
        """
        interval = 1.0 / self.fps if self.fps > 0 else 1.0 / 30.0
        frames_written = 0  # frames in the current container's timeline
        total_written = 0
        dup_frames = 0
        start_ts: Optional[float] = None
        first_ts: Optional[float] = None
        last_ts: Optional[float] = None
        self._active_writer = vid_writer
        try:
            while True:
                try:
                    item = self._frame_queue.get(timeout=0.5)
                except queue.Empty:
                    # Secondary exit: producer is gone and the queue is
                    # drained, but the stop sentinel never arrived.
                    if self._stop_event.is_set():
                        break
                    continue
                if item is None:
                    break
                frame, ts, resync = item
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
                if resync or start_ts is None:
                    # Start of stream, or resume after pause: realign so the
                    # gap is not back-filled with duplicate frames.
                    start_ts = ts - frames_written * interval
                # +1e-6 slots absorbs float error at exact slot boundaries
                slots_due = int((ts - start_ts) / interval + 1e-6) + 1
                deficit = slots_due - frames_written
                if deficit > self.fps * _RESYNC_DEFICIT_SECONDS:
                    logger.warning(
                        "Video timeline stalled %.1fs — resyncing instead of "
                        "writing %d duplicate frames",
                        deficit * interval,
                        deficit,
                    )
                    start_ts = ts - frames_written * interval
                    deficit = 1
                repeats = min(max(deficit, 0), _MAX_DUP_PER_FRAME)
                try:
                    for _ in range(repeats):
                        vid_writer.write(frame)
                except Exception:
                    logger.warning(
                        "Video writer failed mid-recording; attempting failover",
                        exc_info=True,
                    )
                    vid_writer = self._failover_to_cv2(vid_writer, cv2_module)
                    self._active_writer = vid_writer
                    if vid_writer is None:
                        self._writer_failed = True
                        logger.error(
                            "Screen recording stopped: no working video "
                            "writer. Audio capture is unaffected."
                        )
                        break
                    # Fresh container: restart its timeline at the next frame
                    frames_written = 0
                    start_ts = None
                    continue
                frames_written += repeats
                total_written += repeats
                if repeats > 1:
                    dup_frames += repeats - 1
        except Exception:
            logger.exception("Video writer thread error")
            self._writer_failed = True
        finally:
            if vid_writer is not None:
                try:
                    vid_writer.release()
                except Exception:
                    logger.warning("VideoWriter.release() failed", exc_info=True)
            self._active_writer = None
            if total_written and first_ts is not None and last_ts is not None:
                wall = max(last_ts - first_ts + interval, interval)
                achieved = total_written / wall
                logger.info(
                    "Screen capture complete: %d frames -> %.1fs video over "
                    "%.1fs wall-clock (achieved %.1f FPS vs %.0f configured), "
                    "%d duplicates inserted for timing, %d dropped from queue",
                    total_written,
                    total_written * interval,
                    wall,
                    achieved,
                    self.fps,
                    dup_frames,
                    self._dropped_frames,
                )

    def _capture_loop(self) -> None:
        """Main capture loop: grab frames and queue them for encoding."""
        writer = None
        writer_thread: Optional[threading.Thread] = None
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
            if init_width <= 0 or init_height <= 0:
                logger.warning("Window has zero dimensions. Screen recording disabled.")
                return

            writer = self._create_writer(cv2, init_width, init_height)
            if writer is None:
                logger.error("Failed to open video writer for %s", self.output_path)
                return

            self._writer_size = (init_width, init_height)
            self._frame_queue = queue.Queue(maxsize=_FRAME_QUEUE_SIZE)
            self._dropped_frames = 0
            self._writer_failed = False
            self._pending_resync = False
            writer_thread = threading.Thread(
                target=self._writer_loop,
                args=(writer, cv2),
                name="screen-encode",
                daemon=True,
            )
            writer_thread.start()

            logger.info(
                "Screen capture recording: %dx%d @ %.0f FPS -> %s",
                init_width,
                init_height,
                self.fps,
                self.output_path,
            )

            interval = 1.0 / self.fps
            last_good_frame = None  # Cache for gap-filling dropped frames
            flicker_drops = 0  # Count of dropped glitch frames
            was_paused = False
            # Time-based glitch reset: if glitches persist for >2s, the content
            # has genuinely changed (screen share, slide, theme switch). Using
            # wall-clock time instead of a frame counter avoids the problem of
            # a single good frame resetting a consecutive counter.
            last_non_glitch_time = time.monotonic()

            # current_hwnd tracks the active window (can be changed by switch_window())
            current_hwnd = hwnd

            # Share-fallback state: when the tracked window is minimized for
            # _SHARE_FALLBACK_SECONDS we assume the user is presenting and
            # switch the video source to the monitor it was last on.
            share_mode = False
            share_monitor: Optional[dict] = None
            minimized_since: Optional[float] = None
            last_rect: Optional[tuple[int, int, int, int]] = None

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
                # Queue the test frame as the first frame
                self._submit_frame(test_frame, time.monotonic())
                self._latest_frame = test_frame
                last_good_frame = test_frame

            while not self._stop_event.is_set():
                frame_start = time.monotonic()

                # Detect pause -> resume so the writer realigns its timeline
                # instead of back-filling the paused gap with duplicates.
                paused_now = self.paused
                if was_paused and not paused_now:
                    self._pending_resync = True
                was_paused = paused_now

                # Check for a pending window-switch request
                pending = self._override_hwnd
                if pending is not None and pending != current_hwnd:
                    self._override_hwnd = None  # consume
                    # Validate the new handle before using it
                    if ctypes.windll.user32.IsWindow(pending):
                        new_rect = get_window_rect(pending)
                        if new_rect is not None and new_rect[2] > 0 and new_rect[3] > 0:
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
                            # Reset glitch baseline for new window
                            last_good_frame = None
                            last_non_glitch_time = time.monotonic()
                            # Reset share-fallback state — user chose a new target
                            share_mode = False
                            share_monitor = None
                            minimized_since = None
                            last_rect = None
                        else:
                            logger.warning(
                                "Requested HWND %d is not visible; ignoring switch.", pending
                            )
                    else:
                        logger.warning(
                            "Requested HWND %d is no longer valid; ignoring switch.", pending
                        )

                # Get current window position/size
                rect = get_window_rect(current_hwnd)
                if rect is None:
                    # Window minimized or transiently unavailable. If it stays
                    # minimized past the share-fallback threshold, capture the
                    # whole monitor instead — this handles screen-shares where
                    # Zoom/Teams minimize the meeting window while presenting.
                    now = time.monotonic()
                    if minimized_since is None:
                        minimized_since = now
                    elapsed_min = now - minimized_since

                    if not share_mode and elapsed_min >= _SHARE_FALLBACK_SECONDS:
                        try:
                            if sct is None:
                                import mss
                                sct = mss.mss()
                            # Try to identify the monitor being shared via
                            # the meeting app's own floating toolbar/overlay.
                            share_monitor = _find_share_monitor(
                                sct, self.pid, self.process_name, current_hwnd
                            )
                            source = "share-overlay"
                            if share_monitor is None:
                                # No toolbar found — fall back to the monitor
                                # the meeting window was last on.
                                share_monitor = _pick_monitor_for_rect(sct, last_rect)
                                source = "last-window-position"
                            share_mode = True
                            logger.info(
                                "Tracked window minimized for %.1fs — falling back "
                                "to desktop capture (likely screen share). Monitor: "
                                "%dx%d @ (%d,%d) [source: %s]",
                                elapsed_min,
                                share_monitor["width"], share_monitor["height"],
                                share_monitor["left"], share_monitor["top"],
                                source,
                            )
                        except Exception:
                            logger.debug(
                                "Could not engage desktop fallback", exc_info=True
                            )

                    if share_mode and sct is not None and share_monitor is not None:
                        try:
                            shot = sct.grab(share_monitor)
                            frame = np.array(shot)[:, :, :3]  # BGRA -> BGR
                            if frame.shape[:2] != (init_height, init_width):
                                frame = cv2.resize(frame, (init_width, init_height))
                            self._submit_frame(frame, frame_start)
                            self._latest_frame = frame
                            last_good_frame = frame
                            _sleep_remaining(frame_start, interval)
                            continue
                        except Exception:
                            logger.debug(
                                "Desktop fallback grab failed", exc_info=True
                            )

                    # Haven't hit threshold yet, or fallback failed: repeat
                    # the last good frame so the video timing stays intact.
                    if last_good_frame is not None:
                        self._submit_frame(last_good_frame, frame_start)
                    else:
                        black = np.zeros((init_height, init_width, 3), dtype=np.uint8)
                        self._submit_frame(black, frame_start)
                    _sleep_remaining(frame_start, interval)
                    continue

                # Window is visible again — exit share mode if it was engaged.
                if share_mode:
                    logger.info(
                        "Tracked window restored — resuming window capture."
                    )
                    share_mode = False
                    share_monitor = None
                minimized_since = None

                left, top, cur_w, cur_h = rect
                last_rect = (left, top, cur_w, cur_h)

                # Skip degenerate window dimensions (collapsed, zero-size)
                if cur_w <= 0 or cur_h <= 0:
                    if last_good_frame is not None:
                        self._submit_frame(last_good_frame, frame_start)
                    _sleep_remaining(frame_start, interval)
                    continue

                try:
                    if use_printwindow:
                        frame = self._capture_printwindow(current_hwnd, cur_w, cur_h)
                        if frame is None:
                            # PrintWindow failed — repeat last good frame instead
                            # of skipping, which causes timing gaps and flicker.
                            if last_good_frame is not None:
                                self._submit_frame(last_good_frame, frame_start)
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
                        if screenshot is None:
                            if last_good_frame is not None:
                                self._submit_frame(last_good_frame, frame_start)
                            _sleep_remaining(frame_start, interval)
                            continue
                        frame = np.array(screenshot)[:, :, :3]  # BGRA -> BGR

                    # Resize if window changed size (keep video dimensions consistent)
                    if cur_w != init_width or cur_h != init_height:
                        frame = cv2.resize(frame, (init_width, init_height))

                    # Anti-flicker: detect glitch frames (blank, flash, or torn)
                    # from PrintWindow DWM composition artifacts and drop them.
                    now = time.monotonic()
                    if last_good_frame is not None and _is_glitch_frame(frame, last_good_frame):
                        flicker_drops += 1
                        glitch_duration = now - last_non_glitch_time
                        if glitch_duration >= 2.0:
                            # Content has genuinely changed (screen share, slide,
                            # theme switch). Accept this frame as the new baseline
                            # to avoid freezing the capture indefinitely.
                            last_good_frame = frame
                            last_non_glitch_time = now
                            logger.info(
                                "Glitch detector reset — content change detected "
                                "after %.1fs (%d total drops)",
                                glitch_duration, flicker_drops,
                            )
                        else:
                            if flicker_drops % 50 == 1:
                                logger.debug(
                                    "Dropped glitch frame (%d total drops)",
                                    flicker_drops,
                                )
                            frame = last_good_frame
                    else:
                        last_good_frame = frame
                        last_non_glitch_time = now

                    self._submit_frame(frame, frame_start)
                    self._latest_frame = frame
                except Exception:
                    # Capture exception — repeat last good frame to avoid gap
                    if last_good_frame is not None:
                        self._submit_frame(last_good_frame, frame_start)
                    logger.debug("Frame capture failed, repeating last frame", exc_info=True)

                _sleep_remaining(frame_start, interval)

            logger.info(
                "Screen grab loop finished: %d glitch frames dropped, "
                "%d frames dropped from queue",
                flicker_drops,
                self._dropped_frames,
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
            if writer_thread is not None and writer_thread.is_alive():
                # Drain: the sentinel queues behind any remaining frames, so
                # the writer encodes everything before releasing the writer.
                try:
                    self._frame_queue.put(None, timeout=2.0)
                except queue.Full:
                    logger.warning(
                        "Could not signal video writer thread (queue full)"
                    )
                writer_thread.join(timeout=10.0)
                if writer_thread.is_alive():
                    logger.warning("Video writer thread did not drain within 10s")
            elif writer_thread is None and writer is not None:
                # Writer created but its thread never started — release directly
                try:
                    writer.release()
                except Exception:
                    logger.warning("VideoWriter.release() failed", exc_info=True)
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass

    @property
    def latest_frame(self) -> Optional[np.ndarray]:
        """The most recently captured frame (BGR), or None if not yet captured."""
        return self._latest_frame

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def _is_glitch_frame(frame: np.ndarray, last_good: np.ndarray) -> bool:
    """Detect probable capture glitch frames from PrintWindow.

    PrintWindow + DWM can produce partially-rendered, blank, or flash
    frames during composition transitions.  Detects these cheaply by
    sampling ~1000 pixels and comparing brightness to the last known-good
    frame.  Returns True if the frame should be dropped.
    """
    # Sample every Nth pixel to keep cost <0.1ms per frame
    step = max(1, frame.size // 3000)
    sample = frame.flat[::step]
    mean_val = float(sample.mean())

    # Near-black frame (PrintWindow returned blank)
    if mean_val < 3:
        return True

    # Near-white frame (DWM flash)
    if mean_val > 252:
        return True

    # Compare to last good frame — a sudden large brightness shift
    # across the whole image is almost certainly a capture artifact,
    # not a real content change.  Real content changes are caught by
    # the time-based glitch reset in the main loop.
    ref_sample = last_good.flat[::step]
    ref_mean = float(ref_sample.mean())

    if ref_mean > 5:
        ratio = abs(mean_val - ref_mean) / ref_mean
        if ratio > 0.60:
            return True

    return False


def _find_share_monitor(
    sct,
    pid: int,
    process_name: str,
    exclude_hwnd: int,
) -> Optional[dict]:
    """Pick the monitor Zoom/Teams is most likely sharing on.

    During screen share, Zoom/Teams create a small floating toolbar (and
    often a full-screen border overlay) on the monitor being shared,
    while the main meeting window is minimized. This enumerates visible,
    non-minimized windows owned by any process with the meeting app's
    name, excluding the (minimized) main window, and returns the monitor
    containing the largest such window. Returns None when no candidate
    is found — caller should fall back to the last-known-rect heuristic.
    """
    try:
        import psutil
    except ImportError:
        return None

    # Zoom spawns several child processes with the same image name;
    # any of them may own the sharing overlay. Gather all matching PIDs.
    target_pids = {pid}
    try:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = p.info.get("name")
                if name and name.lower() == process_name.lower():
                    target_pids.add(p.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        logger.debug("psutil iteration failed during share-monitor search", exc_info=True)

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    candidates: list[tuple[int, int, int, int]] = []

    def _cb(hwnd, _lparam):
        if hwnd == exclude_hwnd:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        found_pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found_pid))
        if found_pid.value not in target_pids:
            return True
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return True
        candidates.append((rect.left, rect.top, w, h))
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        logger.debug("EnumWindows failed during share-monitor search", exc_info=True)
        return None

    if not candidates:
        return None
    # Pick the largest — most likely the sharing overlay rather than a
    # 1-pixel tooltip/tray-popup that happens to be visible.
    candidates.sort(key=lambda r: r[2] * r[3], reverse=True)
    return _pick_monitor_for_rect(sct, candidates[0])


def _pick_monitor_for_rect(
    sct, last_rect: Optional[tuple[int, int, int, int]]
) -> dict:
    """Pick the mss monitor containing the centre of *last_rect*.

    ``sct.monitors[0]`` is the virtual union of every display and ``[1:]``
    are the individual monitors. On single-display systems we always return
    monitor 1. If *last_rect* is missing or doesn't intersect any monitor,
    we fall back to monitor 1 (the primary).
    """
    monitors = sct.monitors
    if len(monitors) <= 1:
        return monitors[0]
    if last_rect is None:
        return monitors[1]
    left, top, w, h = last_rect
    cx = left + w // 2
    cy = top + h // 2
    for mon in monitors[1:]:
        m_right = mon["left"] + mon["width"]
        m_bottom = mon["top"] + mon["height"]
        if mon["left"] <= cx < m_right and mon["top"] <= cy < m_bottom:
            return mon
    return monitors[1]


def _sleep_remaining(frame_start: float, interval: float) -> None:
    """Sleep for the remainder of the frame interval.

    Pacing only — timing correctness does not depend on this: the writer
    thread fills missed slots from frame timestamps when grabs overrun.
    """
    elapsed = time.monotonic() - frame_start
    sleep_time = interval - elapsed
    if sleep_time > 0:
        time.sleep(sleep_time)
