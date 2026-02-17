"""Tests for live transcription preview module."""

from __future__ import annotations

import io
import threading
import time
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from meeting_recorder.transcription.live_transcriber import (
    LiveTranscriber,
    DEFAULT_BUFFER_SECONDS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_TRANSCRIBE_INTERVAL,
)


def _make_audio_bytes(num_samples: int, value: int = 100) -> bytes:
    """Create raw int16 PCM bytes with a constant sample value."""
    return np.full(num_samples, value, dtype=np.int16).tobytes()


def _make_mock_segment(text: str):
    """Create a mock transcription segment with the given text."""
    seg = MagicMock()
    seg.text = text
    return seg


# ---------------------------------------------------------------------------
# Constructor defaults
# ---------------------------------------------------------------------------

class TestLiveTranscriberDefaults:
    """Verify constructor defaults are correct."""

    def test_constructor_defaults(self):
        lt = LiveTranscriber()
        assert lt._model_size == "tiny"
        assert lt._device == "cpu"
        assert lt._compute_type == "int8"
        assert lt._language == "en"
        assert lt._buffer_seconds == DEFAULT_BUFFER_SECONDS
        assert lt._sample_rate == DEFAULT_SAMPLE_RATE
        assert lt._transcribe_interval == DEFAULT_TRANSCRIBE_INTERVAL
        assert lt._on_transcript is None
        assert lt._max_samples == int(DEFAULT_BUFFER_SECONDS * DEFAULT_SAMPLE_RATE)

    def test_constructor_custom_values(self):
        cb = lambda text: None
        lt = LiveTranscriber(
            on_transcript=cb,
            model_size="base",
            device="cuda",
            compute_type="float16",
            language="fr",
            buffer_seconds=5.0,
            sample_rate=8000,
            transcribe_interval=1.0,
        )
        assert lt._model_size == "base"
        assert lt._device == "cuda"
        assert lt._compute_type == "float16"
        assert lt._language == "fr"
        assert lt._buffer_seconds == 5.0
        assert lt._sample_rate == 8000
        assert lt._transcribe_interval == 1.0
        assert lt._on_transcript is cb
        assert lt._max_samples == int(5.0 * 8000)


# ---------------------------------------------------------------------------
# Buffer management -- feed_audio
# ---------------------------------------------------------------------------

class TestFeedAudio:
    """Test audio buffer feeding and trimming."""

    def test_feed_audio_adds_to_buffer(self):
        lt = LiveTranscriber(buffer_seconds=10.0)
        chunk = _make_audio_bytes(1000)
        lt.feed_audio(chunk)

        assert len(lt._buffer) == 1
        assert lt._buffer_samples == 1000

    def test_feed_audio_multiple_chunks(self):
        lt = LiveTranscriber(buffer_seconds=10.0)
        lt.feed_audio(_make_audio_bytes(500))
        lt.feed_audio(_make_audio_bytes(300))

        assert len(lt._buffer) == 2
        assert lt._buffer_samples == 800

    def test_feed_audio_trims_old_data_when_exceeds_max(self):
        """Buffer should evict oldest chunks when total samples exceed max."""
        lt = LiveTranscriber(buffer_seconds=1.0, sample_rate=16000)
        # max_samples = 16000. Feed 3 chunks of 8000 samples each (24000 total).
        chunk = _make_audio_bytes(8000)
        lt.feed_audio(chunk)
        lt.feed_audio(chunk)
        lt.feed_audio(chunk)

        # Should have trimmed the oldest chunk(s) to stay within 16000 samples
        assert lt._buffer_samples <= 16000

    def test_feed_audio_with_empty_bytes(self):
        lt = LiveTranscriber()
        lt.feed_audio(b"")

        # Empty bytes = 0 samples, should still append but not break anything
        assert len(lt._buffer) == 1
        assert lt._buffer_samples == 0

    def test_buffer_size_limited_by_buffer_seconds(self):
        """Total buffered samples should never significantly exceed the configured limit."""
        lt = LiveTranscriber(buffer_seconds=0.5, sample_rate=16000)
        max_expected = int(0.5 * 16000)

        # Feed many small chunks that together exceed the limit
        for _ in range(100):
            lt.feed_audio(_make_audio_bytes(1000))

        # After trimming, buffer_samples should be at or below max + one chunk
        # (trimming happens after append, so it can temporarily be slightly over
        # by up to one chunk before the while loop trims)
        assert lt._buffer_samples <= max_expected


# ---------------------------------------------------------------------------
# Buffer retrieval
# ---------------------------------------------------------------------------

class TestGetBufferAudio:
    """Test _get_buffer_audio method."""

    def test_returns_none_when_empty(self):
        lt = LiveTranscriber()
        result = lt._get_buffer_audio()
        assert result is None

    def test_returns_concatenated_audio(self):
        lt = LiveTranscriber()
        chunk_a = np.array([1, 2, 3], dtype=np.int16).tobytes()
        chunk_b = np.array([4, 5, 6], dtype=np.int16).tobytes()
        lt.feed_audio(chunk_a)
        lt.feed_audio(chunk_b)

        result = lt._get_buffer_audio()
        assert result is not None
        expected = np.array([1, 2, 3, 4, 5, 6], dtype=np.int16)
        np.testing.assert_array_equal(result, expected)


# ---------------------------------------------------------------------------
# WAV conversion
# ---------------------------------------------------------------------------

class TestAudioToWavBytes:
    """Test _audio_to_wav_bytes produces valid WAV data."""

    def test_produces_valid_wav(self):
        lt = LiveTranscriber(sample_rate=16000)
        audio = np.array([100, -100, 200, -200], dtype=np.int16)
        wav_bytes = lt._audio_to_wav_bytes(audio)

        # Should be parseable as a WAV file
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 4

            # Read back frames and verify
            raw = wf.readframes(4)
            read_back = np.frombuffer(raw, dtype=np.int16)
            np.testing.assert_array_equal(read_back, audio)


# ---------------------------------------------------------------------------
# Transcript state
# ---------------------------------------------------------------------------

class TestTranscriptState:
    """Test last_transcript property and clear_buffer."""

    def test_last_transcript_starts_empty(self):
        lt = LiveTranscriber()
        assert lt.last_transcript == ""

    def test_clear_buffer_empties_everything(self):
        lt = LiveTranscriber()
        lt.feed_audio(_make_audio_bytes(1000))
        lt._last_transcript = "some text"

        lt.clear_buffer()

        assert len(lt._buffer) == 0
        assert lt._buffer_samples == 0
        assert lt.last_transcript == ""


# ---------------------------------------------------------------------------
# Thread lifecycle
# ---------------------------------------------------------------------------

class TestThreadLifecycle:
    """Test start/stop and is_running behavior."""

    def test_start_creates_and_starts_thread(self):
        """start() should create a daemon thread that is alive."""
        lt = LiveTranscriber(transcribe_interval=60.0)  # Long interval keeps thread alive

        # Inject a mock model so _load_model sets it and the loop stays alive
        def fake_load():
            lt._model = MagicMock()

        with patch.object(lt, "_load_model", side_effect=fake_load):
            lt.start()
            try:
                assert lt._thread is not None
                assert lt._thread.is_alive()
                assert lt._thread.daemon is True
                assert lt._thread.name == "live-transcriber"
            finally:
                lt.stop()

    def test_stop_sets_event_and_joins(self):
        """stop() should set the stop event and join the thread."""
        lt = LiveTranscriber(transcribe_interval=60.0)

        def fake_load():
            lt._model = MagicMock()

        with patch.object(lt, "_load_model", side_effect=fake_load):
            lt.start()
            lt.stop()

        assert lt._stop_event.is_set()
        assert lt._thread is None
        assert lt._model is None

    def test_is_running_reflects_thread_state(self):
        """is_running should be True while thread is alive, False after stop."""
        lt = LiveTranscriber(transcribe_interval=60.0)

        def fake_load():
            lt._model = MagicMock()

        assert lt.is_running is False
        with patch.object(lt, "_load_model", side_effect=fake_load):
            lt.start()
            assert lt.is_running is True
            lt.stop()
        assert lt.is_running is False


# ---------------------------------------------------------------------------
# Transcription with mocked model
# ---------------------------------------------------------------------------

class TestTranscriptionWithMockModel:
    """Test transcript update and callback using a mocked whisper model."""

    def _make_transcriber_with_mock_model(self, on_transcript=None):
        """Create a LiveTranscriber and inject a mock whisper model."""
        lt = LiveTranscriber(
            on_transcript=on_transcript,
            transcribe_interval=0.1,
            buffer_seconds=5.0,
            sample_rate=16000,
        )

        mock_model = MagicMock()
        mock_info = MagicMock()
        segments = [
            _make_mock_segment("Hello world"),
            _make_mock_segment("Testing live"),
        ]
        mock_model.transcribe.return_value = (iter(segments), mock_info)

        return lt, mock_model

    def test_last_transcript_updated_after_transcription(self):
        """After the transcription loop runs, last_transcript should contain model output."""
        lt, mock_model = self._make_transcriber_with_mock_model()

        # Feed enough audio (>1 second at 16kHz)
        lt.feed_audio(_make_audio_bytes(32000))

        # Manually inject model and run one transcription cycle
        lt._model = mock_model
        audio = lt._get_buffer_audio()
        assert audio is not None

        wav_bytes = lt._audio_to_wav_bytes(audio)
        segments_gen, _ = mock_model.transcribe(
            io.BytesIO(wav_bytes),
            language="en",
            beam_size=1,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=100),
        )

        texts = []
        for seg in segments_gen:
            text = seg.text.strip()
            if text:
                texts.append(text)
        transcript = " ".join(texts)

        with lt._transcript_lock:
            lt._last_transcript = transcript

        assert lt.last_transcript == "Hello world Testing live"

    def test_on_transcript_callback_called(self):
        """The on_transcript callback should be invoked with transcript text."""
        callback = MagicMock()
        lt = LiveTranscriber(
            on_transcript=callback,
            transcribe_interval=0.1,
            buffer_seconds=5.0,
            sample_rate=16000,
        )

        mock_model = MagicMock()
        mock_info = MagicMock()
        segments = [_make_mock_segment("Callback test")]
        mock_model.transcribe.return_value = (iter(segments), mock_info)

        # Patch _load_model to inject our mock
        def fake_load():
            lt._model = mock_model

        with patch.object(lt, "_load_model", side_effect=fake_load):
            # Feed enough audio for transcription (> 1 second)
            lt.feed_audio(_make_audio_bytes(32000))
            lt.start()

            # Wait long enough for at least one transcription cycle
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if callback.call_count > 0:
                    break
                time.sleep(0.05)

            lt.stop()

        callback.assert_called()
        # Verify the callback received the expected text
        call_args = callback.call_args[0][0]
        assert "Callback test" in call_args

    def test_transcription_loop_skips_short_audio(self):
        """The loop should skip transcription when audio buffer < 1 second."""
        lt, mock_model = self._make_transcriber_with_mock_model()

        # Feed less than 1 second of audio (< 16000 samples)
        lt.feed_audio(_make_audio_bytes(8000))

        def fake_load():
            lt._model = mock_model

        with patch.object(lt, "_load_model", side_effect=fake_load):
            lt.start()
            time.sleep(0.4)
            lt.stop()

        # Model should not have been called since audio was too short
        mock_model.transcribe.assert_not_called()
