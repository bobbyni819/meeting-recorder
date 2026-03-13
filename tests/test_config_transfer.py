"""Tests for config export/import (multi-machine setup)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from meeting_recorder.config_transfer import export_config, import_config


@pytest.fixture
def config_dir(tmp_path):
    """Set up a temporary config directory."""
    cfg_dir = tmp_path / ".meeting_recorder"
    cfg_dir.mkdir()
    return cfg_dir


@pytest.fixture
def sample_secrets():
    """Realistic secrets dict (what goes in secrets.toml)."""
    return {
        "transcription": {"gemini_api_key": "test-key-123"},
        "diarization": {"huggingface_token": "hf-token-abc"},
        "summary": {"api_key": "summary-key"},
        "audio": {"mic_device": "Brio 101"},
        "dashboard": {"position_x": 1920, "position_y": 50},
    }


@pytest.fixture
def secrets_file(config_dir, sample_secrets):
    """Write a sample secrets.toml."""
    path = config_dir / "secrets.toml"
    with open(path, "wb") as f:
        tomli_w.dump(sample_secrets, f)
    return path


class TestExportConfig:
    def test_export_creates_bundle_file(self, secrets_file, config_dir, tmp_path):
        dest = str(tmp_path / "export.json")
        with patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", config_dir / "no_legacy.toml"), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "google_token.json"):
            result = export_config(dest)

        assert result == 0
        assert Path(dest).exists()

        with open(dest) as f:
            bundle = json.load(f)
        assert bundle["version"] == 2
        assert "exported_at" in bundle
        assert "secrets" in bundle
        assert bundle["secrets"]["transcription"]["gemini_api_key"] == "test-key-123"

    def test_export_includes_google_token(self, secrets_file, config_dir, tmp_path):
        token_file = config_dir / "google_token.json"
        token_file.write_text(json.dumps({"refresh_token": "rt-123"}))
        dest = str(tmp_path / "export.json")

        with patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", config_dir / "no.toml"), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", token_file):
            result = export_config(dest)

        assert result == 0
        with open(dest) as f:
            bundle = json.load(f)
        assert bundle["google_token"]["refresh_token"] == "rt-123"

    def test_export_without_google_token(self, secrets_file, config_dir, tmp_path):
        dest = str(tmp_path / "export.json")
        with patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", config_dir / "no.toml"), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "nonexistent.json"):
            result = export_config(dest)

        assert result == 0
        with open(dest) as f:
            bundle = json.load(f)
        assert "google_token" not in bundle

    def test_export_no_secrets_file_returns_error(self, tmp_path):
        with patch("meeting_recorder.config_transfer.SECRETS_FILE", tmp_path / "nope.toml"), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", tmp_path / "nope2.toml"):
            result = export_config(str(tmp_path / "out.json"))
        assert result == 1

    def test_export_falls_back_to_legacy_config(self, config_dir, tmp_path):
        """If secrets.toml doesn't exist, extract secrets from legacy config.toml."""
        legacy = config_dir / "config.toml"
        with open(legacy, "wb") as f:
            tomli_w.dump({
                "transcription": {"backend": "gemini", "gemini_api_key": "legacy-key"},
                "recording": {"user_name": "LegacyUser"},
            }, f)

        dest = str(tmp_path / "export.json")
        with patch("meeting_recorder.config_transfer.SECRETS_FILE", config_dir / "no_secrets.toml"), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", legacy), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "nope.json"):
            result = export_config(dest)

        assert result == 0
        with open(dest) as f:
            bundle = json.load(f)
        assert bundle["secrets"]["transcription"]["gemini_api_key"] == "legacy-key"

    def test_export_default_dest(self, secrets_file, config_dir, tmp_path):
        with patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", config_dir / "no.toml"), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "nope.json"), \
             patch("pathlib.Path.home", return_value=tmp_path):
            result = export_config(None)

        assert result == 0
        assert (tmp_path / "meeting_recorder_config.json").exists()

    def test_export_lists_api_keys(self, secrets_file, config_dir, tmp_path, capsys):
        dest = str(tmp_path / "export.json")
        with patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", config_dir / "no.toml"), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "nope.json"):
            export_config(dest)

        output = capsys.readouterr().out
        assert "Gemini" in output
        assert "HuggingFace" in output


class TestImportConfig:
    def _make_v2_bundle(self, tmp_path, secrets, google_token=None):
        """Create a v2 bundle (secrets only)."""
        bundle = {"version": 2, "secrets": secrets}
        if google_token:
            bundle["google_token"] = google_token
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle))
        return str(path)

    def _make_v1_bundle(self, tmp_path, config, google_token=None):
        """Create a v1 legacy bundle (full config)."""
        bundle = {"version": 1, "config": config}
        if google_token:
            bundle["google_token"] = google_token
        path = tmp_path / "bundle_v1.json"
        path.write_text(json.dumps(bundle))
        return str(path)

    def test_import_v2_writes_secrets(self, tmp_path, sample_secrets):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        secrets_file = cfg_dir / "secrets.toml"
        bundle_path = self._make_v2_bundle(tmp_path, sample_secrets)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"):
            result = import_config(bundle_path)

        assert result == 0
        assert secrets_file.exists()

        with open(secrets_file, "rb") as f:
            written = tomllib.load(f)

        # Machine-specific fields should be stripped
        assert "mic_device" not in written.get("audio", {})
        assert "position_x" not in written.get("dashboard", {})

        # API keys should be preserved
        assert written["transcription"]["gemini_api_key"] == "test-key-123"
        assert written["diarization"]["huggingface_token"] == "hf-token-abc"

    def test_import_v1_extracts_secrets(self, tmp_path):
        """v1 bundle with full config: extract only secrets."""
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        secrets_file = cfg_dir / "secrets.toml"

        v1_config = {
            "recording": {"user_name": "V1User"},
            "transcription": {"backend": "gemini", "gemini_api_key": "v1-key"},
            "diarization": {"huggingface_token": "v1-token"},
            "audio": {"mic_device": "hw:0"},
        }
        bundle_path = self._make_v1_bundle(tmp_path, v1_config)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"):
            result = import_config(bundle_path)

        assert result == 0
        with open(secrets_file, "rb") as f:
            written = tomllib.load(f)

        assert written["transcription"]["gemini_api_key"] == "v1-key"
        assert written["diarization"]["huggingface_token"] == "v1-token"
        # mic_device is machine-specific, should be stripped
        assert "mic_device" not in written.get("audio", {})

    def test_import_writes_google_token(self, tmp_path, sample_secrets):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        token_file = cfg_dir / "token.json"
        bundle_path = self._make_v2_bundle(
            tmp_path, sample_secrets, google_token={"refresh_token": "rt-xyz"}
        )

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.SECRETS_FILE", cfg_dir / "secrets.toml"), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", token_file):
            result = import_config(bundle_path)

        assert result == 0
        assert token_file.exists()
        with open(token_file) as f:
            token = json.load(f)
        assert token["refresh_token"] == "rt-xyz"

    def test_import_existing_secrets_aborted(self, tmp_path, sample_secrets):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        secrets_file = cfg_dir / "secrets.toml"
        secrets_file.write_bytes(b"[transcription]\n")
        bundle_path = self._make_v2_bundle(tmp_path, sample_secrets)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"), \
             patch("builtins.input", return_value="n"):
            result = import_config(bundle_path)

        assert result == 0  # abort is not an error

    def test_import_existing_secrets_overwritten(self, tmp_path, sample_secrets):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        secrets_file = cfg_dir / "secrets.toml"
        secrets_file.write_bytes(b"[transcription]\n")
        bundle_path = self._make_v2_bundle(tmp_path, sample_secrets)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.SECRETS_FILE", secrets_file), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"), \
             patch("builtins.input", return_value="y"):
            result = import_config(bundle_path)

        assert result == 0
        assert secrets_file.stat().st_size > 20

    def test_import_file_not_found(self, tmp_path):
        result = import_config(str(tmp_path / "nope.json"))
        assert result == 1

    def test_import_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{")
        result = import_config(str(bad))
        assert result == 1

    def test_import_v2_missing_secrets_key(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 2}))
        result = import_config(str(bad))
        assert result == 1

    def test_import_v1_missing_config_key(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 1}))
        result = import_config(str(bad))
        assert result == 1

    def test_import_prints_next_steps(self, tmp_path, sample_secrets, capsys):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        bundle_path = self._make_v2_bundle(tmp_path, sample_secrets)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.SECRETS_FILE", cfg_dir / "secrets.toml"), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"):
            import_config(bundle_path)

        output = capsys.readouterr().out
        assert "diagnose" in output
        assert "Next steps" in output
        assert "git pull" in output
