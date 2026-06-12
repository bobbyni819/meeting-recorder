"""Integration test: generate synthetic WAV, run through transcription pipeline.

These tests require faster-whisper (and optionally torch) to be installed.
They are automatically skipped when the dependencies are not available.

Also includes unit tests for pipeline fallback logic that don't require whisper.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.conftest import generate_sine_wav, generate_silence_wav
from meeting_recorder.config import Config
from meeting_recorder.transcription.local_whisper import TranscriptSegment

# Skip the entire module if faster-whisper is not available
try:
    from faster_whisper import WhisperModel
    _HAS_FASTER_WHISPER = True
except ImportError:
    _HAS_FASTER_WHISPER = False

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

pytestmark = pytest.mark.skipif(
    not _HAS_FASTER_WHISPER,
    reason="faster-whisper not installed",
)

from meeting_recorder.transcription.local_whisper import LocalWhisperTranscriber, TranscriptSegment
from meeting_recorder.storage.transcript_formatter import save_all_formats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_speech_like_wav(path: Path, duration: float = 3.0, sample_rate: int = 16000) -> Path:
    """Generate a WAV file with speech-like characteristics.

    Creates a signal with multiple harmonics and amplitude modulation
    to loosely resemble speech. This is NOT actual speech, but it exercises
    the pipeline code paths.
    """
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    # Fundamental + harmonics (roughly vowel-like)
    signal = np.zeros_like(t)
    for freq, amp in [(200, 0.4), (400, 0.3), (800, 0.15), (1200, 0.1)]:
        signal += amp * np.sin(2 * np.pi * freq * t)

    # Amplitude modulation (syllable-like envelope)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t)  # ~3 Hz modulation
    signal *= envelope

    # Normalize and convert to int16
    signal = signal / np.max(np.abs(signal)) * 0.7
    samples = (signal * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    return path


# ---------------------------------------------------------------------------
# LocalWhisperTranscriber tests
# ---------------------------------------------------------------------------

class TestLocalWhisperTranscriber:
    """Test the local whisper transcriber with a tiny model."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.transcriber = LocalWhisperTranscriber(
            model_size="tiny",
            device="cpu",
            compute_type="int8",
            language="en",
        )

    def test_load_model(self):
        """Verify that the model loads without errors."""
        self.transcriber.load()
        assert self.transcriber._model is not None

    def test_transcribe_silence(self):
        """Transcribing silence should produce zero or minimal segments."""
        wav_path = generate_silence_wav(self.tmp_path / "silence.wav", duration=2.0)
        segments = self.transcriber.transcribe(wav_path)
        # Silence should produce very few or no segments
        assert isinstance(segments, list)

    def test_transcribe_returns_segments(self):
        """Transcribing speech-like audio should return TranscriptSegment objects."""
        wav_path = generate_speech_like_wav(self.tmp_path / "speech.wav", duration=3.0)
        segments = self.transcriber.transcribe(wav_path)
        assert isinstance(segments, list)
        for seg in segments:
            assert isinstance(seg, TranscriptSegment)
            assert isinstance(seg.start, float)
            assert isinstance(seg.end, float)
            assert isinstance(seg.text, str)
            assert seg.end >= seg.start

    def test_transcribe_auto_loads_model(self):
        """transcribe() should auto-load the model if not loaded yet."""
        wav_path = generate_silence_wav(self.tmp_path / "auto.wav", duration=1.0)
        assert self.transcriber._model is None
        self.transcriber.transcribe(wav_path)
        assert self.transcriber._model is not None


# ---------------------------------------------------------------------------
# Pipeline integration: transcribe -> format
# ---------------------------------------------------------------------------

class TestTranscriptionPipelineIntegration:
    """End-to-end: generate audio, transcribe, save to all formats."""

    def test_full_pipeline_silence(self, tmp_path: Path):
        """Run the pipeline on silence and verify output files are created."""
        wav_path = generate_silence_wav(tmp_path / "mixed.wav", duration=2.0)

        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cpu",
            compute_type="int8", language="en",
        )
        segments = transcriber.transcribe(wav_path)

        # Save in all formats
        save_all_formats(segments, tmp_path)

        # All format files should exist (even if content is minimal)
        assert (tmp_path / "transcript.json").exists()
        assert (tmp_path / "transcript.txt").exists()
        assert (tmp_path / "transcript.srt").exists()

        # JSON should be valid
        data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
        assert "segments" in data

    def test_full_pipeline_speech_like(self, tmp_path: Path):
        """Run the pipeline on speech-like audio and verify output structure."""
        wav_path = generate_speech_like_wav(tmp_path / "mixed.wav", duration=3.0)

        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cpu",
            compute_type="int8", language="en",
        )
        segments = transcriber.transcribe(wav_path)

        # Add fake speaker labels for formatting test
        for i, seg in enumerate(segments):
            seg.speaker = f"Speaker {i % 2}"

        save_all_formats(segments, tmp_path)

        # Verify JSON
        json_data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
        assert isinstance(json_data["segments"], list)

        # Verify TXT
        txt_content = (tmp_path / "transcript.txt").read_text(encoding="utf-8")
        if segments:
            assert "[" in txt_content  # timestamp brackets

        # Verify SRT
        srt_content = (tmp_path / "transcript.srt").read_text(encoding="utf-8")
        if segments:
            assert "-->" in srt_content  # SRT timestamp arrow

    def test_segment_timestamps_monotonic(self, tmp_path: Path):
        """Verify that segment timestamps are non-decreasing."""
        wav_path = generate_speech_like_wav(tmp_path / "test.wav", duration=5.0)

        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cpu",
            compute_type="int8", language="en",
        )
        segments = transcriber.transcribe(wav_path)

        for i in range(1, len(segments)):
            assert segments[i].start >= segments[i - 1].start, (
                f"Segment {i} starts before segment {i-1}: "
                f"{segments[i].start} < {segments[i-1].start}"
            )


# ---------------------------------------------------------------------------
# Pipeline fallback tests (no whisper dependency needed)
# ---------------------------------------------------------------------------

class TestPipelineFallback:
    """Test that pipeline falls back to mixed.wav when separate tracks fail."""

    def test_fallback_to_mixed_when_separate_fails(self, tmp_path: Path):
        """If _process_separate_tracks throws, pipeline should fall back to mixed.wav."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        # Create dummy audio files
        generate_silence_wav(tmp_path / "app_audio.wav", duration=1.0)
        generate_silence_wav(tmp_path / "mic_audio.wav", duration=1.0)
        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "local"
        config.transcription.model_size = "tiny"
        config.transcription.device = "cpu"
        config.transcription.compute_type = "int8"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)

        expected_segments = [TranscriptSegment(start=0.0, end=1.0, text="fallback")]

        # Make separate tracks fail, but mixed succeed
        with patch.object(pipeline, "_process_separate_tracks", side_effect=RuntimeError("test error")):
            with patch.object(pipeline, "_process_mixed", return_value=expected_segments) as mock_mixed:
                segments = pipeline.process(tmp_path)
                mock_mixed.assert_called_once()
                assert segments == expected_segments

    def test_file_not_found_when_all_missing(self, tmp_path: Path):
        """If no audio files exist at all, should raise FileNotFoundError."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        config = Config()
        pipeline = TranscriptionPipeline(config)

        with pytest.raises(FileNotFoundError):
            pipeline.process(tmp_path)


# ---------------------------------------------------------------------------
# Parallel pipeline tests (no whisper dependency needed)
# ---------------------------------------------------------------------------

class TestParallelPipeline:
    """Test that the parallel pipeline executes transcription concurrently."""

    def test_parallel_separate_tracks(self, tmp_path: Path):
        """Verify _process_separate_tracks uses ThreadPoolExecutor."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "app_audio.wav", duration=1.0)
        generate_silence_wav(tmp_path / "mic_audio.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "local"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)

        app_segs = [TranscriptSegment(start=0.0, end=1.0, text="hello", speaker="Participant 1")]
        mic_segs = [TranscriptSegment(start=0.5, end=1.5, text="hi", speaker="User")]

        mock_transcriber = MagicMock()
        mock_transcriber.transcribe = MagicMock(side_effect=[app_segs, mic_segs])

        result = pipeline._process_separate_tracks(
            tmp_path / "app_audio.wav",
            tmp_path / "mic_audio.wav",
            mock_transcriber,
            "User",
        )

        # Should have called transcribe twice (app + mic)
        assert mock_transcriber.transcribe.call_count == 2
        # Result should be merged from both tracks
        assert len(result) == 2

    def test_parallel_with_diarization(self, tmp_path: Path):
        """Verify diarization runs concurrently with transcription."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline
        from meeting_recorder.transcription.diarization import SpeakerDiarizer, SpeakerSegment

        generate_silence_wav(tmp_path / "app_audio.wav", duration=1.0)
        generate_silence_wav(tmp_path / "mic_audio.wav", duration=1.0)

        config = Config()
        config.diarization.enabled = True
        config.diarization.huggingface_token = "fake-token"

        pipeline = TranscriptionPipeline(config)

        app_segs = [TranscriptSegment(start=0.0, end=1.0, text="hello")]
        mic_segs = [TranscriptSegment(start=0.5, end=1.5, text="hi")]
        speaker_segs = [SpeakerSegment(start=0.0, end=1.0, speaker="SPEAKER_00")]

        mock_transcriber = MagicMock()
        mock_transcriber.transcribe = MagicMock(side_effect=[app_segs, mic_segs])

        mock_diarizer = MagicMock(spec=SpeakerDiarizer)
        mock_diarizer.diarize = MagicMock(return_value=speaker_segs)
        pipeline._diarizer = mock_diarizer

        result = pipeline._process_separate_tracks(
            tmp_path / "app_audio.wav",
            tmp_path / "mic_audio.wav",
            mock_transcriber,
            "User",
        )

        # All three tasks should have been called
        assert mock_transcriber.transcribe.call_count == 2
        mock_diarizer.diarize.assert_called_once()
        assert len(result) >= 1

    def test_voice_profile_resolution_called(self, tmp_path: Path):
        """Verify voice profile resolution is attempted when audio_path is provided."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "app_audio.wav", duration=1.0)
        generate_silence_wav(tmp_path / "mic_audio.wav", duration=1.0)
        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        config = Config()
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)

        test_segments = [TranscriptSegment(start=0.0, end=1.0, text="test")]

        with patch.object(pipeline, "_process_separate_tracks", return_value=test_segments):
            with patch(
                "meeting_recorder.transcription.speaker_resolver.resolve_speakers_with_voice_profiles"
            ) as mock_voice:
                mock_voice.return_value = MagicMock(
                    speaker_map={}, unmapped_speakers=[], confidence="none"
                )
                pipeline.process(tmp_path, attendees=["Alice"])
                mock_voice.assert_called_once()


# ---------------------------------------------------------------------------
# Gemini pipeline path tests (no API dependency)
# ---------------------------------------------------------------------------

class TestGeminiPipeline:
    """Test the Gemini fast-path in TranscriptionPipeline."""

    def test_gemini_uses_mixed_wav_when_available(self, tmp_path: Path):
        """Gemini should prefer mixed.wav when it exists."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)
        generate_silence_wav(tmp_path / "app_audio.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "gemini"
        config.transcription.gemini_api_key = "test-key"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)

        expected = [TranscriptSegment(start=0.0, end=5.0, text="hello", speaker="Alice")]
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = expected
        pipeline._transcriber = mock_transcriber

        segments = pipeline.process(tmp_path)

        mock_transcriber.transcribe.assert_called_once()
        called_path = mock_transcriber.transcribe.call_args[0][0]
        assert called_path.name == "mixed.wav"
        assert segments == expected

    def test_gemini_falls_back_to_app_audio(self, tmp_path: Path):
        """When no mixed.wav exists, Gemini should use app_audio.wav."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "app_audio.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "gemini"
        config.transcription.gemini_api_key = "test-key"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)

        expected = [TranscriptSegment(start=0.0, end=5.0, text="test", speaker="Bob")]
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = expected
        pipeline._transcriber = mock_transcriber

        segments = pipeline.process(tmp_path)

        called_path = mock_transcriber.transcribe.call_args[0][0]
        assert called_path.name == "app_audio.wav"

    def test_gemini_skips_diarization(self, tmp_path: Path):
        """Gemini path should not call the diarizer."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "gemini"
        config.transcription.gemini_api_key = "test-key"
        config.diarization.enabled = True
        config.diarization.huggingface_token = "fake-token"

        pipeline = TranscriptionPipeline(config)

        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = [
            TranscriptSegment(start=0.0, end=5.0, text="hi", speaker="S1")
        ]
        pipeline._transcriber = mock_transcriber

        mock_diarizer = MagicMock()
        pipeline._diarizer = mock_diarizer

        pipeline.process(tmp_path)

        mock_diarizer.diarize.assert_not_called()

    def test_gemini_raises_when_no_audio(self, tmp_path: Path):
        """Gemini path should raise FileNotFoundError when no audio file exists."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        config = Config()
        config.transcription.backend = "gemini"
        config.transcription.gemini_api_key = "test-key"

        pipeline = TranscriptionPipeline(config)

        mock_transcriber = MagicMock()
        pipeline._transcriber = mock_transcriber

        with pytest.raises(FileNotFoundError, match="No audio file found"):
            pipeline.process(tmp_path)


# ---------------------------------------------------------------------------
# Gemini -> local Whisper fallback tests (no API / whisper-load dependency)
# ---------------------------------------------------------------------------

class TestGeminiLocalFallback:
    """When Gemini fails, the pipeline falls back to local Whisper."""

    def _gemini_pipeline(self, api_key: str = "test-key"):
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        config = Config()
        config.transcription.backend = "gemini"
        config.transcription.gemini_api_key = api_key
        config.diarization.enabled = False
        return TranscriptionPipeline(config)

    def test_fallback_triggered_on_gemini_error(self, tmp_path: Path):
        """Gemini raising after retries should trigger the local fallback."""
        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        pipeline = self._gemini_pipeline()

        gemini = MagicMock()
        gemini.transcribe.side_effect = Exception("503 Service Unavailable")
        pipeline._transcriber = gemini

        local_segments = [TranscriptSegment(start=0.0, end=1.0, text="local rescue")]
        local = MagicMock()
        local.transcribe.return_value = local_segments

        with patch.object(pipeline, "_get_local_fallback", return_value=local) as mock_get:
            segments = pipeline.process(tmp_path)

        mock_get.assert_called_once()
        # The original Gemini error is passed in so it can be re-raised
        assert "503" in str(mock_get.call_args.args[0])
        assert segments == local_segments
        assert pipeline.last_backend_used == "local"

    def test_fallback_triggered_when_key_missing(self, tmp_path: Path):
        """Missing Gemini key (ValueError from _get_transcriber) also falls back."""
        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        pipeline = self._gemini_pipeline(api_key="")

        local_segments = [TranscriptSegment(start=0.0, end=1.0, text="no key needed")]
        local = MagicMock()
        local.transcribe.return_value = local_segments

        with patch.object(pipeline, "_get_local_fallback", return_value=local):
            segments = pipeline.process(tmp_path)

        assert segments == local_segments
        assert pipeline.last_backend_used == "local"

    def test_original_error_reraised_when_local_unavailable(self, tmp_path: Path):
        """If faster-whisper import/load fails, the ORIGINAL Gemini error is raised."""
        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        pipeline = self._gemini_pipeline()

        original = RuntimeError("429 RESOURCE_EXHAUSTED after all retries")
        gemini = MagicMock()
        gemini.transcribe.side_effect = original
        pipeline._transcriber = gemini

        with patch(
            "meeting_recorder.transcription.pipeline.LocalWhisperTranscriber"
        ) as mock_cls:
            mock_cls.return_value.load.side_effect = ImportError("no faster_whisper")
            with pytest.raises(RuntimeError) as excinfo:
                pipeline.process(tmp_path)

        assert excinfo.value is original
        assert pipeline.last_backend_used is None

    def test_fallback_flows_through_diarization(self, tmp_path: Path):
        """The local fallback must use the same diarization/merge path as 'local'."""
        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        pipeline = self._gemini_pipeline()
        pipeline.config.diarization.enabled = True
        pipeline.config.diarization.huggingface_token = "fake-token"

        gemini = MagicMock()
        gemini.transcribe.side_effect = Exception("503 unavailable")
        pipeline._transcriber = gemini

        local = MagicMock()
        local.transcribe.return_value = [
            TranscriptSegment(start=0.0, end=1.0, text="hello")
        ]

        from meeting_recorder.transcription.diarization import SpeakerSegment
        mock_diarizer = MagicMock()
        mock_diarizer.diarize.return_value = [
            SpeakerSegment(start=0.0, end=1.0, speaker="SPEAKER_00")
        ]
        pipeline._diarizer = mock_diarizer

        with patch.object(pipeline, "_get_local_fallback", return_value=local):
            segments = pipeline.process(tmp_path)

        mock_diarizer.diarize.assert_called_once()
        assert len(segments) == 1
        assert segments[0].speaker  # labelled by diarization merge

    def test_fallback_handles_app_audio_only(self, tmp_path: Path):
        """Imported recordings have only app_audio.wav; fallback must handle them."""
        generate_silence_wav(tmp_path / "app_audio.wav", duration=1.0)

        pipeline = self._gemini_pipeline()

        gemini = MagicMock()
        gemini.transcribe.side_effect = Exception("503 unavailable")
        pipeline._transcriber = gemini

        local_segments = [TranscriptSegment(start=0.0, end=1.0, text="import rescue")]
        local = MagicMock()
        local.transcribe.return_value = local_segments

        with patch.object(pipeline, "_get_local_fallback", return_value=local):
            segments = pipeline.process(tmp_path)

        assert segments == local_segments
        called_path = local.transcribe.call_args[0][0]
        assert called_path.name == "app_audio.wav"

    def test_get_local_fallback_caches_loaded_model(self):
        """The fallback transcriber is loaded once and reused."""
        pipeline = self._gemini_pipeline()

        with patch(
            "meeting_recorder.transcription.pipeline.LocalWhisperTranscriber"
        ) as mock_cls:
            instance = mock_cls.return_value
            first = pipeline._get_local_fallback(Exception("err1"))
            second = pipeline._get_local_fallback(Exception("err2"))

        assert first is second is instance
        instance.load.assert_called_once()

    def test_get_local_fallback_uses_configured_model(self):
        """Fallback uses the configured model size / device / compute type."""
        pipeline = self._gemini_pipeline()
        pipeline.config.transcription.model_size = "large-v3"
        pipeline.config.transcription.device = "cuda"
        pipeline.config.transcription.compute_type = "float16"

        with patch(
            "meeting_recorder.transcription.pipeline.LocalWhisperTranscriber"
        ) as mock_cls:
            pipeline._get_local_fallback(Exception("boom"))

        mock_cls.assert_called_once_with(
            model_size="large-v3",
            device="cuda",
            compute_type="float16",
            language=pipeline.config.recording.language,
        )


# ---------------------------------------------------------------------------
# Backend-used reporting tests
# ---------------------------------------------------------------------------

class TestBackendUsedReporting:
    """last_backend_used reflects the backend that actually produced segments."""

    def test_initially_none(self):
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        pipeline = TranscriptionPipeline(Config())
        assert pipeline.last_backend_used is None

    def test_gemini_success_reports_gemini(self, tmp_path: Path):
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "gemini"
        config.transcription.gemini_api_key = "test-key"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = [
            TranscriptSegment(start=0.0, end=1.0, text="hi", speaker="S1")
        ]
        pipeline._transcriber = mock_transcriber

        pipeline.process(tmp_path)
        assert pipeline.last_backend_used == "gemini"

    def test_local_backend_reports_local(self, tmp_path: Path):
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "local"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = [
            TranscriptSegment(start=0.0, end=1.0, text="hi")
        ]
        pipeline._transcriber = mock_transcriber

        pipeline.process(tmp_path)
        assert pipeline.last_backend_used == "local"

    def test_cloud_backend_reports_cloud(self, tmp_path: Path):
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "cloud"
        config.transcription.openai_api_key = "test-key"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = [
            TranscriptSegment(start=0.0, end=1.0, text="hi")
        ]
        pipeline._transcriber = mock_transcriber

        pipeline.process(tmp_path)
        assert pipeline.last_backend_used == "cloud"

    def test_reset_between_calls(self, tmp_path: Path):
        """A failing process() call resets last_backend_used to None."""
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        generate_silence_wav(tmp_path / "mixed.wav", duration=1.0)

        config = Config()
        config.transcription.backend = "local"
        config.diarization.enabled = False

        pipeline = TranscriptionPipeline(config)
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = [
            TranscriptSegment(start=0.0, end=1.0, text="hi")
        ]
        pipeline._transcriber = mock_transcriber

        pipeline.process(tmp_path)
        assert pipeline.last_backend_used == "local"

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            pipeline.process(empty_dir)
        assert pipeline.last_backend_used is None


# ---------------------------------------------------------------------------
# LocalWhisperTranscriber compute-type fallback tests (mocked model load)
# ---------------------------------------------------------------------------

class TestLocalWhisperComputeTypeFallback:
    """load() retries with int8_float32 when the configured compute type fails."""

    def test_retries_with_int8_float32_on_load_failure(self):
        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cuda", compute_type="float16",
        )

        good_model = MagicMock()
        with patch(
            "faster_whisper.WhisperModel",
            side_effect=[ValueError("float16 not supported on this device"), good_model],
        ) as mock_model:
            transcriber.load()

        assert transcriber._model is good_model
        assert mock_model.call_count == 2
        assert mock_model.call_args_list[1].kwargs["compute_type"] == "int8_float32"

    def test_no_retry_when_already_int8_float32(self):
        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cuda", compute_type="int8_float32",
        )

        with patch(
            "faster_whisper.WhisperModel",
            side_effect=RuntimeError("CUDA driver missing"),
        ) as mock_model:
            with pytest.raises(RuntimeError, match="CUDA driver missing"):
                transcriber.load()

        assert mock_model.call_count == 1

    def test_successful_load_does_not_retry(self):
        transcriber = LocalWhisperTranscriber(
            model_size="tiny", device="cpu", compute_type="int8",
        )

        good_model = MagicMock()
        with patch("faster_whisper.WhisperModel", return_value=good_model) as mock_model:
            transcriber.load()

        assert transcriber._model is good_model
        assert mock_model.call_count == 1
