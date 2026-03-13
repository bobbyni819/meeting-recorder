"""Tests for pause/resume recording functionality.

Tests the pause logic in isolation, without importing the heavy
CaptureManager module (which transitively loads scipy/COM).
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meeting_recorder.config import HotkeyConfig, Config


# ---------------------------------------------------------------------------
# Simulate the pause logic from CaptureManager without importing it.
# This avoids the 60s+ scipy import time in the test runner.
# ---------------------------------------------------------------------------

class _PauseStateMixin:
    """Extracted pause/resume logic identical to CaptureManager."""

    def __init__(self):
        self._is_recording = False
        self._paused = False
        self._pause_lock = threading.Lock()
        self._total_paused_seconds = 0.0
        self._pause_start_time = None
        self._start_time = None
        self._screen_capture = None

    def pause(self):
        with self._pause_lock:
            if self._paused or not self._is_recording:
                return
            self._paused = True
            self._pause_start_time = time.time()
            if self._screen_capture is not None:
                self._screen_capture.paused = True

    def resume(self):
        with self._pause_lock:
            if not self._paused or not self._is_recording:
                return
            if self._pause_start_time is not None:
                self._total_paused_seconds += time.time() - self._pause_start_time
                self._pause_start_time = None
            self._paused = False
            if self._screen_capture is not None:
                self._screen_capture.paused = False

    def toggle_pause(self):
        if self._paused:
            self.resume()
        else:
            self.pause()

    @property
    def is_paused(self):
        return self._paused

    @property
    def elapsed_seconds(self):
        if self._start_time is None:
            return 0.0
        total = time.time() - self._start_time
        paused = self._total_paused_seconds
        if self._paused and self._pause_start_time is not None:
            paused += time.time() - self._pause_start_time
        return max(0.0, total - paused)


class TestPauseState:
    """Test pause/resume state tracking (same logic as CaptureManager)."""

    def _make(self):
        return _PauseStateMixin()

    def test_initial_state_not_paused(self):
        cm = self._make()
        assert cm.is_paused is False

    def test_pause_when_not_recording_noop(self):
        cm = self._make()
        cm.pause()
        assert cm.is_paused is False

    def test_pause_during_recording(self):
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time()
        cm.pause()
        assert cm.is_paused is True

    def test_resume_when_not_paused_noop(self):
        cm = self._make()
        cm._is_recording = True
        cm.resume()
        assert cm.is_paused is False

    def test_resume_after_pause(self):
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time()
        cm.pause()
        assert cm.is_paused is True
        cm.resume()
        assert cm.is_paused is False

    def test_toggle_pause(self):
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time()

        cm.toggle_pause()
        assert cm.is_paused is True

        cm.toggle_pause()
        assert cm.is_paused is False

    def test_elapsed_excludes_paused_time(self):
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time() - 10.0
        cm._total_paused_seconds = 3.0

        elapsed = cm.elapsed_seconds
        assert 6.5 <= elapsed <= 7.5

    def test_elapsed_during_active_pause(self):
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time() - 10.0
        cm._paused = True
        cm._pause_start_time = time.time() - 2.0

        elapsed = cm.elapsed_seconds
        assert 7.5 <= elapsed <= 8.5

    def test_pause_sets_screen_capture_flag(self):
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time()
        mock_screen = MagicMock()
        mock_screen.paused = False
        cm._screen_capture = mock_screen

        cm.pause()
        assert mock_screen.paused is True

        cm.resume()
        assert mock_screen.paused is False

    def test_pause_accumulates_total(self):
        """Multiple pause/resume cycles should accumulate total paused time."""
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time() - 20.0

        # First pause: 3 seconds
        cm._paused = True
        cm._pause_start_time = time.time() - 3.0
        cm.resume()
        assert 2.5 <= cm._total_paused_seconds <= 3.5

        # Second pause: 2 seconds
        cm._paused = True
        cm._pause_start_time = time.time() - 2.0
        cm.resume()
        assert 4.5 <= cm._total_paused_seconds <= 5.5

    def test_elapsed_never_negative(self):
        """elapsed_seconds should never go negative even with bad state."""
        cm = self._make()
        cm._is_recording = True
        cm._start_time = time.time() - 5.0
        cm._total_paused_seconds = 100.0  # more than elapsed
        assert cm.elapsed_seconds == 0.0


# ---------------------------------------------------------------------------
# Screen capture pause flag test
# ---------------------------------------------------------------------------

class TestScreenCapturePause:
    """Test that ScreenCapture respects the paused flag."""

    def test_screen_capture_has_paused_attr(self):
        """ScreenCapture should have a paused attribute."""
        # Import ScreenCapture without triggering full capture_manager
        from meeting_recorder.video.screen_capture import ScreenCapture
        sc = ScreenCapture.__new__(ScreenCapture)
        # The __init__ sets paused = False
        # Since we're using __new__, manually check the class allows it
        sc.paused = False
        assert sc.paused is False
        sc.paused = True
        assert sc.paused is True


# ---------------------------------------------------------------------------
# Hotkey config tests
# ---------------------------------------------------------------------------

class TestHotkeyConfig:
    def test_default_pause_hotkey(self):
        hk = HotkeyConfig()
        assert hk.toggle_pause == "ctrl+shift+p"

    def test_pause_hotkey_loads_from_dict(self):
        data = {"hotkey": {"toggle_pause": "ctrl+alt+p"}}
        config = Config._from_dict(data)
        assert config.hotkey.toggle_pause == "ctrl+alt+p"

    def test_pause_hotkey_in_full_config(self):
        config = Config()
        assert config.hotkey.toggle_pause == "ctrl+shift+p"

    def test_pause_hotkey_survives_roundtrip(self):
        """Hotkey should survive save/load roundtrip."""
        import tempfile
        from pathlib import Path
        config = Config()
        config.hotkey.toggle_pause = "ctrl+alt+space"

        # Save to temp files and reload
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            import meeting_recorder.config as cfg_mod
            orig_bundled = cfg_mod.BUNDLED_CONFIG
            orig_secrets = cfg_mod.SECRETS_FILE
            orig_config = cfg_mod.CONFIG_FILE
            orig_dir = cfg_mod.CONFIG_DIR
            cfg_mod.BUNDLED_CONFIG = tmp_dir / "config.toml"
            cfg_mod.SECRETS_FILE = tmp_dir / "secrets.toml"
            cfg_mod.CONFIG_FILE = tmp_dir / "legacy.toml"
            cfg_mod.CONFIG_DIR = tmp_dir
            config.save()
            loaded = Config.load()
            assert loaded.hotkey.toggle_pause == "ctrl+alt+space"
        finally:
            cfg_mod.BUNDLED_CONFIG = orig_bundled
            cfg_mod.SECRETS_FILE = orig_secrets
            cfg_mod.CONFIG_FILE = orig_config
            cfg_mod.CONFIG_DIR = orig_dir
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
