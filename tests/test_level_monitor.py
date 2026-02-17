"""Tests for real-time audio level monitoring."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from meeting_recorder.audio.level_monitor import (
    MIN_DB,
    REFERENCE_AMPLITUDE,
    AudioLevelMonitor,
    compute_peak_db,
    compute_rms_db,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_const_audio(value: int, num_samples: int = 1000) -> bytes:
    """Create raw int16 PCM bytes filled with a constant sample value."""
    return np.full(num_samples, value, dtype=np.int16).tobytes()


def _make_silence(num_samples: int = 1000) -> bytes:
    """Create raw int16 PCM bytes of silence (all zeros)."""
    return np.zeros(num_samples, dtype=np.int16).tobytes()


# ---------------------------------------------------------------------------
# compute_rms_db
# ---------------------------------------------------------------------------

class TestComputeRmsDb:
    """Tests for the compute_rms_db function."""

    def test_silence_returns_min_db(self):
        audio = _make_silence()
        assert compute_rms_db(audio) == MIN_DB

    def test_full_scale_returns_approx_zero_db(self):
        # Full-scale int16 is 32767; constant signal => RMS == amplitude
        audio = _make_const_audio(32767)
        db = compute_rms_db(audio)
        # 20*log10(32767/32768) is approximately -0.00026 dB
        assert db == pytest.approx(0.0, abs=0.01)

    def test_half_amplitude_returns_approx_minus_6_db(self):
        # Half of 32768 = 16384; 20*log10(16384/32768) = -6.02 dB
        audio = _make_const_audio(16384)
        db = compute_rms_db(audio)
        assert db == pytest.approx(-6.02, abs=0.1)

    def test_empty_bytes_returns_min_db(self):
        assert compute_rms_db(b"") == MIN_DB

    def test_single_byte_returns_min_db(self):
        # A single byte is less than one int16 sample
        assert compute_rms_db(b"\x01") == MIN_DB

    def test_single_sample(self):
        # One int16 sample of value 1000
        audio = np.array([1000], dtype=np.int16).tobytes()
        db = compute_rms_db(audio)
        expected = 20.0 * np.log10(1000.0 / REFERENCE_AMPLITUDE)
        assert db == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# compute_peak_db
# ---------------------------------------------------------------------------

class TestComputePeakDb:
    """Tests for the compute_peak_db function."""

    def test_silence_returns_min_db(self):
        audio = _make_silence()
        assert compute_peak_db(audio) == MIN_DB

    def test_full_scale_returns_approx_zero_db(self):
        audio = _make_const_audio(32767)
        db = compute_peak_db(audio)
        assert db == pytest.approx(0.0, abs=0.01)

    def test_known_peak(self):
        # Mix of values; peak should be the largest absolute value
        samples = np.array([100, -8000, 4000, 200], dtype=np.int16)
        audio = samples.tobytes()
        db = compute_peak_db(audio)
        expected = 20.0 * np.log10(8000.0 / REFERENCE_AMPLITUDE)
        assert db == pytest.approx(expected, abs=0.01)

    def test_empty_bytes_returns_min_db(self):
        assert compute_peak_db(b"") == MIN_DB


# ---------------------------------------------------------------------------
# AudioLevelMonitor -- level updates
# ---------------------------------------------------------------------------

class TestAudioLevelMonitorUpdates:
    """Test that update methods correctly set internal levels."""

    def test_update_app_level_updates_properties(self):
        monitor = AudioLevelMonitor()
        audio = _make_const_audio(16384)
        monitor.update_app_level(audio)
        rms, peak = monitor.app_level
        assert rms > MIN_DB
        assert peak > MIN_DB

    def test_update_mic_level_updates_properties(self):
        monitor = AudioLevelMonitor()
        audio = _make_const_audio(8000)
        monitor.update_mic_level(audio)
        rms, peak = monitor.mic_level
        assert rms > MIN_DB
        assert peak > MIN_DB

    def test_app_level_tuple_format(self):
        monitor = AudioLevelMonitor()
        audio = _make_const_audio(16384)
        monitor.update_app_level(audio)
        result = monitor.app_level
        assert isinstance(result, tuple)
        assert len(result) == 2
        rms_db, peak_db = result
        # For a constant signal, RMS == peak, so both dB values should match
        assert rms_db == pytest.approx(peak_db, abs=0.01)

    def test_mic_level_tuple_format(self):
        monitor = AudioLevelMonitor()
        audio = _make_const_audio(8000)
        monitor.update_mic_level(audio)
        result = monitor.mic_level
        assert isinstance(result, tuple)
        assert len(result) == 2
        rms_db, peak_db = result
        assert rms_db == pytest.approx(peak_db, abs=0.01)


# ---------------------------------------------------------------------------
# AudioLevelMonitor -- history / averages
# ---------------------------------------------------------------------------

class TestAudioLevelMonitorHistory:
    """Test history tracking and average computation."""

    def test_app_avg_db_computes_average(self):
        monitor = AudioLevelMonitor()
        # Feed two different levels and check average
        audio_loud = _make_const_audio(16384)
        audio_quiet = _make_const_audio(1000)
        monitor.update_app_level(audio_loud)
        monitor.update_app_level(audio_quiet)

        loud_rms = compute_rms_db(audio_loud)
        quiet_rms = compute_rms_db(audio_quiet)
        expected_avg = (loud_rms + quiet_rms) / 2.0

        assert monitor.app_avg_db == pytest.approx(expected_avg, abs=0.01)

    def test_mic_avg_db_returns_min_db_when_empty(self):
        monitor = AudioLevelMonitor()
        assert monitor.mic_avg_db == MIN_DB

    def test_history_bounded_by_history_size(self):
        history_size = 5
        monitor = AudioLevelMonitor(history_size=history_size)
        audio = _make_const_audio(16384)

        # Feed more entries than history_size
        for _ in range(history_size + 10):
            monitor.update_app_level(audio)

        # The internal deque should have been capped; verify via avg
        # (all identical values, so avg == that value regardless of count)
        expected_rms = compute_rms_db(audio)
        assert monitor.app_avg_db == pytest.approx(expected_rms, abs=0.01)

        # Verify the deque length directly
        assert len(monitor._app_history) == history_size


# ---------------------------------------------------------------------------
# AudioLevelMonitor -- callback and reset
# ---------------------------------------------------------------------------

class TestAudioLevelMonitorCallbackAndReset:
    """Test the notify callback and reset functionality."""

    def test_notify_calls_callback_with_correct_args(self):
        callback = MagicMock()
        monitor = AudioLevelMonitor(on_levels=callback)

        app_audio = _make_const_audio(16384)
        mic_audio = _make_const_audio(8000)
        monitor.update_app_level(app_audio)
        monitor.update_mic_level(mic_audio)
        monitor.notify()

        callback.assert_called_once()
        args = callback.call_args[0]
        assert len(args) == 4
        app_rms, app_peak, mic_rms, mic_peak = args
        assert app_rms == pytest.approx(compute_rms_db(app_audio), abs=0.01)
        assert app_peak == pytest.approx(compute_peak_db(app_audio), abs=0.01)
        assert mic_rms == pytest.approx(compute_rms_db(mic_audio), abs=0.01)
        assert mic_peak == pytest.approx(compute_peak_db(mic_audio), abs=0.01)

    def test_reset_clears_everything(self):
        monitor = AudioLevelMonitor()
        audio = _make_const_audio(16384)
        monitor.update_app_level(audio)
        monitor.update_mic_level(audio)

        # Confirm non-silence before reset
        assert monitor.app_level[0] > MIN_DB
        assert monitor.mic_level[0] > MIN_DB

        monitor.reset()

        assert monitor.app_level == (MIN_DB, MIN_DB)
        assert monitor.mic_level == (MIN_DB, MIN_DB)
        assert monitor.app_avg_db == MIN_DB
        assert monitor.mic_avg_db == MIN_DB
