"""Extract action items from meeting transcripts.

Scans transcript text for patterns indicating tasks, commitments,
follow-ups, and decisions. Returns structured action items with
assignee, description, and context.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

# Patterns that indicate an action item (case-insensitive)
_ACTION_PATTERNS: list[tuple[str, str]] = [
    # Direct commitments: "I will...", "I'll...", "I'm going to..."
    (r"\b(I(?:'ll| will| am going to| 'm going to))\s+(.{10,120}?)(?:\.|$)", "commitment"),
    # Assignments: "X will...", "X should...", "X needs to..."
    (r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(will|should|needs? to|has to|is going to)\s+(.{10,120}?)(?:\.|$)", "assignment"),
    # Let's / We need to / We should
    (r"\b((?:let'?s|we (?:need to|should|have to|must)))\s+(.{10,120}?)(?:\.|$)", "team_action"),
    # Can you / Could you / Would you
    (r"\b((?:can|could|would) you)\s+(.{10,120}?)(?:\.|$)", "request"),
    # Action verbs at sentence start (after speaker label)
    (r"(?:^|\n)(?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)?:\s*)?((?:Follow up|Schedule|Send|Create|Update|Review|Set up|Prepare|Draft|Submit|Book|Arrange|Organize|Plan|Write|Complete|Finalize|Check|Verify|Confirm|Reach out|Contact|Email|Call|Meet with|Talk to|Look into|Investigate|Research|Analyze|Test|Deploy|Fix|Resolve|Address))\s+(.{10,120}?)(?:\.|$)", "directive"),
    # TODO / action item / follow-up markers
    (r"\b((?:todo|action item|follow[- ]?up|next step|takeaway|deliverable)[:\s]+)(.{10,120}?)(?:\.|$)", "explicit"),
    # Due dates: "by Friday", "by end of week", "before the meeting"
    (r"\b(.{15,100}?)\s+(by (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|tomorrow|end of (?:day|week|month|quarter|sprint)|next (?:week|Monday|Tuesday|Wednesday|Thursday|Friday)|the (?:\d{1,2}(?:st|nd|rd|th)?|meeting|deadline|EOD|EOW)))\b", "deadline"),
]

# Compiled patterns for performance
_COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), category)
    for pattern, category in _ACTION_PATTERNS
]

# Words that indicate something is NOT an action item
_NEGATIVE_INDICATORS = frozenset({
    "yesterday", "last week", "last month", "already", "completed",
    "finished", "done", "did", "was", "were", "had",
})

# Minimum text length for a valid action item
_MIN_ITEM_LENGTH = 15


@dataclass
class ActionItem:
    """A single extracted action item."""
    description: str
    category: str  # commitment, assignment, team_action, request, directive, explicit, deadline
    assignee: str  # Who should do it (empty if unknown)
    context: str  # Surrounding text for reference
    line_number: int  # Approximate position in transcript

    def to_dict(self) -> dict:
        return asdict(self)


def extract_action_items(
    text: str,
    max_items: int = 20,
) -> list[ActionItem]:
    """Extract action items from transcript text.

    Args:
        text: Full transcript text.
        max_items: Maximum number of items to return.

    Returns:
        List of ActionItem objects, deduplicated and ranked by confidence.
    """
    if not text or len(text) < 50:
        return []

    items: list[ActionItem] = []
    seen_descriptions: set[str] = set()
    lines = text.split("\n")

    for pattern, category in _COMPILED_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()

            if category == "commitment":
                prefix, desc = groups[0], groups[1]
                assignee = "me"
                description = f"{prefix} {desc}".strip()
            elif category == "assignment":
                name, verb, desc = groups[0], groups[1], groups[2]
                assignee = name
                description = f"{name} {verb} {desc}".strip()
            elif category in ("team_action", "request"):
                prefix, desc = groups[0], groups[1]
                assignee = ""
                description = f"{prefix} {desc}".strip()
            elif category == "directive":
                verb, desc = groups[0], groups[1]
                assignee = ""
                description = f"{verb} {desc}".strip()
            elif category == "explicit":
                _, desc = groups[0], groups[1]
                assignee = ""
                description = desc.strip()
            elif category == "deadline":
                desc, deadline = groups[0], groups[1]
                assignee = ""
                description = f"{desc.strip()} ({deadline})"
            else:
                continue

            # Clean up description
            description = _clean_description(description)

            if len(description) < _MIN_ITEM_LENGTH:
                continue

            # Check for negative indicators (past tense / already done)
            desc_lower = description.lower()
            if any(neg in desc_lower for neg in _NEGATIVE_INDICATORS):
                continue

            # Deduplicate by normalized description
            norm = _normalize(description)
            if norm in seen_descriptions:
                continue
            seen_descriptions.add(norm)

            # Find line number
            match_start = match.start()
            line_no = text[:match_start].count("\n") + 1

            # Get context (surrounding line)
            context_line_idx = min(line_no - 1, len(lines) - 1)
            context = lines[context_line_idx].strip() if context_line_idx >= 0 else ""

            items.append(ActionItem(
                description=description,
                category=category,
                assignee=assignee,
                context=context[:200],
                line_number=line_no,
            ))

    # Sort: explicit > directive > assignment > commitment > team_action > request > deadline
    priority = {
        "explicit": 0, "directive": 1, "assignment": 2,
        "commitment": 3, "team_action": 4, "request": 5, "deadline": 6,
    }
    items.sort(key=lambda x: (priority.get(x.category, 99), x.line_number))

    return items[:max_items]


def extract_action_items_for_recording(
    rec_path: Path,
    meta: dict | None = None,
) -> list[ActionItem]:
    """Extract action items from a recording directory.

    Reads transcript.txt and optionally summary.md.

    Args:
        rec_path: Path to recording directory.
        meta: Optional pre-loaded metadata dict.

    Returns:
        List of ActionItem objects.
    """
    text_parts: list[str] = []

    # Read transcript
    transcript_path = rec_path / "transcript.txt"
    if transcript_path.exists():
        try:
            text_parts.append(transcript_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read transcript for action items: %s", rec_path)

    # Also scan summary for action items
    summary_path = rec_path / "summary.md"
    if summary_path.exists():
        try:
            text_parts.append(summary_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not text_parts:
        return []

    combined = "\n\n".join(text_parts)
    return extract_action_items(combined)


def save_action_items(rec_path: Path, items: list[ActionItem]) -> None:
    """Save extracted action items to action_items.json in the recording dir."""
    data = [item.to_dict() for item in items]
    try:
        with open(rec_path / "action_items.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to save action items for %s", rec_path)


def load_action_items(rec_path: Path) -> list[ActionItem]:
    """Load previously extracted action items from disk."""
    path = rec_path / "action_items.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [ActionItem(**item) for item in data]
    except Exception:
        logger.warning("Failed to load action items from %s", rec_path)
        return []


def format_action_items(items: list[ActionItem]) -> str:
    """Format action items as readable text."""
    if not items:
        return ""

    lines: list[str] = ["ACTION ITEMS", "=" * 40, ""]

    # Group by category
    by_cat: dict[str, list[ActionItem]] = {}
    for item in items:
        by_cat.setdefault(item.category, []).append(item)

    category_labels = {
        "explicit": "Explicit Action Items",
        "directive": "Directives",
        "assignment": "Assignments",
        "commitment": "Commitments",
        "team_action": "Team Actions",
        "request": "Requests",
        "deadline": "Items with Deadlines",
    }

    for cat in ["explicit", "directive", "assignment", "commitment",
                "team_action", "request", "deadline"]:
        cat_items = by_cat.get(cat, [])
        if not cat_items:
            continue
        lines.append(f"## {category_labels.get(cat, cat)}")
        lines.append("")
        for item in cat_items:
            assignee_str = f" [{item.assignee}]" if item.assignee else ""
            lines.append(f"  - {item.description}{assignee_str}")
        lines.append("")

    return "\n".join(lines)


def _clean_description(text: str) -> str:
    """Clean up an extracted action item description."""
    # Remove leading/trailing whitespace and punctuation
    text = text.strip().rstrip(",;:")
    # Remove repeated spaces
    text = re.sub(r"\s+", " ", text)
    # Capitalize first letter
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def _normalize(text: str) -> str:
    """Normalize text for deduplication."""
    return re.sub(r"\s+", " ", text.lower().strip())
