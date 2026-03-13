"""Tests for meeting summary diff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.summary_diff import (
    diff_summaries,
    diff_series,
    format_diff,
    format_series_diffs,
    _extract_topics,
    _extract_action_lines,
    _text_similarity,
    SummaryDiff,
)


def _make_rec(
    base: Path,
    name: str,
    summary: str = "",
    subject: str = "Meeting",
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump({"duration_seconds": 1800, "meeting_subject": subject}, f)
    if summary:
        (rec / "summary.md").write_text(summary, encoding="utf-8")
    return rec


class TestExtractTopics:
    def test_basic(self):
        text = "The deployment pipeline needs review. deployment pipeline is critical."
        topics = _extract_topics(text)
        assert "deployment" in topics or "pipeline" in topics

    def test_bullet_points(self):
        text = "- Review the sprint backlog\n- Update documentation\n- Fix login bug"
        topics = _extract_topics(text)
        assert any("review the sprint backlog" in t for t in topics)

    def test_empty(self):
        assert _extract_topics("") == set()


class TestExtractActionLines:
    def test_checkbox(self):
        text = "- [ ] Send the report\n- [x] Review PR\nRegular text"
        actions = _extract_action_lines(text)
        assert len(actions) == 2

    def test_action_keyword(self):
        text = "- Action: follow up with team\n- Follow up on design"
        actions = _extract_action_lines(text)
        assert len(actions) >= 1

    def test_no_actions(self):
        text = "Just a regular summary with no action items."
        assert _extract_action_lines(text) == set()


class TestTextSimilarity:
    def test_identical(self):
        text = "The quick brown fox jumps"
        assert _text_similarity(text, text) == pytest.approx(1.0)

    def test_completely_different(self):
        score = _text_similarity("apple banana cherry", "xylophone zebra quantum")
        assert score == pytest.approx(0.0)

    def test_partial(self):
        score = _text_similarity(
            "sprint planning review backlog",
            "sprint review retrospective backlog items",
        )
        assert 0.2 < score < 0.9

    def test_empty(self):
        assert _text_similarity("", "hello") == 0.0
        assert _text_similarity("hello", "") == 0.0
        assert _text_similarity("", "") == 0.0


class TestDiffSummaries:
    def test_basic(self, tmp_path):
        rec_a = _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                          summary="Sprint review deployment pipeline status")
        rec_b = _make_rec(tmp_path, "2026-03-17_09-00-00_B",
                          summary="Sprint review new feature launch design")
        diff = diff_summaries(rec_a, rec_b)
        assert diff is not None
        assert diff.similarity > 0

    def test_no_summaries(self, tmp_path):
        rec_a = _make_rec(tmp_path, "2026-03-10_09-00-00_A")
        rec_b = _make_rec(tmp_path, "2026-03-17_09-00-00_B")
        assert diff_summaries(rec_a, rec_b) is None

    def test_one_summary(self, tmp_path):
        rec_a = _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                          summary="Content here content here content here")
        rec_b = _make_rec(tmp_path, "2026-03-17_09-00-00_B")
        diff = diff_summaries(rec_a, rec_b)
        assert diff is not None
        assert diff.similarity == 0.0

    def test_action_items_detected(self, tmp_path):
        rec_a = _make_rec(tmp_path, "2026-03-10_09-00-00_A",
                          summary="- [ ] Send report\n- [ ] Review code")
        rec_b = _make_rec(tmp_path, "2026-03-17_09-00-00_B",
                          summary="- [ ] Review code\n- [ ] Deploy feature")
        diff = diff_summaries(rec_a, rec_b)
        assert diff is not None
        assert len(diff.new_action_items) >= 1  # "deploy feature"
        assert len(diff.resolved_items) >= 1  # "send report"


class TestDiffSeries:
    def test_empty_dir(self, tmp_path):
        assert diff_series(tmp_path) == []

    def test_single_recording(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_A", summary="Content")
        assert diff_series(tmp_path) == []

    def test_basic_series(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Sprint",
                  summary="Sprint review deployment", subject="Sprint Review")
        _make_rec(tmp_path, "2026-03-17_09-00-00_Sprint",
                  summary="Sprint review new features", subject="Sprint Review")
        _make_rec(tmp_path, "2026-03-24_09-00-00_Sprint",
                  summary="Sprint review bug fixes", subject="Sprint Review")

        diffs = diff_series(tmp_path)
        assert len(diffs) == 2

    def test_pattern_filter(self, tmp_path):
        _make_rec(tmp_path, "2026-03-10_09-00-00_Sprint",
                  summary="Sprint review", subject="Sprint Review")
        _make_rec(tmp_path, "2026-03-11_09-00-00_Standup",
                  summary="Standup update", subject="Daily Standup")
        _make_rec(tmp_path, "2026-03-17_09-00-00_Sprint",
                  summary="Sprint review v2", subject="Sprint Review")

        diffs = diff_series(tmp_path, subject_pattern="sprint")
        assert len(diffs) == 1

    def test_max_diffs(self, tmp_path):
        for i in range(10):
            _make_rec(tmp_path, f"2026-03-{10+i:02d}_09-00-00_Sprint",
                      summary=f"Sprint review iteration {i}", subject="Sprint")
        diffs = diff_series(tmp_path, max_diffs=3)
        assert len(diffs) <= 3

    def test_nonexistent_dir(self, tmp_path):
        assert diff_series(tmp_path / "nope") == []


class TestFormatDiff:
    def test_basic(self):
        diff = SummaryDiff(
            rec_a_name="2026-03-10_Sprint",
            rec_b_name="2026-03-17_Sprint",
            new_topics=["feature launch"],
            dropped_topics=["bug fix"],
            common_topics=["sprint review"],
            new_action_items=["- [ ] deploy v2"],
            resolved_items=["- [ ] fix login"],
            similarity=0.65,
        )
        text = format_diff(diff)
        assert "SUMMARY DIFF" in text
        assert "65%" in text
        assert "NEW TOPICS" in text
        assert "feature launch" in text
        assert "DROPPED" in text
        assert "bug fix" in text
        assert "ONGOING" in text
        assert "sprint review" in text

    def test_empty_diff(self):
        diff = SummaryDiff(
            rec_a_name="A", rec_b_name="B",
            new_topics=[], dropped_topics=[], common_topics=[],
            new_action_items=[], resolved_items=[], similarity=0.0,
        )
        text = format_diff(diff)
        assert "SUMMARY DIFF" in text
        assert "0%" in text

    def test_format_series_empty(self):
        text = format_series_diffs([])
        assert "No consecutive" in text
