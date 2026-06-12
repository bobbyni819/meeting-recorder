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
        self._fallback_transcriber = None
        self._diarizer = None
        self._last_speaker_mapping = None
        self._last_backend_used = None

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
                model=getattr(dc, "model", "") or SpeakerDiarizer._FALLBACK_MODEL,
            )
        return self._diarizer

    def _get_local_fallback(self, original_error: Exception) -> LocalWhisperTranscriber:
        """Build (and cache) the local Whisper transcriber used as Gemini fallback.

        Loads the model eagerly so import/model-load failures surface here.
        If the fallback is unavailable, re-raises *original_error* (the
        Gemini failure) so the user sees the root cause rather than a
        misleading local-stack error.
        """
        try:
            if self._fallback_transcriber is None:
                tc = self.config.transcription
                model_size = self._fallback_model_size(tc.model_size, tc.device)
                transcriber = LocalWhisperTranscriber(
                    model_size=model_size,
                    device=tc.device,
                    compute_type=tc.compute_type,
                    language=self.config.recording.language,
                )
                transcriber.load()
                self._fallback_transcriber = transcriber
            return self._fallback_transcriber
        except Exception:
            logger.exception(
                "Local Whisper fallback unavailable — re-raising original Gemini error"
            )
            raise original_error

    # Model sizes ordered small -> large; a tier cap selects the largest
    # model at or below its ceiling.
    _MODEL_ORDER = ("tiny", "base", "small", "medium", "large-v3")

    def _fallback_model_size(self, configured: str, device: str) -> str:
        """Cap the fallback model by the machine's performance tier.

        On a weak machine, large-v3 on CPU could take hours; the tier picks
        a smaller model so the fallback actually finishes. Never upgrades
        beyond what the user configured.
        """
        try:
            from meeting_recorder.performance import resolve_tier

            ceiling = resolve_tier(self.config.performance.profile).fallback_model_size
        except Exception:
            return configured
        order = self._MODEL_ORDER
        if configured not in order or ceiling not in order:
            return configured
        capped = order[min(order.index(configured), order.index(ceiling))]
        if capped != configured:
            logger.info(
                "Fallback model capped %s -> %s for this performance tier",
                configured, capped,
            )
        return capped

    @property
    def last_speaker_mapping(self):
        """Return the speaker mapping from the last process() call, or None."""
        return self._last_speaker_mapping

    @property
    def last_backend_used(self) -> str | None:
        """Backend that actually produced the last transcript, or None.

        One of "gemini", "cloud", or "local" — "local" is also reported when
        the Gemini backend failed and the local Whisper fallback kicked in.
        """
        return self._last_backend_used

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
        self._last_backend_used = None
        app_audio = recording_dir / "app_audio.wav"
        mixed_audio = recording_dir / "mixed.wav"

        user_name = self.config.recording.user_name

        # Gemini fast path: send the mixed audio to the API in a single call.
        # Gemini handles multi-speaker identification natively, so we skip
        # pyannote diarization (which would be redundant and slower).
        if self.config.transcription.backend == "gemini":
            audio_path = mixed_audio if mixed_audio.exists() else app_audio
            if not audio_path.exists():
                raise FileNotFoundError(f"No audio file found in {recording_dir}")
            try:
                transcriber = self._get_transcriber()
                segments = transcriber.transcribe(audio_path)
            except Exception as gemini_error:
                logger.exception(
                    "Gemini transcription failed; attempting local Whisper fallback"
                )
                # Re-raises gemini_error if faster-whisper is unavailable
                fallback = self._get_local_fallback(gemini_error)
                segments = self._process_with_fallback_transcriber(
                    recording_dir, audio_path, fallback,
                    attendees, organizer, user_name,
                )
                self._last_backend_used = "local"
                logger.info(
                    "Local Whisper fallback complete: %d segments", len(segments)
                )
                return segments
            self._last_backend_used = "gemini"
            # Use the separate mic track as ground truth to label the user's
            # own turns (Gemini only ever sees the mixed audio, so it can't
            # know which generic speaker is "you").
            self._attribute_user_from_mic(segments, recording_dir, user_name)
            # Still attempt calendar-based speaker name resolution on top of
            # Gemini's generic "Speaker 1 / Speaker 2" labels.
            self._resolve_speakers(
                segments, attendees, organizer, user_name, audio_path=None,
            )
            logger.info("Gemini pipeline complete: %d segments", len(segments))
            return segments

        transcriber = self._get_transcriber()
        segments = self._process_standard(
            recording_dir, transcriber, attendees, organizer, user_name
        )
        self._last_backend_used = (
            "cloud" if self.config.transcription.backend == "cloud" else "local"
        )
        return segments

    def _process_standard(
        self,
        recording_dir: Path,
        transcriber,
        attendees: list[str] | None,
        organizer: str,
        user_name: str,
    ) -> list[TranscriptSegment]:
        """Transcribe via the separate-tracks / mixed-audio strategy.

        Shared by the local/cloud backends and the Gemini-to-local fallback
        so the fallback flows through the exact same diarization + merge
        logic as a normal local run.
        """
        app_audio = recording_dir / "app_audio.wav"
        mic_audio = recording_dir / "mic_audio.wav"
        mixed_audio = recording_dir / "mixed.wav"

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

    def _process_with_fallback_transcriber(
        self,
        recording_dir: Path,
        audio_path: Path,
        transcriber,
        attendees: list[str] | None,
        organizer: str,
        user_name: str,
    ) -> list[TranscriptSegment]:
        """Run the standard local path for a recording Gemini failed on.

        The standard path requires app+mic tracks or mixed.wav; recordings
        with only app_audio.wav (e.g. imported audio) are transcribed
        directly from the file Gemini would have used.
        """
        try:
            return self._process_standard(
                recording_dir, transcriber, attendees, organizer, user_name
            )
        except FileNotFoundError:
            segments = self._process_mixed(audio_path, transcriber, user_name)
            self._resolve_speakers(
                segments, attendees, organizer, user_name, audio_path=audio_path,
            )
            return segments

    def _attribute_user_from_mic(
        self,
        segments: list[TranscriptSegment],
        recording_dir: Path,
        user_name: str,
    ) -> None:
        """Relabel the mic-matched speaker to the user's name (best-effort)."""
        mic_audio = recording_dir / "mic_audio.wav"
        if not mic_audio.exists():
            return
        try:
            from meeting_recorder.transcription.mic_attribution import attribute_user

            duration = None
            try:
                import wave

                with wave.open(str(mic_audio), "rb") as wf:
                    if wf.getframerate():
                        duration = wf.getnframes() / wf.getframerate()
            except Exception:
                pass
            renamed = attribute_user(segments, mic_audio, user_name, duration)
            if renamed:
                logger.info("Mic attribution: relabelled %s -> %s", renamed, user_name)
        except Exception:
            logger.debug("Mic attribution skipped (non-fatal)", exc_info=True)

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
