"""Robustness tests: thread safety, error handling, edge cases.

Covers:
- Ring buffer concurrent put/get_all correctness
- Capture manager callback exception safety
- Pipeline error handling for missing API keys
- Config backwards-compatibility with missing fields
- Metadata atomic save
- Process finder None process name handling
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import numpy as np
import pytest

from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.config import Config


# ---------------------------------------------------------------------------
# Ring buffer thread safety
# ---------------------------------------------------------------------------

class TestRingBufferConcurrency:
    """Verify no data loss under concurrent put/get_all."""

    def test_concurrent_put_and_get_all_no_data_loss(self):
        """All items put into the buffer must be retrievable via get_all.

        Thread A: continuously puts 1000 items
        Thread B: continuously drains via get_all
        After completion, total items retrieved == total items put.
        """
        buf = RingBuffer(max_chunks=5000)  # Large enough that nothing is dropped
        total_puts = 1000
        retrieved = []
        done = threading.Event()

        def producer():
            for i in range(total_puts):
                buf.put(i.to_bytes(4, "little"))
            done.set()

        def consumer():
            while not done.is_set() or not buf.is_empty:
                chunks = buf.get_all()
                retrieved.extend(chunks)
                if not chunks:
                    time.sleep(0.001)

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert len(retrieved) == total_puts

    def test_concurrent_multiple_producers(self):
        """Multiple producers writing simultaneously should not corrupt data."""
        buf = RingBuffer(max_chunks=10000)
        items_per_producer = 500
        num_producers = 4
        barrier = threading.Barrier(num_producers)

        def producer(producer_id):
            barrier.wait()  # Synchronize start
            for i in range(items_per_producer):
                tag = f"{producer_id}:{i}"
                buf.put(tag.encode())

        threads = [
            threading.Thread(target=producer, args=(pid,))
            for pid in range(num_producers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        chunks = buf.get_all()
        assert len(chunks) == num_producers * items_per_producer

        # Verify each producer's items are present
        tags = {c.decode() for c in chunks}
        for pid in range(num_producers):
            for i in range(items_per_producer):
                assert f"{pid}:{i}" in tags

    def test_buffer_overflow_drops_oldest(self):
        """When buffer exceeds max_chunks, oldest items should be dropped."""
        buf = RingBuffer(max_chunks=5)
        for i in range(10):
            buf.put(i.to_bytes(4, "little"))

        chunks = buf.get_all()
        # Only the last 5 should remain
        assert len(chunks) == 5
        values = [int.from_bytes(c, "little") for c in chunks]
        assert values == [5, 6, 7, 8, 9]

    def test_get_all_returns_fifo_order(self):
        """Items should be returned in FIFO order."""
        buf = RingBuffer()
        for i in range(5):
            buf.put(i.to_bytes(4, "little"))

        chunks = buf.get_all()
        values = [int.from_bytes(c, "little") for c in chunks]
        assert values == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Capture manager callback exception safety
# ---------------------------------------------------------------------------

class TestCaptureManagerCallbackSafety:
    """Callbacks that raise should not crash the capture manager."""

    def test_on_capture_mode_changed_exception_does_not_crash_switch(self):
        """If on_capture_mode_changed raises, switch_to_desktop_audio should still complete.

        The callback exception is caught and logged — it should NOT propagate.
        """
        from meeting_recorder.audio.capture_manager import CaptureManager

        def bad_callback(is_desktop):
            raise RuntimeError("callback error")

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.DesktopAudioCapture") as MockDAC,
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            MockDAC.return_value = mock.Mock()
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
                on_capture_mode_changed=bad_callback,
            )

            # Should NOT raise — the exception is caught internally
            mgr.switch_to_desktop_audio()

            # The switch should have completed (flag set, capture started)
            assert mgr._is_desktop_audio is True

    def test_on_stopped_none_does_not_crash_monitor(self):
        """Monitor with on_stopped=None should not crash when process exits."""
        from meeting_recorder.audio.capture_manager import CaptureManager

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
        ):
            mgr = CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
                on_stopped=None,  # No callback
            )

        with mock.patch(
            "meeting_recorder.audio.capture_manager.is_process_running", return_value=False
        ):
            # Should not crash
            mgr._monitor_process()


class TestStartMutedDefault:
    """The recorder starts muted by default (users usually join muted)."""

    def _make(self, start_muted_default, detected):
        from meeting_recorder.audio.capture_manager import CaptureManager

        with (
            mock.patch("meeting_recorder.audio.capture_manager.AppAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.MicAudioCapture"),
            mock.patch("meeting_recorder.audio.capture_manager.VoiceActivityDetector"),
            mock.patch("meeting_recorder.audio.capture_manager.AudioLevelMonitor"),
            mock.patch(
                "meeting_recorder.audio.capture_manager.get_all_pids_for_process",
                return_value={100},
            ),
            mock.patch(
                "meeting_recorder.audio.capture_manager.detect_initial_mute_state",
                return_value=detected,
            ),
            mock.patch(
                "meeting_recorder.audio.capture_manager.MuteSync"
            ) as MockMuteSync,
        ):
            CaptureManager(
                pid=100,
                output_dir=Path("/tmp/test"),
                screen_recording_enabled=False,
                process_name="ms-teams.exe",
                app_key="teams",
                start_muted_default=start_muted_default,
            )
        return MockMuteSync

    def test_forces_muted_even_when_detected_unmuted(self):
        """start_muted_default=True overrides a detected 'unmuted' at join."""
        MockMuteSync = self._make(start_muted_default=True, detected=False)
        assert MockMuteSync.call_args.kwargs["start_muted"] is True

    def test_uses_detection_when_default_off(self):
        """With the default off, a detected 'unmuted' is honored."""
        MockMuteSync = self._make(start_muted_default=False, detected=False)
        assert MockMuteSync.call_args.kwargs["start_muted"] is False

    def test_muted_when_detection_inconclusive(self):
        """No detection -> still muted (the safe default), default off."""
        MockMuteSync = self._make(start_muted_default=False, detected=None)
        assert MockMuteSync.call_args.kwargs["start_muted"] is True


# ---------------------------------------------------------------------------
# Pipeline error handling
# ---------------------------------------------------------------------------

class TestPipelineErrorHandling:
    """Test pipeline behavior with missing credentials and bad configs."""

    def test_cloud_backend_missing_api_key_raises_clear_error(self):
        """Cloud backend without API key should raise ValueError with clear message."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        config = Config()
        config.transcription.backend = "cloud"
        config.transcription.openai_api_key = ""

        pipeline = TranscriptionPipeline(config)

        with pytest.raises(ValueError, match="OpenAI API key"):
            pipeline._get_transcriber()

    def test_gemini_backend_missing_api_key_raises_clear_error(self):
        """Gemini backend without API key should raise ValueError with clear message."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        config = Config()
        config.transcription.backend = "gemini"
        config.transcription.gemini_api_key = ""

        pipeline = TranscriptionPipeline(config)

        with pytest.raises(ValueError, match="Gemini API key"):
            pipeline._get_transcriber()

    def test_diarization_disabled_returns_none(self):
        """Diarizer should return None when disabled."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        config = Config()
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)
        assert pipeline._get_diarizer() is None

    def test_diarization_missing_token_logs_warning(self, caplog):
        """Diarizer should return None and warn when token is missing."""
        import logging
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        config = Config()
        config.diarization.enabled = True
        config.diarization.huggingface_token = ""

        pipeline = TranscriptionPipeline(config)
        with caplog.at_level(logging.WARNING, logger="meeting_recorder.transcription.pipeline"):
            result = pipeline._get_diarizer()

        assert result is None
        assert any("HuggingFace token" in r.message for r in caplog.records)

    def test_speaker_resolution_failure_is_nonfatal(self, tmp_path):
        """If speaker resolution raises, pipeline should log and continue."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline
        from meeting_recorder.transcription.local_whisper import TranscriptSegment

        config = Config()
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)
        segments = [TranscriptSegment(start=0.0, end=1.0, text="hello", speaker="S1")]

        # Force the lazy import inside _resolve_speakers to return a crashing function.
        # The import is: from meeting_recorder.transcription.speaker_resolver import ...
        # We patch at the source module so the lazy import picks it up.
        with mock.patch(
            "meeting_recorder.transcription.speaker_resolver.resolve_speakers_with_voice_profiles",
            side_effect=RuntimeError("voice resolver crash"),
        ):
            with mock.patch(
                "meeting_recorder.transcription.speaker_resolver.resolve_speakers",
                side_effect=RuntimeError("resolver crash"),
            ):
                # Should not raise — _resolve_speakers has try/except
                pipeline._resolve_speakers(
                    segments,
                    attendees=["Alice"],
                    organizer="Bob",
                    user_name="User",
                    audio_path=tmp_path / "audio.wav",
                )

        # Segments should be unchanged
        assert segments[0].speaker == "S1"


# ---------------------------------------------------------------------------
# Config backwards-compatibility
# ---------------------------------------------------------------------------

class TestConfigBackwardsCompatibility:
    """Config should handle missing sections from old config files."""

    def test_missing_section_uses_defaults(self):
        """A config dict missing a section should use defaults for that section."""
        data = {
            "recording": {"output_dir": "~/Custom", "language": "fr"},
            # audio, vad, transcription, etc. all missing
        }
        config = Config._from_dict(data)

        assert config.recording.output_dir == "~/Custom"
        assert config.recording.language == "fr"
        # Missing sections should have defaults
        assert config.audio.sample_rate == 16000
        assert config.vad.threshold == 0.5
        assert config.transcription.backend == "local"
        assert config.diarization.enabled is True
        assert config.dashboard.enabled is True

    def test_unknown_keys_ignored(self):
        """Unknown keys in the config file should be silently ignored."""
        data = {
            "recording": {"output_dir": "~/Test", "nonexistent_key": "value"},
            "totally_new_section": {"foo": "bar"},
        }
        config = Config._from_dict(data)
        assert config.recording.output_dir == "~/Test"

    def test_partial_section_fills_defaults(self):
        """A section with only some keys should fill in defaults for the rest."""
        data = {
            "transcription": {"backend": "gemini"},
            # model_size, device, compute_type all missing
        }
        config = Config._from_dict(data)
        assert config.transcription.backend == "gemini"
        assert config.transcription.model_size == "large-v3"
        assert config.transcription.device == "cuda"


# ---------------------------------------------------------------------------
# Metadata atomic save
# ---------------------------------------------------------------------------

class TestMetadataAtomicSave:
    """Verify metadata.save() writes atomically."""

    def test_save_creates_valid_json(self, tmp_path):
        """Saved metadata should be valid JSON."""
        from meeting_recorder.storage.metadata import RecordingMetadata

        meta = RecordingMetadata.create(
            app_name="Zoom",
            app_pid=1234,
            sample_rate=16000,
            channels=1,
            language="en",
            transcription_backend="local",
        )
        meta.meeting_subject = "Test Meeting"
        meta.save(tmp_path)

        path = tmp_path / "metadata.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["app_name"] == "Zoom"
        assert data["meeting_subject"] == "Test Meeting"

    def test_save_does_not_leave_tmp_file(self, tmp_path):
        """After save(), no .tmp file should remain."""
        from meeting_recorder.storage.metadata import RecordingMetadata

        meta = RecordingMetadata.create(
            app_name="Teams", app_pid=5678,
            sample_rate=16000, channels=1,
            language="en", transcription_backend="local",
        )
        meta.save(tmp_path)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_save_overwrites_previous(self, tmp_path):
        """Saving twice should update the file, not duplicate it."""
        from meeting_recorder.storage.metadata import RecordingMetadata

        meta = RecordingMetadata.create(
            app_name="Zoom", app_pid=1234,
            sample_rate=16000, channels=1,
            language="en", transcription_backend="local",
        )
        meta.save(tmp_path)
        meta.status = "completed"
        meta.save(tmp_path)

        data = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert data["status"] == "completed"

    def test_load_roundtrip(self, tmp_path):
        """Save then load should produce equivalent metadata."""
        from meeting_recorder.storage.metadata import RecordingMetadata

        meta = RecordingMetadata.create(
            app_name="Webex", app_pid=999,
            sample_rate=16000, channels=1,
            language="ja", transcription_backend="gemini",
        )
        meta.meeting_attendees = ["Alice", "Bob"]
        meta.speaker_map = {"S1": "Alice", "S2": "Bob"}
        meta.save(tmp_path)

        loaded = RecordingMetadata.load(tmp_path)
        assert loaded.app_name == "Webex"
        assert loaded.language == "ja"
        assert loaded.meeting_attendees == ["Alice", "Bob"]
        assert loaded.speaker_map == {"S1": "Alice", "S2": "Bob"}


# ---------------------------------------------------------------------------
# MuteSync initialization
# ---------------------------------------------------------------------------

class TestMuteSyncInitialization:
    """Verify MuteSync has proper attribute initialization."""

    def test_manual_hotkey_initialized(self):
        """_manual_hotkey should be initialized to empty string in __init__."""
        from meeting_recorder.audio.mute_sync import MuteSync

        ms = MuteSync(app_key="zoom", target_pids={100})
        assert hasattr(ms, "_manual_hotkey")
        assert ms._manual_hotkey == ""

    def test_stop_without_start_is_safe(self):
        """Calling stop() without start() should not crash."""
        from meeting_recorder.audio.mute_sync import MuteSync

        ms = MuteSync(app_key="zoom", target_pids={100})
        ms.stop()  # Should not raise


# ---------------------------------------------------------------------------
# Resampling edge cases
# ---------------------------------------------------------------------------

class TestResamplingEdgeCases:
    """Additional resampling edge cases from audit."""

    def test_upsample_8khz_to_16khz(self):
        """8kHz -> 16kHz (2x upsample) should double the sample count."""
        from meeting_recorder.audio.resampling import resample_to_16khz_mono

        n_input = 800  # 100ms at 8kHz
        audio = np.random.uniform(-0.5, 0.5, n_input).astype(np.float32)
        result = resample_to_16khz_mono(audio, source_rate=8000, source_channels=1)

        assert result.dtype == np.int16
        assert len(result) == n_input * 2  # 1600

    def test_downsample_96khz_to_16khz(self):
        """96kHz -> 16kHz (6x downsample) should reduce to 1/6 the samples."""
        from meeting_recorder.audio.resampling import resample_to_16khz_mono

        n_input = 9600  # 100ms at 96kHz
        audio = np.random.uniform(-0.5, 0.5, n_input).astype(np.float32)
        result = resample_to_16khz_mono(audio, source_rate=96000, source_channels=1)

        assert result.dtype == np.int16
        assert len(result) == n_input // 6  # 1600

    def test_noise_gate_resets_with_fresh_audio(self):
        """NoiseGate should reopen when loud audio follows quiet audio."""
        from meeting_recorder.audio.resampling import NoiseGate

        gate = NoiseGate(threshold_db=-50.0, smoothing=0.1)

        # Close the gate with silence
        silence = np.zeros(480, dtype=np.int16)
        for _ in range(50):
            gate.process(silence)
        assert gate._gain < 0.1  # Gate should be mostly closed

        # Now feed loud audio to reopen
        loud = (np.sin(np.linspace(0, 10, 480)) * 10000).astype(np.int16)
        for _ in range(50):
            gate.process(loud)
        assert gate._gain > 0.9  # Gate should be open again
