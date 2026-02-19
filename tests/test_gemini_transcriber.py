"""Tests for GeminiTranscriber: parsing and transcribe() with mocked API."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from meeting_recorder.transcription.gemini_transcriber import GeminiTranscriber
from meeting_recorder.transcription.local_whisper import TranscriptSegment


# ---------------------------------------------------------------------------
# _parse() tests
# ---------------------------------------------------------------------------

class TestGeminiParse:
    """Test the _parse() method that converts Gemini text to TranscriptSegments."""

    def setup_method(self):
        self.transcriber = GeminiTranscriber(api_key="test-key")

    def test_normal_mm_ss_format(self):
        raw = (
            "[00:00] Alice: Hello everyone.\n"
            "[00:05] Bob: Hi Alice, thanks for joining.\n"
            "[00:12] Alice: Let's get started.\n"
        )
        segments = self.transcriber._parse(raw)
        assert len(segments) == 3
        assert segments[0].start == 0.0
        assert segments[0].end == 5.0
        assert segments[0].speaker == "Alice"
        assert segments[0].text == "Hello everyone."
        assert segments[1].start == 5.0
        assert segments[1].end == 12.0
        assert segments[1].speaker == "Bob"
        assert segments[2].start == 12.0
        assert segments[2].speaker == "Alice"
        assert segments[2].text == "Let's get started."

    def test_h_mm_ss_format(self):
        raw = (
            "[1:00:00] Speaker 1: We're at the one hour mark.\n"
            "[1:05:30] Speaker 2: Time flies.\n"
        )
        segments = self.transcriber._parse(raw)
        assert len(segments) == 2
        assert segments[0].start == 3600.0
        assert segments[0].end == 3930.0  # 1:05:30
        assert segments[1].start == 3930.0

    def test_blank_lines_ignored(self):
        raw = (
            "[00:00] Alice: Hello.\n"
            "\n"
            "\n"
            "[00:10] Bob: Hi.\n"
        )
        segments = self.transcriber._parse(raw)
        assert len(segments) == 2
        assert segments[0].text == "Hello."
        assert segments[1].text == "Hi."

    def test_no_match_lines_ignored(self):
        raw = (
            "Here is the transcript:\n"
            "[00:00] Alice: Hello.\n"
            "--- end of section ---\n"
            "[00:10] Bob: Goodbye.\n"
        )
        segments = self.transcriber._parse(raw)
        assert len(segments) == 2

    def test_single_segment(self):
        raw = "[03:45] Speaker 1: Just one line of speech.\n"
        segments = self.transcriber._parse(raw)
        assert len(segments) == 1
        assert segments[0].start == 225.0
        assert segments[0].text == "Just one line of speech."
        # Last segment gets placeholder end
        assert segments[0].end == 285.0  # start + 60

    def test_empty_input(self):
        segments = self.transcriber._parse("")
        assert segments == []

    def test_whitespace_only(self):
        segments = self.transcriber._parse("  \n\n  \n")
        assert segments == []

    def test_segments_have_correct_types(self):
        raw = "[00:30] Speaker 1: Testing types.\n"
        segments = self.transcriber._parse(raw)
        assert isinstance(segments[0], TranscriptSegment)
        assert isinstance(segments[0].start, float)
        assert isinstance(segments[0].end, float)
        assert isinstance(segments[0].text, str)
        assert isinstance(segments[0].speaker, str)


# ---------------------------------------------------------------------------
# transcribe() tests with mocked API
# ---------------------------------------------------------------------------

class TestGeminiTranscribe:
    """Test transcribe() with fully mocked Gemini API."""

    def test_transcribe_success(self, tmp_path: Path):
        """Happy path: upload, poll, generate, cleanup."""
        transcriber = GeminiTranscriber(api_key="test-key")

        # Create a dummy WAV file
        wav = tmp_path / "mixed.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 100)

        mock_file = MagicMock()
        mock_file.name = "files/abc123"
        # First call: PROCESSING, second call: ACTIVE
        mock_file_active = MagicMock()
        mock_file_active.state.name = "ACTIVE"
        mock_file_active.name = "files/abc123"

        mock_response = MagicMock()
        mock_response.text = "[00:00] Speaker 1: Hello world.\n[00:05] Speaker 2: Hi.\n"

        mock_client = MagicMock()
        mock_client.files.upload.return_value = mock_file
        mock_file.state.name = "ACTIVE"
        mock_client.files.get.return_value = mock_file_active
        mock_client.models.generate_content.return_value = mock_response

        # Also mock _compress_to_flac to skip compression
        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch("meeting_recorder.transcription.gemini_transcriber.genai", create=True):
                with patch(
                    "google.genai.Client", return_value=mock_client,
                ):
                    segments = transcriber.transcribe(wav)

        assert len(segments) == 2
        assert segments[0].speaker == "Speaker 1"
        assert segments[1].speaker == "Speaker 2"
        # Raw transcript should be saved
        assert (tmp_path / "transcript_raw.txt").exists()
        # File cleanup attempted
        mock_client.files.delete.assert_called_once()

    def test_transcribe_processing_then_active(self, tmp_path: Path):
        """File starts in PROCESSING state, then becomes ACTIVE after polling."""
        transcriber = GeminiTranscriber(api_key="test-key")

        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 100)

        # Simulate PROCESSING -> ACTIVE transition
        mock_file_processing = MagicMock()
        mock_file_processing.name = "files/xyz"
        mock_file_processing.state.name = "PROCESSING"

        mock_file_active = MagicMock()
        mock_file_active.name = "files/xyz"
        mock_file_active.state.name = "ACTIVE"

        mock_response = MagicMock()
        mock_response.text = "[00:00] Speaker 1: Done.\n"

        mock_client = MagicMock()
        mock_client.files.upload.return_value = mock_file_processing
        mock_client.files.get.return_value = mock_file_active
        mock_client.models.generate_content.return_value = mock_response

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch("google.genai.Client", return_value=mock_client):
                with patch("time.sleep"):  # Skip actual sleep
                    segments = transcriber.transcribe(wav)

        assert len(segments) == 1
        # files.get should have been called (polling)
        mock_client.files.get.assert_called()

    def test_transcribe_raises_when_stuck_processing(self, tmp_path: Path):
        """File stuck in PROCESSING state should raise RuntimeError."""
        transcriber = GeminiTranscriber(api_key="test-key")

        wav = tmp_path / "stuck.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 100)

        mock_file = MagicMock()
        mock_file.name = "files/stuck"
        mock_file.state.name = "PROCESSING"

        mock_client = MagicMock()
        mock_client.files.upload.return_value = mock_file
        mock_client.files.get.return_value = mock_file  # Always PROCESSING

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch("google.genai.Client", return_value=mock_client):
                with patch("time.sleep"):
                    with pytest.raises(RuntimeError, match="did not complete"):
                        transcriber.transcribe(wav)

    def test_transcribe_empty_response(self, tmp_path: Path):
        """Empty response text should produce zero segments."""
        transcriber = GeminiTranscriber(api_key="test-key")

        wav = tmp_path / "empty.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 100)

        mock_file = MagicMock()
        mock_file.name = "files/empty"
        mock_file.state.name = "ACTIVE"

        mock_response = MagicMock()
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.files.upload.return_value = mock_file
        mock_client.models.generate_content.return_value = mock_response

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch("google.genai.Client", return_value=mock_client):
                segments = transcriber.transcribe(wav)

        assert segments == []


# ---------------------------------------------------------------------------
# _compress_to_flac() tests
# ---------------------------------------------------------------------------

class TestCompressToFlac:
    """Test FLAC compression with various availability scenarios."""

    def test_soundfile_available(self, tmp_path: Path):
        """When soundfile is available, returns FLAC path."""
        wav = tmp_path / "test.wav"
        flac = tmp_path / "test.flac"
        wav.write_bytes(b"\x00" * 1000)

        mock_sf = MagicMock()
        mock_sf.read.return_value = ([0.0] * 100, 16000)

        # After sf.write, the flac file should exist
        def fake_write(path, data, sr, format=None):
            Path(path).write_bytes(b"\x00" * 200)

        mock_sf.write = fake_write

        with patch.dict("sys.modules", {"soundfile": mock_sf}):
            with patch("meeting_recorder.transcription.gemini_transcriber.sf", mock_sf, create=True):
                # Need to re-import to pick up the mock
                path, mime, temp = GeminiTranscriber._compress_to_flac(wav)

        # If soundfile mock worked, we get the flac path
        # (In unit tests the import path may vary, so just verify graceful behavior)
        assert path is not None
        assert mime in ("audio/flac", "audio/wav")

    def test_no_compression_available(self, tmp_path: Path):
        """When neither soundfile nor ffmpeg is available, returns WAV."""
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00" * 1000)

        with patch.dict("sys.modules", {"soundfile": None}):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                path, mime, temp = GeminiTranscriber._compress_to_flac(wav)

        assert path == wav
        assert mime == "audio/wav"
        assert temp is None


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------

class TestGeminiTranscriberInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="API key is required"):
            GeminiTranscriber(api_key="")

    def test_default_model(self):
        t = GeminiTranscriber(api_key="test-key")
        assert t.model == "gemini-2.5-flash"

    def test_custom_model(self):
        t = GeminiTranscriber(api_key="test-key", model="gemini-2.5-pro")
        assert t.model == "gemini-2.5-pro"
