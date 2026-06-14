"""Cross-platform ffmpeg subprocess video writer."""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Probed ffmpeg encoder choice, cached for the process lifetime (probing
# spawns 1-2 ffmpeg subprocesses). Only successful probes are cached so a
# transient failure can recover on the next recording.
_ffmpeg_encoder_cache: Optional[str] = None
_ffmpeg_probe_lock = threading.Lock()


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
    on machines without an NVIDIA GPU - a 2-frame test encode is the only
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
        quality: int = 21,
    ):
        import imageio_ffmpeg

        self._exe = imageio_ffmpeg.get_ffmpeg_exe()
        self.encoder = encoder or _probe_best_encoder(self._exe)
        self._frame_bytes = width * height * 3
        # CQ/CRF: lower = higher quality (crisper text/slides), larger file.
        quality = max(1, min(51, int(quality)))
        # Keyframe ~every 2s (seek granularity). Fragments are cut more often
        # than this via -frag_duration below, so crash loss stays ~1s.
        gop = max(int(fps * 2), 2)

        args = [
            self._exe, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "-",
            "-an",
        ]
        if width % 2 or height % 2:
            # H.264 yuv420p requires even dimensions; crop a 1px edge
            args += ["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2:0:0"]
        args += ["-c:v", self.encoder, "-g", str(gop)]
        if self.encoder == "h264_nvenc":
            args += ["-preset", "p5", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
        else:
            args += ["-preset", "veryfast", "-crf", str(quality)]
        # Fragmented MP4: the index is written upfront in fragments, so the
        # video is playable even if the app crashes or is killed mid-recording
        # - instead of the whole file being unrecoverable for lack of a final
        # moov atom. -frag_duration caps fragments at ~1s and -flush_packets
        # pushes them to disk as they complete, so a crash loses at most ~1s.
        args += [
            "-pix_fmt", "yuv420p",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration", "1000000",
            "-flush_packets", "1",
            str(output_path),
        ]

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
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()  # don't leak the PIPE fd
            except OSError:
                pass
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
