"""Generate self-contained HTML reports from recordings."""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_html_report(rec_path: Path, meta: dict | None = None) -> str:
    """Generate a self-contained HTML report for a recording.

    Args:
        rec_path: Path to the recording directory.
        meta: Pre-loaded metadata dict, or None to load from disk.

    Returns:
        Complete HTML string ready to save or share.
    """
    if meta is None:
        meta = {}
        try:
            meta_path = rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
        except Exception:
            pass

    name = rec_path.name
    subject = meta.get("meeting_subject", "")
    title = subject if subject else (
        name[20:].replace("_", " ").strip() if len(name) > 20 else "Recording"
    )
    title_escaped = html.escape(title)

    # Date/time from folder name
    date_str = name[:10] if len(name) >= 10 else name
    time_str = name[11:19].replace("-", ":") if len(name) >= 19 else ""

    # Duration
    dur = meta.get("duration_seconds", 0)
    if dur > 0:
        h, remainder = divmod(int(dur), 3600)
        m, s = divmod(remainder, 60)
        dur_str = f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
    else:
        dur_str = ""

    app_name = html.escape(meta.get("app_name", ""))
    status = meta.get("status", "")
    speakers = meta.get("speaker_count", 0)

    # Attendees
    attendees = meta.get("meeting_attendees", [])
    organizer = meta.get("meeting_organizer", "")

    # Read transcript and summary
    transcript = _read_file(rec_path / "transcript.txt")
    summary = _read_file(rec_path / "summary.md")
    notes = _read_file(rec_path / "notes.md")

    # Speaker stats from transcript.json
    speaker_stats_html = _build_speaker_stats(rec_path)

    # Build info badges
    badges = []
    if date_str:
        badges.append(f'<span class="badge">{html.escape(date_str)} {html.escape(time_str)}</span>')
    if dur_str:
        badges.append(f'<span class="badge">{html.escape(dur_str)}</span>')
    if app_name:
        badges.append(f'<span class="badge">{app_name}</span>')
    if speakers > 0:
        badges.append(f'<span class="badge">{speakers} speaker{"s" if speakers != 1 else ""}</span>')
    if status:
        badges.append(f'<span class="badge status-{html.escape(status)}">{html.escape(status)}</span>')

    badges_html = " ".join(badges)

    # Attendees section
    attendees_html = ""
    if organizer or attendees:
        att_items = []
        if organizer:
            att_items.append(f"<li><strong>{html.escape(organizer)}</strong> (organizer)</li>")
        for att in attendees:
            if att != organizer:
                att_items.append(f"<li>{html.escape(att)}</li>")
        attendees_html = f"""
        <div class="section">
            <h2>Attendees</h2>
            <ul>{"".join(att_items)}</ul>
        </div>"""

    # Summary section
    summary_html = ""
    if summary:
        summary_html = f"""
        <div class="section">
            <h2>Summary</h2>
            <div class="content">{_markdown_to_html(summary)}</div>
        </div>"""

    # Transcript section
    transcript_html = ""
    if transcript:
        transcript_html = f"""
        <div class="section">
            <h2>Transcript</h2>
            <div class="transcript">{_format_transcript_html(transcript)}</div>
        </div>"""

    # Notes section
    notes_html = ""
    if notes:
        notes_html = f"""
        <div class="section">
            <h2>Notes</h2>
            <div class="content">{_markdown_to_html(notes)}</div>
        </div>"""

    # Quality section
    quality = meta.get("quality_scores", {})
    quality_html = ""
    if quality and quality.get("overall_score") is not None:
        overall = quality["overall_score"]
        q_color = "#27ae60" if overall >= 75 else "#f39c12" if overall >= 50 else "#e74c3c"
        quality_html = f"""
        <div class="section">
            <h2>Quality</h2>
            <div class="quality-bar">
                <div class="quality-fill" style="width: {overall}%; background: {q_color};"></div>
            </div>
            <p class="quality-label">{overall}/100</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_escaped} — Meeting Recording</title>
<style>
:root {{
    --bg: #1a1a2e;
    --bg-card: #16213e;
    --bg-panel: #0f1a2e;
    --text: #e0e0e0;
    --text-dim: #8899aa;
    --text-bright: #ffffff;
    --accent: #0f3460;
    --green: #27ae60;
    --amber: #f39c12;
    --red: #e74c3c;
    --blue: #3498db;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
}}
h1 {{
    color: var(--text-bright);
    font-size: 1.5em;
    margin-bottom: 8px;
}}
h2 {{
    color: var(--text-bright);
    font-size: 1.1em;
    margin-bottom: 8px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--accent);
}}
.header {{
    background: var(--bg-card);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 16px;
}}
.badges {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
}}
.badge {{
    background: var(--accent);
    color: var(--text);
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.85em;
}}
.badge.status-completed {{ background: var(--green); color: #fff; }}
.badge.status-error {{ background: var(--red); color: #fff; }}
.badge.status-processing {{ background: var(--amber); color: #000; }}
.section {{
    background: var(--bg-card);
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 12px;
}}
.section ul {{
    padding-left: 20px;
}}
.section li {{
    margin-bottom: 4px;
}}
.content {{
    white-space: pre-wrap;
}}
.content p {{
    margin-bottom: 8px;
}}
.transcript {{
    font-size: 0.92em;
    line-height: 1.7;
}}
.speaker {{
    color: var(--blue);
    font-weight: 600;
}}
.timestamp {{
    color: var(--text-dim);
    font-size: 0.85em;
}}
.speaker-stats {{
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 4px 12px;
    align-items: center;
}}
.speaker-bar {{
    background: var(--bg-panel);
    border-radius: 4px;
    height: 16px;
    overflow: hidden;
}}
.speaker-bar-fill {{
    height: 100%;
    border-radius: 4px;
    background: var(--blue);
}}
.quality-bar {{
    background: var(--bg-panel);
    border-radius: 4px;
    height: 20px;
    overflow: hidden;
    margin: 8px 0;
}}
.quality-fill {{
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s;
}}
.quality-label {{
    text-align: center;
    font-size: 0.9em;
    color: var(--text-dim);
}}
.footer {{
    text-align: center;
    color: var(--text-dim);
    font-size: 0.8em;
    margin-top: 24px;
    padding-top: 12px;
    border-top: 1px solid var(--accent);
}}
@media (prefers-color-scheme: light) {{
    :root {{
        --bg: #f5f5f5;
        --bg-card: #ffffff;
        --bg-panel: #eee;
        --text: #333;
        --text-dim: #777;
        --text-bright: #000;
        --accent: #ddd;
    }}
    .badge {{ background: #e0e0e0; color: #333; }}
    .badge.status-completed {{ background: #27ae60; color: #fff; }}
}}
@media print {{
    body {{ max-width: 100%; padding: 10px; }}
    .section {{ break-inside: avoid; }}
}}
</style>
</head>
<body>
<div class="header">
    <h1>{title_escaped}</h1>
    <div class="badges">{badges_html}</div>
</div>
{attendees_html}
{summary_html}
{speaker_stats_html}
{notes_html}
{quality_html}
{transcript_html}
<div class="footer">
    Generated by Meeting Recorder &mdash; {html.escape(date_str)}
</div>
</body>
</html>"""


def _read_file(path: Path) -> str:
    """Read a text file, return empty string on failure."""
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _markdown_to_html(text: str) -> str:
    """Very basic markdown to HTML conversion."""
    lines = text.split("\n")
    result = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(f"<h3>{html.escape(stripped[2:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(f"<h4>{html.escape(stripped[3:])}</h4>")
        elif stripped.startswith("### "):
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(f"<h5>{html.escape(stripped[4:])}</h5>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                result.append("<ul>")
                in_list = True
            item = _inline_format(stripped[2:])
            result.append(f"<li>{item}</li>")
        elif stripped == "---":
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append("<hr>")
        elif stripped == "":
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append("<br>")
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(f"<p>{_inline_format(stripped)}</p>")
    if in_list:
        result.append("</ul>")
    return "\n".join(result)


def _inline_format(text: str) -> str:
    """Apply bold/italic formatting."""
    import re
    escaped = html.escape(text)
    # Bold: **text**
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    # Italic: *text*
    escaped = re.sub(r'\*(.+?)\*', r'<em>\1</em>', escaped)
    return escaped


def _format_transcript_html(transcript: str) -> str:
    """Format plain transcript text into styled HTML."""
    import re
    lines = transcript.split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            result.append("<br>")
            continue
        # Match patterns like "[00:01:23] Speaker Name: text" or "Speaker Name: text"
        ts_match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]\s*', line)
        timestamp = ""
        rest = line
        if ts_match:
            timestamp = ts_match.group(1)
            rest = line[ts_match.end():]

        # Check for "Speaker: text" pattern
        speaker_match = re.match(r'([\w\s]+?):\s+(.+)', rest)
        if speaker_match:
            speaker = html.escape(speaker_match.group(1))
            text = html.escape(speaker_match.group(2))
            ts_html = f'<span class="timestamp">[{html.escape(timestamp)}]</span> ' if timestamp else ""
            result.append(
                f'<p>{ts_html}<span class="speaker">{speaker}:</span> {text}</p>'
            )
        else:
            ts_html = f'<span class="timestamp">[{html.escape(timestamp)}]</span> ' if timestamp else ""
            result.append(f'<p>{ts_html}{html.escape(rest)}</p>')
    return "\n".join(result)


def _build_speaker_stats(rec_path: Path) -> str:
    """Build speaker statistics HTML from transcript.json."""
    transcript_json = rec_path / "transcript.json"
    if not transcript_json.exists():
        return ""

    try:
        with open(transcript_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""

    speaker_times: dict[str, float] = {}
    for seg in data.get("segments", []):
        spk = seg.get("speaker", "Unknown")
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        duration = max(0.0, end - start)
        speaker_times[spk] = speaker_times.get(spk, 0.0) + duration

    if not speaker_times:
        return ""

    total = sum(speaker_times.values())
    rows = []
    for spk, secs in sorted(speaker_times.items(), key=lambda x: -x[1]):
        pct = (secs / total * 100) if total > 0 else 0
        mins = int(secs // 60)
        remaining = int(secs % 60)
        rows.append(
            f'<span>{html.escape(spk)}</span>'
            f'<div class="speaker-bar"><div class="speaker-bar-fill" '
            f'style="width: {pct:.0f}%;"></div></div>'
            f'<span class="timestamp">{mins}:{remaining:02d} ({pct:.0f}%)</span>'
        )

    return f"""
    <div class="section">
        <h2>Speaker Stats</h2>
        <div class="speaker-stats">{"".join(rows)}</div>
    </div>"""
