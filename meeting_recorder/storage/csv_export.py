"""CSV/data export for meeting recordings.

Export recording metadata, speaker stats, action items, and focus time
to CSV files for analysis in spreadsheets or BI tools.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import fields
from pathlib import Path

logger = logging.getLogger(__name__)


def export_recordings_csv(recordings_dir: Path) -> str:
    """Export recording metadata to CSV string.

    Columns: folder, date, time, subject, app, duration_min, speakers,
    status, attendees, organizer, has_transcript, has_summary, tags.
    """
    rows: list[dict] = []

    if not recordings_dir.exists():
        return ""

    for rec_dir in sorted(recordings_dir.iterdir(), reverse=True):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        meta = _load_meta(rec_dir)
        name = rec_dir.name
        date_str = name[:10] if len(name) >= 10 else ""
        time_str = name[11:19].replace("-", ":") if len(name) >= 19 else ""

        dur = meta.get("duration_seconds", 0)
        attendees = meta.get("meeting_attendees", [])

        # Quality scores
        qs = meta.get("quality_scores", {})
        quality = qs.get("overall_score", "")
        audio_q = qs.get("audio_score", "")
        transcript_q = qs.get("transcript_score", "")

        # Sentiment
        sentiment_score = ""
        sentiment_label = ""
        try:
            from meeting_recorder.storage.sentiment import analyze_recording_sentiment
            sent = analyze_recording_sentiment(rec_dir)
            if sent:
                sentiment_score = sent.score
                sentiment_label = sent.label
        except Exception:
            pass

        # Meeting type classification
        meeting_type = ""
        try:
            from meeting_recorder.storage.meeting_classifier import classify_recording
            cls = classify_recording(rec_dir, meta=meta)
            if cls and cls.confidence > 0.2:
                meeting_type = cls.meeting_type
        except Exception:
            pass

        # Decision count
        dec_count = ""
        dec_path = rec_dir / "decisions.json"
        if dec_path.exists():
            try:
                with open(dec_path, "r", encoding="utf-8") as f:
                    dec_data = json.load(f)
                dec_list = dec_data.get("decisions", []) if isinstance(dec_data, dict) else dec_data
                dec_count = len(dec_list)
            except Exception:
                pass

        # Action items count
        action_count = ""
        ai_path = rec_dir / "action_items.json"
        if ai_path.exists():
            try:
                with open(ai_path, "r", encoding="utf-8") as f:
                    items = json.load(f)
                action_count = len(items)
            except Exception:
                pass

        # Velocity score
        velocity = ""
        try:
            from meeting_recorder.storage.velocity import analyze_velocity
            vel = analyze_velocity(rec_dir, meta=meta)
            if vel:
                velocity = vel.overall_velocity
        except Exception:
            pass

        # Interruption count
        interruptions = ""
        flow_score = ""
        try:
            from meeting_recorder.storage.interruptions import analyze_interruptions
            ir = analyze_interruptions(rec_dir)
            if ir is not None:
                interruptions = ir.total_interruptions
                flow_score = ir.flow_score
        except Exception:
            pass

        rows.append({
            "folder": name,
            "date": date_str,
            "time": time_str,
            "subject": meta.get("meeting_subject", ""),
            "app": meta.get("app_name", ""),
            "duration_min": round(dur / 60, 1) if dur else 0,
            "speakers": meta.get("speaker_count", 0),
            "status": meta.get("status", ""),
            "attendees": "; ".join(attendees) if attendees else "",
            "attendee_count": len(attendees),
            "organizer": meta.get("meeting_organizer", ""),
            "has_transcript": "yes" if (rec_dir / "transcript.json").exists() else "no",
            "has_summary": "yes" if meta.get("has_summary") else "no",
            "tags": "; ".join(meta.get("tags", [])),
            "quality": quality,
            "audio_quality": audio_q,
            "transcript_quality": transcript_q,
            "sentiment_score": sentiment_score,
            "sentiment": sentiment_label,
            "meeting_type": meeting_type,
            "decisions": dec_count,
            "action_items": action_count,
            "velocity": velocity,
            "interruptions": interruptions,
            "flow_score": flow_score,
        })

    return _to_csv(rows)


def export_speakers_csv(recordings_dir: Path) -> str:
    """Export per-speaker stats across all recordings to CSV.

    Columns: folder, date, speaker, talk_minutes, talk_pct, word_count,
    wpm, turn_count.
    """
    rows: list[dict] = []

    if not recordings_dir.exists():
        return ""

    for rec_dir in sorted(recordings_dir.iterdir(), reverse=True):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue

        try:
            from meeting_recorder.storage.speaker_analytics import analyze_speakers
            meta = _load_meta(rec_dir)
            result = analyze_speakers(rec_dir, meta)
            if not result:
                continue
            date_str = rec_dir.name[:10]
            for spk in result.speakers:
                rows.append({
                    "folder": rec_dir.name,
                    "date": date_str,
                    "speaker": spk.name,
                    "talk_minutes": round(spk.talk_seconds / 60, 1),
                    "talk_pct": round(spk.talk_pct, 1),
                    "word_count": spk.word_count,
                    "wpm": round(spk.wpm, 0),
                    "turn_count": spk.turn_count,
                })
        except Exception:
            continue

    return _to_csv(rows)


def export_action_items_csv(recordings_dir: Path) -> str:
    """Export action items across all recordings to CSV.

    Columns: folder, date, description, category, assignee, context.
    """
    rows: list[dict] = []

    if not recordings_dir.exists():
        return ""

    for rec_dir in sorted(recordings_dir.iterdir(), reverse=True):
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue

        try:
            from meeting_recorder.storage.action_items import extract_action_items_for_recording
            meta = _load_meta(rec_dir)
            items = extract_action_items_for_recording(rec_dir, meta)
            date_str = rec_dir.name[:10]
            for item in items:
                rows.append({
                    "folder": rec_dir.name,
                    "date": date_str,
                    "description": item.description,
                    "category": item.category,
                    "assignee": item.assignee,
                    "context": item.context[:200] if item.context else "",
                })
        except Exception:
            continue

    return _to_csv(rows)


def export_focus_time_csv(recordings_dir: Path, weeks: int = 8) -> str:
    """Export focus time analysis to CSV.

    Columns: week_start, day, date, work_hours, meeting_hours,
    focus_hours, focus_pct, meeting_count.
    """
    rows: list[dict] = []

    try:
        from meeting_recorder.storage.focus_time import analyze_focus_time
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        week_data = analyze_focus_time(recordings_dir, weeks=weeks)
        for week in week_data:
            for i, day in enumerate(week.days):
                rows.append({
                    "week_start": week.week_start,
                    "day": day_names[i],
                    "date": day.date,
                    "work_hours": day.work_hours,
                    "meeting_hours": day.meeting_hours,
                    "focus_hours": day.focus_hours,
                    "focus_pct": day.focus_pct,
                    "meeting_count": day.meeting_count,
                })
    except Exception:
        pass

    return _to_csv(rows)


def export_topic_trends_csv(recordings_dir: Path, weeks: int = 8) -> str:
    """Export topic trends to CSV.

    Columns: week_start, keyword, count.
    """
    rows: list[dict] = []

    try:
        from meeting_recorder.storage.topic_trends import analyze_topic_trends
        report = analyze_topic_trends(recordings_dir, weeks=weeks)
        for week in report.weeks:
            if week.recording_count == 0:
                continue
            for keyword, count in week.top_keywords:
                rows.append({
                    "week_start": week.week_start,
                    "keyword": keyword,
                    "count": count,
                    "recordings": week.recording_count,
                })
    except Exception:
        pass

    return _to_csv(rows)


def export_all(recordings_dir: Path, output_dir: Path) -> list[Path]:
    """Export all data tables to CSV files in output_dir.

    Returns list of created file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    exports = [
        ("recordings.csv", export_recordings_csv),
        ("speakers.csv", export_speakers_csv),
        ("action_items.csv", export_action_items_csv),
        ("focus_time.csv", lambda d: export_focus_time_csv(d, weeks=8)),
        ("topic_trends.csv", lambda d: export_topic_trends_csv(d, weeks=8)),
    ]

    for filename, func in exports:
        try:
            csv_text = func(recordings_dir)
            if csv_text:
                path = output_dir / filename
                path.write_text(csv_text, encoding="utf-8-sig")  # BOM for Excel
                created.append(path)
                logger.info("Exported %s (%d bytes)", filename, len(csv_text))
        except Exception:
            logger.exception("Failed to export %s", filename)

    return created


def _load_meta(rec_dir: Path) -> dict:
    """Load metadata.json from a recording directory."""
    try:
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _to_csv(rows: list[dict]) -> str:
    """Convert list of dicts to CSV string."""
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
