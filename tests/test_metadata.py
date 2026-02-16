"""Tests for recording metadata management."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from meeting_recorder.storage.metadata import RecordingMetadata, METADATA_FILENAME


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

class TestMetadataCreate:
    """Test RecordingMetadata.create factory method."""

    def test_create_sets_fields(self):
        meta = RecordingMetadata.create(
            app_name="Zoom",
            app_pid=12345,
            sample_rate=16000,
            channels=1,
            language="en",
            transcription_backend="local",
        )
        assert meta.app_name == "Zoom"
        assert meta.app_pid == 12345
        assert meta.sample_rate == 16000
        assert meta.channels == 1
        assert meta.language == "en"
        assert meta.transcription_backend == "local"
        assert meta.status == "recording"

    def test_create_sets_start_time(self):
        before = datetime.now().isoformat()
        meta = RecordingMetadata.create(
            app_name="Teams", app_pid=1, sample_rate=16000,
            channels=1, language="en", transcription_backend="cloud",
        )
        after = datetime.now().isoformat()
        assert before <= meta.start_time <= after

    def test_create_defaults(self):
        meta = RecordingMetadata.create(
            app_name="Webex", app_pid=99, sample_rate=44100,
            channels=2, language="de", transcription_backend="local",
        )
        assert meta.end_time == ""
        assert meta.duration_seconds == 0.0
        assert meta.has_app_audio is False
        assert meta.has_mic_audio is False
        assert meta.has_mixed_audio is False
        assert meta.has_transcript is False
        assert meta.has_screen_recording is False
        assert meta.speaker_count == 0
        assert meta.segment_count == 0
        assert meta.error_message == ""


# ---------------------------------------------------------------------------
# Save / Load round-trip
# ---------------------------------------------------------------------------

class TestMetadataSaveLoad:
    """Test save and load operations."""

    def test_save_creates_file(self, recording_dir: Path):
        meta = RecordingMetadata(app_name="Zoom", app_pid=100)
        meta.save(recording_dir)
        assert (recording_dir / METADATA_FILENAME).exists()

    def test_save_load_roundtrip(self, recording_dir: Path):
        original = RecordingMetadata.create(
            app_name="Teams", app_pid=5678, sample_rate=16000,
            channels=1, language="ja", transcription_backend="local",
        )
        original.save(recording_dir)

        loaded = RecordingMetadata.load(recording_dir)
        assert loaded.app_name == "Teams"
        assert loaded.app_pid == 5678
        assert loaded.language == "ja"
        assert loaded.start_time == original.start_time

    def test_save_produces_valid_json(self, recording_dir: Path):
        meta = RecordingMetadata(app_name="Test", app_pid=1)
        meta.save(recording_dir)

        path = recording_dir / METADATA_FILENAME
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["app_name"] == "Test"
        assert isinstance(data["app_pid"], int)

    def test_load_nonexistent_raises(self, recording_dir: Path):
        with pytest.raises(FileNotFoundError):
            RecordingMetadata.load(recording_dir)

    def test_load_ignores_unknown_fields(self, recording_dir: Path):
        """Extra JSON fields should be silently ignored."""
        data = {
            "app_name": "Zoom",
            "app_pid": 42,
            "start_time": "",
            "end_time": "",
            "duration_seconds": 0,
            "sample_rate": 16000,
            "channels": 1,
            "language": "en",
            "transcription_backend": "local",
            "has_app_audio": False,
            "has_mic_audio": False,
            "has_mixed_audio": False,
            "has_transcript": False,
            "has_screen_recording": False,
            "speaker_count": 0,
            "segment_count": 0,
            "status": "completed",
            "error_message": "",
            "unknown_future_field": "should be ignored",
        }
        path = recording_dir / METADATA_FILENAME
        path.write_text(json.dumps(data), encoding="utf-8")

        loaded = RecordingMetadata.load(recording_dir)
        assert loaded.app_name == "Zoom"
        assert not hasattr(loaded, "unknown_future_field")


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

class TestMetadataFinalize:
    """Test finalize() which marks recording as completed."""

    def test_finalize_sets_completed(self, recording_dir: Path):
        meta = RecordingMetadata.create(
            app_name="Zoom", app_pid=1, sample_rate=16000,
            channels=1, language="en", transcription_backend="local",
        )
        meta.finalize(recording_dir, speaker_count=3, segment_count=42)

        assert meta.status == "completed"
        assert meta.speaker_count == 3
        assert meta.segment_count == 42
        assert meta.end_time != ""
        assert meta.duration_seconds >= 0.0

    def test_finalize_detects_files(self, recording_dir: Path):
        """finalize() should detect which audio/transcript files exist."""
        # Create some dummy files
        (recording_dir / "app_audio.wav").write_bytes(b"fake")
        (recording_dir / "mixed.wav").write_bytes(b"fake")
        (recording_dir / "transcript.json").write_text("{}", encoding="utf-8")

        meta = RecordingMetadata.create(
            app_name="Teams", app_pid=2, sample_rate=16000,
            channels=1, language="en", transcription_backend="local",
        )
        meta.finalize(recording_dir)

        assert meta.has_app_audio is True
        assert meta.has_mic_audio is False  # not created
        assert meta.has_mixed_audio is True
        assert meta.has_transcript is True
        assert meta.has_screen_recording is False

    def test_finalize_saves_to_disk(self, recording_dir: Path):
        meta = RecordingMetadata.create(
            app_name="Test", app_pid=1, sample_rate=16000,
            channels=1, language="en", transcription_backend="local",
        )
        meta.finalize(recording_dir, speaker_count=2, segment_count=10)

        # Load from disk
        loaded = RecordingMetadata.load(recording_dir)
        assert loaded.status == "completed"
        assert loaded.speaker_count == 2

    def test_finalize_calculates_duration(self, recording_dir: Path):
        meta = RecordingMetadata.create(
            app_name="Test", app_pid=1, sample_rate=16000,
            channels=1, language="en", transcription_backend="local",
        )
        # Small sleep so duration > 0
        time.sleep(0.05)
        meta.finalize(recording_dir)
        assert meta.duration_seconds > 0.0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestMetadataError:
    """Test set_error() marking recording as errored."""

    def test_set_error(self, recording_dir: Path):
        meta = RecordingMetadata.create(
            app_name="Zoom", app_pid=1, sample_rate=16000,
            channels=1, language="en", transcription_backend="local",
        )
        meta.set_error("Transcription failed", recording_dir)

        assert meta.status == "error"
        assert meta.error_message == "Transcription failed"
        assert meta.end_time != ""

    def test_set_error_saves_to_disk(self, recording_dir: Path):
        meta = RecordingMetadata.create(
            app_name="Test", app_pid=1, sample_rate=16000,
            channels=1, language="en", transcription_backend="local",
        )
        meta.set_error("GPU OOM", recording_dir)

        loaded = RecordingMetadata.load(recording_dir)
        assert loaded.status == "error"
        assert loaded.error_message == "GPU OOM"
