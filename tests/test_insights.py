"""Tests for meeting insights engine."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.insights import (
    Insight,
    generate_insights,
    format_insights,
    _time_insights,
    _trend_insights,
    _collaboration_insights,
    _issue_insights,
)


def _make_rec(
    base: Path,
    date_str: str,
    subject: str = "Meeting",
    duration: float = 1800,
    status: str = "completed",
    attendees: list[str] = None,
    has_transcript: bool = True,
    action_items: list[dict] = None,
) -> Path:
    name = f"{date_str}_09-00-00_{subject.replace(' ', '_')}"
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {
        "duration_seconds": duration,
        "status": status,
        "meeting_subject": subject,
    }
    if attendees:
        meta["meeting_attendees"] = attendees
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if has_transcript:
        (rec / "transcript.txt").write_text("hello world", encoding="utf-8")
    if action_items:
        with open(rec / "action_items.json", "w", encoding="utf-8") as f:
            json.dump(action_items, f)
    return rec


class TestGenerateInsights:
    def test_empty_dir(self, tmp_path):
        assert generate_insights(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert generate_insights(tmp_path / "noexist") == []

    def test_basic(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, today, attendees=["Alice", "Bob"])
        insights = generate_insights(tmp_path)
        assert len(insights) > 0
        assert all(isinstance(i, Insight) for i in insights)

    def test_sorted_by_priority(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, today, status="error")
        _make_rec(tmp_path, today, subject="Other", attendees=["Alice"])
        insights = generate_insights(tmp_path)
        priorities = [i.priority for i in insights]
        assert priorities == sorted(priorities)

    def test_max_insights(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        for i in range(10):
            _make_rec(tmp_path, today, subject=f"Meeting {i}",
                      attendees=[f"Person{j}" for j in range(5)])
        insights = generate_insights(tmp_path, max_insights=3)
        assert len(insights) <= 3


class TestTimeInsights:
    def test_this_week_summary(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        _make_rec(tmp_path, today, duration=3600)
        _make_rec(tmp_path, today, subject="B", duration=1800)
        recs = _load_recordings(tmp_path)
        insights = _time_insights(recs)
        texts = [i.text for i in insights]
        assert any("2 meetings" in t for t in texts)

    def test_week_over_week_change(self, tmp_path):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        last_week = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        # This week: 4h, last week: 2h → 100% more
        _make_rec(tmp_path, today, duration=14400)
        _make_rec(tmp_path, last_week, subject="Old", duration=7200)
        recs = _load_recordings(tmp_path)
        insights = _time_insights(recs)
        texts = [i.text for i in insights]
        assert any("more than last week" in t for t in texts)

    def test_heavy_week(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        # 8h × 3 = 24h (60% of 40h work week)
        for i in range(3):
            _make_rec(tmp_path, today, subject=f"M{i}", duration=28800)
        recs = _load_recordings(tmp_path)
        insights = _time_insights(recs)
        texts = [i.text for i in insights]
        assert any("work week" in t for t in texts)

    def test_longest_meeting(self, tmp_path):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        _make_rec(tmp_path, today, subject="Short", duration=1800)
        _make_rec(tmp_path, today, subject="Long Workshop", duration=7200)  # 2h
        recs = _load_recordings(tmp_path)
        insights = _time_insights(recs)
        texts = [i.text for i in insights]
        assert any("Long Workshop" in t for t in texts)


class TestTrendInsights:
    def test_duration_trend(self, tmp_path):
        now = datetime.now()
        # 5 meetings getting longer: 30m, 30m, 45m, 60m, 60m
        for i, dur in enumerate([1800, 1800, 2700, 3600, 3600]):
            date_str = (now - timedelta(days=30 - i * 7)).strftime("%Y-%m-%d")
            _make_rec(tmp_path, date_str, subject="Standup", duration=dur)
        recs = _load_recordings(tmp_path)
        insights = _trend_insights(recs)
        texts = [i.text for i in insights]
        assert any("Standup" in t and "longer" in t for t in texts)

    def test_no_trend_with_few_meetings(self, tmp_path):
        now = datetime.now()
        for i in range(2):
            date_str = (now - timedelta(days=i * 7)).strftime("%Y-%m-%d")
            _make_rec(tmp_path, date_str, subject="Rare Meeting", duration=1800)
        recs = _load_recordings(tmp_path)
        insights = _trend_insights(recs)
        # Not enough data for trend
        assert not any("Rare Meeting" in i.text for i in insights)


class TestCollaborationInsights:
    def test_frequent_collaborator(self, tmp_path):
        now = datetime.now()
        for i in range(5):
            date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            _make_rec(tmp_path, date_str, subject=f"M{i}",
                      attendees=["Alice", "Bob"])
        recs = _load_recordings(tmp_path)
        insights = _collaboration_insights(recs)
        texts = [i.text for i in insights]
        assert any("Alice" in t or "Bob" in t for t in texts)

    def test_solo_meetings(self, tmp_path):
        now = datetime.now()
        for i in range(5):
            date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            _make_rec(tmp_path, date_str, subject=f"Solo{i}")
        recs = _load_recordings(tmp_path)
        insights = _collaboration_insights(recs)
        texts = [i.text for i in insights]
        assert any("no attendee information" in t for t in texts)

    def test_no_insight_when_attendees_present(self, tmp_path):
        now = datetime.now()
        for i in range(5):
            date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            _make_rec(tmp_path, date_str, subject=f"Team{i}",
                      attendees=["Alice"])
        recs = _load_recordings(tmp_path)
        insights = _collaboration_insights(recs)
        texts = [i.text for i in insights]
        assert not any("no attendee information" in t for t in texts)


class TestIssueInsights:
    def test_failed_recordings(self, tmp_path):
        now = datetime.now()
        date_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        _make_rec(tmp_path, date_str, subject="FailedOne", status="error")
        recs = _load_recordings(tmp_path)
        insights = _issue_insights(recs)
        texts = [i.text for i in insights]
        assert any("failed processing" in t for t in texts)

    def test_no_transcript_warning(self, tmp_path):
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        _make_rec(tmp_path, date_str, has_transcript=False)
        recs = _load_recordings(tmp_path)
        insights = _issue_insights(recs)
        texts = [i.text for i in insights]
        assert any("no transcript" in t for t in texts)

    def test_old_failures_not_shown(self, tmp_path):
        date_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        _make_rec(tmp_path, date_str, status="error")
        recs = _load_recordings(tmp_path)
        insights = _issue_insights(recs)
        texts = [i.text for i in insights]
        assert not any("failed processing" in t for t in texts)


class TestFormatInsights:
    def test_empty(self):
        text = format_insights([])
        assert "No insights" in text

    def test_basic_format(self):
        insights = [
            Insight("time_management", "Test insight 1", 2, {}),
            Insight("collaboration", "Test insight 2", 3, {}),
        ]
        text = format_insights(insights)
        assert "MEETING INSIGHTS" in text
        assert "Test insight 1" in text
        assert "Test insight 2" in text
        assert "Time Management" in text
        assert "2 insights" in text

    def test_single_insight(self):
        insights = [Insight("issues", "Problem here", 1, {})]
        text = format_insights(insights)
        assert "1 insight" in text
        assert "insights)" not in text


def _load_recordings(base: Path):
    """Helper to load recordings in the same format as generate_insights."""
    recordings = []
    for rec_dir in base.iterdir():
        if not rec_dir.is_dir() or len(rec_dir.name) < 10:
            continue
        meta = {}
        meta_path = rec_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        try:
            dt = datetime.strptime(rec_dir.name[:10], "%Y-%m-%d")
        except ValueError:
            continue
        recordings.append((rec_dir, meta, dt))
    recordings.sort(key=lambda x: x[2], reverse=True)
    return recordings
