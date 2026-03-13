"""Tests for talk-time balance analyzer."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from meeting_recorder.storage.talk_balance import (
    analyze_talk_balance,
    analyze_talk_balance_report,
    format_talk_balance,
    TalkBalance,
    TalkBalanceReport,
)


def _make_rec(base: Path, name: str, meta: dict, segments: list) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (rec / "transcript.json").write_text(
        json.dumps({"segments": segments}), encoding="utf-8"
    )
    return rec


class TestAnalyzeTalkBalance:
    def test_no_transcript(self, tmp_path):
        rec = tmp_path / "rec"
        rec.mkdir()
        assert analyze_talk_balance(rec) is None

    def test_single_speaker(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {}, [
            {"speaker": "Alice", "start": 0, "end": 100},
        ])
        # Need at least 2 speakers
        assert analyze_talk_balance(rec) is None

    def test_balanced_two_speakers(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {"meeting_subject": "Chat"}, [
            {"speaker": "Alice", "start": 0, "end": 300},
            {"speaker": "Bob", "start": 300, "end": 600},
        ])
        tb = analyze_talk_balance(rec)
        assert tb is not None
        assert tb.speaker_count == 2
        assert tb.balance_score > 90  # near perfect
        assert not tb.is_imbalanced

    def test_imbalanced(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {"meeting_subject": "Lecture"}, [
            {"speaker": "Professor", "start": 0, "end": 900},
            {"speaker": "Student", "start": 900, "end": 1000},
        ])
        tb = analyze_talk_balance(rec)
        assert tb is not None
        assert tb.dominant_speaker == "Professor"
        assert tb.dominant_pct > 80
        assert tb.is_imbalanced

    def test_three_equal_speakers(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {}, [
            {"speaker": "A", "start": 0, "end": 100},
            {"speaker": "B", "start": 100, "end": 200},
            {"speaker": "C", "start": 200, "end": 300},
        ])
        tb = analyze_talk_balance(rec)
        assert tb is not None
        assert tb.balance_score > 95
        assert tb.speaker_count == 3

    def test_speaker_map_applied(self, tmp_path):
        meta = {"speaker_map": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}}
        rec = _make_rec(tmp_path, "rec", meta, [
            {"speaker": "SPEAKER_00", "start": 0, "end": 300},
            {"speaker": "SPEAKER_01", "start": 300, "end": 600},
        ])
        tb = analyze_talk_balance(rec)
        assert tb is not None
        assert tb.dominant_speaker in ("Alice", "Bob")

    def test_custom_threshold(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {}, [
            {"speaker": "A", "start": 0, "end": 600},
            {"speaker": "B", "start": 600, "end": 1000},
        ])
        # 60% threshold
        tb = analyze_talk_balance(rec, imbalance_threshold=60.0)
        assert tb is not None
        assert tb.is_imbalanced  # A has 60%

    def test_speakers_sorted_by_time(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {}, [
            {"speaker": "A", "start": 0, "end": 100},
            {"speaker": "B", "start": 100, "end": 500},
            {"speaker": "C", "start": 500, "end": 600},
        ])
        tb = analyze_talk_balance(rec)
        assert tb is not None
        assert tb.speakers[0][0] == "B"  # most talk time

    def test_subject_from_metadata(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {"meeting_subject": "Sprint Review"}, [
            {"speaker": "A", "start": 0, "end": 300},
            {"speaker": "B", "start": 300, "end": 600},
        ])
        tb = analyze_talk_balance(rec)
        assert tb.subject == "Sprint Review"

    def test_too_short_excluded(self, tmp_path):
        rec = _make_rec(tmp_path, "rec", {}, [
            {"speaker": "A", "start": 0, "end": 10},
            {"speaker": "B", "start": 10, "end": 20},
        ])
        assert analyze_talk_balance(rec) is None


class TestAnalyzeTalkBalanceReport:
    def _this_week(self, offset: int = 0) -> date:
        today = date.today()
        return today - timedelta(days=today.weekday()) + timedelta(days=offset)

    def test_no_dir(self, tmp_path):
        assert analyze_talk_balance_report(tmp_path / "nope") is None

    def test_empty_dir(self, tmp_path):
        assert analyze_talk_balance_report(tmp_path) is None

    def test_basic_report(self, tmp_path):
        d = self._this_week()
        _make_rec(
            tmp_path, f"{d.isoformat()}_09-00-00_Meeting_A",
            {"meeting_subject": "Meeting A"},
            [
                {"speaker": "A", "start": 0, "end": 300},
                {"speaker": "B", "start": 300, "end": 600},
            ],
        )
        _make_rec(
            tmp_path, f"{d.isoformat()}_14-00-00_Meeting_B",
            {"meeting_subject": "Meeting B"},
            [
                {"speaker": "X", "start": 0, "end": 900},
                {"speaker": "Y", "start": 900, "end": 1000},
            ],
        )
        report = analyze_talk_balance_report(tmp_path)
        assert report is not None
        assert report.recordings_analyzed == 2
        assert len(report.most_balanced) <= 3
        assert len(report.most_imbalanced) <= 3

    def test_imbalanced_count(self, tmp_path):
        d = self._this_week()
        for i in range(3):
            _make_rec(
                tmp_path, f"{d.isoformat()}_0{i+9}-00-00_Mtg_{i}",
                {},
                [
                    {"speaker": "Boss", "start": 0, "end": 900},
                    {"speaker": "Employee", "start": 900, "end": 1000},
                ],
            )
        report = analyze_talk_balance_report(tmp_path)
        assert report is not None
        assert report.meetings_with_one_speaker_over_70 == 3

    def test_frequent_dominators(self, tmp_path):
        d = self._this_week()
        for i in range(3):
            _make_rec(
                tmp_path, f"{d.isoformat()}_0{i+9}-00-00_Mtg_{i}",
                {},
                [
                    {"speaker": "Boss", "start": 0, "end": 900},
                    {"speaker": f"Person{i}", "start": 900, "end": 1000},
                ],
            )
        report = analyze_talk_balance_report(tmp_path)
        assert report is not None
        assert len(report.frequent_dominators) >= 1
        assert report.frequent_dominators[0][0] == "Boss"
        assert report.frequent_dominators[0][1] == 3

    def test_old_excluded(self, tmp_path):
        old = self._this_week() - timedelta(weeks=20)
        _make_rec(
            tmp_path, f"{old.isoformat()}_09-00-00_Old",
            {},
            [
                {"speaker": "A", "start": 0, "end": 300},
                {"speaker": "B", "start": 300, "end": 600},
            ],
        )
        assert analyze_talk_balance_report(tmp_path, weeks=4) is None


class TestFormatTalkBalance:
    def test_none(self):
        text = format_talk_balance(None)
        assert "Not enough" in text

    def test_basic_format(self):
        report = TalkBalanceReport(
            recordings_analyzed=10,
            avg_balance_score=72.5,
            most_balanced=[
                TalkBalance(
                    recording_name="rec1", subject="Standup",
                    dominant_speaker="Alice", dominant_pct=35.0,
                    speaker_count=4, balance_score=95.0,
                    is_imbalanced=False,
                    speakers=[("Alice", 35), ("Bob", 30), ("Carol", 20), ("Dave", 15)],
                ),
            ],
            most_imbalanced=[
                TalkBalance(
                    recording_name="rec2", subject="Lecture",
                    dominant_speaker="Professor", dominant_pct=85.0,
                    speaker_count=2, balance_score=30.0,
                    is_imbalanced=True,
                    speakers=[("Professor", 85), ("Student", 15)],
                ),
            ],
            frequent_dominators=[("Professor", 5)],
            meetings_with_one_speaker_over_70=3,
        )
        text = format_talk_balance(report)
        assert "TALK-TIME BALANCE" in text
        assert "10" in text
        assert "72" in text or "73" in text
        assert "Standup" in text
        assert "Lecture" in text
        assert "Professor" in text
