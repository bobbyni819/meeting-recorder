"""Meeting question tracker.

Detects questions in transcripts, identifies who asked them, whether they
appear to have been answered in subsequent dialogue, and surfaces unanswered
questions for follow-up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Patterns that strongly indicate a question
_QUESTION_STARTERS = re.compile(
    r"^\s*(?:who|what|where|when|why|how|which|whose|whom|"
    r"can|could|would|should|will|shall|do|does|did|is|are|was|were|"
    r"have|has|had|might|may)\b",
    re.IGNORECASE,
)

# Answer signals in subsequent text
_ANSWER_SIGNALS = re.compile(
    r"\b(?:yes|no|yeah|nah|sure|absolutely|definitely|"
    r"I think|I believe|the answer|that's because|it's because|"
    r"we decided|the plan is|we should|we will|"
    r"correct|exactly|right|agreed)\b",
    re.IGNORECASE,
)

# Filter out non-questions (rhetorical, filler)
_FILLER_PATTERNS = re.compile(
    r"^\s*(?:you know\??|right\??|okay\??|ok\??|huh\??|"
    r"isn't it\??|aren't they\??|doesn't it\??|"
    r"how are you|how's it going|how's everyone)\s*$",
    re.IGNORECASE,
)


@dataclass
class Question:
    """A detected question from the transcript."""
    text: str
    speaker: str
    line_number: int
    likely_answered: bool
    answer_context: str  # brief context of the answer if found


@dataclass
class QuestionReport:
    """Question analysis for a recording."""
    total_questions: int
    answered_count: int
    unanswered_count: int
    unanswered_questions: list[Question]
    per_speaker: dict[str, int]  # speaker -> question count
    top_questioner: str


def _is_question(text: str) -> bool:
    """Check if text is a genuine question."""
    text = text.strip()
    if len(text) < 10:
        return False
    if _FILLER_PATTERNS.match(text):
        return False
    # Must end with ? or start with question word
    if text.endswith("?"):
        return True
    if _QUESTION_STARTERS.match(text):
        return True
    return False


def _check_answered(lines: list[str], q_line: int, window: int = 5) -> tuple[bool, str]:
    """Check if a question appears answered in subsequent lines.

    Looks at the next `window` lines for answer signals.

    Returns:
        (likely_answered, answer_context)
    """
    answer_lines = lines[q_line + 1: q_line + 1 + window]
    for line in answer_lines:
        if _ANSWER_SIGNALS.search(line):
            # Extract a brief context snippet
            clean = line.strip()
            # Remove speaker prefix if present
            if ":" in clean[:30]:
                clean = clean[clean.index(":") + 1:].strip()
            snippet = clean[:80] + ("..." if len(clean) > 80 else "")
            return True, snippet
    return False, ""


def extract_questions(
    text: str,
    max_questions: int = 30,
) -> list[Question]:
    """Extract questions from transcript text.

    Args:
        text: Full transcript text.
        max_questions: Maximum questions to extract.

    Returns:
        List of Question objects.
    """
    if not text or len(text) < 20:
        return []

    lines = text.splitlines()
    questions: list[Question] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Detect speaker prefix
        speaker = ""
        content = stripped
        if ":" in stripped[:40]:
            parts = stripped.split(":", 1)
            # Check if prefix looks like a speaker name (no spaces in first word, short)
            candidate = parts[0].strip()
            if len(candidate) < 30 and not candidate[0].isdigit():
                speaker = candidate
                content = parts[1].strip()

        # Split on sentence boundaries to find questions within a line
        sentences = re.split(r"(?<=[.!?])\s+", content)
        for sent in sentences:
            if _is_question(sent):
                answered, context = _check_answered(lines, i)
                questions.append(Question(
                    text=sent.strip(),
                    speaker=speaker,
                    line_number=i + 1,
                    likely_answered=answered,
                    answer_context=context,
                ))
                if len(questions) >= max_questions:
                    return questions

    return questions


def analyze_questions(
    rec_path: Path,
) -> QuestionReport | None:
    """Analyze questions in a recording's transcript.

    Args:
        rec_path: Recording directory.

    Returns:
        QuestionReport or None if insufficient data.
    """
    txt_path = rec_path / "transcript.txt"
    if not txt_path.exists():
        return None

    try:
        text = txt_path.read_text(encoding="utf-8")
    except Exception:
        return None

    if len(text) < 50:
        return None

    questions = extract_questions(text)
    if not questions:
        return None

    answered = sum(1 for q in questions if q.likely_answered)
    unanswered = [q for q in questions if not q.likely_answered]

    per_speaker: dict[str, int] = {}
    for q in questions:
        name = q.speaker or "Unknown"
        per_speaker[name] = per_speaker.get(name, 0) + 1

    top = max(per_speaker.items(), key=lambda x: x[1]) if per_speaker else ("", 0)

    return QuestionReport(
        total_questions=len(questions),
        answered_count=answered,
        unanswered_count=len(unanswered),
        unanswered_questions=unanswered,
        per_speaker=per_speaker,
        top_questioner=top[0],
    )


def format_question_report(report: QuestionReport | None) -> str:
    """Format question report as readable text."""
    if report is None:
        return "No questions detected."

    lines = [
        "QUESTION TRACKER",
        "-" * 40,
        f"  Total questions: {report.total_questions}",
        f"  Answered:        {report.answered_count}",
        f"  Unanswered:      {report.unanswered_count}",
        "",
    ]

    if report.per_speaker:
        lines.append("  Questions by Speaker")
        for name, count in sorted(report.per_speaker.items(), key=lambda x: -x[1]):
            lines.append(f"    {name:<20} {count}")
        if report.top_questioner:
            lines.append(f"  Most questions: {report.top_questioner}")
        lines.append("")

    if report.unanswered_questions:
        lines.append("  Unanswered Questions")
        lines.append("  " + "-" * 36)
        for q in report.unanswered_questions[:10]:
            speaker = f"[{q.speaker}] " if q.speaker else ""
            lines.append(f"    {speaker}{q.text}")
        if len(report.unanswered_questions) > 10:
            lines.append(f"    ... and {len(report.unanswered_questions) - 10} more")

    return "\n".join(lines)
