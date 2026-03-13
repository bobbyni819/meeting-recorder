"""Tests for error classification."""

from __future__ import annotations

import pytest

from meeting_recorder.storage.error_classifier import (
    classify_error,
    format_error,
    ErrorClassification,
)


class TestClassifyError:
    def test_empty_message(self):
        ec = classify_error("")
        assert ec.category == "unknown"
        assert ec.retryable is True

    def test_corrupt_audio(self):
        ec = classify_error("App audio file corrupt or empty")
        assert ec.category == "audio"
        assert "audio" in ec.title.lower()
        assert ec.retryable is False

    def test_no_audio_device(self):
        ec = classify_error("No audio device found for recording")
        assert ec.category == "audio"

    def test_pyaudio_error(self):
        ec = classify_error("PyAudio stream error: [Errno -9999]")
        assert ec.category == "audio"
        assert ec.retryable is True

    def test_silence_detected(self):
        ec = classify_error("No speech detected in recording")
        assert ec.category == "audio"
        assert "speech" in ec.title.lower() or "silence" in ec.title.lower()

    def test_whisper_failure(self):
        ec = classify_error("Whisper transcription failed: model error")
        assert ec.category == "transcription"
        assert ec.retryable is True

    def test_model_not_found(self):
        ec = classify_error("Model weights not found: large-v3")
        assert ec.category == "transcription"

    def test_gemini_api(self):
        ec = classify_error("Gemini API quota limit exceeded")
        assert ec.category == "transcription"
        assert "gemini" in ec.title.lower()

    def test_openai_api(self):
        ec = classify_error("OpenAI API timeout after 30s")
        assert ec.category == "transcription"
        assert "openai" in ec.title.lower()

    def test_cuda_oom(self):
        ec = classify_error("CUDA out of memory. Tried to allocate 2.00 GiB")
        assert ec.category == "gpu"
        assert ec.retryable is True

    def test_torch_error(self):
        ec = classify_error("RuntimeError: CUBLAS_STATUS_NOT_INITIALIZED")
        assert ec.category == "gpu"

    def test_diarization_error(self):
        ec = classify_error("pyannote diarization failed: invalid token")
        assert ec.category == "diarization"

    def test_summary_error(self):
        ec = classify_error("Summary generation error: API key invalid")
        assert ec.category == "summary"

    def test_network_error(self):
        ec = classify_error("Connection timed out after 30 seconds")
        assert ec.category == "network"
        assert ec.retryable is True

    def test_storage_error(self):
        ec = classify_error("Permission denied: cannot write to disk")
        assert ec.category == "storage"

    def test_screen_capture_error(self):
        ec = classify_error("Screen capture failed: PrintWindow returned empty")
        assert ec.category == "video"

    def test_unknown_error(self):
        ec = classify_error("Something completely unexpected happened")
        assert ec.category == "unknown"
        assert ec.retryable is True
        assert len(ec.suggestions) >= 1

    def test_case_insensitive(self):
        ec = classify_error("CUDA OUT OF MEMORY error")
        assert ec.category == "gpu"

    def test_suggestions_not_empty(self):
        for msg in [
            "corrupt audio", "CUDA OOM", "network timeout",
            "whisper failed", "something weird",
        ]:
            ec = classify_error(msg)
            assert len(ec.suggestions) >= 1, f"No suggestions for: {msg}"


class TestFormatError:
    def test_basic_format(self):
        ec = ErrorClassification(
            category="audio",
            title="Test Error",
            explanation="Something went wrong.",
            suggestions=["Fix it", "Try again"],
            retryable=True,
        )
        text = format_error(ec)
        assert "[AUDIO]" in text
        assert "Test Error" in text
        assert "Fix it" in text
        assert "re-processing" in text.lower()

    def test_non_retryable(self):
        ec = ErrorClassification(
            category="storage",
            title="Disk Full",
            explanation="No space left.",
            suggestions=["Free up space"],
            retryable=False,
        )
        text = format_error(ec)
        assert "re-processing" not in text.lower()
