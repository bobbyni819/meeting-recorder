"""Tests for silence detection and auto-switch to desktop audio."""

from __future__ import annotations

import struct
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from meeting_recorder.audio.capture_manager import (
    _SILENCE_CHECK_SECONDS,
    _SILENCE_RMS_THRESHOLD,
    _is_buffer_silent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_silence(num_samples: int = 1000) -> bytes:
    """Create raw int16 PCM bytes of all zeros (silence)."""
    return b"\x00\x00" * num_samples


def _make_audio(amplitude: int = 5000, num_samples: int = 1000) -> bytes:
    """Create raw int16 PCM bytes with a constant non-zero amplitude."""
    return np.full(num_samples, amplitude, dtype=np.int16).tobytes()


def _make_low_noise(amplitude: int = 5, num_samples: int = 1000) -> bytes:
    """Create raw int16 PCM bytes with very low amplitude noise (below threshold)."""
    rng = np.random.default_rng(42)
    samples = rng.integers(-amplitude, amplitude + 1, size=num_samples, dtype=np.int16)
    return samples.tobytes()


# ---------------------------------------------------------------------------
# _is_buffer_silent — core function tests
# ---------------------------------------------------------------------------

class TestIsBufferSilent:
    """Tests for the module-level _is_buffer_silent helper."""

    def test_all_zeros_is_silent(self):
        """All-zero buffer should be detected as silent."""
        data = _make_silence(1000)
        assert _is_buffer_silent(data) is True

    def test_real_audio_is_not_silent(self):
        """Buffer with substantial amplitude should NOT be silent."""
        data = _make_audio(amplitude=5000, num_samples=1000)
        assert _is_buffer_silent(data) is False

    def test_very_low_noise_is_silent(self):
        """Buffer with amplitude well below threshold should be silent."""
        data = _make_low_noise(amplitude=3, num_samples=1000)
        assert _is_buffer_silent(data) is True

    def test_empty_bytes_is_silent(self):
        """Empty input should be treated as silent."""
        assert _is_buffer_silent(b"") is True

    def test_single_byte_is_silent(self):
        """A single byte is less than one int16 sample — should be silent."""
        assert _is_buffer_silent(b"\x01") is True

    def test_threshold_boundary_below(self):
        """Constant amplitude just below threshold should be silent."""
        # RMS of a constant signal == the amplitude itself
        amplitude = _SILENCE_RMS_THRESHOLD - 1
        data = np.full(500, amplitude, dtype=np.int16).tobytes()
        assert _is_buffer_silent(data) is True

    def test_threshold_boundary_above(self):
        """Constant amplitude at or above threshold should NOT be silent."""
        amplitude = _SILENCE_RMS_THRESHOLD + 1
        data = np.full(500, amplitude, dtype=np.int16).tobytes()
        assert _is_buffer_silent(data) is False

    def test_negative_samples_counted(self):
        """Negative amplitude should still register as non-silent."""
        data = np.full(500, -5000, dtype=np.int16).tobytes()
        assert _is_buffer_silent(data) is False

    def test_uses_struct_unpack(self):
        """Verify the function correctly decodes via struct (little-endian int16)."""
        # Manually encode two samples: 100 and -100
        data = struct.pack("<hh", 100, -100)
        # RMS = sqrt((100^2 + 100^2) / 2) = 100, well above threshold
        assert _is_buffer_silent(data) is False

    def test_odd_length_ignores_trailing_byte(self):
        """Odd-length data should decode only complete int16 samples."""
        # 5 bytes = 2 samples (4 bytes) + 1 trailing byte
        data = struct.pack("<hh", 5000, 5000) + b"\xff"
        assert _is_buffer_silent(data) is False


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

class TestConstants:
    """Verify module constants have reasonable values."""

    def test_silence_check_seconds(self):
        assert _SILENCE_CHECK_SECONDS == 3.0

    def test_silence_rms_threshold(self):
        assert _SILENCE_RMS_THRESHOLD == 10
