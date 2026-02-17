"""Tests for shared audio resampling utility."""

from __future__ import annotations

import numpy as np
import pytest

from meeting_recorder.audio.resampling import resample_to_16khz_mono


# ---------------------------------------------------------------------------
# ProcTap-like: 48kHz stereo float32 -> 16kHz mono int16
# ---------------------------------------------------------------------------

class TestProcTapPath:
    """48kHz stereo float32 input (typical ProcTap capture)."""

    def test_48khz_stereo_float32_to_16khz_mono(self):
        """Standard ProcTap path: 48kHz stereo float32 -> 16kHz mono int16."""
        duration = 0.1  # 100ms
        n_samples = int(48000 * duration)
        t = np.linspace(0, duration, n_samples, endpoint=False, dtype=np.float32)
        # Interleaved stereo: L and R identical sine
        mono = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
        stereo = np.column_stack([mono, mono]).flatten()

        result = resample_to_16khz_mono(stereo, source_rate=48000, source_channels=2)

        assert result.dtype == np.int16
        assert result.ndim == 1
        expected_samples = int(n_samples / 3)  # 48kHz -> 16kHz = 1/3
        assert abs(len(result) - expected_samples) <= 1


# ---------------------------------------------------------------------------
# Mic-like: 44.1kHz stereo int16 -> 16kHz mono int16
# ---------------------------------------------------------------------------

class TestMicPath:
    """44.1kHz stereo int16 input (typical mic capture like Brio 101)."""

    def test_44100hz_stereo_int16_to_16khz_mono(self):
        """Mic path: 44.1kHz stereo int16 -> 16kHz mono int16."""
        duration = 0.1
        n_samples = int(44100 * duration)
        t = np.linspace(0, duration, n_samples, endpoint=False)
        mono = (0.5 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
        stereo = np.column_stack([mono, mono]).flatten()

        result = resample_to_16khz_mono(stereo, source_rate=44100, source_channels=2)

        assert result.dtype == np.int16
        assert result.ndim == 1
        expected_samples = int(n_samples * 16000 / 44100)
        # Allow small tolerance for resampling edge effects
        assert abs(len(result) - expected_samples) <= 2


# ---------------------------------------------------------------------------
# Fast path: 16kHz mono int16 passthrough
# ---------------------------------------------------------------------------

class TestFastPath:
    """16kHz mono int16 should be returned as-is (fast path)."""

    def test_16khz_mono_int16_passthrough(self):
        """Fast path should return the exact same array object."""
        audio = np.array([100, -200, 300, -400, 500], dtype=np.int16)

        result = resample_to_16khz_mono(audio, source_rate=16000, source_channels=1)

        assert result is audio  # Same object, not a copy
        assert result.dtype == np.int16


# ---------------------------------------------------------------------------
# Format conversion only: 16kHz mono float32
# ---------------------------------------------------------------------------

class TestFormatConversion:
    """16kHz mono float32 -- no resample needed, but format conversion is."""

    def test_16khz_mono_float32_converts_to_int16(self):
        """Float32 at target rate should still convert to int16."""
        audio = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)

        result = resample_to_16khz_mono(audio, source_rate=16000, source_channels=1)

        assert result.dtype == np.int16
        # Check known conversions: 0.5 * 32767 = 16383.5 -> 16383
        assert result[0] == 0
        assert result[1] == 16383
        assert result[2] == -16383
        assert result[3] == 32767
        assert result[4] == -32767


# ---------------------------------------------------------------------------
# Clipping
# ---------------------------------------------------------------------------

class TestClipping:
    """Input values outside [-1, 1] should be clipped to int16 range."""

    def test_clipping_extreme_values(self):
        """Values beyond [-1, 1] should be clipped to int16 bounds."""
        audio = np.array([2.0, -2.0, 1.5, -1.5], dtype=np.float32)

        result = resample_to_16khz_mono(audio, source_rate=16000, source_channels=1)

        assert result.dtype == np.int16
        assert result[0] == 32767    # clipped max
        assert result[1] == -32768   # clipped min
        assert result[2] == 32767    # clipped max
        assert result[3] == -32768   # clipped min


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: empty arrays, single samples."""

    def test_empty_array(self):
        """Empty float32 input should produce empty int16 output."""
        audio = np.array([], dtype=np.float32)

        result = resample_to_16khz_mono(audio, source_rate=48000, source_channels=1)

        assert result.dtype == np.int16
        assert len(result) == 0

    def test_single_sample(self):
        """A single float32 sample should convert without error."""
        audio = np.array([0.25], dtype=np.float32)

        result = resample_to_16khz_mono(audio, source_rate=16000, source_channels=1)

        assert result.dtype == np.int16
        assert len(result) == 1
        assert result[0] == int(0.25 * 32767)


# ---------------------------------------------------------------------------
# Stereo cancellation
# ---------------------------------------------------------------------------

class TestStereoCancellation:
    """Left = -Right should produce near-zero mono after averaging."""

    def test_stereo_cancellation(self):
        """Opposite-phase stereo should cancel to near silence."""
        n_samples = 1600  # 100ms at 16kHz
        left = 0.5 * np.ones(n_samples, dtype=np.float32)
        right = -0.5 * np.ones(n_samples, dtype=np.float32)
        stereo = np.column_stack([left, right]).flatten()

        result = resample_to_16khz_mono(
            stereo, source_rate=16000, source_channels=2,
        )

        assert result.dtype == np.int16
        # Average of 0.5 and -0.5 is 0.0, so all samples should be zero
        np.testing.assert_array_equal(result, np.zeros(n_samples, dtype=np.int16))


# ---------------------------------------------------------------------------
# Output invariants
# ---------------------------------------------------------------------------

class TestOutputInvariants:
    """Output should always be int16 and 1D regardless of input."""

    def test_output_always_int16(self):
        """Result dtype must be int16 for all code paths."""
        for dtype in [np.float32, np.int16]:
            audio = np.zeros(4800, dtype=dtype)
            result = resample_to_16khz_mono(
                audio, source_rate=48000, source_channels=1,
            )
            assert result.dtype == np.int16, f"Failed for input dtype {dtype}"

    def test_output_always_1d(self):
        """Result must be 1D for both mono and stereo inputs."""
        # Stereo float32
        stereo = np.zeros(9600, dtype=np.float32)  # 4800 frames * 2 channels
        result = resample_to_16khz_mono(
            stereo, source_rate=48000, source_channels=2,
        )
        assert result.ndim == 1

        # Mono int16
        mono = np.zeros(4800, dtype=np.int16)
        result = resample_to_16khz_mono(
            mono, source_rate=48000, source_channels=1,
        )
        assert result.ndim == 1


# ---------------------------------------------------------------------------
# Sample count after resampling
# ---------------------------------------------------------------------------

class TestSampleCount:
    """Verify the output length matches expected resampling ratio."""

    def test_48khz_to_16khz_sample_count(self):
        """48kHz -> 16kHz should produce exactly 1/3 the samples."""
        n_input = 4800  # 100ms at 48kHz
        audio = np.zeros(n_input, dtype=np.float32)

        result = resample_to_16khz_mono(audio, source_rate=48000, source_channels=1)

        assert len(result) == n_input // 3  # exactly 1600


# ---------------------------------------------------------------------------
# 48kHz mono float32 (no stereo conversion)
# ---------------------------------------------------------------------------

class TestMonoResampleOnly:
    """48kHz mono float32 -- resample only, no channel conversion."""

    def test_48khz_mono_float32(self):
        """Mono input should skip stereo-to-mono and only resample."""
        duration = 0.1
        n_samples = int(48000 * duration)
        t = np.linspace(0, duration, n_samples, endpoint=False, dtype=np.float32)
        audio = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float32)

        result = resample_to_16khz_mono(audio, source_rate=48000, source_channels=1)

        assert result.dtype == np.int16
        assert result.ndim == 1
        expected_samples = n_samples // 3
        assert len(result) == expected_samples
        # Verify signal has non-zero content (not all silence)
        assert np.abs(result).max() > 0
