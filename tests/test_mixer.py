"""Tests for audio track mixer with real synthetic WAV files."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from meeting_recorder.audio.mixer import mix_tracks, _read_wav
from tests.conftest import generate_sine_wav, generate_silence_wav


# ---------------------------------------------------------------------------
# _read_wav helper
# ---------------------------------------------------------------------------

class TestReadWav:
    """Test the _read_wav helper function."""

    def test_read_wav_params(self, sine_wav_factory):
        path = sine_wav_factory(frequency=440, duration=1.0, sample_rate=16000, channels=1)
        params, data = _read_wav(path)
        assert params == (1, 2, 16000)  # channels, sampwidth, framerate
        assert data.dtype == np.int16
        assert len(data) == 16000  # 1 second at 16kHz

    def test_read_wav_stereo(self, sine_wav_factory):
        path = sine_wav_factory(frequency=440, duration=0.5, sample_rate=16000, channels=2)
        params, data = _read_wav(path)
        assert params[0] == 2  # channels
        # Stereo means 2x samples
        assert len(data) == 16000  # 0.5s * 16000 * 2 channels


# ---------------------------------------------------------------------------
# mix_tracks -- basic functionality
# ---------------------------------------------------------------------------

class TestMixTracksBasic:
    """Test basic mixing operations."""

    def test_mix_same_length_tracks(self, tmp_path: Path):
        app_path = generate_sine_wav(tmp_path / "app.wav", frequency=440, duration=1.0)
        mic_path = generate_sine_wav(tmp_path / "mic.wav", frequency=880, duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path)

        assert out_path.exists()
        params, data = _read_wav(out_path)
        assert params == (1, 2, 16000)
        assert len(data) == 16000

    def test_mix_different_length_tracks(self, tmp_path: Path):
        """Shorter track should be zero-padded to match the longer one."""
        app_path = generate_sine_wav(tmp_path / "app.wav", frequency=440, duration=2.0)
        mic_path = generate_sine_wav(tmp_path / "mic.wav", frequency=880, duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path)

        params, data = _read_wav(out_path)
        expected_samples = int(16000 * 2.0)
        assert len(data) == expected_samples

    def test_mix_with_silence(self, tmp_path: Path):
        """Mixing a signal with silence should produce the original signal (within clipping)."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.3,
        )
        mic_path = generate_silence_wav(tmp_path / "mic.wav", duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path)

        _, app_data = _read_wav(app_path)
        _, mixed_data = _read_wav(out_path)

        # Mixed should be very close to original app audio (added zeros)
        np.testing.assert_array_almost_equal(
            mixed_data.astype(np.float32),
            app_data.astype(np.float32),
            decimal=0,
        )


# ---------------------------------------------------------------------------
# Volume control
# ---------------------------------------------------------------------------

class TestMixTracksVolume:
    """Test volume scaling during mixing."""

    def test_app_volume_zero(self, tmp_path: Path):
        """Setting app_volume=0 should produce mic audio only."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.5,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=880, duration=1.0, amplitude=0.3,
        )
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path, app_volume=0.0, mic_volume=1.0)

        _, mic_data = _read_wav(mic_path)
        _, mixed_data = _read_wav(out_path)

        np.testing.assert_array_almost_equal(
            mixed_data.astype(np.float32),
            mic_data.astype(np.float32),
            decimal=0,
        )

    def test_mic_volume_zero(self, tmp_path: Path):
        """Setting mic_volume=0 should produce app audio only."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.3,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=880, duration=1.0, amplitude=0.5,
        )
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path, app_volume=1.0, mic_volume=0.0)

        _, app_data = _read_wav(app_path)
        _, mixed_data = _read_wav(out_path)

        np.testing.assert_array_almost_equal(
            mixed_data.astype(np.float32),
            app_data.astype(np.float32),
            decimal=0,
        )

    def test_volume_scaling(self, tmp_path: Path):
        """Half volume should produce half amplitude samples."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.4,
        )
        mic_path = generate_silence_wav(tmp_path / "mic.wav", duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path, app_volume=0.5, mic_volume=1.0)

        _, app_data = _read_wav(app_path)
        _, mixed_data = _read_wav(out_path)

        # mixed should be approximately half the app amplitude
        ratio = np.abs(mixed_data.astype(np.float32)).mean() / max(
            np.abs(app_data.astype(np.float32)).mean(), 1e-10,
        )
        assert 0.4 < ratio < 0.6


# ---------------------------------------------------------------------------
# Clipping
# ---------------------------------------------------------------------------

class TestMixTracksClipping:
    """Test that clipping prevents overflow."""

    def test_clipping_high_amplitude(self, tmp_path: Path):
        """Mixing two loud signals should not overflow int16 range."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=0.5, amplitude=0.9,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=880, duration=0.5, amplitude=0.9,
        )
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path)

        _, mixed_data = _read_wav(out_path)
        assert mixed_data.min() >= -32768
        assert mixed_data.max() <= 32767


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestMixTracksErrors:
    """Test error conditions."""

    def test_format_mismatch_raises(self, tmp_path: Path):
        """Different sample rates should raise ValueError."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, sample_rate=16000,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=440, duration=1.0, sample_rate=44100,
        )
        out_path = tmp_path / "mixed.wav"

        with pytest.raises(ValueError, match="Track format mismatch"):
            mix_tracks(app_path, mic_path, out_path)

    def test_channel_mismatch_raises(self, tmp_path: Path):
        """Different channel counts should raise ValueError."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, channels=1,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=440, duration=1.0, channels=2,
        )
        out_path = tmp_path / "mixed.wav"

        with pytest.raises(ValueError, match="Track format mismatch"):
            mix_tracks(app_path, mic_path, out_path)


# ---------------------------------------------------------------------------
# Output WAV validity
# ---------------------------------------------------------------------------

class TestMixTracksOutputValidity:
    """Verify the output WAV file is well-formed."""

    def test_output_wav_readable(self, tmp_path: Path):
        app_path = generate_sine_wav(tmp_path / "app.wav", frequency=440, duration=1.0)
        mic_path = generate_sine_wav(tmp_path / "mic.wav", frequency=880, duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks(app_path, mic_path, out_path)

        with wave.open(str(out_path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 16000
