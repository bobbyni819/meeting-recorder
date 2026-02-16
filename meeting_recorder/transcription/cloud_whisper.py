"""Cloud transcription using OpenAI Whisper API."""

from __future__ import annotations

import logging
from pathlib import Path

from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)


class CloudWhisperTranscriber:
    """Transcription backend using OpenAI Whisper API.

    Requires an OpenAI API key. Sends audio to OpenAI's servers for
    transcription. Good fallback when local GPU is not available.
    """

    def __init__(
        self,
        api_key: str,
        language: str = "en",
        model: str = "whisper-1",
    ):
        self.api_key = api_key
        self.language = language
        self.model = model

    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        """Transcribe an audio file using OpenAI Whisper API.

        Args:
            audio_path: Path to a WAV audio file.

        Returns:
            List of TranscriptSegment with timestamps and text.
        """
        from openai import OpenAI

        logger.info("Transcribing via OpenAI API: %s", audio_path.name)

        client = OpenAI(api_key=self.api_key)

        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=self.model,
                file=f,
                language=self.language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        segments = []
        for seg in response.segments:
            segments.append(TranscriptSegment(
                start=seg["start"],
                end=seg["end"],
                text=seg["text"].strip(),
            ))

        logger.info("Cloud transcription complete: %d segments", len(segments))
        return segments
