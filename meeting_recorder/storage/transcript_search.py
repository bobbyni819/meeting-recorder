"""Full-text search across all recording transcripts.

Searches transcript.txt and summary.md files for keywords,
returning matching recordings with surrounding context.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    """A single search match in a recording."""
    recording_path: str
    recording_name: str
    subject: str
    date: str
    file_name: str  # transcript.txt or summary.md
    line_number: int
    context: str  # line with match + surrounding text
    match_count: int  # total matches in this recording


def search_transcripts(
    recordings_dir: Path,
    query: str,
    max_results: int = 20,
    search_summaries: bool = True,
    case_sensitive: bool = False,
) -> list[SearchHit]:
    """Search across all recording transcripts for a query.

    Args:
        recordings_dir: Base recordings directory.
        query: Search query (plain text or regex pattern).
        max_results: Maximum number of recordings to return.
        search_summaries: Also search summary.md files.
        case_sensitive: Whether the search is case-sensitive.

    Returns:
        List of SearchHit objects, newest first.
    """
    if not recordings_dir.exists() or not query.strip():
        return []

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error:
        # Fall back to literal match
        pattern = re.compile(re.escape(query), flags)

    hits: list[SearchHit] = []

    files_to_search = ["transcript.txt"]
    if search_summaries:
        files_to_search.append("summary.md")

    # Sort recording directories newest-first
    rec_dirs = sorted(
        (d for d in recordings_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )

    for rec_dir in rec_dirs:
        if len(hits) >= max_results:
            break

        # Load metadata for subject
        subject = ""
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                subject = meta.get("meeting_subject", "")
            except Exception:
                pass

        if not subject and len(rec_dir.name) > 20:
            subject = rec_dir.name[20:].replace("_", " ").strip()

        date_str = rec_dir.name[:10] if len(rec_dir.name) >= 10 else ""

        for fname in files_to_search:
            fpath = rec_dir / fname
            if not fpath.exists():
                continue

            try:
                text = fpath.read_text(encoding="utf-8")
            except Exception:
                continue

            lines = text.split("\n")
            match_count = 0
            first_match_line = 0
            first_context = ""

            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    match_count += 1
                    if match_count == 1:
                        first_match_line = i
                        # Build context: line before, match line, line after
                        ctx_lines = []
                        if i > 1:
                            ctx_lines.append(lines[i - 2].strip())
                        ctx_lines.append(f"> {line.strip()}")
                        if i < len(lines):
                            ctx_lines.append(lines[i].strip())
                        first_context = "\n".join(ctx_lines)[:300]

            if match_count > 0:
                hits.append(SearchHit(
                    recording_path=str(rec_dir),
                    recording_name=rec_dir.name,
                    subject=subject or "Meeting",
                    date=date_str,
                    file_name=fname,
                    line_number=first_match_line,
                    context=first_context,
                    match_count=match_count,
                ))

                if len(hits) >= max_results:
                    break

    return hits


def format_search_results(hits: list[SearchHit], query: str) -> str:
    """Format search results as readable text."""
    if not hits:
        return f"No results found for \"{query}\"."

    lines = [
        f"TRANSCRIPT SEARCH: \"{query}\"",
        "=" * 55,
        f"  {len(hits)} recording(s) matched",
        "",
    ]

    for i, hit in enumerate(hits, 1):
        lines.append(f"  {i}. {hit.subject} ({hit.date})")
        lines.append(f"     {hit.file_name}  |  {hit.match_count} match(es)  |  line {hit.line_number}")
        # Show context indented
        for ctx_line in hit.context.split("\n"):
            lines.append(f"     {ctx_line}")
        lines.append("")

    return "\n".join(lines)
