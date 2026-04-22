"""Post-recording pipeline for dictation clips.

Flow:
  temp WAV (system tempdir) → Gemini transcription → resolve project folder
  → move audio + write transcript to <drive>/<project>/Sources/voice-memos/<date>/.

If the project folder doesn't exist on this machine (e.g. default_project
"general", or Gemini inferred something unmapped), memos land in the flat
fallback dir <drive>/voice-memos/<date>/ instead.

On any failure the audio is kept (in the fallback dir) and an ``.error``
sidecar is written next to it so nothing is lost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from meeting_recorder.config import Config
from meeting_recorder.transcription.gemini_transcriber import (
    DictationResult,
    GeminiTranscriber,
)

logger = logging.getLogger(__name__)


@dataclass
class FinalizeOutcome:
    """Result of running the post-recording pipeline."""
    audio_path: Path
    transcript_path: Optional[Path]
    error_path: Optional[Path]
    result: Optional[DictationResult]


def fallback_output_dir(drive_root: Path, recorded_at: datetime) -> Path:
    """Return ``<drive_root>/voice-memos/YYYY-MM-DD/`` (not created)."""
    return drive_root / "voice-memos" / recorded_at.strftime("%Y-%m-%d")


# Backwards-compatible alias; older tests may import this name.
build_output_dir = fallback_output_dir


def resolve_project_dir(
    drive_root: Path,
    project: str,
    template: str,
    recorded_at: datetime,
    default_project: str = "general",
) -> Path:
    """Work out where a memo for *project* should live.

    Uses ``template`` (e.g. ``"{project}/Sources/voice-memos"``) under
    ``drive_root``. If the template's parent directory doesn't exist on
    disk (e.g. unknown project, or default ``"general"`` with no matching
    folder), falls back to ``<drive_root>/voice-memos/<date>/`` so memos
    are never orphaned.
    """
    if project and project != default_project:
        try:
            sub = template.format(project=project)
        except (KeyError, IndexError):
            sub = ""
        if sub:
            candidate_parent = drive_root / sub  # e.g. G:/.../metabolism/Sources/voice-memos
            # Only use the project path if the PROJECT folder already exists.
            # We auto-create the voice-memos sub-leaf, but we don't want to
            # create whole new project trees — that would confuse the KB layout.
            project_root = drive_root / project
            if project_root.is_dir():
                return candidate_parent / recorded_at.strftime("%Y-%m-%d")

    return fallback_output_dir(drive_root, recorded_at)


def render_markdown(
    result: DictationResult,
    audio_filename: str,
    recorded_at: datetime,
    duration_seconds: float,
) -> str:
    """Render the ``.md`` file contents with YAML frontmatter + transcript."""
    return (
        "---\n"
        "mode: dictation\n"
        f"recorded_at: {recorded_at.isoformat(timespec='seconds')}\n"
        f"duration_seconds: {duration_seconds:.1f}\n"
        f"audio_file: {audio_filename}\n"
        f"slug: {result.slug}\n"
        f"project: {result.project}\n"
        f"transcription_model: {result.model}\n"
        "---\n"
        "\n"
        f"{result.transcript}\n"
    )


def finalize_recording(
    temp_audio: Path,
    config: Config,
    recorded_at: datetime,
    duration_seconds: float,
) -> FinalizeOutcome:
    """Transcribe *temp_audio* with Gemini and route to the right folder.

    Success: moves audio to ``<drive>/<project>/Sources/voice-memos/<date>/HHMM-<slug>.wav``
    (or the flat fallback) and writes a matching ``.md`` next to it.

    Failure: moves audio to ``<drive>/voice-memos/<date>/HHMM-recording.wav``
    with a ``.error`` sidecar. Audio is never lost.
    """
    drive_root = Path(config.dictation.drive_root).expanduser()
    hhmm = recorded_at.strftime("%H%M")

    api_key = config.transcription.gemini_api_key
    if not api_key:
        return _write_error(
            temp_audio, drive_root, recorded_at, hhmm,
            "No Gemini API key set in secrets.toml (transcription.gemini_api_key)",
        )

    model = config.dictation.gemini_model or config.transcription.gemini_model or ""
    transcriber = GeminiTranscriber(api_key=api_key, model=model)

    try:
        result = transcriber.transcribe_dictation(
            temp_audio,
            project_choices=list(config.dictation.project_list),
            default_project=config.dictation.default_project,
        )
    except Exception as e:
        logger.exception("Dictation transcription failed")
        return _write_error(temp_audio, drive_root, recorded_at, hhmm, str(e))

    final_dir = resolve_project_dir(
        drive_root=drive_root,
        project=result.project,
        template=config.dictation.project_subpath_template,
        recorded_at=recorded_at,
        default_project=config.dictation.default_project,
    )
    final_dir.mkdir(parents=True, exist_ok=True)

    final_audio = final_dir / f"{hhmm}-{result.slug}.wav"
    final_md = final_dir / f"{hhmm}-{result.slug}.md"

    # Avoid cross-device rename issues: try replace first, fall back to copy
    _move_file(temp_audio, final_audio)

    md = render_markdown(
        result=result,
        audio_filename=final_audio.name,
        recorded_at=recorded_at,
        duration_seconds=duration_seconds,
    )
    final_md.write_text(md, encoding="utf-8")
    logger.info(
        "Dictation saved: %s (project=%s, dir=%s)",
        final_audio.name, result.project, final_dir,
    )

    return FinalizeOutcome(
        audio_path=final_audio,
        transcript_path=final_md,
        error_path=None,
        result=result,
    )


def _move_file(src: Path, dst: Path) -> None:
    """Move *src* to *dst*, falling back to copy+unlink across drives."""
    try:
        src.replace(dst)
    except OSError:
        # Cross-device (e.g. C: tempdir → G: Drive) — use copy+unlink
        import shutil
        shutil.copy2(src, dst)
        try:
            src.unlink()
        except OSError:
            logger.debug("Could not remove source after copy: %s", src)


def _write_error(
    temp_audio: Path,
    drive_root: Path,
    recorded_at: datetime,
    hhmm: str,
    message: str,
) -> FinalizeOutcome:
    """Move *temp_audio* to the fallback dir and write a ``.error`` sidecar."""
    fallback_dir = fallback_output_dir(drive_root, recorded_at)
    fallback_dir.mkdir(parents=True, exist_ok=True)
    audio_path = fallback_dir / f"{hhmm}-recording.wav"
    _move_file(temp_audio, audio_path)

    err_path = audio_path.with_suffix(".error")
    err_path.write_text(message, encoding="utf-8")
    logger.error("Dictation kept audio at %s (see %s)", audio_path, err_path.name)
    return FinalizeOutcome(
        audio_path=audio_path,
        transcript_path=None,
        error_path=err_path,
        result=None,
    )
