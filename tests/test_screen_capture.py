"""Tests for ScreenCapture frame cache, glitch detection, and CaptureManager.get_screen_frame()."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from meeting_recorder.video.screen_capture import ScreenCapture, _is_glitch_frame


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
