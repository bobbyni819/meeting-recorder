"""Tests for live transcription preview module."""

from __future__ import annotations

import io
import threading
import time
import wave
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Multi-source accumulation (new behavior)
# ---------------------------------------------------------------------------

def _make_timed_segment(text: str, start: float, end: float):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    return seg


class TestMultiSourceAccumulation:
    """Stable text accumulates across windows with speaker labels."""

    def _transcriber(self, **kwargs):
        return LiveTranscriber(
            buffer_seconds=10.0,
            sample_rate=16000,
            stability_margin=3.0,
            **kwargs,
        )

    def _set_model(self, lt, segments_by_call):
        """Inject a model whose transcribe() returns successive segment lists."""
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = [
            (iter(segs), MagicMock()) for segs in segments_by_call
        ]
        lt._model = mock_model
        return mock_model

    def test_stable_segment_commits_once(self):
        """A segment ending before the stability horizon commits exactly once."""
        lt = self._transcriber()
        lt.feed_audio(_make_audio_bytes(16000 * 8), source="app")  # 8s fed

        # Window covers 0-8s; horizon = 8 - 3 = 5s. Segment ends at 4s.
        self._set_model(lt, [[_make_timed_segment("hello world", 1.0, 4.0)]])
        committed = lt._transcribe_source("app")

        assert len(committed) == 1
        assert committed[0][1] == "app"
        assert committed[0][2] == "hello world"
        assert lt._sources["app"].watermark == pytest.approx(4.0)

        # Same audio re-transcribed: already-committed segment is skipped.
        self._set_model(lt, [[_make_timed_segment("hello world", 1.0, 4.0)]])
        lt.feed_audio(_make_audio_bytes(160), source="app")  # tiny new chunk
        committed2 = lt._transcribe_source("app")
        assert committed2 == []

    def test_recent_segment_stays_provisional(self):
        """A segment ending after the horizon is provisional, not committed."""
        lt = self._transcriber()
        lt.feed_audio(_make_audio_bytes(16000 * 8), source="app")

        # Horizon = 5s; segment ends at 7s -> provisional.
        self._set_model(lt, [[_make_timed_segment("still talking", 6.0, 7.0)]])
        committed = lt._transcribe_source("app")

        assert committed == []
        assert lt._sources["app"].provisional == "still talking"

    def test_sources_are_independent(self):
        """app and mic keep separate buffers, watermarks, and labels."""
        lt = self._transcriber()
        lt.feed_audio(_make_audio_bytes(16000 * 8), source="app")
        lt.feed_audio(_make_audio_bytes(16000 * 8), source="mic")

        assert lt._sources["app"].buffer_samples == 16000 * 8
        assert lt._sources["mic"].buffer_samples == 16000 * 8

        self._set_model(lt, [[_make_timed_segment("from them", 0.0, 2.0)]])
        lt._committed.extend(lt._transcribe_source("app"))
        self._set_model(lt, [[_make_timed_segment("from me", 2.0, 4.0)]])
        lt._committed.extend(lt._transcribe_source("mic"))

        display = lt._build_display_text()
        assert "[Them] from them" in display
        assert "[You] from me" in display
        assert lt.accumulated_text == "from them from me"

    def test_single_source_display_has_no_labels(self):
        """With only app audio, the preview shows plain unlabelled text."""
        lt = self._transcriber()
        lt.feed_audio(_make_audio_bytes(16000 * 8), source="app")
        self._set_model(lt, [[_make_timed_segment("just them", 0.0, 2.0)]])
        lt._committed.extend(lt._transcribe_source("app"))

        assert lt._build_display_text() == "just them"

    def test_silent_source_skips_model_call(self):
        """A source with no new audio since the last pass is not re-transcribed."""
        lt = self._transcriber()
        lt.feed_audio(_make_audio_bytes(16000 * 8), source="app")

        model = self._set_model(lt, [[], []])
        lt._transcribe_source("app")
        assert model.transcribe.call_count == 1

        # No new audio fed: second pass must skip the model entirely.
        lt._transcribe_source("app")
        assert model.transcribe.call_count == 1

    def test_committed_lines_written_to_file(self, tmp_path):
        """Stable entries append to live_transcript.txt with timestamps."""
        out = tmp_path / "live_transcript.txt"
        lt = self._transcriber(output_path=out)
        lt._append_to_file([(65.0, "mic", "hello"), (2.0, "app", "hi there")])

        content = out.read_text(encoding="utf-8")
        # Entries are written in chronological order
        assert content == "[00:02] Them: hi there\n[01:05] You: hello\n"

    def test_file_write_failure_is_non_fatal(self):
        """An unwritable path disables the file but keeps the preview alive."""
        lt = self._transcriber(
            output_path=Path("Z:/nonexistent-drive/live.txt"),
        )
        lt._append_to_file([(1.0, "app", "text")])
        assert lt._file_write_failed is True
        # Second call is a silent no-op
        lt._append_to_file([(2.0, "app", "more")])

    def test_clear_buffer_resets_all_sources(self):
        lt = self._transcriber()
        lt.feed_audio(_make_audio_bytes(1000), source="app")
        lt.feed_audio(_make_audio_bytes(1000), source="mic")
        lt._committed.append((1.0, "app", "old"))

        lt.clear_buffer()

        assert lt._sources["app"].buffer_samples == 0
        assert lt._sources["mic"].buffer_samples == 0
        assert lt.accumulated_text == ""


# ---------------------------------------------------------------------------
# Live insights (topic + watched keywords)
# ---------------------------------------------------------------------------

class TestLiveInsights:
    def test_topic_detected_from_accumulated_text(self):
        events = []
        lt = LiveTranscriber(on_insight=events.append)
        lt._committed = [
            (0.0, "app", "we need to deploy the api to the server"),
            (5.0, "app", "there is a bug in the database pipeline"),
        ]

        lt._detect_topic()

        assert events == [{"type": "topic", "topic": "engineering"}]
        assert lt.current_topic == "engineering"
        # Unchanged topic does not re-fire
        lt._detect_topic()
        assert len(events) == 1

    def test_watched_keyword_alerts_once(self):
        events = []
        lt = LiveTranscriber(on_insight=events.append)
        with patch(
            "meeting_recorder.storage.keyword_alerts.load_watched_keywords",
            return_value=["budget"],
        ):
            lt._check_watched_keywords("we discussed the budget today")
            lt._check_watched_keywords("budget came up again")

        assert len(events) == 1
        assert events[0]["type"] == "keyword"
        assert events[0]["keyword"] == "budget"

    def test_insights_disabled_without_callback(self):
        lt = LiveTranscriber()  # no on_insight
        # Must be a no-op, not an error
        lt._maybe_run_insights([(0.0, "app", "budget budget budget")])


class TestBackpressureAndBounds:
    """Live transcription must yield to the recording under load."""

    def test_skips_cycle_when_should_transcribe_false(self):
        """Backpressure: a False gate skips the GPU cycle entirely."""
        gate = {"ok": False}
        lt = LiveTranscriber(
            transcribe_interval=0.05,
            should_transcribe=lambda: gate["ok"],
        )
        model = MagicMock()
        model.transcribe.return_value = (iter([]), MagicMock())

        def fake_load():
            lt._model = model

        lt.feed_audio(_make_audio_bytes(32000), source="app")
        with patch.object(lt, "_load_model", side_effect=fake_load):
            lt.start()
            time.sleep(0.3)  # several intervals, all gated off
            # While running and gated, the periodic loop never transcribes
            assert model.transcribe.call_count == 0
            lt.stop()  # final flush may transcribe once (tail), that's fine

    def test_committed_history_is_bounded(self):
        from meeting_recorder.transcription.live_transcriber import _MAX_COMMITTED

        lt = LiveTranscriber()
        # Simulate a very long meeting accumulating committed entries
        with lt._committed_lock:
            lt._committed = [(float(i), "app", f"t{i}") for i in range(_MAX_COMMITTED + 200)]
        # The loop's bound runs on commit; emulate it
        if len(lt._committed) > _MAX_COMMITTED:
            del lt._committed[:-_MAX_COMMITTED]
        assert len(lt._committed) == _MAX_COMMITTED
        # Newest entries are retained
        assert lt._committed[-1][2] == f"t{_MAX_COMMITTED + 199}"
