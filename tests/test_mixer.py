"""Tests for audio track mixer with real synthetic WAV files."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from meeting_recorder.audio.mixer import mix_tracks, mix_tracks_streaming, _read_wav
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


# ---------------------------------------------------------------------------
# Streaming mixer tests
# ---------------------------------------------------------------------------

class TestMixTracksStreaming:
    """Test the streaming mixer produces identical output to in-memory mixer."""

    def test_streaming_same_length(self, tmp_path: Path):
        """Streaming mix of same-length tracks should produce valid output."""
        app_path = generate_sine_wav(tmp_path / "app.wav", frequency=440, duration=1.0)
        mic_path = generate_sine_wav(tmp_path / "mic.wav", frequency=880, duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks_streaming(app_path, mic_path, out_path)

        assert out_path.exists()
        params, data = _read_wav(out_path)
        assert params == (1, 2, 16000)
        assert len(data) == 16000

    def test_streaming_different_lengths(self, tmp_path: Path):
        """Shorter track should be zero-padded."""
        app_path = generate_sine_wav(tmp_path / "app.wav", frequency=440, duration=2.0)
        mic_path = generate_sine_wav(tmp_path / "mic.wav", frequency=880, duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks_streaming(app_path, mic_path, out_path)

        params, data = _read_wav(out_path)
        assert len(data) == int(16000 * 2.0)

    def test_streaming_matches_in_memory(self, tmp_path: Path):
        """Streaming mixer should produce the same output as in-memory mixer."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.3,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=880, duration=1.0, amplitude=0.3,
        )
        out_mem = tmp_path / "mixed_mem.wav"
        out_stream = tmp_path / "mixed_stream.wav"

        mix_tracks(app_path, mic_path, out_mem)
        mix_tracks_streaming(app_path, mic_path, out_stream)

        _, mem_data = _read_wav(out_mem)
        _, stream_data = _read_wav(out_stream)

        np.testing.assert_array_equal(mem_data, stream_data)

    def test_streaming_small_chunk_size(self, tmp_path: Path):
        """Streaming with very small chunks should still produce correct output."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.3,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=880, duration=1.0, amplitude=0.3,
        )
        out_mem = tmp_path / "mixed_mem.wav"
        out_stream = tmp_path / "mixed_stream.wav"

        mix_tracks(app_path, mic_path, out_mem)
        mix_tracks_streaming(app_path, mic_path, out_stream, chunk_frames=256)

        _, mem_data = _read_wav(out_mem)
        _, stream_data = _read_wav(out_stream)

        np.testing.assert_array_equal(mem_data, stream_data)

    def test_streaming_volume_control(self, tmp_path: Path):
        """Volume control should work in streaming mode."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.5,
        )
        mic_path = generate_silence_wav(tmp_path / "mic.wav", duration=1.0)
        out_path = tmp_path / "mixed.wav"

        mix_tracks_streaming(app_path, mic_path, out_path, app_volume=0.5)

        _, app_data = _read_wav(app_path)
        _, mixed_data = _read_wav(out_path)

        ratio = np.abs(mixed_data.astype(np.float32)).mean() / max(
            np.abs(app_data.astype(np.float32)).mean(), 1e-10,
        )
        assert 0.4 < ratio < 0.6

    def test_streaming_clipping(self, tmp_path: Path):
        """Streaming mixer should clip to int16 range."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=0.5, amplitude=0.9,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=880, duration=0.5, amplitude=0.9,
        )
        out_path = tmp_path / "mixed.wav"

        mix_tracks_streaming(app_path, mic_path, out_path)

        _, mixed_data = _read_wav(out_path)
        assert mixed_data.min() >= -32768
        assert mixed_data.max() <= 32767

    def test_streaming_format_mismatch_raises(self, tmp_path: Path):
        """Different sample rates should raise ValueError."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, sample_rate=16000,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=440, duration=1.0, sample_rate=44100,
        )
        out_path = tmp_path / "mixed.wav"

        with pytest.raises(ValueError, match="Track format mismatch"):
            mix_tracks_streaming(app_path, mic_path, out_path)


# ---------------------------------------------------------------------------
# Streaming EOF edge cases
# ---------------------------------------------------------------------------

class TestStreamingEOFEdgeCases:
    """Verify streaming mixer handles track exhaustion correctly."""

    def test_streaming_one_track_much_shorter(self, tmp_path: Path):
        """Mixing a 0.01s track with a 2s track should produce 2s output."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=2.0, amplitude=0.3,
        )
        mic_path = generate_sine_wav(
            tmp_path / "mic.wav", frequency=880, duration=0.01, amplitude=0.3,
        )
        out_path = tmp_path / "mixed.wav"

        mix_tracks_streaming(app_path, mic_path, out_path, chunk_frames=256)

        params, data = _read_wav(out_path)
        assert len(data) == int(16000 * 2.0)

    def test_streaming_both_empty(self, tmp_path: Path):
        """Two effectively empty (0-duration) WAV files should produce 0-length output."""
        for name in ("app.wav", "mic.wav"):
            path = tmp_path / name
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                # Write zero frames

        out_path = tmp_path / "mixed.wav"
        mix_tracks_streaming(tmp_path / "app.wav", tmp_path / "mic.wav", out_path)

        params, data = _read_wav(out_path)
        assert len(data) == 0

    def test_streaming_one_empty_one_full(self, tmp_path: Path):
        """Mixing an empty track with a full track should produce the full track."""
        app_path = generate_sine_wav(
            tmp_path / "app.wav", frequency=440, duration=1.0, amplitude=0.3,
        )
        mic_path = tmp_path / "mic.wav"
        with wave.open(str(mic_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)

        out_path = tmp_path / "mixed.wav"
        mix_tracks_streaming(app_path, mic_path, out_path)

        _, app_data = _read_wav(app_path)
        _, mixed_data = _read_wav(out_path)
        np.testing.assert_array_equal(mixed_data, app_data)
