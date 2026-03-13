"""Meeting decision log extractor.

Scans transcript text for statements indicating decisions were made.
Distinct from action items — decisions record *what was decided*, not
*what someone will do*. E.g. "We decided to use PostgreSQL" vs "John will
set up the database".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Patterns that indicate a decision (case-insensitive)
_DECISION_PATTERNS: list[tuple[str, str]] = [
    # Explicit decision language
    (r"\b((?:we|they|the team|the group|everyone)\s+(?:decided|agreed|concluded|determined|resolved))\s+(?:to\s+|that\s+)?(.{10,150}?)(?:\.|$)", "explicit"),
    # "The decision is/was..."
    (r"\b((?:the\s+)?decision\s+(?:is|was|will be))\s+(?:to\s+|that\s+)?(.{10,150}?)(?:\.|$)", "explicit"),
    # "We're going with / going to go with"
    (r"\b((?:we(?:'re| are)\s+going (?:with|to go with)))\s+(.{10,150}?)(?:\.|$)", "choice"),
    # "Let's go with / go ahead with"
    (r"\b((?:let'?s\s+go (?:with|ahead with|ahead and)))\s+(.{10,150}?)(?:\.|$)", "choice"),
    # "We chose / selected / picked"
    (r"\b((?:we|they)\s+(?:chose|selected|picked|opted for|settled on|landed on))\s+(.{10,150}?)(?:\.|$)", "choice"),
    # "The plan is..."
    (r"\b((?:the\s+plan\s+(?:is|will be)))\s+(?:to\s+)?(.{10,150}?)(?:\.|$)", "plan"),
    # "We'll / We will (do something)" — often signals a decision
    (r"\b((?:we(?:'ll| will))\s+(?:use|implement|adopt|switch to|move to|migrate to|go with|roll out|deploy|launch|ship|release|build|create|start|stop|keep|continue|drop|remove|add|enable|disable))\s+(.{10,150}?)(?:\.|$)", "decision_verb"),
    # "It's been decided / It was agreed"
    (r"\b((?:it(?:'s| has| was)\s+been\s+(?:decided|agreed|determined|concluded)))\s+(?:to\s+|that\s+)?(.{10,150}?)(?:\.|$)", "explicit"),
    # "Final answer / final decision"
    (r"\b((?:(?:the\s+)?final\s+(?:answer|decision|call|verdict)\s+is))\s+(.{10,150}?)(?:\.|$)", "explicit"),
    # "Consensus is..."
    (r"\b((?:(?:the\s+)?consensus\s+(?:is|was|seems to be)))\s+(?:to\s+|that\s+)?(.{10,150}?)(?:\.|$)", "consensus"),
    # "We approved / signed off on"
    (r"\b((?:we|they)\s+(?:approved|signed off on|gave the go-ahead|greenlit|green-lit|ratified|endorsed))\s+(.{10,150}?)(?:\.|$)", "approval"),
    # "Moving forward with..."
    (r"\b(moving forward (?:with|we(?:'ll| will)))\s+(.{10,150}?)(?:\.|$)", "choice"),
]

_COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), category)
    for pattern, category in _DECISION_PATTERNS
]

# Words that suggest past tense / historical context (not a new decision)
_HISTORICAL_INDICATORS = frozenset({
    "previously", "last time", "last week", "last month", "back then",
    "in the past", "originally", "used to",
})

_MIN_DECISION_LENGTH = 15


@dataclass
class Decision:
    """A single extracted decision."""
    description: str
    category: str  # explicit, choice, plan, decision_verb, consensus, approval
    context: str  # surrounding text
    speaker: str  # who announced it (if detectable)
    line_number: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecisionLog:
    """Collection of decisions from a meeting."""
    decisions: list[Decision]
    recording_path: str
    meeting_subject: str
    meeting_date: str

    def to_dict(self) -> dict:
        return {
            "decisions": [d.to_dict() for d in self.decisions],
            "recording_path": self.recording_path,
            "meeting_subject": self.meeting_subject,
            "meeting_date": self.meeting_date,
        }


def extract_decisions(
    text: str,
    max_items: int = 20,
) -> list[Decision]:
    """Extract decisions from transcript text.

    Args:
        text: Full transcript text.
        max_items: Maximum number of decisions to return.

    Returns:
        List of Decision objects, deduplicated.
    """
    if not text or len(text) < 50:
        return []

    lines = text.split("\n")
    seen_descriptions: set[str] = set()
    decisions: list[Decision] = []

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        # Skip lines that are clearly historical
        lower = stripped.lower()
        if any(ind in lower for ind in _HISTORICAL_INDICATORS):
            continue

        # Try speaker extraction
        speaker = ""
        speaker_match = re.match(r"^([A-Z][a-z]+(?:\s[A-Z][a-z]+)?):\s*", stripped)
        if speaker_match:
            speaker = speaker_match.group(1)

        for pattern, category in _COMPILED_PATTERNS:
            for m in pattern.finditer(stripped):
                # Get the decision text from the last capture group
                desc = m.group(m.lastindex or 0).strip()

                # Clean up
                desc = re.sub(r"\s+", " ", desc).strip()
                desc = desc.rstrip(",;:")

                if len(desc) < _MIN_DECISION_LENGTH:
                    continue

                # Deduplicate by normalized text
                norm = desc.lower()
                if norm in seen_descriptions:
                    continue

                # Check for substantial overlap with existing decisions
                skip = False
                for existing in seen_descriptions:
                    if _text_overlap(norm, existing) > 0.7:
                        skip = True
                        break
                if skip:
                    continue

                seen_descriptions.add(norm)

                # Context: the full line
                context = stripped[:200]

                decisions.append(Decision(
                    description=desc[:150],
                    category=category,
                    context=context,
                    speaker=speaker,
                    line_number=line_num,
                ))

                if len(decisions) >= max_items:
                    return decisions

    return decisions


def extract_recording_decisions(
    rec_path: Path,
    meta: dict | None = None,
) -> DecisionLog | None:
    """Extract decisions from a recording directory.

    Args:
        rec_path: Recording directory.
        meta: Pre-loaded metadata (loaded from file if None).

    Returns:
        DecisionLog or None if no transcript.
    """
    txt_path = rec_path / "transcript.txt"
    if not txt_path.exists():
        return None

    try:
        text = txt_path.read_text(encoding="utf-8")
    except Exception:
        return None

    if not text.strip():
        return None

    if meta is None:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        else:
            meta = {}

    decisions = extract_decisions(text)
    if not decisions:
        return None

    subject = meta.get("meeting_subject", "")
    date_str = rec_path.name[:10] if len(rec_path.name) >= 10 else ""

    return DecisionLog(
        decisions=decisions,
        recording_path=str(rec_path),
        meeting_subject=subject,
        meeting_date=date_str,
    )


def save_decisions(rec_path: Path, log: DecisionLog) -> Path:
    """Save decisions to a JSON file in the recording directory.

    Returns:
        Path to the saved file.
    """
    out_path = rec_path / "decisions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(log.to_dict(), f, indent=2)
    return out_path


def format_decision_log(log: DecisionLog | None) -> str:
    """Format a decision log as readable text."""
    if log is None or not log.decisions:
        return "No decisions detected in this recording."

    lines = [
        "DECISION LOG",
        "=" * 50,
    ]

    if log.meeting_subject:
        lines.append(f"  Meeting: {log.meeting_subject}")
    if log.meeting_date:
        lines.append(f"  Date:    {log.meeting_date}")
    lines.append(f"  Decisions: {len(log.decisions)}")
    lines.append("")

    category_labels = {
        "explicit": "Decision",
        "choice": "Choice",
        "plan": "Plan",
        "decision_verb": "Direction",
        "consensus": "Consensus",
        "approval": "Approval",
    }

    for i, d in enumerate(log.decisions, 1):
        label = category_labels.get(d.category, d.category.title())
        lines.append(f"  {i}. [{label}] {d.description}")
        if d.speaker:
            lines.append(f"     Announced by: {d.speaker}")
        lines.append("")

    return "\n".join(lines)


def _text_overlap(a: str, b: str) -> float:
    """Compute word overlap ratio between two strings."""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))
