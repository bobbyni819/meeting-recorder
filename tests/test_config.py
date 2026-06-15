"""Tests for configuration management."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from meeting_recorder.config import (
    Config,
    RecordingConfig,
    AudioConfig,
    VadConfig,
    TranscriptionConfig,
    DiarizationConfig,
    OutputConfig,
    HotkeyConfig,
    ScreenRecordingConfig,
    CONFIG_DIR,
    CONFIG_FILE,
    SECRETS_FILE,
    BUNDLED_CONFIG,
    _deep_merge,
    _split_secrets,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    """Verify that default configuration values are correct."""

    def test_default_recording_config(self):
        cfg = RecordingConfig()
        assert cfg.output_dir == "~/MeetingRecordings"
        assert cfg.language == "en"
        assert cfg.user_name == "User"
        assert cfg.live_model_size == ""

    def test_default_audio_config(self):
        cfg = AudioConfig()
        assert cfg.sample_rate == 16000
        assert cfg.channels == 1
        assert cfg.chunk_duration_ms == 30
        assert cfg.mic_device == ""

    def test_default_vad_config(self):
        cfg = VadConfig()
        assert cfg.threshold == 0.5
        assert cfg.min_speech_duration_ms == 250
        assert cfg.min_silence_duration_ms == 300

    def test_default_transcription_config(self):
        cfg = TranscriptionConfig()
        assert cfg.backend == "local"
        assert cfg.model_size == "large-v3"
        assert cfg.device == "cuda"
        assert cfg.compute_type == "float16"
        assert cfg.openai_api_key == ""

    def test_default_diarization_config(self):
        cfg = DiarizationConfig()
        assert cfg.enabled is True
        assert cfg.huggingface_token == ""
        assert cfg.min_speakers == 2
        assert cfg.max_speakers == 6

    def test_default_output_config(self):
        cfg = OutputConfig()
        assert cfg.formats == ["json", "txt", "srt"]

    def test_default_hotkey_config(self):
        cfg = HotkeyConfig()
        assert cfg.toggle_recording == "ctrl+shift+r"
        assert cfg.toggle_mute == "ctrl+shift+u"

    def test_default_screen_recording_config(self):
        cfg = ScreenRecordingConfig()
        assert cfg.enabled is True
        assert cfg.fps == 30.0

    def test_full_default_config(self):
        cfg = Config()
        assert isinstance(cfg.recording, RecordingConfig)
        assert isinstance(cfg.audio, AudioConfig)
        assert isinstance(cfg.vad, VadConfig)
        assert isinstance(cfg.transcription, TranscriptionConfig)
        assert isinstance(cfg.diarization, DiarizationConfig)
        assert isinstance(cfg.output, OutputConfig)
        assert isinstance(cfg.hotkey, HotkeyConfig)
        assert isinstance(cfg.screen_recording, ScreenRecordingConfig)


# ---------------------------------------------------------------------------
# _from_dict
# ---------------------------------------------------------------------------

class TestConfigFromDict:
    """Test Config._from_dict with partial and full data."""

    def test_empty_dict_gives_defaults(self):
        cfg = Config._from_dict({})
        assert cfg.recording.output_dir == "~/MeetingRecordings"
        assert cfg.audio.sample_rate == 16000
        assert cfg.screen_recording.enabled is True

    def test_partial_dict_overrides(self):
        data = {
            "recording": {"output_dir": "/tmp/recordings", "language": "de"},
            "screen_recording": {"enabled": False},
        }
        cfg = Config._from_dict(data)
        assert cfg.recording.output_dir == "/tmp/recordings"
        assert cfg.recording.language == "de"
        assert cfg.recording.user_name == "User"  # default preserved
        assert cfg.screen_recording.enabled is False
        assert cfg.screen_recording.fps == 30.0  # default preserved

    def test_full_dict(self):
        data = {
            "recording": {"output_dir": "/out", "language": "ja", "user_name": "Taro"},
            "audio": {"sample_rate": 44100, "channels": 2, "chunk_duration_ms": 20, "mic_device": "hw:0"},
            "vad": {"threshold": 0.8, "min_speech_duration_ms": 100, "min_silence_duration_ms": 200},
            "transcription": {
                "backend": "cloud",
                "model_size": "small",
                "device": "cpu",
                "compute_type": "int8",
                "openai_api_key": "sk-test",
            },
            "diarization": {"enabled": False, "huggingface_token": "hf-tok", "min_speakers": 1, "max_speakers": 3},
            "output": {"formats": ["json"]},
            "hotkey": {"toggle_recording": "ctrl+r", "toggle_mute": "ctrl+m"},
            "screen_recording": {"enabled": False, "fps": 10.0},
        }
        cfg = Config._from_dict(data)
        assert cfg.recording.user_name == "Taro"
        assert cfg.audio.sample_rate == 44100
        assert cfg.vad.threshold == 0.8
        assert cfg.transcription.openai_api_key == "sk-test"
        assert cfg.diarization.enabled is False
        assert cfg.output.formats == ["json"]
        assert cfg.hotkey.toggle_mute == "ctrl+m"
        assert cfg.screen_recording.fps == 10.0

    def test_unknown_keys_in_diarization_ignored(self):
        """Extra keys in diarization section should be silently ignored."""
        data = {
            "diarization": {
                "enabled": True,
                "huggingface_token": "tok",
                "min_speakers": 2,
                "max_speakers": 4,
                "unknown_future_key": "value",
            },
        }
        cfg = Config._from_dict(data)
        assert cfg.diarization.max_speakers == 4

    def test_unknown_keys_in_screen_recording_ignored(self):
        data = {
            "screen_recording": {
                "enabled": True,
                "fps": 2.0,
                "extra_field": 42,
            },
        }
        cfg = Config._from_dict(data)
        assert cfg.screen_recording.fps == 2.0


# ---------------------------------------------------------------------------
# Split config helpers
# ---------------------------------------------------------------------------

class TestDeepMerge:
    """Test _deep_merge helper."""

    def test_merge_disjoint(self):
        base = {"a": 1}
        _deep_merge(base, {"b": 2})
        assert base == {"a": 1, "b": 2}

    def test_merge_overlapping(self):
        base = {"a": {"x": 1, "y": 2}}
        _deep_merge(base, {"a": {"y": 99, "z": 3}})
        assert base == {"a": {"x": 1, "y": 99, "z": 3}}

    def test_merge_non_dict_replaces(self):
        base = {"a": 1}
        _deep_merge(base, {"a": "replaced"})
        assert base == {"a": "replaced"}


class TestSplitSecrets:
    """Test _split_secrets extraction."""

    def test_extracts_api_keys(self):
        data = {
            "transcription": {"backend": "gemini", "gemini_api_key": "sk-123"},
            "diarization": {"huggingface_token": "hf-tok"},
        }
        secrets = _split_secrets(data)
        # Secrets extracted
        assert secrets["transcription"]["gemini_api_key"] == "sk-123"
        assert secrets["diarization"]["huggingface_token"] == "hf-tok"
        # Data has defaults in place
        assert data["transcription"]["gemini_api_key"] == ""
        assert data["diarization"]["huggingface_token"] == ""
        # Non-secret field preserved
        assert data["transcription"]["backend"] == "gemini"

    def test_empty_secrets_not_extracted(self):
        data = {
            "transcription": {"openai_api_key": "", "gemini_api_key": ""},
        }
        secrets = _split_secrets(data)
        assert secrets == {}

    def test_dashboard_position_extracted(self):
        data = {"dashboard": {"position_x": 500, "position_y": 200, "opacity": 0.9}}
        secrets = _split_secrets(data)
        assert secrets["dashboard"]["position_x"] == 500
        assert data["dashboard"]["position_x"] == -1  # reset to default
        assert data["dashboard"]["opacity"] == 0.9  # non-local field preserved

    def test_mic_device_extracted(self):
        data = {"audio": {"sample_rate": 16000, "mic_device": "hw:1"}}
        secrets = _split_secrets(data)
        assert secrets["audio"]["mic_device"] == "hw:1"
        assert data["audio"]["mic_device"] == ""
        assert data["audio"]["sample_rate"] == 16000


# ---------------------------------------------------------------------------
# Load from split config
# ---------------------------------------------------------------------------

class TestConfigLoadSplit:
    """Test loading from bundled config + secrets overlay."""

    def test_load_from_bundled_config(self, tmp_path: Path):
        """Load from bundled config.toml (no secrets)."""
        bundled = tmp_path / "repo" / "config.toml"
        bundled.parent.mkdir()
        bundled_data = {"recording": {"user_name": "RepoUser"}, "screen_recording": {"fps": 15.0}}
        with open(bundled, "wb") as f:
            tomli_w.dump(bundled_data, f)

        secrets_file = tmp_path / "secrets.toml"
        config_file = tmp_path / "config.toml"  # legacy (doesn't exist)

        with (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.CONFIG_FILE", config_file),
        ):
            cfg = Config.load()

        assert cfg.recording.user_name == "RepoUser"
        assert cfg.screen_recording.fps == 15.0

    def test_load_merges_secrets(self, tmp_path: Path):
        """Secrets overlay on top of bundled config."""
        bundled = tmp_path / "config.toml"
        with open(bundled, "wb") as f:
            tomli_w.dump({
                "transcription": {"backend": "gemini", "gemini_api_key": ""},
                "recording": {"user_name": "RepoUser"},
            }, f)

        secrets_file = tmp_path / "secrets.toml"
        with open(secrets_file, "wb") as f:
            tomli_w.dump({
                "transcription": {"gemini_api_key": "sk-secret"},
                "audio": {"mic_device": "hw:2"},
            }, f)

        with (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.CONFIG_FILE", tmp_path / "no_legacy.toml"),
        ):
            cfg = Config.load()

        assert cfg.transcription.backend == "gemini"
        assert cfg.transcription.gemini_api_key == "sk-secret"
        assert cfg.audio.mic_device == "hw:2"
        assert cfg.recording.user_name == "RepoUser"

    def test_load_no_files_returns_defaults(self, tmp_path: Path):
        """When no config files exist, return defaults."""
        with (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", tmp_path / "nope.toml"),
            mock.patch("meeting_recorder.config.SECRETS_FILE", tmp_path / "nope_secrets.toml"),
            mock.patch("meeting_recorder.config.CONFIG_FILE", tmp_path / "nope_legacy.toml"),
        ):
            cfg = Config.load()
        assert cfg.recording.output_dir == "~/MeetingRecordings"


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

class TestConfigMigration:
    """Test migration from legacy single-file config to split config."""

    def test_migration_creates_secrets_file(self, tmp_path: Path):
        """Legacy config.toml with API keys migrates to secrets.toml."""
        legacy = tmp_path / "config.toml"
        with open(legacy, "wb") as f:
            tomli_w.dump({
                "transcription": {"backend": "gemini", "gemini_api_key": "sk-123"},
                "diarization": {"huggingface_token": "hf-tok", "enabled": True},
                "recording": {"user_name": "TestUser"},
            }, f)

        secrets_file = tmp_path / "secrets.toml"
        bundled = tmp_path / "bundled_config.toml"
        # Create a pre-existing bundled config
        with open(bundled, "wb") as f:
            tomli_w.dump({"recording": {"user_name": "OldDefault"}}, f)

        with (
            mock.patch("meeting_recorder.config.CONFIG_FILE", legacy),
            mock.patch("meeting_recorder.config.CONFIG_DIR", tmp_path),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
        ):
            Config.load()

        # Secrets file created
        assert secrets_file.exists()
        with open(secrets_file, "rb") as f:
            secrets = tomllib.load(f)
        assert secrets["transcription"]["gemini_api_key"] == "sk-123"
        assert secrets["diarization"]["huggingface_token"] == "hf-tok"

        # Bundled config updated with non-secret settings
        with open(bundled, "rb") as f:
            repo_data = tomllib.load(f)
        assert repo_data["recording"]["user_name"] == "TestUser"
        # API keys replaced with empty defaults in repo config
        assert repo_data["transcription"]["gemini_api_key"] == ""

        # Legacy config backed up
        assert not legacy.exists()
        assert (tmp_path / "config.toml.bak").exists()

    def test_no_migration_when_secrets_exists(self, tmp_path: Path):
        """If secrets.toml exists, legacy config.toml is ignored."""
        legacy = tmp_path / "config.toml"
        with open(legacy, "wb") as f:
            tomli_w.dump({"transcription": {"gemini_api_key": "old-key"}}, f)

        secrets_file = tmp_path / "secrets.toml"
        with open(secrets_file, "wb") as f:
            tomli_w.dump({"transcription": {"gemini_api_key": "new-key"}}, f)

        bundled = tmp_path / "bundled.toml"
        with open(bundled, "wb") as f:
            tomli_w.dump({"recording": {"user_name": "A"}}, f)

        with (
            mock.patch("meeting_recorder.config.CONFIG_FILE", legacy),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
        ):
            cfg = Config.load()

        # Uses secrets.toml, not legacy
        assert cfg.transcription.gemini_api_key == "new-key"
        # Legacy file untouched
        assert legacy.exists()


# ---------------------------------------------------------------------------
# Save (split write)
# ---------------------------------------------------------------------------

class TestConfigSave:
    """Test Config.save() writes to repo config and secrets file."""

    def test_save_splits_secrets(self, tmp_path: Path):
        """Save writes non-secret to bundled config, secrets to secrets.toml."""
        bundled = tmp_path / "config.toml"
        secrets_file = tmp_path / "secrets.toml"

        cfg = Config()
        cfg.recording.user_name = "SaveTest"
        cfg.transcription.gemini_api_key = "sk-save-test"
        cfg.screen_recording.fps = 20.0

        with (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.CONFIG_DIR", tmp_path),
        ):
            cfg.save()

        # Bundled config has non-secret values, API keys are empty
        with open(bundled, "rb") as f:
            repo_data = tomllib.load(f)
        assert repo_data["recording"]["user_name"] == "SaveTest"
        assert repo_data["screen_recording"]["fps"] == 20.0
        assert repo_data["transcription"]["gemini_api_key"] == ""

        # Secrets file has the API key
        with open(secrets_file, "rb") as f:
            secrets = tomllib.load(f)
        assert secrets["transcription"]["gemini_api_key"] == "sk-save-test"

    def test_save_and_reload_roundtrip(self, tmp_path: Path):
        """Save then load preserves all values."""
        bundled = tmp_path / "config.toml"
        secrets_file = tmp_path / "secrets.toml"

        cfg = Config()
        cfg.recording.user_name = "RoundTrip"
        cfg.transcription.backend = "gemini"
        cfg.transcription.gemini_api_key = "sk-round"
        cfg.diarization.huggingface_token = "hf-round"
        cfg.screen_recording.fps = 8.0
        cfg.audio.mic_device = "hw:3"

        patches = (
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", bundled),
            mock.patch("meeting_recorder.config.SECRETS_FILE", secrets_file),
            mock.patch("meeting_recorder.config.CONFIG_DIR", tmp_path),
            mock.patch("meeting_recorder.config.CONFIG_FILE", tmp_path / "no_legacy.toml"),
        )

        with patches[0], patches[1], patches[2], patches[3]:
            cfg.save()
            loaded = Config.load()

        assert loaded.recording.user_name == "RoundTrip"
        assert loaded.transcription.backend == "gemini"
        assert loaded.transcription.gemini_api_key == "sk-round"
        assert loaded.diarization.huggingface_token == "hf-round"
        assert loaded.screen_recording.fps == 8.0
        assert loaded.audio.mic_device == "hw:3"


# ---------------------------------------------------------------------------
# output_dir property
# ---------------------------------------------------------------------------

class TestConfigOutputDir:
    """Test the output_dir property expansion."""

    def test_output_dir_expands_tilde(self):
        cfg = Config()
        result = cfg.output_dir
        assert "~" not in str(result)
        assert result.is_absolute()

    def test_output_dir_custom(self):
        cfg = Config()
        cfg.recording.output_dir = "/tmp/my_recordings"
        assert cfg.output_dir == Path("/tmp/my_recordings")


class TestConfigValidation:
    def test_valid_config_no_warnings(self):
        cfg = Config()
        cfg.diarization.enabled = False  # skip token check
        assert cfg.validate() == []

    def test_invalid_backend_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False  # isolate to backend warning
        cfg.transcription.backend = "invalid"
        warnings = cfg.validate()
        assert len(warnings) == 1
        assert "backend" in warnings[0]

    def test_invalid_summary_provider_warns(self):
        cfg = Config()
        cfg.summary.enabled = True
        cfg.summary.provider = "bad"
        warnings = cfg.validate()
        assert any("provider" in w for w in warnings)

    def test_invalid_fps_warns(self):
        cfg = Config()
        cfg.screen_recording.fps = -5
        warnings = cfg.validate()
        assert any("fps" in w for w in warnings)

    def test_invalid_vad_threshold_warns(self):
        cfg = Config()
        cfg.vad.threshold = 2.0
        warnings = cfg.validate()
        assert any("vad" in w.lower() or "threshold" in w for w in warnings)

    def test_multiple_issues(self):
        cfg = Config()
        cfg.transcription.backend = "bad"
        cfg.screen_recording.fps = 0
        warnings = cfg.validate()
        assert len(warnings) >= 2

    def test_cloud_backend_no_key_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.transcription.backend = "cloud"
        cfg.transcription.openai_api_key = ""
        warnings = cfg.validate()
        assert any("openai_api_key" in w for w in warnings)

    def test_gemini_backend_no_key_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.transcription.backend = "gemini"
        cfg.transcription.gemini_api_key = ""
        warnings = cfg.validate()
        assert any("gemini_api_key" in w for w in warnings)

    def test_cloud_backend_with_key_ok(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.transcription.backend = "cloud"
        cfg.transcription.openai_api_key = "sk-test123"
        warnings = cfg.validate()
        assert not any("openai_api_key" in w for w in warnings)

    def test_summary_no_key_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.summary.enabled = True
        cfg.summary.api_key = ""
        warnings = cfg.validate()
        assert any("api_key" in w for w in warnings)

    def test_diarization_no_token_warns(self):
        cfg = Config()
        cfg.diarization.enabled = True
        cfg.diarization.huggingface_token = ""
        warnings = cfg.validate()
        assert any("huggingface_token" in w for w in warnings)

    def test_invalid_model_size_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.transcription.model_size = "super-large"
        warnings = cfg.validate()
        assert any("model" in w.lower() for w in warnings)

    def test_valid_model_size_ok(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.transcription.model_size = "large-v3"
        warnings = cfg.validate()
        assert not any("model" in w.lower() for w in warnings)

    def test_new_whisper_model_names_ok(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.transcription.model_size = "distil-large-v3"
        cfg.recording.live_model_size = "large-v3-turbo"
        warnings = cfg.validate()
        assert not any("model" in w.lower() for w in warnings)

    def test_invalid_live_model_size_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.recording.live_model_size = "not-a-model"
        warnings = cfg.validate()
        assert any("recording.live_model_size" in w for w in warnings)

    def test_invalid_device_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.transcription.device = "tpu"
        warnings = cfg.validate()
        assert any("device" in w for w in warnings)

    def test_min_speakers_zero_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.diarization.min_speakers = 0
        warnings = cfg.validate()
        assert any("min_speakers" in w for w in warnings)

    def test_max_less_than_min_speakers_warns(self):
        cfg = Config()
        cfg.diarization.enabled = False
        cfg.diarization.min_speakers = 5
        cfg.diarization.max_speakers = 2
        warnings = cfg.validate()
        assert any("max_speakers" in w for w in warnings)
