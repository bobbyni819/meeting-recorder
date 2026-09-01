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

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    from bobby_brain.codex_llm import ask_json
except ImportError:
    ask_json = None

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
    candidate_subjects: list[object],
    summary_config=None,
    *,
    recording_start_time: str = "",
    duration_seconds: float = 0.0,
    llm_backend: str = "luna",
    gemini_api_key: str = "",
    gemini_model: str = "",
) -> tuple[Optional[str], str]:
    """Pick the meeting title from calendar candidates, or derive one.

    Returns (title, source) where source is one of:
        "calendar"  — LLM chose a candidate from the list (matches calendar)
        "generated" — LLM produced a new title (no candidate fit)
        "none"      — could not decide; caller should keep the current name

    One or more candidates are always arbitrated against transcript content;
    zero candidates returns "none" to avoid naming every ad-hoc recording.
    Plain string candidates remain supported for callers without calendar
    event details.
    """
    candidates = _normalize_candidates(candidate_subjects)
    if not candidates:
        return None, "none"

    return _llm_pick_title(
        transcript_excerpt,
        candidates,
        summary_config,
        recording_start_time=recording_start_time,
        duration_seconds=duration_seconds,
        llm_backend=llm_backend,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
    )


_PICK_PROMPT = """\
Decide whether this recording actually matches one of its candidate calendar \
events. Use the recording timing and transcript content together.

Recording start: {recording_start}
Recording duration: {duration_seconds} seconds

Candidate calendar events (indices are zero-based):
{candidates}

If the transcript clearly matches one event, copy that event's title exactly. \
If none fit, generate a short 3-7 word title from the transcript content.

Return only JSON with this exact shape:
{{"title": "...", "source": "calendar"|"generated", \
"matched_candidate_index": 0|null}}

Transcript excerpt:
{excerpt}
"""


@dataclass(frozen=True)
class _TitleCandidate:
    """Normalized calendar event context used by the title arbiters."""

    subject: str
    start_time: str = ""
    end_time: str = ""
    attendees: tuple[str, ...] = ()


def _normalize_candidates(candidates: list[object]) -> list[_TitleCandidate]:
    """Normalize CalendarEvent-like objects and legacy strings."""
    normalized: list[_TitleCandidate] = []
    for candidate in candidates or []:
        try:
            if isinstance(candidate, str):
                subject = candidate.strip()
                start_time = ""
                end_time = ""
                attendees: tuple[str, ...] = ()
            else:
                subject = str(getattr(candidate, "subject", "") or "").strip()
                start_time = str(getattr(candidate, "start_time", "") or "")
                end_time = str(getattr(candidate, "end_time", "") or "")
                raw_attendees = getattr(candidate, "attendees", None) or []
                attendees = tuple(
                    str(attendee).strip() for attendee in raw_attendees
                    if str(attendee).strip()
                )
            if subject:
                normalized.append(_TitleCandidate(
                    subject=subject,
                    start_time=start_time,
                    end_time=end_time,
                    attendees=attendees,
                ))
        except Exception:
            logger.debug("Ignoring malformed smart-name candidate", exc_info=True)
    return normalized


def _build_pick_prompt(
    transcript_excerpt: str,
    candidates: list[_TitleCandidate],
    recording_start_time: str,
    duration_seconds: float,
) -> str:
    """Build the shared Luna/Gemini arbitration prompt."""
    event_lines = []
    for index, candidate in enumerate(candidates):
        details = [f"{index}. {candidate.subject}"]
        details.append(f"   start: {candidate.start_time or 'unknown'}")
        details.append(f"   end: {candidate.end_time or 'unknown'}")
        if candidate.attendees:
            details.append(f"   attendees: {', '.join(candidate.attendees)}")
        event_lines.append("\n".join(details))
    return _PICK_PROMPT.format(
        recording_start=recording_start_time or "unknown",
        duration_seconds=float(duration_seconds or 0.0),
        candidates="\n".join(event_lines),
        excerpt=str(transcript_excerpt or "")[:4000],
    )


# Title words too generic to disambiguate meetings by.
_TITLE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "meeting", "call", "sync", "weekly",
    "monthly", "talk", "chat", "discussion", "review", "session", "zoom",
    "teams", "webex", "via", "and", "program", "team", "group", "update",
    "catch", "standup", "check", "in", "on", "at", "to", "of", "am", "pm",
})


def _best_candidate_by_content(
    transcript_excerpt: str, candidates: list[str],
) -> str:
    """Pick the candidate whose title words best match the transcript.

    Free, local, deterministic — used when the LLM disambiguator is
    unavailable or fails (common on the free tier). Scores each candidate by
    the fraction of its significant title words that appear in the transcript,
    so a same-slot meeting whose subject is actually discussed wins over an
    unrelated one. Falls back to the first candidate only on a true tie.
    """
    low = transcript_excerpt.lower()
    best = candidates[0]
    best_score = -1
    for cand in candidates:
        words = {
            w for w in re.findall(r"[a-zA-Z]{3,}", cand.lower())
            if w not in _TITLE_STOPWORDS
        }
        # Total whole-word occurrences of the title's significant words in the
        # transcript. Frequency matters: a meeting whose subject is discussed
        # repeatedly (e.g. "ABM" 5x) beats one with a single generic hit
        # (e.g. "data" 1x), which fraction-of-words would tie.
        score = sum(
            len(re.findall(rf"\b{re.escape(w)}\b", low)) for w in words
        )
        if score > best_score:
            best, best_score = cand, score
    return best


def _llm_pick_title(
    transcript_excerpt: str,
    candidates: list[_TitleCandidate],
    summary_config,
    *,
    recording_start_time: str = "",
    duration_seconds: float = 0.0,
    llm_backend: str = "luna",
    gemini_api_key: str = "",
    gemini_model: str = "",
) -> tuple[Optional[str], str]:
    """Disambiguate among candidate meeting titles.

    The configured rung starts a no-raise chain: Luna -> Gemini -> local
    content scorer. ``gemini`` and ``local`` skip earlier rungs.
    """
    try:
        subjects = [candidate.subject for candidate in candidates]
        if not subjects:
            return None, "none"
        local_best = _best_candidate_by_content(
            str(transcript_excerpt or ""), subjects,
        )
        prompt = _build_pick_prompt(
            transcript_excerpt,
            candidates,
            recording_start_time,
            duration_seconds,
        )
        backend = str(llm_backend or "luna").strip().lower()
        if backend not in {"luna", "gemini", "local"}:
            logger.warning(
                "Unknown smart rename LLM %r; using local scorer", llm_backend,
            )
            backend = "local"

        if backend == "luna" and ask_json is not None:
            try:
                reply_value = ask_json(
                    prompt,
                    model="gpt-5.6-luna",
                    timeout=60,
                )
                if isinstance(reply_value, tuple) and len(reply_value) == 2:
                    data, reply = reply_value
                    if getattr(reply, "ok", False):
                        selection = _validate_title_response(data, candidates)
                        if selection is not None:
                            return selection
            except Exception:
                logger.debug(
                    "Luna title selection failed; trying Gemini",
                    exc_info=True,
                )

        if backend in {"luna", "gemini"}:
            try:
                provider_name = str(
                    getattr(summary_config, "provider", "") or ""
                ).lower()
                summary_gemini_key = (
                    getattr(summary_config, "api_key", "") or ""
                    if provider_name in {"gemini", "luna"}
                    else ""
                )
                api_key = gemini_api_key or summary_gemini_key
                if api_key:
                    from meeting_recorder.summary.summarizer import (
                        GeminiSummaryProvider,
                    )

                    summary_model = str(
                        getattr(summary_config, "model", "") or ""
                    ) if provider_name in {"gemini", "luna"} else ""
                    configured_model = str(gemini_model or summary_model)
                    model = (
                        configured_model
                        if configured_model.startswith("gemini")
                        else "gemini-2.5-flash"
                    )
                    provider = GeminiSummaryProvider(
                        api_key=api_key,
                        model=model,
                    )
                    raw = provider.generate(
                        "You label meeting recordings. Reply with valid JSON only.",
                        prompt,
                    )
                    selection = _validate_title_response(
                        _parse_json_object(raw), candidates,
                    )
                    if selection is not None:
                        return selection
            except Exception:
                logger.debug(
                    "Gemini title selection failed; using local content match",
                    exc_info=True,
                )

        return local_best, "calendar"
    except Exception:
        logger.debug(
            "Smart title selection failed unexpectedly",
            exc_info=True,
        )
        try:
            subjects = [
                candidate.subject for candidate in candidates if candidate.subject
            ]
            if subjects:
                return _best_candidate_by_content(
                    str(transcript_excerpt or ""), subjects,
                ), "calendar"
        except Exception:
            logger.debug("Local title selection also failed", exc_info=True)
        return None, "none"


def _parse_json_object(raw: Any) -> object | None:
    """Extract a JSON object from a provider response without raising."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            return None
        try:
            return json.loads(match.group(0))
        except (TypeError, json.JSONDecodeError):
            return None


def _validate_title_response(
    data: object,
    candidates: list[_TitleCandidate],
) -> Optional[tuple[str, str]]:
    """Validate and canonicalize the structured arbiter response."""
    if not isinstance(data, dict):
        return None
    title = data.get("title")
    source = data.get("source")
    index = data.get("matched_candidate_index")
    if not isinstance(title, str) or not title.strip():
        return None
    title = title.strip()

    if source == "calendar":
        if isinstance(index, bool) or not isinstance(index, int):
            return None
        if index < 0 or index >= len(candidates):
            return None
        canonical = candidates[index].subject
        if title.casefold() != canonical.casefold():
            return None
        return canonical, "calendar"

    if source == "generated":
        if index is not None:
            return None
        if any(title.casefold() == candidate.subject.casefold() for candidate in candidates):
            return None
        word_count = len(re.findall(r"\b[\w'-]+\b", title))
        if not 3 <= word_count <= 7:
            return None
        return title, "generated"

    return None
