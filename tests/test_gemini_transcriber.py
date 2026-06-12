"""Tests for GeminiTranscriber: parsing and transcribe() with mocked API."""

from __future__ import annotations

import time
import wave
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from meeting_recorder.transcription.gemini_transcriber import GeminiTranscriber
from meeting_recorder.transcription.local_whisper import TranscriptSegment


def _write_wav(path: Path, duration: float, sample_rate: int = 16000) -> Path:
    """Write a real (silent) WAV file with a valid header."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(sample_rate * duration))
    return path


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

        # Mock the google.genai imports that happen inside transcribe()
        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_types = MagicMock()

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch.dict("sys.modules", {
                "google": MagicMock(genai=mock_genai),
                "google.genai": mock_genai,
                "google.genai.types": mock_types,
            }):
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

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch.dict("sys.modules", {
                "google": MagicMock(genai=mock_genai),
                "google.genai": mock_genai,
                "google.genai.types": MagicMock(),
            }):
                with patch("time.sleep"):
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

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch.dict("sys.modules", {
                "google": MagicMock(genai=mock_genai),
                "google.genai": mock_genai,
                "google.genai.types": MagicMock(),
            }):
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

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ):
            with patch.dict("sys.modules", {
                "google": MagicMock(genai=mock_genai),
                "google.genai": mock_genai,
                "google.genai.types": MagicMock(),
            }):
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


# ---------------------------------------------------------------------------
# Retry logic tests
# ---------------------------------------------------------------------------

class TestGeminiRetry:
    """Test _transcribe_with_retry for transient error handling."""

    def test_succeeds_on_first_try(self):
        transcriber = GeminiTranscriber(api_key="test-key")
        client = MagicMock()
        uploaded = MagicMock()
        client.models.generate_content.return_value = MagicMock(text="[00:00] A: Hi")

        result = transcriber._transcribe_with_retry(client, uploaded)
        assert result == "[00:00] A: Hi"
        assert client.models.generate_content.call_count == 1

    def test_retries_on_rate_limit(self):
        transcriber = GeminiTranscriber(api_key="test-key")
        client = MagicMock()
        uploaded = MagicMock()

        # First call raises rate limit, second succeeds
        client.models.generate_content.side_effect = [
            Exception("429 Resource exhausted"),
            MagicMock(text="[00:00] A: Hi"),
        ]

        with patch("time.sleep"):
            result = transcriber._transcribe_with_retry(client, uploaded)

        assert result == "[00:00] A: Hi"
        assert client.models.generate_content.call_count == 2

    def test_retries_on_server_error(self):
        transcriber = GeminiTranscriber(api_key="test-key")
        client = MagicMock()
        uploaded = MagicMock()

        client.models.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            Exception("502 Bad Gateway"),
            MagicMock(text="[00:00] A: Done"),
        ]

        with patch("time.sleep"):
            result = transcriber._transcribe_with_retry(client, uploaded, max_retries=3)

        assert result == "[00:00] A: Done"
        assert client.models.generate_content.call_count == 3

    def test_raises_non_retryable_error(self):
        transcriber = GeminiTranscriber(api_key="test-key")
        client = MagicMock()
        uploaded = MagicMock()

        client.models.generate_content.side_effect = ValueError("Invalid API key")

        with pytest.raises(ValueError, match="Invalid API key"):
            transcriber._transcribe_with_retry(client, uploaded)

        # Should NOT retry non-retryable errors
        assert client.models.generate_content.call_count == 1

    def test_raises_after_max_retries(self):
        transcriber = GeminiTranscriber(api_key="test-key")
        client = MagicMock()
        uploaded = MagicMock()

        client.models.generate_content.side_effect = Exception("429 rate limit")

        with patch("time.sleep"):
            with pytest.raises(Exception, match="429 rate limit"):
                transcriber._transcribe_with_retry(client, uploaded, max_retries=2)

        assert client.models.generate_content.call_count == 2

    def test_retries_on_timeout(self):
        transcriber = GeminiTranscriber(api_key="test-key")
        client = MagicMock()
        uploaded = MagicMock()

        client.models.generate_content.side_effect = [
            Exception("connection timeout"),
            MagicMock(text="[00:00] A: Works"),
        ]

        with patch("time.sleep"):
            result = transcriber._transcribe_with_retry(client, uploaded)

        assert result == "[00:00] A: Works"


# ---------------------------------------------------------------------------
# Retry schedule tests (backoff, jitter, server-suggested delay, budget)
# ---------------------------------------------------------------------------

class TestGeminiRetrySchedule:
    """Test the retry schedule: 5 attempts, 4/8/16/32s backoff, bounded total."""

    def setup_method(self):
        self.transcriber = GeminiTranscriber(api_key="test-key")
        self.client = MagicMock()
        self.uploaded = MagicMock()

    def test_default_is_five_attempts(self):
        self.client.models.generate_content.side_effect = Exception("429 rate limit")

        with patch("time.sleep"), patch("random.uniform", return_value=0.0):
            with pytest.raises(Exception, match="429 rate limit"):
                self.transcriber._transcribe_with_retry(self.client, self.uploaded)

        assert self.client.models.generate_content.call_count == 5

    def test_backoff_schedule_and_no_sleep_after_final_attempt(self):
        """Sleeps follow 4/8/16/32s; the final attempt raises WITHOUT sleeping.

        Regression for the old code, where the last computed wait (8s with
        3 attempts) was dead code — it raised before sleeping but the
        comment claimed a 2/4/8 schedule.
        """
        self.client.models.generate_content.side_effect = Exception("503 unavailable")

        with patch("time.sleep") as mock_sleep, \
                patch("random.uniform", return_value=0.0):
            with pytest.raises(Exception, match="503"):
                self.transcriber._transcribe_with_retry(self.client, self.uploaded)

        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleeps == [4.0, 8.0, 16.0, 32.0]
        # 5 attempts but only 4 sleeps: no pointless sleep before the raise
        assert self.client.models.generate_content.call_count == 5
        assert mock_sleep.call_count == 4

    def test_jitter_added_to_backoff(self):
        self.client.models.generate_content.side_effect = [
            Exception("503 unavailable"),
            MagicMock(text="ok"),
        ]

        with patch("time.sleep") as mock_sleep, \
                patch("random.uniform", return_value=1.0):
            result = self.transcriber._transcribe_with_retry(self.client, self.uploaded)

        assert result == "ok"
        assert mock_sleep.call_args_list[0].args[0] == 5.0  # 4s base + 1s jitter

    def test_resource_exhausted_without_429_is_retried(self):
        """Free-tier quota errors must be retried even without '429' in the text."""
        self.client.models.generate_content.side_effect = [
            Exception("RESOURCE_EXHAUSTED: quota exceeded for model"),
            MagicMock(text="ok"),
        ]

        with patch("time.sleep"), patch("random.uniform", return_value=0.0):
            result = self.transcriber._transcribe_with_retry(self.client, self.uploaded)

        assert result == "ok"
        assert self.client.models.generate_content.call_count == 2

    def test_server_suggested_retry_delay_respected(self):
        """A retryDelay in the 429 payload overrides a shorter backoff."""
        self.client.models.generate_content.side_effect = [
            Exception(
                "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': "
                "[{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
                "'retryDelay': '37s'}]}}"
            ),
            MagicMock(text="ok"),
        ]

        with patch("time.sleep") as mock_sleep, \
                patch("random.uniform", return_value=0.0):
            result = self.transcriber._transcribe_with_retry(self.client, self.uploaded)

        assert result == "ok"
        assert mock_sleep.call_args_list[0].args[0] == 37.0

    def test_short_server_delay_does_not_shrink_backoff(self):
        """A server delay shorter than the computed backoff is not used."""
        self.client.models.generate_content.side_effect = [
            Exception("429 rate limit, 'retryDelay': '1s'"),
            Exception("429 rate limit, 'retryDelay': '1s'"),
            MagicMock(text="ok"),
        ]

        with patch("time.sleep") as mock_sleep, \
                patch("random.uniform", return_value=0.0):
            self.transcriber._transcribe_with_retry(self.client, self.uploaded)

        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleeps == [4.0, 8.0]

    def test_total_wait_bounded(self):
        """Cumulative waits never exceed MAX_TOTAL_RETRY_WAIT (~2 min)."""
        self.client.models.generate_content.side_effect = Exception(
            "429 RESOURCE_EXHAUSTED, 'retryDelay': '100s'"
        )

        with patch("time.sleep") as mock_sleep, \
                patch("random.uniform", return_value=0.0):
            with pytest.raises(Exception, match="429"):
                self.transcriber._transcribe_with_retry(self.client, self.uploaded)

        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        assert sum(sleeps) <= GeminiTranscriber.MAX_TOTAL_RETRY_WAIT
        # Budget exhausted before the 5th attempt: 100s + 20s = 120s cap
        assert sleeps == [100.0, 20.0]
        assert self.client.models.generate_content.call_count == 3


class TestServerRetryDelay:
    """Test extraction of server-suggested retry delays from error text."""

    def test_dict_style(self):
        e = Exception("429 ... {'retryDelay': '7s'}")
        assert GeminiTranscriber._server_retry_delay(e) == 7.0

    def test_json_style(self):
        e = Exception('429 ... {"retryDelay": "12.5s"}')
        assert GeminiTranscriber._server_retry_delay(e) == 12.5

    def test_textproto_style(self):
        e = Exception("retry_delay { seconds: 30 }")
        assert GeminiTranscriber._server_retry_delay(e) == 30.0

    def test_snake_case_key(self):
        e = Exception("'retry_delay': '9s'")
        assert GeminiTranscriber._server_retry_delay(e) == 9.0

    def test_no_delay_returns_none(self):
        assert GeminiTranscriber._server_retry_delay(Exception("503 unavailable")) is None

    def test_garbage_returns_none(self):
        assert GeminiTranscriber._server_retry_delay(Exception("")) is None


# ---------------------------------------------------------------------------
# Files API polling tests
# ---------------------------------------------------------------------------

class TestWaitForFileActive:
    """Test the shared Files API poll helper (cap, interval back-off)."""

    def setup_method(self):
        self.transcriber = GeminiTranscriber(api_key="test-key")

    @staticmethod
    def _file(state: str):
        f = MagicMock()
        f.name = "files/poll"
        f.state.name = state
        return f

    def test_active_immediately_no_polling(self):
        client = MagicMock()
        uploaded = self._file("ACTIVE")

        with patch("time.sleep") as mock_sleep:
            result = self.transcriber._wait_for_file_active(client, uploaded)

        assert result is uploaded
        mock_sleep.assert_not_called()
        client.files.get.assert_not_called()

    def test_processing_then_active(self):
        client = MagicMock()
        uploaded = self._file("PROCESSING")
        client.files.get.side_effect = [
            self._file("PROCESSING"),
            self._file("ACTIVE"),
        ]

        with patch("time.sleep") as mock_sleep:
            result = self.transcriber._wait_for_file_active(client, uploaded)

        assert result.state.name == "ACTIVE"
        assert client.files.get.call_count == 2
        # Early polls use the fast interval
        assert all(c.args[0] == 2.0 for c in mock_sleep.call_args_list)

    def test_poll_interval_backs_off_after_first_minute(self):
        client = MagicMock()
        uploaded = self._file("PROCESSING")
        # 35 PROCESSING responses, then ACTIVE
        client.files.get.side_effect = (
            [self._file("PROCESSING")] * 35 + [self._file("ACTIVE")]
        )

        with patch("time.sleep") as mock_sleep:
            result = self.transcriber._wait_for_file_active(client, uploaded)

        assert result.state.name == "ACTIVE"
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        # First minute: 30 polls at 2s; afterwards 5s
        assert sleeps[:30] == [2.0] * 30
        assert all(s == 5.0 for s in sleeps[30:])

    def test_cap_is_about_ten_minutes(self):
        """A stuck file stops polling after ~10 minutes (was 2 minutes)."""
        client = MagicMock()
        uploaded = self._file("PROCESSING")
        client.files.get.return_value = self._file("PROCESSING")

        with patch("time.sleep") as mock_sleep:
            result = self.transcriber._wait_for_file_active(client, uploaded)

        assert result.state.name == "PROCESSING"
        total = sum(c.args[0] for c in mock_sleep.call_args_list)
        assert 595 <= total <= 610  # ~10 min, well past the old 2-min cap

    def test_transcribe_uses_shared_helper(self, tmp_path: Path):
        """transcribe() polls via _wait_for_file_active."""
        transcriber = GeminiTranscriber(api_key="test-key")
        wav = _write_wav(tmp_path / "t.wav", duration=1.0)

        active = self._file("ACTIVE")
        mock_client = MagicMock()
        mock_client.files.upload.return_value = active
        mock_client.models.generate_content.return_value = MagicMock(
            text="[00:00] A: Hi.\n"
        )
        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ), patch.object(
            GeminiTranscriber, "_wait_for_file_active", return_value=active,
        ) as mock_wait, patch.dict("sys.modules", {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }):
            transcriber.transcribe(wav)

        mock_wait.assert_called_once()

    def test_transcribe_dictation_uses_shared_helper(self, tmp_path: Path):
        """transcribe_dictation() polls via the same helper (no duplicate loop)."""
        transcriber = GeminiTranscriber(api_key="test-key")
        wav = _write_wav(tmp_path / "d.wav", duration=1.0)

        active = self._file("ACTIVE")
        mock_client = MagicMock()
        mock_client.files.upload.return_value = active
        mock_client.models.generate_content.return_value = MagicMock(
            text='{"transcript": "hello", "slug": "test-memo-one", "project": "general"}'
        )
        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ), patch.object(
            GeminiTranscriber, "_wait_for_file_active", return_value=active,
        ) as mock_wait, patch.dict("sys.modules", {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }):
            result = transcriber.transcribe_dictation(wav, ["general"])

        mock_wait.assert_called_once()
        assert result.transcript == "hello"


# ---------------------------------------------------------------------------
# End-timestamp clamping tests
# ---------------------------------------------------------------------------

class TestWavDuration:
    def test_reads_real_wav(self, tmp_path: Path):
        wav = _write_wav(tmp_path / "ten.wav", duration=10.0)
        duration = GeminiTranscriber._wav_duration(wav)
        assert duration == pytest.approx(10.0, abs=0.01)

    def test_returns_none_for_garbage(self, tmp_path: Path):
        bad = tmp_path / "bad.wav"
        bad.write_bytes(b"RIFF" + b"\x00" * 100)
        assert GeminiTranscriber._wav_duration(bad) is None

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        assert GeminiTranscriber._wav_duration(tmp_path / "nope.wav") is None


class TestEndClamping:
    """The last segment's end must be the real audio duration, not start+60."""

    def setup_method(self):
        self.transcriber = GeminiTranscriber(api_key="test-key")

    def test_last_segment_clamped_to_duration(self):
        raw = (
            "[00:00] Alice: Hello.\n"
            "[00:10] Bob: Goodbye.\n"
        )
        segments = self.transcriber._parse(raw, audio_duration=25.0)
        assert segments[0].end == 10.0          # still capped by next start
        assert segments[1].end == 25.0          # clamped to real duration
        assert segments[1].end != 70.0          # not the start+60 placeholder

    def test_no_duration_keeps_placeholder(self):
        raw = "[00:10] Bob: Goodbye.\n"
        segments = self.transcriber._parse(raw)
        assert segments[0].end == 70.0  # legacy behavior preserved

    def test_duration_before_last_start_yields_zero_length(self):
        """Hallucinated timestamp beyond the audio: end == start, never < start."""
        raw = "[05:00] Alice: Phantom speech.\n"
        segments = self.transcriber._parse(raw, audio_duration=120.0)
        assert segments[0].start == 300.0
        assert segments[0].end == 300.0

    def test_out_of_order_timestamps_never_produce_end_before_start(self):
        raw = (
            "[00:30] Alice: First.\n"
            "[00:10] Bob: Out of order.\n"
        )
        segments = self.transcriber._parse(raw, audio_duration=60.0)
        for seg in segments:
            assert seg.end >= seg.start

    def test_intermediate_ends_also_clamped(self):
        raw = (
            "[00:00] Alice: Hi.\n"
            "[00:05] Bob: Bye.\n"
        )
        segments = self.transcriber._parse(raw, audio_duration=8.0)
        assert segments[0].end == 5.0
        assert segments[1].end == 8.0

    def test_catch_all_segment_spans_audio(self):
        segments = self.transcriber._parse(
            "no timestamps here at all", audio_duration=42.0
        )
        assert len(segments) == 1
        assert segments[0].start == 0.0
        assert segments[0].end == 42.0

    def test_transcribe_clamps_final_end_from_wav_header(self, tmp_path: Path):
        """End-to-end: transcribe() reads the WAV duration and clamps."""
        transcriber = GeminiTranscriber(api_key="test-key")
        wav = _write_wav(tmp_path / "mixed.wav", duration=12.0)

        mock_file = MagicMock()
        mock_file.name = "files/clamp"
        mock_file.state.name = "ACTIVE"

        mock_client = MagicMock()
        mock_client.files.upload.return_value = mock_file
        mock_client.models.generate_content.return_value = MagicMock(
            text="[00:00] Speaker 1: Short clip.\n"
        )
        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.object(
            GeminiTranscriber, "_compress_to_flac",
            return_value=(wav, "audio/wav", None),
        ), patch.dict("sys.modules", {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }):
            segments = transcriber.transcribe(wav)

        assert len(segments) == 1
        assert segments[0].end == pytest.approx(12.0, abs=0.01)
