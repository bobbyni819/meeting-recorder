"""Tests for configurable diarization model with fallback."""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from meeting_recorder.transcription.diarization import SpeakerDiarizer


@pytest.fixture
def fake_pyannote(monkeypatch):
    """Install a fake pyannote.audio.Pipeline whose from_pretrained is mockable."""
    calls = []

    class FakePipeline:
        @staticmethod
        def from_pretrained(model_name, *args, **kwargs):
            calls.append(model_name)
            if model_name in fake_pyannote.fail_for:
                raise RuntimeError(f"not accepted on HF: {model_name}")
            return mock.MagicMock(name=f"pipeline:{model_name}")

    mod_audio = types.ModuleType("pyannote.audio")
    mod_audio.Pipeline = FakePipeline
    mod_pkg = types.ModuleType("pyannote")
    mod_pkg.audio = mod_audio
    monkeypatch.setitem(sys.modules, "pyannote", mod_pkg)
    monkeypatch.setitem(sys.modules, "pyannote.audio", mod_audio)
    fake_pyannote.calls = calls
    return fake_pyannote


fake_pyannote.fail_for = set()


def test_loads_configured_model(fake_pyannote):
    fake_pyannote.fail_for = set()
    d = SpeakerDiarizer(
        huggingface_token="hf_x",
        model="pyannote/speaker-diarization-community-1",
    )
    d.load()
    assert d.model == "pyannote/speaker-diarization-community-1"
    assert fake_pyannote.calls == ["pyannote/speaker-diarization-community-1"]


def test_falls_back_to_31_when_model_unavailable(fake_pyannote):
    fake_pyannote.fail_for = {"pyannote/speaker-diarization-community-1"}
    d = SpeakerDiarizer(
        huggingface_token="hf_x",
        model="pyannote/speaker-diarization-community-1",
    )
    d.load()
    # Tried the new model, fell through to the pinned 3.1 baseline
    assert fake_pyannote.calls == [
        "pyannote/speaker-diarization-community-1",
        "pyannote/speaker-diarization-3.1",
    ]
    assert d.model == "pyannote/speaker-diarization-3.1"
    assert d._pipeline is not None


def test_raises_when_no_model_loads(fake_pyannote):
    fake_pyannote.fail_for = {
        "pyannote/speaker-diarization-community-1",
        "pyannote/speaker-diarization-3.1",
    }
    d = SpeakerDiarizer(huggingface_token="hf_x")
    with pytest.raises(RuntimeError, match="No diarization model could be loaded"):
        d.load()


def test_default_model_is_fallback_when_empty(fake_pyannote):
    fake_pyannote.fail_for = set()
    d = SpeakerDiarizer(huggingface_token="hf_x", model="")
    assert d.model == "pyannote/speaker-diarization-3.1"
