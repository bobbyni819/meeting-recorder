"""Tests for top-level CLI feature entry points."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from meeting_recorder.__main__ import main


def _config(api_key: str = ""):
    return SimpleNamespace(
        transcription=SimpleNamespace(
            gemini_api_key=api_key,
            gemini_model="test-model",
        )
    )


def test_export_markdown_cli_writes_file_and_exits_zero(tmp_path, monkeypatch, capsys):
    rec = tmp_path / "2026-03-10_09-00-00_Test"
    rec.mkdir()
    (rec / "metadata.json").write_text(
        json.dumps({"meeting_subject": "CLI Export"}),
        encoding="utf-8",
    )
    (rec / "transcript.txt").write_text("Alice: Export this note.", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["meeting_recorder", "export-markdown", str(rec)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    out_path = rec / f"{rec.name}.md"
    assert out_path.exists()
    assert "# CLI Export" in out_path.read_text(encoding="utf-8")
    assert str(out_path) in capsys.readouterr().out


def test_ask_cli_missing_gemini_key_exits_one(monkeypatch, capsys):
    from meeting_recorder.config import Config

    monkeypatch.setattr(
        sys,
        "argv",
        ["meeting_recorder", "ask", "What decisions were made?"],
    )
    monkeypatch.setattr(Config, "load", staticmethod(lambda: _config(api_key="")))

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "Cannot answer question" in output
    assert "gemini_api_key" in output
