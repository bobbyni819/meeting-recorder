"""Tests for ScreenCapture frame cache, glitch detection, writer timing, and CaptureManager.get_screen_frame()."""

from __future__ import annotations

import queue
import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

import meeting_recorder.video.ffmpeg_writer as ffmpeg_writer
import meeting_recorder.video.screen_capture as screen_capture
from meeting_recorder.video.screen_capture import (
    FFmpegVideoWriter,
    ScreenCapture,
    _find_share_monitor,
    _is_glitch_frame,
    _parse_encoder_names,
    _pick_monitor_for_rect,
    _probe_best_encoder,
)


class _FakeSct:
    """Minimal mss-like stub exposing a .monitors list."""
    def __init__(self, monitors):
        self.monitors = monitors


class TestScreenCaptureLatestFrame:
    """Test the latest_frame cache on ScreenCapture."""

    def test_latest_frame_none_before_capture(self):
        sc = ScreenCapture(pid=1234, process_name="test.exe", output_path=Path("out.mp4"))
        assert sc.latest_frame is None

    def test_latest_frame_returns_assigned_value(self):
        sc = ScreenCapture(pid=1234, process_name="test.exe", output_path=Path("out.mp4"))
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sc._latest_frame = frame
        assert sc.latest_frame is frame

    def test_latest_frame_none_after_stop(self):
        sc = ScreenCapture(pid=1234, process_name="test.exe", output_path=Path("out.mp4"))
        sc._latest_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        sc.stop()
        assert sc.latest_frame is None


class TestGlitchFrameDetection:
    """Test _is_glitch_frame anti-flicker logic."""

    def _make_frame(self, value: int, shape=(480, 640, 3)) -> np.ndarray:
        return np.full(shape, value, dtype=np.uint8)

    def test_black_frame_detected(self):
        """All-black frame (PrintWindow blank) is a glitch."""
        good = self._make_frame(128)
        black = self._make_frame(0)
        assert _is_glitch_frame(black, good) is True

    def test_near_black_frame_detected(self):
        """Nearly-black frame (mean < 3) is a glitch."""
        good = self._make_frame(128)
        dark = self._make_frame(2)
        assert _is_glitch_frame(dark, good) is True

    def test_white_flash_detected(self):
        """All-white frame (DWM flash) is a glitch."""
        good = self._make_frame(128)
        white = self._make_frame(255)
        assert _is_glitch_frame(white, good) is True

    def test_near_white_flash_detected(self):
        """Nearly-white frame (mean > 252) is a glitch."""
        good = self._make_frame(128)
        bright = self._make_frame(253)
        assert _is_glitch_frame(bright, good) is True

    def test_similar_frame_not_glitch(self):
        """Frame with similar brightness is not a glitch."""
        good = self._make_frame(128)
        similar = self._make_frame(135)
        assert _is_glitch_frame(similar, good) is False

    def test_moderate_change_not_glitch(self):
        """Moderate brightness change (e.g., slide change) is not a glitch."""
        good = self._make_frame(100)
        changed = self._make_frame(140)  # 40% change
        assert _is_glitch_frame(changed, good) is False

    def test_extreme_brightness_jump_detected(self):
        """Large sudden brightness jump (>60%) is a glitch."""
        good = self._make_frame(80)
        flash = self._make_frame(200)  # 150% change
        assert _is_glitch_frame(flash, good) is True

    def test_dark_reference_not_flagged(self):
        """When last good frame is very dark (mean <= 5), skip ratio check."""
        dark_ref = self._make_frame(3)
        normal = self._make_frame(128)
        # dark_ref mean is 3 (< 5), so ratio check skipped;
        # normal frame mean is 128, not near-black/white
        assert _is_glitch_frame(normal, dark_ref) is False

    def test_real_content_with_variance(self):
        """Frame with realistic pixel variance is not a glitch."""
        rng = np.random.RandomState(42)
        good = rng.randint(50, 200, (480, 640, 3), dtype=np.uint8)
        # Slightly different content
        similar = good.copy()
        similar[:240, :, :] += 10
        assert _is_glitch_frame(similar, good) is False


class TestPickMonitorForRect:
    """Monitor selection for the screen-share fallback."""

    # mss convention: monitors[0] is the virtual union of every display,
    # monitors[1:] are the individual physical monitors.
    _PRIMARY = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    _SECONDARY = {"left": 1920, "top": 0, "width": 2560, "height": 1440}
    _UNION = {"left": 0, "top": 0, "width": 4480, "height": 1440}

    def test_single_monitor_returns_union(self):
        """Only monitors[0] (union) available → return it."""
        sct = _FakeSct([self._UNION])
        # last_rect doesn't matter here
        assert _pick_monitor_for_rect(sct, (100, 100, 800, 600)) is self._UNION

    def test_no_last_rect_returns_primary(self):
        """No prior rect known → fall back to the primary monitor."""
        sct = _FakeSct([self._UNION, self._PRIMARY, self._SECONDARY])
        assert _pick_monitor_for_rect(sct, None) is self._PRIMARY

    def test_rect_on_primary(self):
        """Window centred on the primary monitor → primary."""
        sct = _FakeSct([self._UNION, self._PRIMARY, self._SECONDARY])
        # Rect centre at (500, 400) → primary
        assert _pick_monitor_for_rect(sct, (100, 100, 800, 600)) is self._PRIMARY

    def test_rect_on_secondary(self):
        """Window centred on the secondary monitor → secondary."""
        sct = _FakeSct([self._UNION, self._PRIMARY, self._SECONDARY])
        # Rect centre at (3200, 720) → inside secondary
        assert _pick_monitor_for_rect(sct, (2900, 500, 600, 400)) is self._SECONDARY

    def test_rect_outside_all_falls_back_to_primary(self):
        """Window off-screen → fall back to primary rather than raising."""
        sct = _FakeSct([self._UNION, self._PRIMARY, self._SECONDARY])
        assert _pick_monitor_for_rect(sct, (-5000, -5000, 100, 100)) is self._PRIMARY


class TestFindShareMonitor:
    """_find_share_monitor uses Zoom/Teams share-toolbar location as the signal."""

    _PRIMARY = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    _SECONDARY = {"left": 1920, "top": 0, "width": 2560, "height": 1440}
    _UNION = {"left": 0, "top": 0, "width": 4480, "height": 1440}

    def test_returns_none_when_enum_finds_no_zoom_windows(self):
        """No Zoom-owned visible windows → None (caller falls back to last-rect)."""
        sct = _FakeSct([self._UNION, self._PRIMARY, self._SECONDARY])
        with mock.patch("psutil.process_iter", return_value=[]), \
             mock.patch("ctypes.windll.user32.EnumWindows") as enum, \
             mock.patch(
                 "meeting_recorder.video.screen_capture._pick_monitor_for_rect"
             ) as pick:
            # EnumWindows is called but the callback never appends a candidate
            # (stubbed away), so the function should bail out without calling
            # the monitor picker.
            result = _find_share_monitor(
                sct, pid=1234, process_name="Zoom.exe", exclude_hwnd=999
            )
        enum.assert_called_once()
        pick.assert_not_called()
        assert result is None

    def test_enum_exception_returns_none_gracefully(self):
        """If Win32 EnumWindows raises, caller gets None (no crash)."""
        sct = _FakeSct([self._UNION, self._PRIMARY])
        with mock.patch("psutil.process_iter", return_value=[]), \
             mock.patch(
                 "ctypes.windll.user32.EnumWindows",
                 side_effect=OSError("enum broke"),
             ):
            result = _find_share_monitor(
                sct, pid=1234, process_name="Zoom.exe", exclude_hwnd=999
            )
        assert result is None


class _FakeWriter:
    """Minimal cv2.VideoWriter-like stub that records written frames."""

    def __init__(self, fail_after: int = -1):
        self.frames: list[np.ndarray] = []
        self.released = False
        self._fail_after = fail_after  # raise on the Nth write (-1 = never)

    def write(self, frame):
        if self._fail_after >= 0 and len(self.frames) >= self._fail_after:
            raise RuntimeError("writer died")
        self.frames.append(frame)

    def isOpened(self):
        return True

    def release(self):
        self.released = True


def _make_capture(fps: float = 10.0) -> ScreenCapture:
    sc = ScreenCapture(pid=1234, process_name="test.exe", output_path=Path("out.mp4"), fps=fps)
    sc._frame_queue = queue.Queue(maxsize=64)
    return sc


def _run_writer(sc: ScreenCapture, writer, items, cv2_module=None):
    """Prefill the queue with items + sentinel and run the writer loop synchronously."""
    for item in items:
        sc._frame_queue.put_nowait(item)
    sc._frame_queue.put_nowait(None)  # stop sentinel
    sc._writer_loop(writer, cv2_module or mock.MagicMock())


class TestWriterLoopTiming:
    """Deadline / frame-duplication math in ScreenCapture._writer_loop."""

    _FRAME = np.zeros((4, 4, 3), dtype=np.uint8)

    def test_one_write_per_slot_when_on_schedule(self):
        """Frames arriving each interval produce exactly one write each."""
        sc = _make_capture(fps=10.0)
        w = _FakeWriter()
        items = [(self._FRAME, 100.0 + i * 0.1, False) for i in range(5)]
        _run_writer(sc, w, items)
        assert len(w.frames) == 5

    def test_overrun_fills_missed_slots(self):
        """A slow grab/encode iteration is compensated with duplicate writes."""
        sc = _make_capture(fps=10.0)
        w = _FakeWriter()
        # Second frame arrives 0.55s late: slots 2-6 are due -> 5 writes
        items = [(self._FRAME, 100.0, False), (self._FRAME, 100.55, False)]
        _run_writer(sc, w, items)
        assert len(w.frames) == 6

    def test_duplicates_capped_per_iteration(self):
        """A pathological stall writes at most _MAX_DUP_PER_FRAME duplicates."""
        sc = _make_capture(fps=30.0)
        w = _FakeWriter()
        # 1.5s stall at 30 FPS = 45 missed slots, capped at 30
        items = [(self._FRAME, 100.0, False), (self._FRAME, 101.5, False)]
        _run_writer(sc, w, items)
        assert len(w.frames) == 1 + screen_capture._MAX_DUP_PER_FRAME

    def test_deficit_carries_over_after_cap(self):
        """Slots not filled due to the cap are filled by subsequent frames."""
        sc = _make_capture(fps=30.0)
        w = _FakeWriter()
        # 45 slots due at second frame (cap 30), remaining 15+1 due at third
        items = [
            (self._FRAME, 100.0, False),
            (self._FRAME, 101.5, False),
            (self._FRAME, 101.5 + 1 / 30.0, False),
        ]
        _run_writer(sc, w, items)
        assert len(w.frames) == 1 + 30 + 16

    def test_extreme_stall_resyncs_timeline(self):
        """Beyond _RESYNC_DEFICIT_SECONDS the timeline jumps instead of duplicating."""
        sc = _make_capture(fps=30.0)
        w = _FakeWriter()
        # 20s stall = 600 slots > 300-slot threshold -> resync, single write
        items = [(self._FRAME, 100.0, False), (self._FRAME, 120.0, False)]
        _run_writer(sc, w, items)
        assert len(w.frames) == 2

    def test_resync_marker_skips_pause_gap(self):
        """A resync-flagged frame (resume after pause) is not back-filled."""
        sc = _make_capture(fps=10.0)
        w = _FakeWriter()
        # 5s gap, but the second frame carries the resync marker
        items = [(self._FRAME, 100.0, False), (self._FRAME, 105.0, True)]
        _run_writer(sc, w, items)
        assert len(w.frames) == 2

    def test_early_frame_in_same_slot_is_skipped(self):
        """A frame arriving within an already-filled slot is not written."""
        sc = _make_capture(fps=10.0)
        w = _FakeWriter()
        items = [(self._FRAME, 100.0, False), (self._FRAME, 100.01, False)]
        _run_writer(sc, w, items)
        assert len(w.frames) == 1

    def test_writer_released_after_sentinel(self):
        sc = _make_capture(fps=10.0)
        w = _FakeWriter()
        _run_writer(sc, w, [(self._FRAME, 100.0, False)])
        assert w.released is True

    def test_cv2_writer_failure_stops_video_gracefully(self):
        """A non-ffmpeg writer failure sets _writer_failed without raising."""
        sc = _make_capture(fps=10.0)
        w = _FakeWriter(fail_after=1)
        items = [(self._FRAME, 100.0, False), (self._FRAME, 100.1, False)]
        _run_writer(sc, w, items)
        assert sc._writer_failed is True
        assert len(w.frames) == 1  # first write succeeded


class TestSubmitFrameQueue:
    """Drop-oldest semantics of ScreenCapture._submit_frame."""

    _FRAME = np.zeros((4, 4, 3), dtype=np.uint8)

    def _capture_with_queue(self, maxsize: int) -> ScreenCapture:
        sc = ScreenCapture(pid=1, process_name="t.exe", output_path=Path("o.mp4"))
        sc._frame_queue = queue.Queue(maxsize=maxsize)
        return sc

    def test_frames_queue_in_order(self):
        sc = self._capture_with_queue(maxsize=4)
        sc._submit_frame(self._FRAME, 1.0)
        sc._submit_frame(self._FRAME, 2.0)
        assert sc._frame_queue.qsize() == 2
        assert sc._frame_queue.get_nowait()[1] == 1.0
        assert sc._frame_queue.get_nowait()[1] == 2.0
        assert sc._dropped_frames == 0

    def test_full_queue_drops_oldest(self):
        sc = self._capture_with_queue(maxsize=2)
        sc._submit_frame(self._FRAME, 1.0)
        sc._submit_frame(self._FRAME, 2.0)
        sc._submit_frame(self._FRAME, 3.0)  # overflow: 1.0 is dropped
        assert sc._dropped_frames == 1
        timestamps = [sc._frame_queue.get_nowait()[1] for _ in range(2)]
        assert timestamps == [2.0, 3.0]

    def test_dropped_resync_marker_is_carried(self):
        """If a resync-flagged frame is dropped, the marker moves to the new frame."""
        sc = self._capture_with_queue(maxsize=1)
        sc._pending_resync = True
        sc._submit_frame(self._FRAME, 1.0)  # queued with resync=True
        sc._submit_frame(self._FRAME, 2.0)  # drops the marked frame
        item = sc._frame_queue.get_nowait()
        assert item[1] == 2.0
        assert item[2] is True

    def test_paused_skips_enqueue(self):
        sc = self._capture_with_queue(maxsize=4)
        sc.paused = True
        sc._submit_frame(self._FRAME, 1.0)
        assert sc._frame_queue.empty()

    def test_writer_failed_skips_enqueue(self):
        sc = self._capture_with_queue(maxsize=4)
        sc._writer_failed = True
        sc._submit_frame(self._FRAME, 1.0)
        assert sc._frame_queue.empty()

    def test_no_queue_is_noop(self):
        sc = ScreenCapture(pid=1, process_name="t.exe", output_path=Path("o.mp4"))
        sc._submit_frame(self._FRAME, 1.0)  # must not raise


class TestParseEncoderNames:
    """Parsing of `ffmpeg -encoders` output."""

    _SAMPLE = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
"""

    def test_video_encoders_extracted(self):
        names = _parse_encoder_names(self._SAMPLE)
        assert "libx264" in names
        assert "h264_nvenc" in names

    def test_audio_encoders_excluded(self):
        assert "aac" not in _parse_encoder_names(self._SAMPLE)

    def test_header_lines_excluded(self):
        assert "=" not in _parse_encoder_names(self._SAMPLE)

    def test_empty_output(self):
        assert _parse_encoder_names("") == set()


class TestProbeBestEncoder:
    """Encoder probing and caching."""

    _LISTING_BOTH = (
        " V....D libx264              libx264 (codec h264)\n"
        " V....D h264_nvenc           NVENC (codec h264)\n"
    )

    @pytest.fixture(autouse=True)
    def _clear_cache(self, monkeypatch):
        monkeypatch.setattr(ffmpeg_writer, "_ffmpeg_encoder_cache", None)

    def _completed(self, returncode=0, stdout=""):
        result = mock.MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        return result

    def test_prefers_nvenc_when_test_encode_passes(self):
        with mock.patch.object(
            ffmpeg_writer.subprocess, "run",
            return_value=self._completed(stdout=self._LISTING_BOTH),
        ), mock.patch.object(ffmpeg_writer, "_test_encode", return_value=True):
            assert _probe_best_encoder("ffmpeg.exe") == "h264_nvenc"

    def test_falls_back_to_libx264_when_nvenc_unusable(self):
        """NVENC listed but fails to initialize (no GPU) -> libx264."""
        with mock.patch.object(
            ffmpeg_writer.subprocess, "run",
            return_value=self._completed(stdout=self._LISTING_BOTH),
        ), mock.patch.object(ffmpeg_writer, "_test_encode", return_value=False):
            assert _probe_best_encoder("ffmpeg.exe") == "libx264"

    def test_raises_when_no_encoder_available(self):
        with mock.patch.object(
            ffmpeg_writer.subprocess, "run",
            return_value=self._completed(stdout=" A....D aac   AAC\n"),
        ):
            with pytest.raises(RuntimeError):
                _probe_best_encoder("ffmpeg.exe")

    def test_raises_when_probe_command_fails(self):
        with mock.patch.object(
            ffmpeg_writer.subprocess, "run",
            return_value=self._completed(returncode=1),
        ):
            with pytest.raises(RuntimeError):
                _probe_best_encoder("ffmpeg.exe")

    def test_successful_probe_is_cached(self):
        with mock.patch.object(
            ffmpeg_writer.subprocess, "run",
            return_value=self._completed(stdout=self._LISTING_BOTH),
        ) as run, mock.patch.object(ffmpeg_writer, "_test_encode", return_value=True):
            assert _probe_best_encoder("ffmpeg.exe") == "h264_nvenc"
            assert _probe_best_encoder("ffmpeg.exe") == "h264_nvenc"
        assert run.call_count == 1


class TestFFmpegVideoWriter:
    """FFmpegVideoWriter subprocess lifecycle (Popen mocked)."""

    def _fake_imageio(self):
        stub = mock.MagicMock()
        stub.get_ffmpeg_exe.return_value = "ffmpeg.exe"
        return stub

    def _make_writer(self, proc):
        with mock.patch.dict(sys.modules, {"imageio_ffmpeg": self._fake_imageio()}), \
             mock.patch.object(ffmpeg_writer.subprocess, "Popen", return_value=proc), \
             mock.patch.object(
                 ffmpeg_writer, "_probe_best_encoder", return_value="libx264"
             ):
            return FFmpegVideoWriter(Path("out.mp4"), fps=30.0, width=4, height=4)

    def _live_proc(self):
        proc = mock.MagicMock()
        proc.poll.return_value = None
        return proc

    def test_missing_imageio_ffmpeg_raises_importerror(self):
        with mock.patch.dict(sys.modules, {"imageio_ffmpeg": None}):
            with pytest.raises(ImportError):
                FFmpegVideoWriter(Path("out.mp4"), fps=30.0, width=4, height=4)

    def test_immediate_process_exit_raises(self):
        proc = mock.MagicMock()
        proc.poll.return_value = 1  # died at startup
        with pytest.raises(RuntimeError):
            self._make_writer(proc)

    def test_write_sends_frame_bytes_to_stdin(self):
        proc = self._live_proc()
        w = self._make_writer(proc)
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        w.write(frame)
        proc.stdin.write.assert_called_once_with(frame.tobytes())

    def test_write_raises_on_broken_pipe(self):
        proc = self._live_proc()
        w = self._make_writer(proc)
        proc.stdin.write.side_effect = BrokenPipeError("ffmpeg gone")
        with pytest.raises(RuntimeError):
            w.write(np.zeros((4, 4, 3), dtype=np.uint8))
        assert w.isOpened() is False

    def test_write_rejects_wrong_frame_size(self):
        w = self._make_writer(self._live_proc())
        with pytest.raises(ValueError):
            w.write(np.zeros((8, 8, 3), dtype=np.uint8))

    def test_release_closes_stdin_and_waits(self):
        proc = self._live_proc()
        w = self._make_writer(proc)
        w.release()
        proc.stdin.close.assert_called_once()
        proc.wait.assert_called_once()
        assert w.isOpened() is False

    def _captured_args(self, **kwargs):
        """Build a writer and return the argv passed to ffmpeg's Popen."""
        proc = self._live_proc()
        with mock.patch.dict(sys.modules, {"imageio_ffmpeg": self._fake_imageio()}), \
             mock.patch.object(
                 ffmpeg_writer.subprocess, "Popen", return_value=proc
             ) as popen, \
             mock.patch.object(
                 ffmpeg_writer, "_probe_best_encoder", return_value="h264_nvenc"
             ):
            FFmpegVideoWriter(Path("out.mp4"), fps=30.0, width=4, height=4, **kwargs)
        return popen.call_args.args[0]

    def test_uses_fragmented_mp4_for_crash_resilience(self):
        # Without these flags a crash mid-recording leaves an unplayable file
        # (no final moov atom). Fragments + packet flush make partials playable.
        args = self._captured_args()
        assert "-movflags" in args
        movflags = args[args.index("-movflags") + 1]
        assert "frag_keyframe" in movflags
        assert "empty_moov" in movflags
        assert "-flush_packets" in args
        assert "-frag_duration" in args

    def test_quality_flows_into_nvenc_cq(self):
        args = self._captured_args(quality=18)
        assert "-cq" in args
        assert args[args.index("-cq") + 1] == "18"

    def test_quality_is_clamped(self):
        args = self._captured_args(quality=999)
        assert args[args.index("-cq") + 1] == "51"


class TestCreateWriterFallback:
    """ScreenCapture._create_writer: ffmpeg preferred, cv2 fallback."""

    def _capture(self) -> ScreenCapture:
        return ScreenCapture(pid=1, process_name="t.exe", output_path=Path("o.mp4"))

    def test_prefers_ffmpeg_writer(self):
        ff = mock.MagicMock()
        ff.encoder = "h264_nvenc"
        cv2_module = mock.MagicMock()
        with mock.patch.object(
            screen_capture, "FFmpegVideoWriter", return_value=ff
        ):
            result = self._capture()._create_writer(cv2_module, 640, 480)
        assert result is ff
        cv2_module.VideoWriter.assert_not_called()

    def test_falls_back_to_cv2_when_ffmpeg_unavailable(self):
        cv2_module = mock.MagicMock()
        cv2_writer = mock.MagicMock()
        cv2_writer.isOpened.return_value = True
        cv2_module.VideoWriter.return_value = cv2_writer
        with mock.patch.object(
            screen_capture, "FFmpegVideoWriter",
            side_effect=RuntimeError("no ffmpeg binary"),
        ):
            result = self._capture()._create_writer(cv2_module, 640, 480)
        assert result is cv2_writer
        # avc1 tried first (existing behavior preserved)
        cv2_module.VideoWriter_fourcc.assert_any_call("a", "v", "c", "1")

    def test_returns_none_when_no_writer_opens(self):
        cv2_module = mock.MagicMock()
        cv2_writer = mock.MagicMock()
        cv2_writer.isOpened.return_value = False
        cv2_module.VideoWriter.return_value = cv2_writer
        with mock.patch.object(
            screen_capture, "FFmpegVideoWriter",
            side_effect=ImportError("imageio_ffmpeg not installed"),
        ):
            result = self._capture()._create_writer(cv2_module, 640, 480)
        assert result is None


class TestFfmpegFailoverMidRecording:
    """Writer loop fails over from a dead ffmpeg process to cv2."""

    _FRAME = np.zeros((4, 4, 3), dtype=np.uint8)

    def _broken_ffmpeg_writer(self):
        proc = mock.MagicMock()
        proc.poll.return_value = None
        stub = mock.MagicMock()
        stub.get_ffmpeg_exe.return_value = "ffmpeg.exe"
        with mock.patch.dict(sys.modules, {"imageio_ffmpeg": stub}), \
             mock.patch.object(ffmpeg_writer.subprocess, "Popen", return_value=proc), \
             mock.patch.object(
                 ffmpeg_writer, "_probe_best_encoder", return_value="h264_nvenc"
             ):
            w = FFmpegVideoWriter(Path("o.mp4"), fps=10.0, width=4, height=4)
        proc.stdin.write.side_effect = BrokenPipeError("ffmpeg died")
        return w

    def test_failover_to_cv2_continues_recording(self):
        sc = _make_capture(fps=10.0)
        sc._writer_size = (4, 4)
        ffmpeg_writer = self._broken_ffmpeg_writer()

        cv2_writer = _FakeWriter()
        cv2_module = mock.MagicMock()
        cv2_module.VideoWriter.return_value = cv2_writer

        items = [(self._FRAME, 100.0, False), (self._FRAME, 100.1, False)]
        _run_writer(sc, ffmpeg_writer, items, cv2_module=cv2_module)

        assert sc._writer_failed is False
        # First frame lost to the dead ffmpeg writer; second lands in cv2
        assert len(cv2_writer.frames) == 1
        assert cv2_writer.released is True

    def test_failover_failure_stops_video_without_raising(self):
        sc = _make_capture(fps=10.0)
        sc._writer_size = (4, 4)
        ffmpeg_writer = self._broken_ffmpeg_writer()

        cv2_writer = mock.MagicMock()
        cv2_writer.isOpened.return_value = False  # cv2 cannot open either
        cv2_module = mock.MagicMock()
        cv2_module.VideoWriter.return_value = cv2_writer

        items = [(self._FRAME, 100.0, False)]
        _run_writer(sc, ffmpeg_writer, items, cv2_module=cv2_module)

        assert sc._writer_failed is True


class TestCaptureManagerGetScreenFrame:
    """Test CaptureManager.get_screen_frame() delegation."""

    def test_returns_none_when_screen_capture_disabled(self):
        from meeting_recorder.audio.capture_manager import CaptureManager

        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"):
            cm = CaptureManager(
                pid=1234,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
            )
            assert cm.get_screen_frame() is None

    def test_returns_frame_from_screen_capture(self):
        from meeting_recorder.audio.capture_manager import CaptureManager

        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        with mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"), \
             mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"):
            cm = CaptureManager(
                pid=1234,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
            )
            # Simulate a screen capture with a frame
            mock_sc = mock.MagicMock()
            mock_sc.latest_frame = frame
            cm._screen_capture = mock_sc
            assert cm.get_screen_frame() is frame
