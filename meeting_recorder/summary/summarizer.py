"""AI meeting summary generation with OpenAI and Anthropic providers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Protocol, runtime_checkable

from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)


@dataclass
class ActionItem:
    """A single action item from the meeting."""
    description: str
    assignee: str = ""


@dataclass
class ParticipantStats:
    """Speaking statistics for a single participant."""
    name: str
    speaking_time_seconds: float
    segment_count: int


@dataclass
class MeetingSummary:
    """Complete AI-generated meeting summary."""
    summary: str = ""
    action_items: list[ActionItem] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    participants: list[ParticipantStats] = field(default_factory=list)
    model_used: str = ""
    provider_used: str = ""


@runtime_checkable
class SummaryProvider(Protocol):
    """Protocol for LLM summary providers."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate text from system + user prompts.

        Returns:
            Raw response text from the LLM.
        """
        ...


class OpenAISummaryProvider:
    """OpenAI-based summary provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.api_key = api_key
        self.model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        import openai

        with openai.OpenAI(api_key=self.api_key) as client:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
            return response.choices[0].message.content


class AnthropicSummaryProvider:
    """Anthropic-based summary provider."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        with anthropic.Anthropic(api_key=self.api_key) as client:
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            return response.content[0].text


class GeminiSummaryProvider:
    """Gemini-based summary provider.

    Uses the same system/user prompt as other providers — Gemini is instructed
    to return JSON so the existing _parse_summary_response() can handle it.
    Uses the current ``google-genai`` SDK (not the deprecated ``google.generativeai``).
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        from google import genai
        from google.genai import types

        with genai.Client(api_key=self.api_key) as client:
            response = client.models.generate_content(
                model=self.model,
                contents=f"{system_prompt}\n\n{user_prompt}",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            return response.text


class LunaSummaryProvider:
    """gpt-5.6-luna via the local Codex CLI (``bobby_brain.codex_llm``).

    Runs on Bobby's Codex subscription — no metered API spend. Falls back to
    a secondary provider (normally Gemini on the free-tier key) whenever the
    toolkit is missing or the call fails, so the automatic post-recording
    summary never silently disappears. ``model`` reflects what actually
    answered, so summary.json's model_used stays honest after a fallback.
    """

    def __init__(
        self,
        fallback: SummaryProvider | None = None,
        model: str = "gpt-5.6-luna",
        timeout: int = 300,
    ):
        self.model = model
        self.timeout = timeout
        self.fallback = fallback

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            from bobby_brain.codex_llm import ask
        except ImportError:
            ask = None
            logger.warning(
                "bobby_brain.codex_llm not installed; Luna summary unavailable."
            )

        if ask is not None:
            try:
                reply = ask(
                    f"{system_prompt}\n\n{user_prompt}",
                    model=self.model,
                    timeout=self.timeout,
                )
                if reply.ok and reply.text.strip():
                    return _strip_json_fences(reply.text)
                logger.warning(
                    "Luna summary failed (%s); falling back.",
                    reply.error or "empty output",
                )
            except Exception as e:
                logger.warning("Luna summary raised (%s); falling back.", e)

        if self.fallback is None:
            raise RuntimeError("Luna summary failed and no fallback is configured.")
        text = self.fallback.generate(system_prompt, user_prompt)
        self.model = getattr(self.fallback, "model", "fallback")
        return text


def _strip_json_fences(text: str) -> str:
    """Unwrap a ```json ...``` fenced block if the model added one."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[-1].strip() == "```":
            return "\n".join(lines[1:-1])
    return stripped


def create_provider(config) -> SummaryProvider:
    """Factory to create the appropriate summary provider.

    Args:
        config: SummaryConfig with provider, api_key, and model fields.

    Returns:
        A SummaryProvider instance.

    Raises:
        ValueError: If provider is unknown, or api_key is empty for a
            provider that needs one (luna needs no key; with a key present
            it gains a Gemini fallback).
    """
    if config.provider == "luna":
        fallback = None
        if config.api_key:
            fallback = GeminiSummaryProvider(
                api_key=config.api_key,
                model="gemini-2.5-flash",
            )
        # The shared [summary] model field usually names a Gemini model (it is
        # kept in sync with transcription); only honor it if it's a GPT model.
        model = config.model if (config.model or "").startswith("gpt") else "gpt-5.6-luna"
        return LunaSummaryProvider(fallback=fallback, model=model)

    if not config.api_key:
        raise ValueError("Summary API key is required.")

    if config.provider == "openai":
        model = config.model or "gpt-4o"
        return OpenAISummaryProvider(api_key=config.api_key, model=model)
    elif config.provider == "anthropic":
        model = config.model or "claude-sonnet-4-20250514"
        return AnthropicSummaryProvider(api_key=config.api_key, model=model)
    elif config.provider == "gemini":
        model = config.model or "gemini-2.5-flash"
        return GeminiSummaryProvider(api_key=config.api_key, model=model)
    else:
        raise ValueError(f"Unknown summary provider: {config.provider}")


def compute_participant_stats(segments: list[TranscriptSegment]) -> list[ParticipantStats]:
    """Compute speaking statistics per participant (deterministic, no LLM).

    Args:
        segments: Transcript segments with speaker labels.

    Returns:
        List of ParticipantStats sorted by speaking time (descending).
    """
    stats: dict[str, ParticipantStats] = {}

    for seg in segments:
        speaker = seg.speaker or "Unknown"
        if speaker not in stats:
            stats[speaker] = ParticipantStats(name=speaker, speaking_time_seconds=0.0, segment_count=0)
        stats[speaker].speaking_time_seconds += max(0.0, seg.end - seg.start)
        stats[speaker].segment_count += 1

    return sorted(stats.values(), key=lambda s: s.speaking_time_seconds, reverse=True)


def format_participant_stats(stats: list[ParticipantStats]) -> str:
    """Format participant stats as human-readable text for the prompt."""
    if not stats:
        return ""
    lines = []
    for s in stats:
        minutes = s.speaking_time_seconds / 60
        lines.append(f"- {s.name}: {minutes:.1f} min speaking time ({s.segment_count} segments)")
    return "\n".join(lines)


def generate_summary(
    segments: list[TranscriptSegment],
    config,
    meeting_subject: str = "",
    attendees: list[str] | None = None,
    duration_seconds: float = 0.0,
) -> MeetingSummary:
    """Generate an AI summary of the meeting transcript.

    Args:
        segments: Transcript segments.
        config: SummaryConfig object.
        meeting_subject: Meeting title from calendar.
        attendees: Attendee names.
        duration_seconds: Meeting duration in seconds.

    Returns:
        MeetingSummary with AI-generated content.
    """
    from meeting_recorder.summary.prompts import SYSTEM_PROMPT, build_user_prompt

    # Build transcript text
    transcript_lines = []
    for seg in segments:
        speaker = seg.speaker or "Unknown"
        transcript_lines.append(f"[{seg.start:.1f}s] {speaker}: {seg.text}")
    transcript_text = "\n".join(transcript_lines)

    # Truncate if max_transcript_tokens is set
    if config.max_transcript_tokens > 0:
        max_chars = config.max_transcript_tokens * 4  # rough token estimate
        if len(transcript_text) > max_chars:
            transcript_text = transcript_text[:max_chars] + "\n[...transcript truncated...]"

    # Compute participant stats (deterministic)
    stats = compute_participant_stats(segments)
    stats_text = format_participant_stats(stats)

    # Format duration
    duration_str = ""
    if duration_seconds > 0:
        m, s = divmod(int(duration_seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            duration_str = f"{h}h {m}m"
        else:
            duration_str = f"{m}m {s}s"

    # Build prompt
    user_prompt = build_user_prompt(
        transcript_text=transcript_text,
        meeting_subject=meeting_subject,
        attendees=attendees,
        duration_str=duration_str,
        participant_stats_text=stats_text,
    )

    # Call LLM
    provider = create_provider(config)
    raw_response = provider.generate(SYSTEM_PROMPT, user_prompt)

    # Parse JSON response
    summary = _parse_summary_response(raw_response)
    summary.participants = stats
    summary.model_used = getattr(provider, "model", "")
    summary.provider_used = config.provider

    return summary


def _parse_summary_response(raw: str) -> MeetingSummary:
    """Parse LLM JSON response into MeetingSummary.

    Falls back to putting raw text in summary field if JSON parsing fails.
    """
    try:
        data = json.loads(raw)
        action_items = []
        for item in (data.get("action_items") or []):
            if isinstance(item, dict):
                action_items.append(ActionItem(
                    description=item.get("description", ""),
                    assignee=item.get("assignee", ""),
                ))
            elif isinstance(item, str):
                action_items.append(ActionItem(description=item))

        return MeetingSummary(
            summary=data.get("summary", ""),
            action_items=action_items,
            key_decisions=data.get("key_decisions", []),
            open_questions=data.get("open_questions", []),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse summary JSON: %s. Using raw text.", e)
        return MeetingSummary(summary=raw)


def save_summary(summary: MeetingSummary, recording_dir: Path) -> None:
    """Save meeting summary as JSON and Markdown files.

    Args:
        summary: The MeetingSummary to save.
        recording_dir: Directory to save files into.
    """
    # Save JSON
    json_path = recording_dir / "summary.json"
    json_data = {
        "summary": summary.summary,
        "action_items": [asdict(item) for item in summary.action_items],
        "key_decisions": summary.key_decisions,
        "open_questions": summary.open_questions,
        "participants": [asdict(p) for p in summary.participants],
        "model_used": summary.model_used,
        "provider_used": summary.provider_used,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    # Save Markdown
    md_path = recording_dir / "summary.md"
    md_lines = ["# Meeting Summary\n"]

    if summary.summary:
        md_lines.append(summary.summary)
        md_lines.append("")

    if summary.action_items:
        md_lines.append("## Action Items\n")
        for item in summary.action_items:
            assignee = f" ({item.assignee})" if item.assignee else ""
            md_lines.append(f"- [ ] {item.description}{assignee}")
        md_lines.append("")

    if summary.key_decisions:
        md_lines.append("## Key Decisions\n")
        for decision in summary.key_decisions:
            md_lines.append(f"- {decision}")
        md_lines.append("")

    if summary.open_questions:
        md_lines.append("## Open Questions\n")
        for question in summary.open_questions:
            md_lines.append(f"- {question}")
        md_lines.append("")

    if summary.participants:
        md_lines.append("## Participant Statistics\n")
        for p in summary.participants:
            minutes = p.speaking_time_seconds / 60
            md_lines.append(f"- **{p.name}**: {minutes:.1f} min ({p.segment_count} segments)")
        md_lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    logger.info("Summary saved: %s, %s", json_path.name, md_path.name)
