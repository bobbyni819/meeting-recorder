"""Generate Markdown/Obsidian exports from recordings."""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any


def export_markdown(rec_path: Path, meta: dict | None = None) -> str:
    """Generate a Markdown document for a recording.

    Args:
        rec_path: Path to the recording directory.
        meta: Pre-loaded metadata dict, or None to load from disk.

    Returns:
        Markdown string containing whatever export sections could be built.
    """
    rec_path = Path(rec_path)
    if meta is None:
        meta = _load_metadata(rec_path)
    if not isinstance(meta, dict):
        meta = {}

    title = str(meta.get("meeting_subject") or rec_path.name)
    frontmatter = _build_frontmatter(rec_path, meta, title)
    sections = [frontmatter, f"# {title}"]

    summary = _read_file(rec_path / "summary.md")
    if summary:
        sections.append(f"## Summary\n\n{summary}")

    decisions = _build_decisions_section(rec_path, meta)
    if decisions:
        sections.append(decisions)

    action_items = _build_action_items_section(rec_path, meta)
    if action_items:
        sections.append(action_items)

    transcript = _read_file(rec_path / "transcript.txt")
    if transcript:
        sections.append(f"## Transcript\n\n{_fenced_code(transcript, 'text')}")

    return "\n\n".join(sections).rstrip() + "\n"


def save_markdown(rec_path: Path, out_path: Path | None = None) -> Path:
    """Write a Markdown export for a recording and return the output path."""
    rec_path = Path(rec_path)
    if out_path is None:
        out_path = rec_path / f"{rec_path.name}.md"
    else:
        out_path = Path(out_path)

    out_path.write_text(export_markdown(rec_path), encoding="utf-8")
    return out_path


def _load_metadata(rec_path: Path) -> dict:
    try:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _build_frontmatter(rec_path: Path, meta: dict, title: str) -> str:
    lines = [
        "---",
        f"title: {_yaml_scalar(title)}",
        f"date: {_yaml_scalar(_date_value(rec_path, meta))}",
        f"platform: {_yaml_scalar(meta.get('app_name') or '')}",
        f"duration: {_yaml_scalar(_format_duration(meta.get('duration_seconds')))}",
        _yaml_list("attendees", _as_list(meta.get("meeting_attendees"))),
        _yaml_list("tags", _as_list(meta.get("tags"))),
        f"speakers: {_yaml_number(meta.get('speaker_count'))}",
        f"source: {_yaml_scalar(rec_path.name)}",
        "---",
    ]
    return "\n".join(lines)


def _date_value(rec_path: Path, meta: dict) -> str:
    start_time = meta.get("start_time") or ""
    if start_time:
        return str(start_time)

    name = rec_path.name
    if len(name) >= 19:
        date_part = name[:10]
        time_part = name[11:19].replace("-", ":")
        if re.match(r"\d{4}-\d{2}-\d{2}", date_part) and re.match(
            r"\d{2}:\d{2}:\d{2}", time_part
        ):
            return f"{date_part} {time_part}"
    if len(name) >= 10 and re.match(r"\d{4}-\d{2}-\d{2}", name[:10]):
        return name[:10]
    return ""


def _format_duration(value: Any) -> str:
    try:
        seconds = int(float(value or 0))
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}:{minutes:02d}"


def _read_file(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _build_decisions_section(rec_path: Path, meta: dict) -> str:
    try:
        decision_mod = importlib.import_module("meeting_recorder.storage.decision_log")
        extractor = getattr(decision_mod, "extract_recording_decisions", None)
        if not callable(extractor):
            return ""
        log = extractor(rec_path, meta)
        decisions = _as_list(_field(log, "decisions", []))
        lines = []
        for decision in decisions:
            description = _clean_text(_field(decision, "description", ""))
            if description:
                lines.append(f"- {description}")
        if lines:
            return "## Decisions\n\n" + "\n".join(lines)
    except Exception:
        pass
    return ""


def _build_action_items_section(rec_path: Path, meta: dict) -> str:
    try:
        action_mod = importlib.import_module("meeting_recorder.storage.action_items")
        items = []

        loader = getattr(action_mod, "load_action_items", None)
        if callable(loader):
            try:
                items = _as_list(loader(rec_path))
            except Exception:
                items = []

        if not items:
            extractor = getattr(action_mod, "extract_action_items_for_recording", None)
            if callable(extractor):
                items = _as_list(extractor(rec_path, meta))

        lines = []
        for item in items:
            description = _clean_text(_field(item, "description", ""))
            if description:
                assignee = _clean_text(_field(item, "assignee", ""))
                suffix = f" ({assignee})" if assignee else ""
                lines.append(f"- [ ] {description}{suffix}")
        if lines:
            return "## Action Items\n\n" + "\n".join(lines)
    except Exception:
        pass
    return ""


def _field(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    return [value]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _fenced_code(text: str, language: str = "") -> str:
    longest = 0
    for match in re.finditer(r"`+", text):
        longest = max(longest, len(match.group(0)))
    fence = "`" * max(3, longest + 1)
    suffix = language if language else ""
    return f"{fence}{suffix}\n{text}\n{fence}"


def _yaml_scalar(value: Any) -> str:
    if value is None:
        value = ""
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_number(value: Any) -> str:
    try:
        return str(int(value or 0))
    except (TypeError, ValueError):
        return "0"


def _yaml_list(name: str, values: list) -> str:
    cleaned = [_clean_text(value) for value in values]
    cleaned = [value for value in cleaned if value]
    if not cleaned:
        return f"{name}: []"
    lines = [f"{name}:"]
    lines.extend(f"  - {_yaml_scalar(value)}" for value in cleaned)
    return "\n".join(lines)
