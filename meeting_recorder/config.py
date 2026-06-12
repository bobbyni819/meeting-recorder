"""Configuration management for Meeting Recorder.

Config is split into two layers:
- **Bundled config** (``config.toml`` in the repo) — non-secret settings
  that sync across machines via git (model choices, FPS, features, etc.).
- **Local secrets** (``~/.meeting_recorder/secrets.toml``) — API keys,
  tokens, and machine-specific settings that never leave the machine.

On load, the bundled config is read first, then local secrets are overlaid.
On save, secrets are extracted and written to the local file while
everything else goes back to the repo's config.toml.
"""

from __future__ import annotations

import logging
import os
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

_logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".meeting_recorder"
CONFIG_FILE = CONFIG_DIR / "config.toml"  # legacy — kept for migration
SECRETS_FILE = CONFIG_DIR / "secrets.toml"
BUNDLED_CONFIG = Path(__file__).parent.parent / "config.toml"

# Fields that stay local (secrets + machine-specific hardware).
# These are stripped from the repo config and written to secrets.toml.
# Structure: {section_name: {field_name: default_value, ...}}
_LOCAL_ONLY_FIELDS: dict[str, dict[str, object]] = {
    "transcription": {"openai_api_key": "", "gemini_api_key": ""},
    "diarization": {"huggingface_token": ""},
    "summary": {"api_key": ""},
    "audio": {"mic_device": ""},
    "dashboard": {"position_x": -1, "position_y": -1},
    # Per-machine performance tier — must NOT sync via git (the workstation
    # and the old PC have different hardware). "auto" detects at startup.
    "performance": {"profile": "auto"},
}


def _safe_init(cls, data: dict, section: str):
    """Create a dataclass instance from a dict, ignoring unknown keys."""
    raw = data.get(section, {})
    return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def _deep_merge(base: dict, overlay: dict) -> None:
    """Merge *overlay* into *base* in-place (one level deep for TOML sections)."""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key].update(value)
        else:
            base[key] = value


def _split_secrets(data: dict) -> dict:
    """Extract local-only fields from *data* (in-place) and return them.

    Replaces secret/local values in *data* with their empty defaults so the
    repo config retains the field names for discoverability.  Returns a dict
    containing only the non-default secret/local values.
    """
    secrets: dict = {}
    for section, fields in _LOCAL_ONLY_FIELDS.items():
        if section not in data:
            continue
        for key, default_val in fields.items():
            if key not in data[section]:
                continue
            actual_val = data[section][key]
            if actual_val != default_val:
                secrets.setdefault(section, {})[key] = actual_val
            data[section][key] = default_val
    return secrets


@dataclass
class RecordingConfig:
    output_dir: str = "~/MeetingRecordings"
    language: str = "en"
    user_name: str = "User"
    live_transcription: bool = False
    # Feed the user's mic into the live preview too (labelled [You]);
    # app audio (other participants) is always fed when live preview is on.
    live_transcript_mic: bool = True
    auto_start: bool = False  # auto-detect meetings and start recording
    # Heal failed/stuck/partially-processed recordings at startup
    auto_retry_failed: bool = True
    # Rename the recording folder from transcript content after processing,
    # disambiguating between meetings booked in the same slot via the
    # calendar. Only the folder moves; files inside are untouched.
    smart_rename: bool = True


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
    backend: str = "local"        # "local", "cloud", or "gemini"
    model_size: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""        # empty = use GeminiTranscriber.DEFAULT_MODEL


@dataclass
class DiarizationConfig:
    enabled: bool = True
    huggingface_token: str = ""
    min_speakers: int = 2
    max_speakers: int = 6
    # Diarization model. "community-1" is newer/more accurate but must be
    # accepted on HF; falls back to speaker-diarization-3.1 if it won't load.
    model: str = "pyannote/speaker-diarization-community-1"


@dataclass
class OutputConfig:
    formats: list[str] = field(default_factory=lambda: ["json", "txt", "srt"])


@dataclass
class HotkeyConfig:
    toggle_recording: str = "ctrl+shift+r"
    toggle_mute: str = "ctrl+shift+u"
    toggle_dashboard: str = "ctrl+shift+d"
    toggle_pause: str = "ctrl+shift+p"


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
    fps: float = 30.0


@dataclass
class OutlookConfig:
    enabled: bool = True
    buffer_minutes: int = 10


@dataclass
class GoogleDriveConfig:
    enabled: bool = False
    credentials_path: str = "~/.config/google/client_secret.json"
    folder_id: str = ""


@dataclass
class SummaryConfig:
    enabled: bool = False
    provider: str = "openai"      # "openai", "anthropic", or "gemini"
    api_key: str = ""
    model: str = ""               # empty = provider default
    max_transcript_tokens: int = 0  # 0 = no limit


@dataclass
class RetentionConfig:
    enabled: bool = False
    max_age_days: int = 90       # delete recordings older than N days (0 = no age limit)
    max_total_gb: float = 0.0    # delete oldest recordings when total exceeds N GB (0 = no size limit)


@dataclass
class PerformanceConfig:
    # "auto" | "light" | "balanced" | "full". Local-only (per machine).
    # "auto" detects GPU/CPU/RAM at startup. See meeting_recorder/performance.py.
    profile: str = "auto"


@dataclass
class DictationConfig:
    enabled: bool = False
    drive_root: str = "~/Documents"
    hotkey: str = "ctrl+shift+v"
    project_list: list[str] = field(default_factory=lambda: [
        "metabolism", "dcp", "ailab", "tools", "career"
    ])
    default_project: str = "general"
    gemini_model: str = ""       # empty = inherit transcription.gemini_model
    # Subpath under drive_root to route project-tagged memos.
    # {project} is substituted with the inferred project name.
    # If the resolved parent dir doesn't exist (e.g. "general" or unknown project),
    # memos fall back to drive_root/voice-memos/<date>/.
    project_subpath_template: str = "{project}/Sources/voice-memos"


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
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    dictation: DictationConfig = field(default_factory=DictationConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)

    @classmethod
    def load(cls) -> Config:
        """Load config: bundled repo config + local secrets overlay.

        Load order:
        1. Read ``config.toml`` from the repo (non-secret settings).
        2. Overlay ``~/.meeting_recorder/secrets.toml`` (API keys, tokens,
           machine-specific values).

        On first run after upgrade, automatically migrates secrets from the
        legacy single-file ``~/.meeting_recorder/config.toml``.
        """
        # One-time migration from legacy single-file config
        if not SECRETS_FILE.exists() and CONFIG_FILE.exists():
            cls._migrate_to_split_config()

        # Read non-secret config from repo
        data: dict = {}
        if BUNDLED_CONFIG.exists():
            with open(BUNDLED_CONFIG, "rb") as f:
                data = tomllib.load(f)

        # Overlay local secrets + machine-specific settings
        if SECRETS_FILE.exists():
            with open(SECRETS_FILE, "rb") as f:
                secrets = tomllib.load(f)
            _deep_merge(data, secrets)

        return cls._from_dict(data) if data else cls()

    @classmethod
    def _migrate_to_split_config(cls) -> None:
        """Migrate legacy ``~/.meeting_recorder/config.toml`` to split config.

        Extracts secrets/local fields into ``secrets.toml`` and writes
        the remaining non-secret settings to the bundled ``config.toml``
        so they sync via git.  Renames the old file to ``config.toml.bak``.
        """
        try:
            with open(CONFIG_FILE, "rb") as f:
                data = tomllib.load(f)

            secrets = _split_secrets(data)

            # Write secrets to local file
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _atomic_write(SECRETS_FILE, secrets)
            _logger.info("Migrated secrets to %s", SECRETS_FILE)

            # Update bundled config with user's non-secret settings
            if BUNDLED_CONFIG.exists():
                try:
                    _atomic_write(BUNDLED_CONFIG, data)
                    _logger.info("Updated repo config: %s", BUNDLED_CONFIG)
                except OSError:
                    _logger.debug(
                        "Could not update bundled config (read-only install?)",
                        exc_info=True,
                    )

            # Rename old config so migration doesn't re-run
            backup = CONFIG_FILE.with_suffix(".toml.bak")
            CONFIG_FILE.rename(backup)
            _logger.info("Legacy config backed up to %s", backup)

        except Exception:
            _logger.exception(
                "Config migration failed (non-fatal, will retry next launch)"
            )

    @classmethod
    def _from_dict(cls, data: dict) -> Config:
        """Create Config from a dictionary, using defaults for missing keys."""
        return cls(
            recording=_safe_init(RecordingConfig, data, "recording"),
            audio=_safe_init(AudioConfig, data, "audio"),
            vad=_safe_init(VadConfig, data, "vad"),
            transcription=_safe_init(TranscriptionConfig, data, "transcription"),
            diarization=_safe_init(DiarizationConfig, data, "diarization"),
            output=_safe_init(OutputConfig, data, "output"),
            hotkey=_safe_init(HotkeyConfig, data, "hotkey"),
            screen_recording=_safe_init(ScreenRecordingConfig, data, "screen_recording"),
            outlook=_safe_init(OutlookConfig, data, "outlook"),
            google_drive=_safe_init(GoogleDriveConfig, data, "google_drive"),
            summary=_safe_init(SummaryConfig, data, "summary"),
            dashboard=_safe_init(DashboardConfig, data, "dashboard"),
            retention=_safe_init(RetentionConfig, data, "retention"),
            dictation=_safe_init(DictationConfig, data, "dictation"),
            performance=_safe_init(PerformanceConfig, data, "performance"),
        )

    def save(self) -> None:
        """Save config: non-secret settings to repo, secrets to local file.

        Uses atomic writes (write to temp file then rename) to prevent
        corruption if the process crashes during the write.
        """
        data = asdict(self)
        secrets = _split_secrets(data)

        # Write non-secret config to repo file
        if BUNDLED_CONFIG.parent.exists():
            try:
                _atomic_write(BUNDLED_CONFIG, data)
            except OSError:
                _logger.debug(
                    "Could not update bundled config (read-only?)", exc_info=True
                )

        # Write secrets to local file
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(SECRETS_FILE, secrets)

    def validate(self) -> list[str]:
        """Validate config values, returning a list of warning messages.

        Does not raise — just logs warnings for invalid values so the user
        can fix them before hitting runtime errors.
        """
        warnings: list[str] = []

        valid_backends = {"local", "cloud", "gemini"}
        if self.transcription.backend not in valid_backends:
            warnings.append(
                f"transcription.backend = '{self.transcription.backend}' "
                f"is not valid (expected one of: {', '.join(sorted(valid_backends))})"
            )

        valid_providers = {"openai", "anthropic", "gemini"}
        if self.summary.enabled and self.summary.provider not in valid_providers:
            warnings.append(
                f"summary.provider = '{self.summary.provider}' "
                f"is not valid (expected one of: {', '.join(sorted(valid_providers))})"
            )

        if self.screen_recording.fps <= 0 or self.screen_recording.fps > 120:
            warnings.append(
                f"screen_recording.fps = {self.screen_recording.fps} "
                f"is out of range (expected 1-120)"
            )

        if self.retention.enabled and self.retention.max_age_days < 0:
            warnings.append(
                f"retention.max_age_days = {self.retention.max_age_days} "
                f"must be >= 0"
            )

        if self.vad.threshold < 0 or self.vad.threshold > 1:
            warnings.append(
                f"vad.threshold = {self.vad.threshold} must be between 0.0 and 1.0"
            )

        # API key checks — warn if a cloud backend is selected but no key
        if self.transcription.backend == "cloud" and not self.transcription.openai_api_key:
            warnings.append(
                "transcription.backend = 'cloud' but no openai_api_key set in secrets.toml"
            )
        if self.transcription.backend == "gemini" and not self.transcription.gemini_api_key:
            warnings.append(
                "transcription.backend = 'gemini' but no gemini_api_key set in secrets.toml"
            )
        if self.summary.enabled and not self.summary.api_key:
            warnings.append(
                f"summary.enabled = true but no api_key set for provider '{self.summary.provider}'"
            )

        # Diarization requires HuggingFace token
        if self.diarization.enabled and not self.diarization.huggingface_token:
            warnings.append(
                "diarization.enabled = true but no huggingface_token set in secrets.toml"
            )

        # Model size validation
        valid_models = {"tiny", "base", "small", "medium", "large", "large-v1", "large-v2", "large-v3"}
        if self.transcription.backend == "local" and self.transcription.model_size not in valid_models:
            warnings.append(
                f"transcription.model_size = '{self.transcription.model_size}' "
                f"is not a known Whisper model"
            )

        # Device validation
        valid_devices = {"cuda", "cpu", "auto"}
        if self.transcription.device not in valid_devices:
            warnings.append(
                f"transcription.device = '{self.transcription.device}' "
                f"is not valid (expected one of: {', '.join(sorted(valid_devices))})"
            )

        # Diarization speaker count
        if self.diarization.min_speakers < 1:
            warnings.append(
                f"diarization.min_speakers = {self.diarization.min_speakers} must be >= 1"
            )
        if self.diarization.max_speakers < self.diarization.min_speakers:
            warnings.append(
                f"diarization.max_speakers ({self.diarization.max_speakers}) "
                f"< min_speakers ({self.diarization.min_speakers})"
            )

        # Output dir writability
        out = self.output_dir
        if out.exists() and not os.access(out, os.W_OK):
            warnings.append(
                f"output directory '{out}' exists but is not writable"
            )

        for w in warnings:
            _logger.warning("Config validation: %s", w)
        return warnings

    @property
    def output_dir(self) -> Path:
        """Get resolved output directory path."""
        return Path(self.recording.output_dir).expanduser()


def _atomic_write(path: Path, data: dict) -> None:
    """Write *data* as TOML to *path* atomically (temp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(data, f)
    tmp.replace(path)
