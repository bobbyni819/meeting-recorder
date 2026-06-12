"""Smart meeting naming: rename a recording folder from its content.

The folder is named at recording START from whatever calendar event was
matched then — which is wrong when several meetings are booked in the same
slot. After transcription we know what was actually discussed, so we can
pick the right calendar event (or, if none fits, derive a short title) and
rename the folder.

Constraints honoured here:
- The timestamp prefix is always preserved, so chronological sorting and
  any tooling keyed on the date is unaffected.
- Files INSIDE the folder (transcript.json, etc.) are never renamed or
  restructured — only the folder itself moves. The original folder name is
  recorded in metadata.original_dir_name.
- When several calendar events overlap, the title is CHOSEN from that list
  (never invented) so it matches the user's calendar verbatim; a title is
  only generated when no calendar event fits.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Matches the "YYYY-MM-DD_HH-MM-SS" prefix create_recording_dir() writes.
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_")


def sanitize_subject(subject: str, max_len: int = 60) -> str:
    """Sanitize a title into a folder-safe fragment (matches RecordingStore)."""
    safe = "".join(
        c if c.isalnum() or c in "._- " else "" for c in subject
    ).strip().replace(" ", "_")
    safe = re.sub(r"_+", "_", safe).strip("_")
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip("_")
    return safe


def timestamp_prefix(dir_name: str) -> Optional[str]:
    """Return the leading timestamp of a recording dir name, or None."""
    m = _TIMESTAMP_RE.match(dir_name)
    return m.group(1) if m else None


def build_dir_name(prefix: str, title: str, app_name: str) -> str:
    """Compose a new folder name preserving the timestamp prefix."""
    safe_title = sanitize_subject(title)
    safe_app = "".join(c if c.isalnum() or c in "._-" else "_" for c in app_name)
    if safe_title:
        return f"{prefix}_{safe_title}_{safe_app}"
    return f"{prefix}_{safe_app}"


def current_subject(dir_name: str, app_name: str) -> str:
    """Extract the current subject fragment from a folder name (best-effort)."""
    prefix = timestamp_prefix(dir_name)
    if not prefix:
        return ""
    rest = dir_name[len(prefix) + 1:]
    safe_app = "".join(c if c.isalnum() or c in "._-" else "_" for c in app_name)
    if rest == safe_app:
        return ""  # "{timestamp}_{app}" — no subject
    if rest.endswith(f"_{safe_app}"):
        rest = rest[: -len(safe_app) - 1]
    return rest


def rename_recording_dir(
    recording_dir: Path,
    new_title: str,
    app_name: str = "Meeting",
) -> Optional[Path]:
    """Rename a recording folder to reflect *new_title*, preserving timestamp.

    Returns the new path, or None if the rename was skipped (no timestamp
    prefix, empty/unchanged title, target exists, or OS error). Never raises.
    """
    recording_dir = Path(recording_dir)
    prefix = timestamp_prefix(recording_dir.name)
    if not prefix:
        return None
    safe_title = sanitize_subject(new_title)
    if not safe_title:
        return None

    new_name = build_dir_name(prefix, new_title, app_name)
    if new_name == recording_dir.name:
        return None

    target = recording_dir.parent / new_name
    if target.exists():
        # Collision (e.g. two recordings, same title, same second): suffix it.
        for i in range(2, 100):
            cand = recording_dir.parent / f"{new_name}_{i}"
            if not cand.exists():
                target = cand
                break
        else:
            return None
    try:
        recording_dir.rename(target)
        logger.info("Renamed recording %s -> %s", recording_dir.name, target.name)
        return target
    except OSError:
        logger.warning(
            "Could not rename %s (in use?)", recording_dir.name, exc_info=True,
        )
        return None


def select_meeting_title(
    transcript_excerpt: str,
    candidate_subjects: list[str],
    summary_config=None,
) -> tuple[Optional[str], str]:
    """Pick the meeting title from calendar candidates, or derive one.

    Returns (title, source) where source is one of:
        "single"    — exactly one candidate; used verbatim, no LLM call
        "calendar"  — LLM chose a candidate from the list (matches calendar)
        "generated" — LLM produced a new title (no candidate fit)
        "none"      — could not decide; caller should keep the current name

    Only makes an LLM call when there are 2+ candidates (the genuine
    double-booking case the user asked to disambiguate). Zero candidates
    returns "none" to avoid spending quota on every ad-hoc recording.
    """
    candidates = [s.strip() for s in candidate_subjects if s and s.strip()]
    if len(candidates) == 1:
        return candidates[0], "single"
    if not candidates:
        return None, "none"

    # 2+ candidates: ask the LLM which one the transcript matches.
    title = _llm_pick_title(transcript_excerpt, candidates, summary_config)
    if title is None:
        return None, "none"
    # Match (case-insensitive) against a candidate to report the source.
    for cand in candidates:
        if title.strip().lower() == cand.lower():
            return cand, "calendar"
    return title.strip(), "generated"


_PICK_PROMPT = """\
Several meetings were booked in this time slot. Based ONLY on what the \
transcript is about, choose which calendar event this recording is.

Candidate calendar events (choose one EXACTLY as written if it matches):
{candidates}

If the transcript clearly matches one event, return that event's title \
EXACTLY as written above. If none of them fit, return a short (3-7 word) \
descriptive title of your own. Return ONLY the title text, nothing else.

Transcript excerpt:
{excerpt}
"""


def _llm_pick_title(
    transcript_excerpt: str,
    candidates: list[str],
    summary_config,
) -> Optional[str]:
    """One small LLM call to disambiguate among candidate meeting titles."""
    if summary_config is None or not getattr(summary_config, "api_key", ""):
        # No LLM available: fall back to the first (calendar-best) candidate
        # rather than guessing — better than spending no signal.
        return candidates[0]
    try:
        from meeting_recorder.summary.summarizer import create_provider

        numbered = "\n".join(f"- {c}" for c in candidates)
        prompt = _PICK_PROMPT.format(
            candidates=numbered, excerpt=transcript_excerpt[:4000],
        )
        provider = create_provider(summary_config)
        raw = provider.generate(
            "You label meeting recordings. Reply with only the title.", prompt,
        )
        title = (raw or "").strip().strip('"').splitlines()[0].strip()
        return title or candidates[0]
    except Exception:
        logger.debug("LLM title selection failed; using first candidate",
                     exc_info=True)
        return candidates[0]
