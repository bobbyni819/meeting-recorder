"""Recording metadata management."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

METADATA_FILENAME = "metadata.json"


@dataclass
class RecordingMetadata:
    """Metadata for a single recording session."""

    app_name: str = ""
    app_pid: int = 0
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    sample_rate: int = 16000
    channels: int = 1
    language: str = "en"
    transcription_backend: str = "local"
    has_app_audio: bool = False
    has_mic_audio: bool = False
    has_mixed_audio: bool = False
    has_transcript: bool = False
    has_screen_recording: bool = False
    speaker_count: int = 0
    segment_count: int = 0
    status: str = "recording"  # recording, processing, completed, error
    error_message: str = ""
    # Calendar integration
    meeting_subject: str = ""
    meeting_organizer: str = ""
    meeting_attendees: list[str] = field(default_factory=list)
    meeting_location: str = ""
    # Google Drive
    google_drive_folder_id: str = ""
    # Speaker resolution
    speaker_map: dict[str, str] = field(default_factory=dict)
    speaker_map_confidence: str = ""
    speaker_map_method: str = ""
    # AI Summary
    has_summary: bool = False
    summary_provider: str = ""
    summary_model: str = ""

    def save(self, recording_dir: Path) -> None:
        """Save metadata to a JSON file in the recording directory.

        Uses atomic write (write to temp file then rename) to prevent
        corruption if the process crashes during the write.
        """
        path = recording_dir / METADATA_FILENAME
        tmp_path = path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        tmp_path.replace(path)  # atomic on NTFS
        logger.debug("Metadata saved: %s", path)

    @classmethod
    def load(cls, recording_dir: Path) -> RecordingMetadata:
        """Load metadata from a recording directory."""
        path = recording_dir / METADATA_FILENAME
        if not path.exists():
            raise FileNotFoundError(f"Metadata not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def create(
        cls,
        app_name: str,
        app_pid: int,
        sample_rate: int,
        channels: int,
        language: str,
        transcription_backend: str,
    ) -> RecordingMetadata:
        """Create metadata for a new recording session."""
        return cls(
            app_name=app_name,
            app_pid=app_pid,
            start_time=datetime.now().isoformat(),
            sample_rate=sample_rate,
            channels=channels,
            language=language,
            transcription_backend=transcription_backend,
            status="recording",
        )

    def finalize(
        self,
        recording_dir: Path,
        speaker_count: int = 0,
        segment_count: int = 0,
        elapsed_seconds: float = 0.0,
    ) -> None:
        """Mark recording as completed and update final metadata.

        Args:
            elapsed_seconds: Pause-adjusted recording duration. If > 0, used
                instead of wall-clock difference (which includes paused time).
        """
        self.end_time = datetime.now().isoformat()
        if elapsed_seconds > 0:
            self.duration_seconds = elapsed_seconds
        elif self.start_time:
            start = datetime.fromisoformat(self.start_time)
            end = datetime.fromisoformat(self.end_time)
            self.duration_seconds = (end - start).total_seconds()
        self.has_app_audio = (recording_dir / "app_audio.wav").exists()
        self.has_mic_audio = (recording_dir / "mic_audio.wav").exists()
        self.has_mixed_audio = (recording_dir / "mixed.wav").exists()
        self.has_transcript = (recording_dir / "transcript.json").exists()
        self.has_screen_recording = (recording_dir / "screen.mp4").exists()
        self.speaker_count = speaker_count
        self.segment_count = segment_count
        self.status = "completed"
        self.save(recording_dir)

    def set_error(self, error: str, recording_dir: Path) -> None:
        """Mark recording as errored."""
        self.status = "error"
        self.error_message = error
        self.end_time = datetime.now().isoformat()
        self.save(recording_dir)
