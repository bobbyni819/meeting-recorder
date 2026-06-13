"""Tests for dictation mode: JSON parsing, pipeline file routing, frontmatter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from meeting_recorder.config import Config, DictationConfig, TranscriptionConfig
from meeting_recorder.dictation.pipeline import (
    _move_file,
    fallback_output_dir,
    finalize_recording,
    render_markdown,
    resolve_project_dir,
)
from meeting_recorder.transcription.gemini_transcriber import (
    DictationResult,
    _slugify,
    parse_dictation_response,
)


# ---------------------------------------------------------------------------
# parse_dictation_response()
# ---------------------------------------------------------------------------

class TestParseDictationResponse:
    PROJECTS = ["metabolism", "dcp", "ailab", "tools", "career"]

    def test_clean_json(self):
        raw = '{"transcript": "Hello world", "slug": "hello-world", "project": "tools"}'
        r = parse_dictation_response(raw, self.PROJECTS, "general", "gemini-2.5-flash")
        assert r.transcript == "Hello world"
        assert r.slug == "hello-world"
        assert r.project == "tools"
        assert r.model == "gemini-2.5-flash"

    def test_markdown_fenced_json(self):
        raw = '```json\n{"transcript": "a", "slug": "one two three", "project": "dcp"}\n```'
        r = parse_dictation_response(raw, self.PROJECTS, "general", "m")
        assert r.transcript == "a"
        assert r.slug == "one-two-three"
        assert r.project == "dcp"

    def test_project_not_in_list_falls_back_to_default(self):
        raw = '{"transcript": "x", "slug": "a-b-c", "project": "astronomy"}'
        r = parse_dictation_response(raw, self.PROJECTS, "general", "m")
        assert r.project == "general"

    def test_default_project_is_always_allowed(self):
        raw = '{"transcript": "x", "slug": "a-b-c", "project": "general"}'
        r = parse_dictation_response(raw, self.PROJECTS, "general", "m")
        assert r.project == "general"

    def test_malformed_json_keeps_transcript_raw(self):
        raw = "not valid json at all, just a blob of text"
        r = parse_dictation_response(raw, self.PROJECTS, "general", "m")
        assert "not valid json" in r.transcript
        assert r.project == "general"
        assert r.slug

    def test_missing_slug_derives_from_transcript(self):
        raw = '{"transcript": "Grant deadline is next Friday", "project": "career"}'
        r = parse_dictation_response(raw, self.PROJECTS, "general", "m")
        assert r.slug == "grant-deadline-is"
        assert r.project == "career"

    def test_extra_prose_around_json(self):
        raw = 'Sure! Here is your JSON: {"transcript": "hi", "slug": "greet", "project": "tools"}'
        r = parse_dictation_response(raw, self.PROJECTS, "general", "m")
        assert r.transcript == "hi"
        assert r.project == "tools"


class TestSlugify:
    def test_normal_case(self):
        assert _slugify("Hello World Foo") == "hello-world-foo"

    def test_punctuation_stripped(self):
        assert _slugify("Fig4: Simpson's Paradox!") == "fig4-simpsons-paradox"

    def test_empty_input(self):
        assert _slugify("") == "untitled"

    def test_takes_first_three_words(self):
        assert _slugify("one two three four five") == "one-two-three"


# ---------------------------------------------------------------------------
# resolve_project_dir()
# ---------------------------------------------------------------------------

class TestResolveProjectDir:
    TEMPLATE = "{project}/Sources/voice-memos"

    def test_project_folder_exists_routes_to_project(self, tmp_path):
        (tmp_path / "metabolism").mkdir()
        when = datetime(2026, 4, 21, 14, 37)
        result = resolve_project_dir(
            tmp_path, "metabolism", self.TEMPLATE, when, "general"
        )
        assert result == tmp_path / "metabolism" / "Sources" / "voice-memos" / "2026-04-21"

    def test_project_folder_missing_falls_back(self, tmp_path):
        # No "ailab" folder created
        when = datetime(2026, 4, 21, 14, 37)
        result = resolve_project_dir(
            tmp_path, "ailab", self.TEMPLATE, when, "general"
        )
        assert result == tmp_path / "voice-memos" / "2026-04-21"

    def test_general_project_uses_fallback(self, tmp_path):
        # Even if a "general" folder exists, default_project routes to fallback
        (tmp_path / "general").mkdir()
        when = datetime(2026, 4, 21, 14, 37)
        result = resolve_project_dir(
            tmp_path, "general", self.TEMPLATE, when, "general"
        )
        assert result == tmp_path / "voice-memos" / "2026-04-21"

    def test_empty_project_uses_fallback(self, tmp_path):
        when = datetime(2026, 4, 21, 14, 37)
        result = resolve_project_dir(
            tmp_path, "", self.TEMPLATE, when, "general"
        )
        assert result == tmp_path / "voice-memos" / "2026-04-21"

    def test_malformed_template_falls_back(self, tmp_path):
        (tmp_path / "metabolism").mkdir()
        when = datetime(2026, 4, 21, 14, 37)
        # Unknown placeholder in template — format() will raise KeyError
        result = resolve_project_dir(
            tmp_path, "metabolism", "{bogus}/voice-memos", when, "general"
        )
        assert result == tmp_path / "voice-memos" / "2026-04-21"

    def test_fallback_output_dir(self, tmp_path):
        when = datetime(2026, 4, 21, 14, 37, 22)
        assert fallback_output_dir(tmp_path, when) == tmp_path / "voice-memos" / "2026-04-21"


# ---------------------------------------------------------------------------
# render_markdown()
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def test_frontmatter_and_body(self):
        result = DictationResult(
            transcript="Test body.\n\nSecond paragraph.",
            slug="my-slug",
            project="dcp",
            model="gemini-2.5-flash",
        )
        md = render_markdown(
            result=result,
            audio_filename="1437-my-slug.wav",
            recorded_at=datetime(2026, 4, 21, 14, 37, 22),
            duration_seconds=47.3,
        )
        assert md.startswith("---\n")
        assert "mode: dictation\n" in md
        assert "recorded_at: 2026-04-21T14:37:22\n" in md
        assert "duration_seconds: 47.3\n" in md
        assert "audio_file: 1437-my-slug.wav\n" in md
        assert "slug: my-slug\n" in md
        assert "project: dcp\n" in md
        assert "transcription_model: gemini-2.5-flash\n" in md
        assert md.endswith("Test body.\n\nSecond paragraph.\n")


# ---------------------------------------------------------------------------
# finalize_recording()
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, gemini_key: str = "test-key") -> Config:
    config = Config()
    config.transcription = TranscriptionConfig(
        backend="gemini",
        gemini_api_key=gemini_key,
        gemini_model="gemini-2.5-flash",
    )
    config.dictation = DictationConfig(
        enabled=True,
        drive_root=str(tmp_path),
        project_list=["metabolism", "dcp", "tools"],
        default_project="general",
        project_subpath_template="{project}/Sources/voice-memos",
    )
    return config


class TestFinalizeRecording:
    def test_success_routes_to_project_folder(self, tmp_path):
        # Simulate existing project folder
        (tmp_path / "metabolism").mkdir()

        temp_wav = tmp_path / "temp.wav"
        temp_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")

        config = _make_config(tmp_path)
        when = datetime(2026, 4, 21, 14, 37, 22)

        fake_result = DictationResult(
            transcript="Hello world.",
            slug="fig4-simpsons-paradox",
            project="metabolism",
            model="gemini-2.5-flash",
        )
        with patch(
            "meeting_recorder.dictation.pipeline.GeminiTranscriber.transcribe_dictation",
            return_value=fake_result,
        ):
            outcome = finalize_recording(
                temp_audio=temp_wav,
                config=config,
                recorded_at=when,
                duration_seconds=47.0,
            )

        assert outcome.error_path is None
        expected_dir = tmp_path / "metabolism" / "Sources" / "voice-memos" / "2026-04-21"
        expected_audio = expected_dir / "1437-fig4-simpsons-paradox.wav"
        expected_md = expected_dir / "1437-fig4-simpsons-paradox.md"
        assert outcome.audio_path == expected_audio
        assert outcome.transcript_path == expected_md
        assert expected_audio.exists()
        assert expected_md.exists()
        # Original temp file has been moved away
        assert not temp_wav.exists()

        md_text = expected_md.read_text(encoding="utf-8")
        assert "slug: fig4-simpsons-paradox" in md_text
        assert "project: metabolism" in md_text
        assert "Hello world." in md_text

    def test_success_falls_back_when_project_folder_missing(self, tmp_path):
        # No ailab folder
        temp_wav = tmp_path / "temp.wav"
        temp_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")

        config = _make_config(tmp_path)
        when = datetime(2026, 4, 21, 14, 37, 22)

        fake_result = DictationResult(
            transcript="Some ailab talk.",
            slug="model-training-plan",
            project="ailab",
            model="gemini-2.5-flash",
        )
        with patch(
            "meeting_recorder.dictation.pipeline.GeminiTranscriber.transcribe_dictation",
            return_value=fake_result,
        ):
            outcome = finalize_recording(
                temp_audio=temp_wav,
                config=config,
                recorded_at=when,
                duration_seconds=12.0,
            )

        assert outcome.error_path is None
        expected_dir = tmp_path / "voice-memos" / "2026-04-21"
        assert outcome.audio_path == expected_dir / "1437-model-training-plan.wav"
        assert outcome.audio_path.exists()
        assert outcome.transcript_path.exists()

    def test_success_with_general_project_uses_fallback(self, tmp_path):
        # Even if a "general" folder exists, default_project always routes flat
        (tmp_path / "general").mkdir()
        temp_wav = tmp_path / "temp.wav"
        temp_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")

        config = _make_config(tmp_path)
        when = datetime(2026, 4, 21, 14, 37, 22)

        fake_result = DictationResult(
            transcript="Random thought.",
            slug="random-thought-now",
            project="general",
            model="gemini-2.5-flash",
        )
        with patch(
            "meeting_recorder.dictation.pipeline.GeminiTranscriber.transcribe_dictation",
            return_value=fake_result,
        ):
            outcome = finalize_recording(
                temp_audio=temp_wav,
                config=config,
                recorded_at=when,
                duration_seconds=5.0,
            )

        assert outcome.audio_path.parent == tmp_path / "voice-memos" / "2026-04-21"

    def test_transcription_failure_writes_error_sidecar(self, tmp_path):
        (tmp_path / "metabolism").mkdir()
        temp_wav = tmp_path / "temp.wav"
        temp_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")

        config = _make_config(tmp_path)
        when = datetime(2026, 4, 21, 14, 37, 22)

        with patch(
            "meeting_recorder.dictation.pipeline.GeminiTranscriber.transcribe_dictation",
            side_effect=RuntimeError("429 resource exhausted"),
        ):
            outcome = finalize_recording(
                temp_audio=temp_wav,
                config=config,
                recorded_at=when,
                duration_seconds=47.0,
            )

        assert outcome.result is None
        assert outcome.transcript_path is None
        assert outcome.error_path is not None
        assert outcome.error_path.exists()
        # Audio kept under staged name in fallback dir
        assert outcome.audio_path.exists()
        assert outcome.audio_path.name == "1437-recording.wav"
        assert outcome.audio_path.parent == tmp_path / "voice-memos" / "2026-04-21"
        assert "429" in outcome.error_path.read_text(encoding="utf-8")

    def test_missing_api_key_writes_error_sidecar(self, tmp_path):
        temp_wav = tmp_path / "temp.wav"
        temp_wav.write_bytes(b"fake wav bytes")

        config = _make_config(tmp_path, gemini_key="")
        when = datetime(2026, 4, 21, 14, 37, 22)

        outcome = finalize_recording(
            temp_audio=temp_wav,
            config=config,
            recorded_at=when,
            duration_seconds=5.0,
        )

        assert outcome.result is None
        assert outcome.error_path is not None
        assert "gemini_api_key" in outcome.error_path.read_text(encoding="utf-8")
        assert outcome.audio_path.exists()
        assert outcome.audio_path.parent == tmp_path / "voice-memos" / "2026-04-21"

    def test_markdown_write_failure_keeps_audio_and_writes_error(
        self, tmp_path, monkeypatch,
    ):
        temp_wav = tmp_path / "temp.wav"
        temp_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")

        config = _make_config(tmp_path)
        when = datetime(2026, 4, 21, 14, 37, 22)
        fake_result = DictationResult(
            transcript="Hello world.",
            slug="finalize-fails-here",
            project="general",
            model="gemini-2.5-flash",
        )
        original_write_text = Path.write_text

        def fail_md_write(path, *args, **kwargs):
            if path.suffix == ".md":
                raise OSError("disk full")
            return original_write_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_md_write)
        with patch(
            "meeting_recorder.dictation.pipeline.GeminiTranscriber.transcribe_dictation",
            return_value=fake_result,
        ):
            outcome = finalize_recording(
                temp_audio=temp_wav,
                config=config,
                recorded_at=when,
                duration_seconds=47.0,
            )

        assert outcome.result is None
        assert outcome.transcript_path is None
        assert outcome.audio_path.exists()
        assert outcome.audio_path.name == "1437-finalize-fails-here.wav"
        assert outcome.error_path == outcome.audio_path.with_suffix(".error")
        assert outcome.error_path.exists()
        assert "disk full" in outcome.error_path.read_text(encoding="utf-8")
        assert not temp_wav.exists()

    def test_move_file_noop_when_source_equals_destination(self, tmp_path):
        wav = tmp_path / "same.wav"
        wav.write_bytes(b"audio")

        _move_file(wav, wav)

        assert wav.read_bytes() == b"audio"


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------

class TestConfigLoadsDictation:
    def test_default_dictation_config(self):
        config = Config()
        assert config.dictation.enabled is False
        assert config.dictation.hotkey == "ctrl+shift+v"
        assert "metabolism" in config.dictation.project_list
        assert config.dictation.default_project == "general"
        assert config.dictation.project_subpath_template == "{project}/Sources/voice-memos"

    def test_dictation_inherits_transcription_model_when_empty(self):
        config = Config()
        config.transcription.gemini_model = "gemini-2.5-flash"
        config.dictation.gemini_model = ""
        effective = config.dictation.gemini_model or config.transcription.gemini_model
        assert effective == "gemini-2.5-flash"
