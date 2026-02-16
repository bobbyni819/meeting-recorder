"""Transcription pipeline orchestrating transcription, diarization, and merging."""

from __future__ import annotations

import logging
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

    The pipeline can use either local (faster-whisper) or cloud (OpenAI) transcription.
    """

    def __init__(self, config: Config):
        self.config = config
        self._transcriber = None
        self._diarizer = None

    def _get_transcriber(self):
        """Get or create the transcription backend."""
        if self._transcriber is not None:
            return self._transcriber

        tc = self.config.transcription
        if tc.backend == "cloud":
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

    def process(self, recording_dir: Path) -> list[TranscriptSegment]:
        """Run the full transcription pipeline on a recording.

        Args:
            recording_dir: Directory containing app_audio.wav, mic_audio.wav, mixed.wav.

        Returns:
            List of TranscriptSegment with speaker labels and timestamps.
        """
        app_audio = recording_dir / "app_audio.wav"
        mic_audio = recording_dir / "mic_audio.wav"
        mixed_audio = recording_dir / "mixed.wav"

        transcriber = self._get_transcriber()
        user_name = self.config.recording.user_name

        # Strategy: Transcribe both tracks separately for better speaker identification
        # If separate tracks exist, transcribe them independently
        if app_audio.exists() and mic_audio.exists():
            return self._process_separate_tracks(
                app_audio, mic_audio, transcriber, user_name
            )

        # Fallback: transcribe mixed audio
        if mixed_audio.exists():
            return self._process_mixed(mixed_audio, transcriber, user_name)

        raise FileNotFoundError(f"No audio files found in {recording_dir}")

    def _process_separate_tracks(
        self,
        app_audio: Path,
        mic_audio: Path,
        transcriber,
        user_name: str,
    ) -> list[TranscriptSegment]:
        """Process separate app and mic audio tracks."""
        logger.info("Processing separate tracks...")

        # Transcribe app audio (remote participants)
        app_segments = transcriber.transcribe(app_audio)

        # Run diarization on app audio to identify different remote speakers
        diarizer = self._get_diarizer()
        if diarizer and app_segments:
            speaker_segments = diarizer.diarize(app_audio)
            app_segments = merge_transcript_with_speakers(
                app_segments, speaker_segments, user_name
            )

        # Transcribe mic audio (user)
        mic_segments = transcriber.transcribe(mic_audio)

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
            speaker_segments = diarizer.diarize(mixed_audio)
            segments = merge_transcript_with_speakers(
                segments, speaker_segments, user_name
            )

        logger.info("Pipeline complete: %d segments", len(segments))
        return segments
