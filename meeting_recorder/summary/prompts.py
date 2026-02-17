"""Prompt templates for AI meeting summary generation."""

from __future__ import annotations

SYSTEM_PROMPT = """You are a meeting summary assistant. Analyze the provided meeting transcript and return a JSON object with this exact schema:

{
    "summary": "A concise 2-4 paragraph summary of the meeting",
    "action_items": [
        {"description": "What needs to be done", "assignee": "Person responsible or empty string"}
    ],
    "key_decisions": ["Decision 1", "Decision 2"],
    "open_questions": ["Unresolved question 1", "Unresolved question 2"]
}

Rules:
- summary: Focus on outcomes and decisions, not play-by-play
- action_items: Extract concrete tasks with assignees when mentioned. If no assignee is clear, use empty string.
- key_decisions: Only include explicit decisions made during the meeting
- open_questions: Questions raised but not resolved
- Return ONLY valid JSON, no markdown code blocks or extra text"""


def build_user_prompt(
    transcript_text: str,
    meeting_subject: str = "",
    attendees: list[str] | None = None,
    duration_str: str = "",
    participant_stats_text: str = "",
) -> str:
    """Build the user prompt with meeting context and transcript.

    Args:
        transcript_text: Full transcript text.
        meeting_subject: Meeting subject/title from calendar.
        attendees: List of attendee names.
        duration_str: Human-readable duration string.
        participant_stats_text: Pre-formatted participant statistics.

    Returns:
        Formatted user prompt string.
    """
    parts = []

    if meeting_subject:
        parts.append(f"Meeting Subject: {meeting_subject}")
    if attendees:
        parts.append(f"Attendees: {', '.join(attendees)}")
    if duration_str:
        parts.append(f"Duration: {duration_str}")
    if participant_stats_text:
        parts.append(f"\nParticipant Statistics:\n{participant_stats_text}")

    parts.append(f"\n--- TRANSCRIPT ---\n{transcript_text}")

    return "\n".join(parts)
