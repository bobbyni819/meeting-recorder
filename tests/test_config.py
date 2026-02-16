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
        assert cfg.fps == 5.0

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
        assert cfg.screen_recording.fps == 5.0  # default preserved

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
# Load from TOML file
# ---------------------------------------------------------------------------

class TestConfigLoadSave:
    """Test loading from and saving to TOML files."""

    def test_load_from_file(self, sample_config_toml: Path):
        """Load a TOML file and verify parsed values."""
        # Patch CONFIG_FILE to point to our test file
        with mock.patch("meeting_recorder.config.CONFIG_FILE", sample_config_toml):
            cfg = Config.load()

        assert cfg.recording.user_name == "TestUser"
        assert cfg.transcription.model_size == "tiny"
        assert cfg.transcription.device == "cpu"
        assert cfg.diarization.enabled is False
        assert cfg.screen_recording.enabled is True

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        """When the config file does not exist and no bundled config, return defaults."""
        fake_file = tmp_path / "does_not_exist.toml"
        fake_dir = tmp_path / "fake_config_dir"
        with (
            mock.patch("meeting_recorder.config.CONFIG_FILE", fake_file),
            mock.patch("meeting_recorder.config.CONFIG_DIR", fake_dir),
            mock.patch("meeting_recorder.config.BUNDLED_CONFIG", tmp_path / "no_bundled.toml"),
        ):
            cfg = Config.load()
        assert cfg.recording.output_dir == "~/MeetingRecordings"

    def test_save_and_reload(self, tmp_path: Path):
        """Save a config and reload it, verifying round-trip fidelity."""
        config_file = tmp_path / "config.toml"
        config_dir = tmp_path

        cfg = Config()
        cfg.recording.user_name = "RoundTrip"
        cfg.screen_recording.fps = 8.0
        cfg.transcription.backend = "cloud"

        with (
            mock.patch("meeting_recorder.config.CONFIG_FILE", config_file),
            mock.patch("meeting_recorder.config.CONFIG_DIR", config_dir),
        ):
            cfg.save()

        # Reload
        with open(config_file, "rb") as f:
            data = tomllib.load(f)

        assert data["recording"]["user_name"] == "RoundTrip"
        assert data["screen_recording"]["fps"] == 8.0
        assert data["transcription"]["backend"] == "cloud"


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
