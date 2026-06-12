"""Tests for the diagnose secrets check (key presence without values)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import tomli_w

from meeting_recorder.diagnose import (
    _check_secrets,
    _check_secrets_structured,
    _SECRET_KEYS,
)

# Sentinel values that must NEVER appear in any diagnose output.
GEMINI_VALUE = "AIza-SUPER-SECRET-gemini-value"
OPENAI_VALUE = "sk-SUPER-SECRET-openai-value"
HF_VALUE = "hf_SUPER-SECRET-hf-value"
SUMMARY_VALUE = "sum-SUPER-SECRET-summary-value"


@pytest.fixture
def secrets_file(tmp_path):
    """A secrets.toml with all four known keys set."""
    path = tmp_path / "secrets.toml"
    with open(path, "wb") as f:
        tomli_w.dump({
            "transcription": {
                "gemini_api_key": GEMINI_VALUE,
                "openai_api_key": OPENAI_VALUE,
            },
            "diarization": {"huggingface_token": HF_VALUE},
            "summary": {"api_key": SUMMARY_VALUE},
        }, f)
    return path


@pytest.fixture
def partial_secrets_file(tmp_path):
    """A secrets.toml with only the Gemini key set."""
    path = tmp_path / "secrets.toml"
    with open(path, "wb") as f:
        tomli_w.dump({
            "transcription": {"gemini_api_key": GEMINI_VALUE, "openai_api_key": ""},
        }, f)
    return path


@pytest.fixture
def empty_secrets_file(tmp_path):
    """A secrets.toml where every known key is empty."""
    path = tmp_path / "secrets.toml"
    with open(path, "wb") as f:
        tomli_w.dump({
            "transcription": {"gemini_api_key": "", "openai_api_key": ""},
            "diarization": {"huggingface_token": ""},
        }, f)
    return path


def _assert_no_secret_values(text: str) -> None:
    for value in (GEMINI_VALUE, OPENAI_VALUE, HF_VALUE, SUMMARY_VALUE):
        assert value not in text


class TestSecretKeysConstant:
    def test_covers_all_documented_keys(self):
        pairs = {(section, key) for section, key, _ in _SECRET_KEYS}
        assert ("transcription", "gemini_api_key") in pairs
        assert ("transcription", "openai_api_key") in pairs
        assert ("diarization", "huggingface_token") in pairs
        assert ("summary", "api_key") in pairs


class TestCheckSecretsCli:
    def test_missing_file_warns_with_migration_pointer(self, tmp_path, capsys):
        with patch("meeting_recorder.config.SECRETS_FILE", tmp_path / "nope.toml"):
            failures = _check_secrets()

        assert failures == 0  # missing secrets is a warn, not a fail
        output = capsys.readouterr().out
        assert "[WARN]" in output
        assert "SETUP.md" in output
        assert "import-config" in output

    def test_all_keys_set(self, secrets_file, capsys):
        with patch("meeting_recorder.config.SECRETS_FILE", secrets_file):
            failures = _check_secrets()

        assert failures == 0
        output = capsys.readouterr().out
        assert str(secrets_file) in output
        assert output.count("SET") == 4
        assert "EMPTY" not in output
        assert "[WARN]" not in output

    def test_partial_keys_show_set_and_empty(self, partial_secrets_file, capsys):
        with patch("meeting_recorder.config.SECRETS_FILE", partial_secrets_file):
            failures = _check_secrets()

        assert failures == 0
        output = capsys.readouterr().out
        assert "gemini_api_key): SET" in output
        assert "openai_api_key): EMPTY" in output
        assert "huggingface_token): EMPTY" in output

    def test_all_keys_empty_warns(self, empty_secrets_file, capsys):
        with patch("meeting_recorder.config.SECRETS_FILE", empty_secrets_file):
            failures = _check_secrets()

        assert failures == 0
        output = capsys.readouterr().out
        assert "[WARN]" in output
        assert "EMPTY" in output

    def test_never_prints_key_values(self, secrets_file, capsys):
        with patch("meeting_recorder.config.SECRETS_FILE", secrets_file):
            _check_secrets()
        _assert_no_secret_values(capsys.readouterr().out)

    def test_corrupted_toml_fails(self, tmp_path, capsys):
        bad = tmp_path / "secrets.toml"
        bad.write_text("this is not = [valid toml {{{")
        with patch("meeting_recorder.config.SECRETS_FILE", bad):
            failures = _check_secrets()

        assert failures == 1
        output = capsys.readouterr().out
        assert "[FAIL]" in output


class TestCheckSecretsStructured:
    def test_category_name(self, secrets_file):
        with patch("meeting_recorder.config.SECRETS_FILE", secrets_file):
            cat = _check_secrets_structured()
        assert cat.name == "Secrets"

    def test_missing_file_is_warn(self, tmp_path):
        with patch("meeting_recorder.config.SECRETS_FILE", tmp_path / "nope.toml"):
            cat = _check_secrets_structured()

        assert cat.status == "warn"
        assert len(cat.results) == 1
        assert "SETUP.md" in cat.results[0].message
        assert "import-config" in cat.results[0].message

    def test_all_keys_set_is_ok(self, secrets_file):
        with patch("meeting_recorder.config.SECRETS_FILE", secrets_file):
            cat = _check_secrets_structured()

        assert cat.status == "ok"
        # 1 file-found result + one per known key
        assert len(cat.results) == 1 + len(_SECRET_KEYS)
        messages = "\n".join(r.message for r in cat.results)
        assert messages.count("SET") == 4

    def test_partial_keys_statuses(self, partial_secrets_file):
        with patch("meeting_recorder.config.SECRETS_FILE", partial_secrets_file):
            cat = _check_secrets_structured()

        messages = "\n".join(r.message for r in cat.results)
        assert "gemini_api_key): SET" in messages
        assert "openai_api_key): EMPTY" in messages
        assert "api_key): EMPTY" in messages  # summary section absent => EMPTY

    def test_all_empty_adds_warning(self, empty_secrets_file):
        with patch("meeting_recorder.config.SECRETS_FILE", empty_secrets_file):
            cat = _check_secrets_structured()

        assert cat.status == "warn"
        warn_messages = [r.message for r in cat.results if r.status == "warn"]
        assert any("EMPTY" in m for m in warn_messages)

    def test_never_includes_key_values(self, secrets_file):
        with patch("meeting_recorder.config.SECRETS_FILE", secrets_file):
            cat = _check_secrets_structured()
        _assert_no_secret_values("\n".join(r.message for r in cat.results))

    def test_corrupted_toml_is_fail(self, tmp_path):
        bad = tmp_path / "secrets.toml"
        bad.write_text("this is not = [valid toml {{{")
        with patch("meeting_recorder.config.SECRETS_FILE", bad):
            cat = _check_secrets_structured()
        assert cat.status == "fail"
