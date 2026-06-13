"""Recovery helpers: heal failed, stuck, or partially-processed recordings.

Shared by the startup retry sweep in app.py and the headless
``python -m meeting_recorder reprocess`` CLI. Everything here works
without the tray/UI so failed recordings can be fixed from a terminal.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from meeting_recorder.config import Config
from meeting_recorder.storage.metadata import RecordingMetadata
from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)

# A recording stuck in status "processing" longer than this is treated as
# a crash leftover (post-processing never survives an app restart).
STALE_PROCESSING_SECONDS = 3600.0


def load_transcript_segments(recording_dir: Path) -> list[TranscriptSegment]:
    """Load saved transcript segments from transcript.json.

    Accepts both the app schema ``{"segments": [...]}`` and the bare-list
    schema the old rescue scripts wrote.
    """
    path = Path(recording_dir) / "transcript.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("segments", []) if isinstance(data, dict) else data
    segments = []
    for s in raw:
        segments.append(TranscriptSegment(
            start=float(s.get("start", 0.0)),
            end=float(s.get("end", 0.0)),
            text=str(s.get("text", "")),
            speaker=s.get("speaker"),
        ))
    return segments


@dataclass
class RecoverableRecordings:
    """Recordings that need some form of recovery, by category."""

    failed_retryable: list[Path] = field(default_factory=list)
    stuck_processing: list[Path] = field(default_factory=list)
    incomplete_tail: list[Path] = field(default_factory=list)  # summary/upload


def find_recoverable(recordings_dir: Path) -> RecoverableRecordings:
    """Scan the recordings directory for recordings needing recovery."""
    result = RecoverableRecordings()
    recordings_dir = Path(recordings_dir).expanduser()
    if not recordings_dir.exists():
        return result

    now = time.time()
    for rec_dir in sorted(recordings_dir.iterdir()):
        meta_path = rec_dir / "metadata.json"
        if not rec_dir.is_dir() or not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        status = meta.get("status", "")
        if status == "error":
            if _is_retryable(meta.get("error_message", "")):
                result.failed_retryable.append(rec_dir)
        elif status == "processing":
            if now - meta_path.stat().st_mtime > STALE_PROCESSING_SECONDS:
                result.stuck_processing.append(rec_dir)
        elif status == "completed":
            if meta.get("summary_failed") or meta.get("upload_pending"):
                result.incomplete_tail.append(rec_dir)
    return result


def _is_retryable(error_message: str) -> bool:
    """Classify an error message via the shared error classifier."""
    try:
        from meeting_recorder.storage.error_classifier import classify_error

        return classify_error(error_message).retryable
    except Exception:
        # Unknown classification: retrying cannot make things worse.
        return True


def retry_tail(
    recording_dir: Path,
    config: Config,
    force_summary: bool = False,
    save_metadata: Optional[Callable[[RecordingMetadata, Path], None]] = None,
) -> list[str]:
    """Re-run only the missing tail steps of a completed recording.

    Retries the AI summary (when flagged failed, missing, or forced) and
    the Google Drive upload (when flagged pending) using the transcript
    already on disk — no re-transcription. Returns the steps performed.
    """
    recording_dir = Path(recording_dir)
    metadata = RecordingMetadata.load(recording_dir)
    performed: list[str] = []

    needs_summary = (
        config.summary.enabled
        and (recording_dir / "transcript.json").exists()
        and (metadata.summary_failed or not metadata.has_summary or force_summary)
    )
    if needs_summary:
        try:
            import copy

            from meeting_recorder.summary.summarizer import (
                generate_summary, save_summary,
            )

            summary_config = copy.deepcopy(config.summary)
            if (
                summary_config.provider == "gemini"
                and not summary_config.api_key
                and config.transcription.gemini_api_key
            ):
                summary_config.api_key = config.transcription.gemini_api_key

            segments = load_transcript_segments(recording_dir)
            summary = generate_summary(
                segments=segments,
                config=summary_config,
                meeting_subject=metadata.meeting_subject,
                attendees=metadata.meeting_attendees,
                duration_seconds=metadata.duration_seconds,
            )
            save_summary(summary, recording_dir)
            metadata.has_summary = True
            metadata.summary_failed = False
            metadata.summary_provider = summary.provider_used
            metadata.summary_model = summary.model_used
            performed.append("summary")
            logger.info("Tail retry: summary generated for %s", recording_dir.name)
        except Exception:
            metadata.summary_failed = True
            logger.exception("Tail retry: summary failed for %s", recording_dir.name)

    if metadata.upload_pending and config.google_drive.enabled:
        try:
            from meeting_recorder.integrations.google_drive import (
                GoogleDriveUploader, is_google_drive_available,
            )

            creds_path = Path(config.google_drive.credentials_path).expanduser()
            if is_google_drive_available(creds_path):
                uploader = GoogleDriveUploader(
                    credentials_path=creds_path,
                    folder_id=config.google_drive.folder_id,
                )
                folder_id = uploader.upload_recording(recording_dir)
                if folder_id:
                    metadata.google_drive_folder_id = folder_id
                    metadata.upload_pending = False
                    performed.append("drive-upload")
                    logger.info(
                        "Tail retry: Drive upload complete for %s",
                        recording_dir.name,
                    )
        except Exception:
            logger.exception(
                "Tail retry: Drive upload failed for %s", recording_dir.name,
            )

    (save_metadata or (lambda m, d: m.save(d)))(metadata, recording_dir)

    if performed:
        try:
            from meeting_recorder.search.index import RecordingIndex

            index = RecordingIndex()
            index.index_recording(recording_dir)
            index.close()
        except Exception:
            logger.debug("Tail retry: re-index failed (non-fatal)", exc_info=True)
    return performed


def reprocess_headless(
    recording_dir: Path,
    config: Config,
    backend_override: Optional[str] = None,
) -> RecordingMetadata:
    """Full re-process of a recording without the tray app.

    Runs the same stages as the in-app pipeline: mix, transcribe (with
    the configured backend or an override), save transcript formats,
    finalize metadata, summary, search index, and Drive upload. Unlike
    the old scripts/retry_transcribe.py, this leaves the recording in a
    fully consistent state (status, summary, index, Drive).
    """
    import copy as _copy

    from meeting_recorder.audio.mixer import mix_tracks_streaming
    from meeting_recorder.storage.transcript_formatter import save_all_formats
    from meeting_recorder.transcription.pipeline import TranscriptionPipeline

    recording_dir = Path(recording_dir)
    cfg = _copy.deepcopy(config)
    if backend_override:
        cfg.transcription.backend = backend_override

    try:
        metadata = RecordingMetadata.load(recording_dir)
    except FileNotFoundError:
        metadata = RecordingMetadata()
    metadata.status = "processing"
    metadata.error_message = ""
    metadata.save(recording_dir)

    app_audio = recording_dir / "app_audio.wav"
    mic_audio = recording_dir / "mic_audio.wav"
    mixed_audio = recording_dir / "mixed.wav"
    if not app_audio.exists() and not mic_audio.exists():
        metadata.set_error("No audio files found", recording_dir)
        raise FileNotFoundError(f"No audio files in {recording_dir}")

    try:
        if app_audio.exists() and mic_audio.exists():
            mix_tracks_streaming(app_audio, mic_audio, mixed_audio)

        pipeline = TranscriptionPipeline(cfg)
        try:
            segments = pipeline.process(
                recording_dir,
                attendees=metadata.meeting_attendees,
                organizer=metadata.meeting_organizer,
            )
        finally:
            if mixed_audio.exists():
                mixed_audio.unlink()

        backend_used = getattr(pipeline, "last_backend_used", None)
        if backend_used:
            metadata.transcription_backend = backend_used

        mapping = pipeline.last_speaker_mapping
        if mapping is not None:
            metadata.speaker_map = mapping.speaker_map
            metadata.speaker_map_confidence = mapping.confidence
            metadata.speaker_map_method = mapping.method

        save_all_formats(segments, recording_dir, formats=cfg.output.formats)

        speakers = set(s.speaker for s in segments if s.speaker)
        metadata.finalize(
            recording_dir,
            speaker_count=len(speakers),
            segment_count=len(segments),
            elapsed_seconds=metadata.duration_seconds,
        )
        metadata.upload_pending = cfg.google_drive.enabled
        metadata.save(recording_dir)

        # Tail: summary + Drive + index, reusing the single-step retry path
        retry_tail(recording_dir, cfg, force_summary=cfg.summary.enabled)
        return RecordingMetadata.load(recording_dir)
    except Exception as e:
        metadata.set_error(str(e), recording_dir)
        raise
