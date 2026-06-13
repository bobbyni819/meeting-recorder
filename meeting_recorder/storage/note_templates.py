"""Meeting note templates.

Pre-formatted note templates that auto-populate from recording metadata,
transcript, and action items. Useful for sharing structured meeting notes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Template:
    """A meeting note template."""
    name: str
    description: str
    format_func: str  # name of the format function


# Available templates
TEMPLATES = [
    Template("standard", "Standard meeting notes with summary and action items", "standard"),
    Template("standup", "Daily standup format (done/doing/blockers)", "standup"),
    Template("decision", "Decision log with context, options, and outcome", "decision"),
    Template("oneonone", "1:1 meeting notes with talking points and follow-ups", "oneonone"),
    Template("executive", "Executive brief — key points only, no transcript", "executive"),
]


def list_templates() -> list[Template]:
    """Return available templates."""
    return list(TEMPLATES)


def render_template(
    template_name: str,
    rec_path: Path,
    meta: dict | None = None,
) -> str:
    """Render a template for a recording.

    Args:
        template_name: Name of template to use.
        rec_path: Path to recording directory.
        meta: Pre-loaded metadata (optional).

    Returns:
        Formatted note string.
    """
    if meta is None:
        meta = _load_meta(rec_path)

    context = _build_context(rec_path, meta)

    renderers = {
        "standard": _render_standard,
        "standup": _render_standup,
        "decision": _render_decision,
        "oneonone": _render_oneonone,
        "executive": _render_executive,
    }

    renderer = renderers.get(template_name, _render_standard)
    return renderer(context)


def _build_context(rec_path: Path, meta: dict) -> dict:
    """Build template context from recording data."""
    ctx: dict = {
        "subject": meta.get("meeting_subject", rec_path.name),
        "date": "",
        "time": "",
        "duration": "",
        "app": meta.get("app_name", ""),
        "organizer": meta.get("meeting_organizer", ""),
        "attendees": meta.get("meeting_attendees", []),
        "speakers": meta.get("speaker_count", 0),
        "summary": "",
        "transcript": "",
        "action_items": [],
        "tags": meta.get("tags", []),
    }

    # Parse date/time from folder name
    name = rec_path.name
    if len(name) >= 10:
        ctx["date"] = name[:10]
    if len(name) >= 19:
        ctx["time"] = name[11:19].replace("-", ":")

    # Duration
    dur = meta.get("duration_seconds", 0)
    if dur > 0:
        h, remainder = divmod(int(dur), 3600)
        m, s = divmod(remainder, 60)
        if h > 0:
            ctx["duration"] = f"{h}h {m:02d}m"
        else:
            ctx["duration"] = f"{m}m"

    # Summary
    summary_path = rec_path / "summary.md"
    if summary_path.exists():
        try:
            ctx["summary"] = summary_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Transcript
    txt_path = rec_path / "transcript.txt"
    if txt_path.exists():
        try:
            ctx["transcript"] = txt_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Action items
    ai_path = rec_path / "action_items.json"
    if ai_path.exists():
        try:
            with open(ai_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                ctx["action_items"] = [
                    item for item in loaded if isinstance(item, dict)
                ]
        except Exception:
            pass

    return ctx


def _render_standard(ctx: dict) -> str:
    """Standard meeting notes template."""
    lines = []
    lines.append(f"# {ctx['subject']}")
    lines.append("")

    # Header
    info = []
    if ctx["date"]:
        info.append(f"**Date:** {ctx['date']}")
    if ctx["time"]:
        info.append(f"**Time:** {ctx['time']}")
    if ctx["duration"]:
        info.append(f"**Duration:** {ctx['duration']}")
    if ctx["organizer"]:
        info.append(f"**Organizer:** {ctx['organizer']}")
    if info:
        lines.append(" | ".join(info))
        lines.append("")

    if ctx["attendees"]:
        lines.append(f"**Attendees:** {', '.join(ctx['attendees'])}")
        lines.append("")

    # Summary
    if ctx["summary"]:
        lines.append("## Summary")
        lines.append("")
        lines.append(ctx["summary"])
        lines.append("")

    # Action items
    if ctx["action_items"]:
        lines.append("## Action Items")
        lines.append("")
        for item in ctx["action_items"]:
            desc = item.get("description", "")
            assignee = item.get("assignee", "")
            if assignee:
                lines.append(f"- [ ] {desc} (@{assignee})")
            else:
                lines.append(f"- [ ] {desc}")
        lines.append("")

    # Tags
    if ctx["tags"]:
        lines.append(f"**Tags:** {', '.join(ctx['tags'])}")
        lines.append("")

    return "\n".join(lines)


def _render_standup(ctx: dict) -> str:
    """Standup meeting template."""
    lines = []
    lines.append(f"# Standup — {ctx['date']}")
    lines.append("")

    if ctx["attendees"]:
        lines.append(f"**Team:** {', '.join(ctx['attendees'])}")
        lines.append("")

    # Try to extract standup sections from summary
    if ctx["summary"]:
        lines.append("## Notes")
        lines.append("")
        lines.append(ctx["summary"])
        lines.append("")

    lines.append("## Done")
    lines.append("- ")
    lines.append("")
    lines.append("## Doing")
    lines.append("- ")
    lines.append("")
    lines.append("## Blockers")
    lines.append("- ")
    lines.append("")

    if ctx["action_items"]:
        lines.append("## Follow-ups")
        lines.append("")
        for item in ctx["action_items"]:
            lines.append(f"- [ ] {item.get('description', '')}")
        lines.append("")

    return "\n".join(lines)


def _render_decision(ctx: dict) -> str:
    """Decision log template."""
    lines = []
    lines.append(f"# Decision Log — {ctx['subject']}")
    lines.append("")

    info = []
    if ctx["date"]:
        info.append(f"**Date:** {ctx['date']}")
    if ctx["attendees"]:
        info.append(f"**Participants:** {', '.join(ctx['attendees'])}")
    if info:
        lines.append(" | ".join(info))
        lines.append("")

    lines.append("## Context")
    lines.append("")
    if ctx["summary"]:
        lines.append(ctx["summary"])
    else:
        lines.append("_What prompted this discussion?_")
    lines.append("")

    lines.append("## Options Considered")
    lines.append("")
    lines.append("1. ")
    lines.append("2. ")
    lines.append("3. ")
    lines.append("")

    lines.append("## Decision")
    lines.append("")
    lines.append("_What was decided?_")
    lines.append("")

    lines.append("## Rationale")
    lines.append("")
    lines.append("_Why was this option chosen?_")
    lines.append("")

    if ctx["action_items"]:
        lines.append("## Next Steps")
        lines.append("")
        for item in ctx["action_items"]:
            assignee = item.get("assignee", "")
            desc = item.get("description", "")
            if assignee:
                lines.append(f"- [ ] {desc} (@{assignee})")
            else:
                lines.append(f"- [ ] {desc}")
        lines.append("")

    return "\n".join(lines)


def _render_oneonone(ctx: dict) -> str:
    """1:1 meeting template."""
    lines = []
    lines.append(f"# 1:1 — {ctx['subject']}")
    lines.append("")

    if ctx["date"]:
        lines.append(f"**Date:** {ctx['date']}")
    if ctx["attendees"]:
        lines.append(f"**With:** {', '.join(ctx['attendees'])}")
    lines.append("")

    lines.append("## Talking Points")
    lines.append("")
    if ctx["summary"]:
        lines.append(ctx["summary"])
    else:
        lines.append("- ")
    lines.append("")

    lines.append("## Feedback")
    lines.append("")
    lines.append("- ")
    lines.append("")

    lines.append("## Career / Growth")
    lines.append("")
    lines.append("- ")
    lines.append("")

    if ctx["action_items"]:
        lines.append("## Action Items")
        lines.append("")
        for item in ctx["action_items"]:
            lines.append(f"- [ ] {item.get('description', '')}")
        lines.append("")

    lines.append("## Next Meeting")
    lines.append("")
    lines.append("- Topics to revisit: ")
    lines.append("")

    return "\n".join(lines)


def _render_executive(ctx: dict) -> str:
    """Executive brief — key points only."""
    lines = []
    lines.append(f"# Executive Brief — {ctx['subject']}")
    lines.append("")

    info_parts = []
    if ctx["date"]:
        info_parts.append(ctx["date"])
    if ctx["duration"]:
        info_parts.append(ctx["duration"])
    if ctx["attendees"]:
        info_parts.append(f"{len(ctx['attendees'])} attendees")
    if info_parts:
        lines.append(" | ".join(info_parts))
        lines.append("")

    lines.append("## Key Points")
    lines.append("")
    if ctx["summary"]:
        # Extract first paragraph or bullet points
        for line in ctx["summary"].split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(f"- {stripped}" if not stripped.startswith("-") else stripped)
        lines.append("")
    else:
        lines.append("- ")
        lines.append("")

    if ctx["action_items"]:
        lines.append("## Decisions & Actions")
        lines.append("")
        for item in ctx["action_items"][:5]:  # Top 5 only
            assignee = item.get("assignee", "")
            desc = item.get("description", "")
            if assignee:
                lines.append(f"- {desc} → {assignee}")
            else:
                lines.append(f"- {desc}")
        lines.append("")

    return "\n".join(lines)


def _load_meta(rec_path: Path) -> dict:
    """Load metadata from recording."""
    try:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}
