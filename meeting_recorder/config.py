"""Configuration management for Meeting Recorder."""

from __future__ import annotations

import sys
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w


CONFIG_DIR = Path.home() / ".meeting_recorder"
CONFIG_FILE = CONFIG_DIR / "config.toml"
BUNDLED_CONFIG = Path(__file__).parent.parent / "config.toml"


@dataclass
class RecordingConfig:
    output_dir: str = "~/MeetingRecordings"
    language: str = "en"
    user_name: str = "User"
    live_transcription: bool = False


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 30
    mic_device: str = ""


@dataclass
class VadConfig:
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 300


@dataclass
class TranscriptionConfig:
    backend: str = "local"
    model_size: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    openai_api_key: str = ""


@dataclass
class DiarizationConfig:
    enabled: bool = True
    huggingface_token: str = ""
    min_speakers: int = 2
    max_speakers: int = 6


@dataclass
class OutputConfig:
    formats: list[str] = field(default_factory=lambda: ["json", "txt", "srt"])


@dataclass
class HotkeyConfig:
    toggle_recording: str = "ctrl+shift+r"
    toggle_mute: str = "ctrl+shift+u"
    toggle_dashboard: str = "ctrl+shift+d"


@dataclass
class DashboardConfig:
    enabled: bool = True
    auto_show: bool = True
    auto_hide: bool = True
    opacity: float = 0.92
    position: str = "top-right"  # top-left, top-right, bottom-left, bottom-right, center
    position_x: int = -1  # -1 = auto (use position preset)
    position_y: int = -1
    start_collapsed: bool = False
    show_transcript: bool = True
    show_screen_preview: bool = True


@dataclass
class ScreenRecordingConfig:
    enabled: bool = True
    fps: float = 5.0


@dataclass
class OutlookConfig:
    enabled: bool = True
    buffer_minutes: int = 10


@dataclass
class GoogleDriveConfig:
    enabled: bool = False
    credentials_path: str = "~/.meeting_recorder/google_credentials.json"
    folder_id: str = ""


@dataclass
class SummaryConfig:
    enabled: bool = False
    provider: str = "openai"      # "openai" or "anthropic"
    api_key: str = ""
    model: str = ""               # empty = provider default
    max_transcript_tokens: int = 0  # 0 = no limit


@dataclass
class Config:
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    diarization: DiarizationConfig = field(default_factory=DiarizationConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    screen_recording: ScreenRecordingConfig = field(default_factory=ScreenRecordingConfig)
    outlook: OutlookConfig = field(default_factory=OutlookConfig)
    google_drive: GoogleDriveConfig = field(default_factory=GoogleDriveConfig)
    summary: SummaryConfig = field(default_factory=SummaryConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    @classmethod
    def load(cls) -> Config:
        """Load config from user config file, falling back to defaults."""
        if not CONFIG_FILE.exists():
            cls._init_config_dir()

        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "rb") as f:
                data = tomllib.load(f)
            return cls._from_dict(data)

        return cls()

    @classmethod
    def _init_config_dir(cls) -> None:
        """Create config directory and copy default config."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if BUNDLED_CONFIG.exists():
            shutil.copy2(BUNDLED_CONFIG, CONFIG_FILE)

    @classmethod
    def _from_dict(cls, data: dict) -> Config:
        """Create Config from a dictionary, using defaults for missing keys."""
        return cls(
            recording=RecordingConfig(**data.get("recording", {})),
            audio=AudioConfig(**data.get("audio", {})),
            vad=VadConfig(**data.get("vad", {})),
            transcription=TranscriptionConfig(**data.get("transcription", {})),
            diarization=DiarizationConfig(**{
                k: v for k, v in data.get("diarization", {}).items()
                if k in DiarizationConfig.__dataclass_fields__
            }),
            output=OutputConfig(**data.get("output", {})),
            hotkey=HotkeyConfig(**data.get("hotkey", {})),
            screen_recording=ScreenRecordingConfig(**{
                k: v for k, v in data.get("screen_recording", {}).items()
                if k in ScreenRecordingConfig.__dataclass_fields__
            }),
            outlook=OutlookConfig(**{
                k: v for k, v in data.get("outlook", {}).items()
                if k in OutlookConfig.__dataclass_fields__
            }),
            google_drive=GoogleDriveConfig(**{
                k: v for k, v in data.get("google_drive", {}).items()
                if k in GoogleDriveConfig.__dataclass_fields__
            }),
            summary=SummaryConfig(**{
                k: v for k, v in data.get("summary", {}).items()
                if k in SummaryConfig.__dataclass_fields__
            }),
            dashboard=DashboardConfig(**{
                k: v for k, v in data.get("dashboard", {}).items()
                if k in DashboardConfig.__dataclass_fields__
            }),
        )

    def save(self) -> None:
        """Save current config to file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        with open(CONFIG_FILE, "wb") as f:
            tomli_w.dump(data, f)

    @property
    def output_dir(self) -> Path:
        """Get resolved output directory path."""
        return Path(self.recording.output_dir).expanduser()
