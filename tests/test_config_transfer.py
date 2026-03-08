"""Tests for config export/import (multi-machine setup)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w

from meeting_recorder.config_transfer import export_config, import_config


@pytest.fixture
def config_dir(tmp_path):
    """Set up a temporary config directory."""
    cfg_dir = tmp_path / ".meeting_recorder"
    cfg_dir.mkdir()
    return cfg_dir


@pytest.fixture
def sample_config():
    """A realistic config dict."""
    return {
        "recording": {"output_dir": "~/MeetingRecordings", "language": "en", "auto_start": True},
        "audio": {"sample_rate": 16000, "channels": 1, "mic_device": "Brio 101"},
        "transcription": {"backend": "gemini", "gemini_api_key": "test-key-123"},
        "diarization": {"enabled": True, "huggingface_token": "hf-token-abc"},
        "dashboard": {"position_x": 1920, "position_y": 50, "opacity": 0.92},
        "summary": {"enabled": True, "provider": "gemini", "api_key": "summary-key"},
    }


@pytest.fixture
def config_file(config_dir, sample_config):
    """Write a sample config.toml."""
    cfg_file = config_dir / "config.toml"
    with open(cfg_file, "wb") as f:
        tomli_w.dump(sample_config, f)
    return cfg_file


class TestExportConfig:
    def test_export_creates_bundle_file(self, config_file, config_dir, tmp_path):
        dest = str(tmp_path / "export.json")
        with patch("meeting_recorder.config_transfer.CONFIG_FILE", config_file), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "google_token.json"):
            result = export_config(dest)

        assert result == 0
        assert Path(dest).exists()

        with open(dest) as f:
            bundle = json.load(f)
        assert bundle["version"] == 1
        assert "exported_at" in bundle
        assert "config" in bundle
        assert bundle["config"]["transcription"]["gemini_api_key"] == "test-key-123"

    def test_export_includes_google_token(self, config_file, config_dir, tmp_path):
        token_file = config_dir / "google_token.json"
        token_file.write_text(json.dumps({"refresh_token": "rt-123"}))
        dest = str(tmp_path / "export.json")

        with patch("meeting_recorder.config_transfer.CONFIG_FILE", config_file), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", token_file):
            result = export_config(dest)

        assert result == 0
        with open(dest) as f:
            bundle = json.load(f)
        assert bundle["google_token"]["refresh_token"] == "rt-123"

    def test_export_without_google_token(self, config_file, config_dir, tmp_path):
        dest = str(tmp_path / "export.json")
        with patch("meeting_recorder.config_transfer.CONFIG_FILE", config_file), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "nonexistent.json"):
            result = export_config(dest)

        assert result == 0
        with open(dest) as f:
            bundle = json.load(f)
        assert "google_token" not in bundle

    def test_export_no_config_returns_error(self, tmp_path):
        with patch("meeting_recorder.config_transfer.CONFIG_FILE", tmp_path / "nonexistent.toml"):
            result = export_config(str(tmp_path / "out.json"))
        assert result == 1

    def test_export_default_dest(self, config_file, config_dir, tmp_path):
        with patch("meeting_recorder.config_transfer.CONFIG_FILE", config_file), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "nope.json"), \
             patch("pathlib.Path.home", return_value=tmp_path):
            result = export_config(None)

        assert result == 0
        assert (tmp_path / "meeting_recorder_config.json").exists()

    def test_export_lists_api_keys(self, config_file, config_dir, tmp_path, capsys):
        dest = str(tmp_path / "export.json")
        with patch("meeting_recorder.config_transfer.CONFIG_FILE", config_file), \
             patch("meeting_recorder.config_transfer.CONFIG_DIR", config_dir), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", config_dir / "nope.json"):
            export_config(dest)

        output = capsys.readouterr().out
        assert "Gemini" in output
        assert "HuggingFace" in output
        assert "Summary API" in output


class TestImportConfig:
    def _make_bundle(self, tmp_path, config, google_token=None):
        bundle = {"version": 1, "config": config}
        if google_token:
            bundle["google_token"] = google_token
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle))
        return str(path)

    def test_import_writes_config(self, tmp_path, sample_config):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        bundle_path = self._make_bundle(tmp_path, sample_config)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", cfg_file), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"):
            result = import_config(bundle_path)

        assert result == 0
        assert cfg_file.exists()

        import sys
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib
        with open(cfg_file, "rb") as f:
            written = tomllib.load(f)

        # Machine-specific fields should be stripped
        assert "mic_device" not in written.get("audio", {})
        assert "position_x" not in written.get("dashboard", {})
        assert "position_y" not in written.get("dashboard", {})

        # Other fields should be preserved
        assert written["transcription"]["gemini_api_key"] == "test-key-123"
        assert written["diarization"]["huggingface_token"] == "hf-token-abc"

    def test_import_writes_google_token(self, tmp_path, sample_config):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        token_file = cfg_dir / "token.json"
        bundle_path = self._make_bundle(
            tmp_path, sample_config, google_token={"refresh_token": "rt-xyz"}
        )

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", cfg_dir / "config.toml"), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", token_file):
            result = import_config(bundle_path)

        assert result == 0
        assert token_file.exists()
        with open(token_file) as f:
            token = json.load(f)
        assert token["refresh_token"] == "rt-xyz"

    def test_import_existing_config_aborted(self, tmp_path, sample_config):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_file.write_bytes(b"[recording]\n")
        bundle_path = self._make_bundle(tmp_path, sample_config)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", cfg_file), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"), \
             patch("builtins.input", return_value="n"):
            result = import_config(bundle_path)

        assert result == 0  # abort is not an error

    def test_import_existing_config_overwritten(self, tmp_path, sample_config):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_file.write_bytes(b"[recording]\n")
        bundle_path = self._make_bundle(tmp_path, sample_config)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", cfg_file), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"), \
             patch("builtins.input", return_value="y"):
            result = import_config(bundle_path)

        assert result == 0
        assert cfg_file.stat().st_size > 20  # not just "[recording]\n"

    def test_import_file_not_found(self, tmp_path):
        result = import_config(str(tmp_path / "nope.json"))
        assert result == 1

    def test_import_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{")
        result = import_config(str(bad))
        assert result == 1

    def test_import_missing_config_key(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 1}))
        result = import_config(str(bad))
        assert result == 1

    def test_import_prints_next_steps(self, tmp_path, sample_config, capsys):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        bundle_path = self._make_bundle(tmp_path, sample_config)

        with patch("meeting_recorder.config_transfer.CONFIG_DIR", cfg_dir), \
             patch("meeting_recorder.config_transfer.CONFIG_FILE", cfg_dir / "config.toml"), \
             patch("meeting_recorder.config_transfer.TOKEN_FILE", cfg_dir / "token.json"):
            import_config(bundle_path)

        output = capsys.readouterr().out
        assert "diagnose" in output
        assert "Next steps" in output
