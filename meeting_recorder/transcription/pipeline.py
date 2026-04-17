"""Transcription pipeline orchestrating transcription, diarization, and merging."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Optional

from meeting_recorder.config import Config
from meeting_recorder.transcription.local_whisper import LocalWhisperTranscriber, TranscriptSegment
from meeting_recorder.transcription.cloud_whisper import CloudWhisperTranscriber
from meeting_recorder.transcription.diarization import SpeakerDiarizer
from meeting_recorder.transcription.transcript_merger import (
    merge_transcript_with_speakers,
    merge_user_and_app_transcripts,
)

logger = logging.getLogger(__name__)


class TranscriptionPipeline:
    """Full post-recording transcription pipeline.

    Pipeline steps:
    1. Transcribe mixed audio (or separate tracks)
    2. Run speaker diarization on app audio
    3. Merge transcription with speaker labels
    4. Label mic segments as user
    5. Resolve speaker names using calendar attendees (if available)

    The pipeline can use either local (faster-whisper) or cloud (OpenAI) transcription.
    """

    def __init__(self, config: Config):
        self.config = config
        self._transcriber = None
        self._diarizer = None
        self._last_speaker_mapping = None

    def _get_transcriber(self):
        """Get or create the transcription backend."""
        if self._transcriber is not None:
            return self._transcriber

        tc = self.config.transcription
        if tc.backend == "gemini":
            from meeting_recorder.transcription.gemini_transcriber import GeminiTranscriber
            if not tc.gemini_api_key:
                raise ValueError(
                    "Gemini API key required for Gemini transcription. "
                    "Set transcription.gemini_api_key in ~/.meeting_recorder/config.toml"
                )
            self._transcriber = GeminiTranscriber(
                api_key=tc.gemini_api_key,
                language=self.config.recording.language,
                model=tc.gemini_model,
            )
        elif tc.backend == "cloud":
            if not tc.openai_api_key:
                raise ValueError("OpenAI API key required for cloud transcription.")
            self._transcriber = CloudWhisperTranscriber(
                api_key=tc.openai_api_key,
                language=self.config.recording.language,
            )
        else:
            self._transcriber = LocalWhisperTranscriber(
                model_size=tc.model_size,
                device=tc.device,
                compute_type=tc.compute_type,
                language=self.config.recording.language,
            )
        return self._transcriber

    def _get_diarizer(self) -> Optional[SpeakerDiarizer]:
        """Get or create the diarization backend."""
        dc = self.config.diarization
        if not dc.enabled:
            return None
        if not dc.huggingface_token:
            logger.warning("Diarization enabled but no HuggingFace token set. Skipping.")
            return None
        if self._diarizer is None:
            self._diarizer = SpeakerDiarizer(
                huggingface_token=dc.huggingface_token,
                min_speakers=dc.min_speakers,
                max_speakers=dc.max_speakers,
            )
        return self._diarizer

    @property
    def last_speaker_mapping(self):
        """Return the speaker mapping from the last process() call, or None."""
        return self._last_speaker_mapping

    def process(
        self,
        recording_dir: Path,
        attendees: list[str] | None = None,
        organizer: str = "",
    ) -> list[TranscriptSegment]:
        """Run the full transcription pipeline on a recording.

        Args:
            recording_dir: Directory containing app_audio.wav, mic_audio.wav, mixed.wav.
            attendees: Optional list of attendee names from calendar for speaker resolution.
            organizer: Optional meeting organizer name for speaker resolution.

        Returns:
            List of TranscriptSegment with speaker labels and timestamps.
        """
        self._last_speaker_mapping = None
        app_audio = recording_dir / "app_audio.wav"
        mic_audio = recording_dir / "mic_audio.wav"
        mixed_audio = recording_dir / "mixed.wav"

        user_name = self.config.recording.user_name

        # Gemini fast path: send the mixed audio to the API in a single call.
        # Gemini handles multi-speaker identification natively, so we skip
        # pyannote diarization (which would be redundant and slower).
        if self.config.transcription.backend == "gemini":
            audio_path = mixed_audio if mixed_audio.exists() else app_audio
            if not audio_path.exists():
                raise FileNotFoundError(f"No audio file found in {recording_dir}")
            transcriber = self._get_transcriber()
            segments = transcriber.transcribe(audio_path)
            # Still attempt calendar-based speaker name resolution on top of
            # Gemini's generic "Speaker 1 / Speaker 2" labels.
            self._resolve_speakers(
                segments, attendees, organizer, user_name, audio_path=None,
            )
            logger.info("Gemini pipeline complete: %d segments", len(segments))
            return segments

        transcriber = self._get_transcriber()

        # Strategy: Transcribe both tracks separately for better speaker identification
        # If separate tracks exist, transcribe them independently
        if app_audio.exists() and mic_audio.exists():
            try:
                segments = self._process_separate_tracks(
                    app_audio, mic_audio, transcriber, user_name
                )
                self._resolve_speakers(
                    segments, attendees, organizer, user_name, audio_path=app_audio,
                )
                return segments
            except Exception:
                logger.exception(
                    "Separate track processing failed, falling back to mixed audio"
                )

        # Fallback: transcribe mixed audio
        if mixed_audio.exists():
            segments = self._process_mixed(mixed_audio, transcriber, user_name)
            self._resolve_speakers(
                segments, attendees, organizer, user_name, audio_path=mixed_audio,
            )
            return segments

        raise FileNotFoundError(f"No audio files found in {recording_dir}")

    def _resolve_speakers(
        self,
        segments: list[TranscriptSegment],
        attendees: list[str] | None,
        organizer: str,
        user_name: str,
        audio_path: Path | None = None,
    ) -> None:
        """Attempt to resolve speaker labels to real names.

        Tries voice profile matching first, then falls back to calendar-based resolution.
        """
        try:
            from meeting_recorder.transcription.speaker_resolver import (
                resolve_speakers,
                resolve_speakers_with_voice_profiles,
                apply_speaker_map,
            )

            # Strategy 1: Voice profile matching (cross-meeting speaker ID)
            if audio_path is not None:
                voice_mapping = resolve_speakers_with_voice_profiles(
                    segments, audio_path, user_name
                )
                if voice_mapping.speaker_map:
                    apply_speaker_map(segments, voice_mapping.speaker_map)
                    self._last_speaker_mapping = voice_mapping
                    if not voice_mapping.unmapped_speakers:
                        return  # All speakers resolved via voice profiles

            # Strategy 2: Calendar-based resolution for remaining speakers
            if attendees:
                mapping = resolve_speakers(segments, attendees, organizer, user_name)
                if mapping.speaker_map:
                    apply_speaker_map(segments, mapping.speaker_map)
                self._last_speaker_mapping = mapping
        except Exception:
            logger.exception("Speaker resolution failed (non-fatal)")

    def _process_separate_tracks(
        self,
        app_audio: Path,
        mic_audio: Path,
        transcriber,
        user_name: str,
    ) -> list[TranscriptSegment]:
        """Process separate app and mic audio tracks with parallel execution.

        Runs app transcription, mic transcription, and diarization concurrently
        using a thread pool to reduce total processing time.
        """
        logger.info("Processing separate tracks (parallel)...")

        diarizer = self._get_diarizer()

        # Submit all independent tasks concurrently
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="pipeline") as pool:
            app_future: Future = pool.submit(transcriber.transcribe, app_audio)
            mic_future: Future = pool.submit(transcriber.transcribe, mic_audio)
            diarize_future: Optional[Future] = None
            if diarizer:
                diarize_future = pool.submit(diarizer.diarize, app_audio)

            # Collect results
            app_segments = app_future.result()
            mic_segments = mic_future.result()

            if diarize_future and app_segments:
                # Don't let diarization failures kill the entire transcription:
                # fall back to speaker-less segments so the user still gets a
                # readable transcript. Common diarization failures include
                # cuDNN mismatches, HF API changes, and missing gated models.
                try:
                    speaker_segments = diarize_future.result()
                    app_segments = merge_transcript_with_speakers(
                        app_segments, speaker_segments, user_name
                    )
                except Exception:
                    logger.exception(
                        "Diarization failed — returning transcript without speaker labels"
                    )

        # Merge both track transcripts chronologically
        merged = merge_user_and_app_transcripts(mic_segments, app_segments, user_name)

        logger.info("Pipeline complete: %d total segments", len(merged))
        return merged

    def _process_mixed(
        self,
        mixed_audio: Path,
        transcriber,
        user_name: str,
    ) -> list[TranscriptSegment]:
        """Process a single mixed audio file."""
        logger.info("Processing mixed audio...")

        segments = transcriber.transcribe(mixed_audio)

        diarizer = self._get_diarizer()
        if diarizer and segments:
            try:
                speaker_segments = diarizer.diarize(mixed_audio)
                segments = merge_transcript_with_speakers(
                    segments, speaker_segments, user_name
                )
            except Exception:
                logger.exception(
                    "Diarization failed — returning transcript without speaker labels"
                )

        logger.info("Pipeline complete: %d segments", len(segments))
        return segments
