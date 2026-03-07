"""Tests for DesktopAudioCapture (system-wide WASAPI loopback)."""

from __future__ import annotations

import threading
from unittest import mock

import numpy as np
import pytest

from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.audio.desktop_audio import DesktopAudioCapture


class TestDesktopAudioCaptureLifecycle:
    def test_start_creates_thread(self):
        """start() should spawn a daemon capture thread."""
        buf = RingBuffer()
        cap = DesktopAudioCapture(ring_buffer=buf)

        # Mock the capture loop so it exits immediately
        cap._capture_loop = mock.Mock()
        cap.start()

        assert cap._thread is not None
        cap._thread.join(timeout=2.0)

    def test_stop_joins_thread(self):
        """stop() should set the stop event and join the thread."""
        buf = RingBuffer()
        cap = DesktopAudioCapture(ring_buffer=buf)

        # Use a simple loop that respects the stop event
        def fake_loop():
            cap._stop_event.wait()

        cap._capture_loop = fake_loop
        cap.start()
        assert cap.is_running

        cap.stop()
        assert not cap.is_running
        assert cap._thread is None

    def test_is_running_reflects_thread_state(self):
        """is_running should be False before start and after stop."""
        buf = RingBuffer()
        cap = DesktopAudioCapture(ring_buffer=buf)
        assert not cap.is_running

    def test_is_process_specific_always_false(self):
        """Desktop capture is always system-wide, never process-specific."""
        buf = RingBuffer()
        cap = DesktopAudioCapture(ring_buffer=buf)
        assert cap.is_process_specific is False

    def test_double_start_is_safe(self):
        """Starting twice should not create a second thread."""
        buf = RingBuffer()
        cap = DesktopAudioCapture(ring_buffer=buf)

        def fake_loop():
            cap._stop_event.wait()

        cap._capture_loop = fake_loop
        cap.start()
        first_thread = cap._thread
        cap.start()  # second start
        assert cap._thread is first_thread

        cap.stop()


class TestFindLoopbackDevice:
    """Tests for _find_loopback_device fallback when default fails."""

    def test_uses_default_when_available(self):
        """Should use default loopback device when it works."""
        mock_pa = mock.Mock()
        expected = {"name": "Speakers [Loopback]", "index": 1}
        mock_pa.get_default_wasapi_loopback.return_value = expected

        result = DesktopAudioCapture._find_loopback_device(mock_pa)
        assert result == expected

    def test_fallback_when_default_fails(self):
        """Should enumerate devices when default loopback raises LookupError."""
        mock_pa = mock.Mock()
        mock_pa.get_default_wasapi_loopback.side_effect = LookupError("No analogue")
        mock_pa.get_device_count.return_value = 3
        mock_pa.get_device_info_by_index.side_effect = [
            {"name": "Microphone", "maxInputChannels": 1},  # not loopback
            {"name": "NVIDIA HDMI [Loopback]", "maxInputChannels": 2},
            {"name": "Realtek Speakers [Loopback]", "maxInputChannels": 2},
        ]

        result = DesktopAudioCapture._find_loopback_device(mock_pa)
        assert result["name"] == "Realtek Speakers [Loopback]"

    def test_fallback_prefers_non_hdmi(self):
        """Should prefer non-NVIDIA/non-HDMI loopback devices."""
        mock_pa = mock.Mock()
        mock_pa.get_default_wasapi_loopback.side_effect = LookupError("No analogue")
        mock_pa.get_device_count.return_value = 2
        mock_pa.get_device_info_by_index.side_effect = [
            {"name": "Acer HDMI [Loopback]", "maxInputChannels": 2},
            {"name": "USB Audio [Loopback]", "maxInputChannels": 2},
        ]

        result = DesktopAudioCapture._find_loopback_device(mock_pa)
        assert result["name"] == "USB Audio [Loopback]"

    def test_uses_hdmi_if_only_option(self):
        """Should fall back to HDMI loopback if it's the only one."""
        mock_pa = mock.Mock()
        mock_pa.get_default_wasapi_loopback.side_effect = LookupError("No analogue")
        mock_pa.get_device_count.return_value = 1
        mock_pa.get_device_info_by_index.side_effect = [
            {"name": "NVIDIA HDMI Output [Loopback]", "maxInputChannels": 2},
        ]

        result = DesktopAudioCapture._find_loopback_device(mock_pa)
        assert "NVIDIA" in result["name"]

    def test_raises_when_no_loopback_devices(self):
        """Should raise LookupError when no loopback devices exist at all."""
        mock_pa = mock.Mock()
        mock_pa.get_default_wasapi_loopback.side_effect = LookupError("No analogue")
        mock_pa.get_device_count.return_value = 2
        mock_pa.get_device_info_by_index.side_effect = [
            {"name": "Microphone", "maxInputChannels": 1},
            {"name": "Speakers", "maxInputChannels": 0},
        ]

        with pytest.raises(LookupError, match="No WASAPI loopback"):
            DesktopAudioCapture._find_loopback_device(mock_pa)


class TestDesktopAudioCaptureLoop:
    def test_capture_loop_writes_resampled_audio_to_buffer(self):
        """Capture loop should resample audio and write to the ring buffer."""
        buf = RingBuffer()
        cap = DesktopAudioCapture(
            ring_buffer=buf,
            sample_rate=16000,
            channels=1,
            chunk_duration_ms=30,
        )

        # Simulate: 48kHz stereo float32 loopback device
        native_rate = 48000
        native_channels = 2
        chunk_samples = int(native_rate * 30 / 1000)  # 1440 samples
        # Generate fake audio: 1440 * 2 channels = 2880 float32 values
        fake_audio = np.random.uniform(-0.1, 0.1, chunk_samples * native_channels).astype(np.float32)
        fake_bytes = fake_audio.tobytes()

        mock_device = {
            "name": "Test Speakers (loopback)",
            "defaultSampleRate": native_rate,
            "maxInputChannels": native_channels,
            "index": 0,
        }

        call_count = 0

        def mock_read(n, exception_on_overflow=False):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                cap._stop_event.set()
            return fake_bytes

        mock_stream = mock.Mock()
        mock_stream.read = mock_read

        mock_pyaudio_instance = mock.Mock()
        mock_pyaudio_instance.get_default_wasapi_loopback.return_value = mock_device
        mock_pyaudio_instance.open.return_value = mock_stream
        mock_pyaudio_instance.paFloat32 = 1  # dummy constant

        mock_pyaudio_module = mock.Mock()
        mock_pyaudio_module.PyAudio.return_value = mock_pyaudio_instance
        mock_pyaudio_module.paFloat32 = 1

        with mock.patch.dict("sys.modules", {"pyaudiowpatch": mock_pyaudio_module}):
            cap._capture_loop()

        # Verify audio was written to the ring buffer
        chunks = buf.get_all()
        assert len(chunks) >= 1, "Expected at least one chunk in ring buffer"
        # Each chunk should be int16 bytes
        for chunk in chunks:
            audio = np.frombuffer(chunk, dtype=np.int16)
            assert len(audio) > 0

    def test_noise_gate_is_applied(self):
        """NoiseGate.process should be called on each chunk."""
        buf = RingBuffer()
        cap = DesktopAudioCapture(ring_buffer=buf)

        with mock.patch.object(cap._noise_gate, "process", wraps=cap._noise_gate.process) as mock_gate:
            # Simulate: feed a single chunk through the pipeline
            native_rate = 48000
            native_channels = 2
            chunk_samples = int(native_rate * 30 / 1000)
            fake_audio = np.zeros(chunk_samples * native_channels, dtype=np.float32)
            fake_bytes = fake_audio.tobytes()

            mock_device = {
                "name": "Test Speakers",
                "defaultSampleRate": native_rate,
                "maxInputChannels": native_channels,
                "index": 0,
            }

            call_count = 0
            def mock_read(n, exception_on_overflow=False):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    cap._stop_event.set()
                return fake_bytes

            mock_stream = mock.Mock()
            mock_stream.read = mock_read

            mock_pa = mock.Mock()
            mock_pa.get_default_wasapi_loopback.return_value = mock_device
            mock_pa.open.return_value = mock_stream
            mock_pa.paFloat32 = 1

            mock_mod = mock.Mock()
            mock_mod.PyAudio.return_value = mock_pa
            mock_mod.paFloat32 = 1

            with mock.patch.dict("sys.modules", {"pyaudiowpatch": mock_mod}):
                cap._capture_loop()

            assert mock_gate.call_count >= 1

    def test_graceful_when_pyaudiowpatch_missing(self, caplog):
        """If PyAudioWPatch is not installed, log an error and don't crash."""
        import logging

        buf = RingBuffer()
        cap = DesktopAudioCapture(ring_buffer=buf)

        # Remove pyaudiowpatch from sys.modules so the import fails
        with mock.patch.dict("sys.modules", {"pyaudiowpatch": None}):
            with caplog.at_level(logging.ERROR, logger="meeting_recorder.audio.desktop_audio"):
                cap._capture_loop()

        assert any("PyAudioWPatch" in r.message for r in caplog.records)
        assert buf.is_empty  # No audio should have been written
