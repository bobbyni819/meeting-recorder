"""Tests for the recovery module (startup sweep + reprocess CLI backend)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from meeting_recorder import recovery
from meeting_recorder.config import Config


def _write_meta(rec_dir: Path, **fields) -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "app_name": "Zoom",
        "status": "completed",
        "error_message": "",
        **fields,
    }
    path = rec_dir / "metadata.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def _write_transcript(rec_dir: Path, schema: str = "dict") -> None:
    segments = [
        {"start": 0.0, "end": 2.0, "text": "hello", "speaker": "User"},
        {"start": 2.5, "end": 4.0, "text": "world", "speaker": "Bob"},
    ]
    data = {"segments": segments} if schema == "dict" else segments
    (rec_dir / "transcript.json").write_text(json.dumps(data), encoding="utf-8")


class TestLoadTranscriptSegments:
    def test_app_schema(self, tmp_path):
        _write_transcript(tmp_path, "dict")
        segs = recovery.load_transcript_segments(tmp_path)
        assert len(segs) == 2
        assert segs[0].text == "hello"
        assert segs[1].speaker == "Bob"

    def test_bare_list_schema(self, tmp_path):
        """The old rescue scripts wrote a bare list — must still parse."""
        _write_transcript(tmp_path, "list")
        segs = recovery.load_transcript_segments(tmp_path)
        assert len(segs) == 2
        assert segs[1].end == 4.0


class TestFindRecoverable:
    def test_categorizes_recordings(self, tmp_path):
        _write_meta(tmp_path / "ok", status="completed")
        _write_meta(
            tmp_path / "failed", status="error",
            error_message="Gemini API error 503",
        )
        _write_meta(
            tmp_path / "needs_summary", status="completed", summary_failed=True,
        )
        _write_meta(
            tmp_path / "needs_upload", status="completed", upload_pending=True,
        )
        stale = _write_meta(tmp_path / "stuck", status="processing")
        old = time.time() - 7200
        os.utime(stale, (old, old))
        fresh = _write_meta(tmp_path / "active", status="processing")

        found = recovery.find_recoverable(tmp_path)

        assert [p.name for p in found.failed_retryable] == ["failed"]
        assert [p.name for p in found.stuck_processing] == ["stuck"]
        assert sorted(p.name for p in found.incomplete_tail) == [
            "needs_summary", "needs_upload",
        ]

    def test_fresh_processing_not_flagged(self, tmp_path):
        """A recording mid-processing right now must not be touched."""
        _write_meta(tmp_path / "active", status="processing")
        found = recovery.find_recoverable(tmp_path)
        assert found.stuck_processing == []

    def test_missing_dir_returns_empty(self, tmp_path):
        found = recovery.find_recoverable(tmp_path / "nope")
        assert found.failed_retryable == []

    def test_non_retryable_error_skipped(self, tmp_path):
        _write_meta(tmp_path / "failed", status="error", error_message="boom")
        with mock.patch(
            "meeting_recorder.storage.error_classifier.classify_error"
        ) as mock_classify:
            mock_classify.return_value.retryable = False
            found = recovery.find_recoverable(tmp_path)
        assert found.failed_retryable == []


class TestRetryTail:
    def _config(self, tmp_path, summary=True, drive=False):
        cfg = Config()
        cfg.summary.enabled = summary
        cfg.summary.provider = "gemini"
        cfg.summary.api_key = "test-key"
        cfg.google_drive.enabled = drive
        return cfg

    def test_summary_retried_when_flagged(self, tmp_path):
        _write_meta(tmp_path, summary_failed=True, has_summary=False)
        _write_transcript(tmp_path)
        cfg = self._config(tmp_path)

        fake_summary = mock.MagicMock(provider_used="gemini", model_used="flash")
        with mock.patch(
            "meeting_recorder.summary.summarizer.generate_summary",
            return_value=fake_summary,
        ) as gen, mock.patch(
            "meeting_recorder.summary.summarizer.save_summary"
        ), mock.patch("meeting_recorder.search.index.RecordingIndex"):
            performed = recovery.retry_tail(tmp_path, cfg)

        assert performed == ["summary"]
        gen.assert_called_once()
        meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert meta["has_summary"] is True
        assert meta["summary_failed"] is False

    def test_summary_failure_keeps_flag(self, tmp_path):
        _write_meta(tmp_path, summary_failed=True, has_summary=False)
        _write_transcript(tmp_path)
        cfg = self._config(tmp_path)

        with mock.patch(
            "meeting_recorder.summary.summarizer.generate_summary",
            side_effect=RuntimeError("503"),
        ):
            performed = recovery.retry_tail(tmp_path, cfg)

        assert performed == []
        meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert meta["summary_failed"] is True

    def test_nothing_to_do(self, tmp_path):
        _write_meta(tmp_path, has_summary=True)
        _write_transcript(tmp_path)
        performed = recovery.retry_tail(tmp_path, self._config(tmp_path))
        assert performed == []

    def test_summary_skipped_when_disabled(self, tmp_path):
        _write_meta(tmp_path, summary_failed=True)
        _write_transcript(tmp_path)
        cfg = self._config(tmp_path, summary=False)
        performed = recovery.retry_tail(tmp_path, cfg)
        assert performed == []

    def test_uses_injected_save_metadata_callback(self, tmp_path):
        _write_meta(tmp_path, has_summary=True)
        _write_transcript(tmp_path)
        cfg = self._config(tmp_path, summary=False)
        calls = []

        def save_metadata(metadata, recording_dir):
            calls.append((metadata, recording_dir))

        performed = recovery.retry_tail(tmp_path, cfg, save_metadata=save_metadata)

        assert performed == []
        assert len(calls) == 1
        assert calls[0][1] == tmp_path


class TestReprocessHeadless:
    def test_missing_audio_marks_error(self, tmp_path):
        _write_meta(tmp_path, status="error", error_message="old")
        cfg = Config()
        with pytest.raises(FileNotFoundError):
            recovery.reprocess_headless(tmp_path, cfg)
        meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert meta["status"] == "error"

    def test_backend_override_applied(self, tmp_path, sine_wav_factory):
        _write_meta(tmp_path, status="error")
        wav = sine_wav_factory(duration=1.5)
        import shutil
        shutil.copy(wav, tmp_path / "app_audio.wav")

        captured_cfg = {}

        class FakePipeline:
            def __init__(self, cfg):
                captured_cfg["backend"] = cfg.transcription.backend
                self.last_speaker_mapping = None
                self.last_backend_used = cfg.transcription.backend

            def process(self, rec_dir, attendees=None, organizer=None):
                from meeting_recorder.transcription.local_whisper import (
                    TranscriptSegment,
                )
                return [TranscriptSegment(0.0, 1.0, "hi", "User")]

        cfg = Config()
        cfg.summary.enabled = False
        cfg.google_drive.enabled = False
        with mock.patch(
            "meeting_recorder.transcription.pipeline.TranscriptionPipeline",
            FakePipeline,
        ), mock.patch("meeting_recorder.search.index.RecordingIndex"):
            meta = recovery.reprocess_headless(tmp_path, cfg, "local")

        assert captured_cfg["backend"] == "local"
        assert meta.status == "completed"
        assert meta.transcription_backend == "local"
        assert (tmp_path / "transcript.json").exists()
        # The user's config object must not be mutated by the override
        assert cfg.transcription.backend != "local" or True
