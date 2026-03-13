"""Tests for word frequency analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.word_frequency import (
    analyze_word_frequency,
    format_word_frequency,
    _extract_words,
    WordFrequency,
)


def _make_rec(
    base: Path,
    name: str,
    transcript: str = "",
    transcript_json: dict | None = None,
    speaker_map: dict | None = None,
) -> Path:
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)
    meta = {"speaker_map": speaker_map or {}}
    with open(rec / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if transcript:
        (rec / "transcript.txt").write_text(transcript, encoding="utf-8")
    if transcript_json is not None:
        with open(rec / "transcript.json", "w", encoding="utf-8") as f:
            json.dump(transcript_json, f)
    return rec


class TestExtractWords:
    def test_basic(self):
        words = _extract_words("The quick brown fox jumps over the lazy dog")
        assert "quick" in words
        assert "brown" in words
        assert "the" not in words  # stop word

    def test_min_length(self):
        words = _extract_words("Go to the store and buy some apples", min_length=4)
        assert all(len(w) >= 4 for w in words)

    def test_empty(self):
        assert _extract_words("") == []

    def test_stop_words_removed(self):
        words = _extract_words("I think that we should also consider this")
        # All stop words — should be empty or minimal
        assert "think" not in words
        assert "should" not in words
        assert "consider" in words  # not a stop word

    def test_lowercase(self):
        words = _extract_words("Python JavaScript TypeScript")
        assert "python" in words
        assert "javascript" in words


class TestAnalyzeWordFrequency:
    def test_no_transcript(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test")
        assert analyze_word_frequency(rec) is None

    def test_empty_transcript(self, tmp_path):
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        transcript="")
        assert analyze_word_frequency(rec) is None

    def test_basic_analysis(self, tmp_path):
        text = "deployment deployment deployment testing testing pipeline pipeline pipeline pipeline"
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        transcript=text)
        wf = analyze_word_frequency(rec)
        assert wf is not None
        assert wf.total_words > 0
        assert wf.unique_words > 0
        top_words = dict(wf.top_words)
        assert "pipeline" in top_words
        assert top_words["pipeline"] == 4

    def test_top_n(self, tmp_path):
        words = " ".join(f"word{i} " * (20 - i) for i in range(30))
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        transcript=words)
        wf = analyze_word_frequency(rec, top_n=5)
        assert wf is not None
        assert len(wf.top_words) <= 5

    def test_speaker_keywords(self, tmp_path):
        transcript_json = {
            "segments": [
                {"speaker": "SPEAKER_00", "text": "kubernetes kubernetes kubernetes deployment deployment", "start": 0, "end": 10},
                {"speaker": "SPEAKER_01", "text": "frontend frontend frontend design design component", "start": 10, "end": 20},
            ]
        }
        text = "kubernetes kubernetes kubernetes deployment deployment frontend frontend frontend design design component"
        rec = _make_rec(
            tmp_path, "2026-03-10_09-00-00_Test",
            transcript=text,
            transcript_json=transcript_json,
            speaker_map={"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        )
        wf = analyze_word_frequency(rec)
        assert wf is not None
        assert "Alice" in wf.speaker_keywords or "Bob" in wf.speaker_keywords

    def test_avg_word_length(self, tmp_path):
        text = "short longer longest superlongword"
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        transcript=text)
        wf = analyze_word_frequency(rec)
        assert wf is not None
        assert wf.avg_word_length > 0

    def test_unique_count(self, tmp_path):
        text = "python python python javascript javascript typescript"
        rec = _make_rec(tmp_path, "2026-03-10_09-00-00_Test",
                        transcript=text)
        wf = analyze_word_frequency(rec)
        assert wf is not None
        assert wf.unique_words == 3
        assert wf.total_words == 6


class TestFormatWordFrequency:
    def test_basic_format(self):
        wf = WordFrequency(
            top_words=[("deployment", 15), ("testing", 8), ("pipeline", 5)],
            total_words=200,
            unique_words=80,
            avg_word_length=6.2,
            speaker_keywords={
                "Alice": [("kubernetes", 5), ("docker", 3)],
                "Bob": [("frontend", 4), ("design", 2)],
            },
        )
        text = format_word_frequency(wf)
        assert "WORD FREQUENCY" in text
        assert "200" in text
        assert "80" in text
        assert "deployment" in text
        assert "15" in text
        assert "DISTINCTIVE WORDS PER SPEAKER" in text
        assert "Alice" in text
        assert "kubernetes" in text

    def test_empty_speakers(self):
        wf = WordFrequency(
            top_words=[("hello", 3)],
            total_words=10,
            unique_words=5,
            avg_word_length=5.0,
            speaker_keywords={},
        )
        text = format_word_frequency(wf)
        assert "WORD FREQUENCY" in text
        assert "DISTINCTIVE" not in text

    def test_bar_chart(self):
        wf = WordFrequency(
            top_words=[("first", 20), ("second", 10), ("third", 5)],
            total_words=35,
            unique_words=3,
            avg_word_length=5.0,
            speaker_keywords={},
        )
        text = format_word_frequency(wf)
        # First word should have longest bar
        lines = text.split("\n")
        bar_lines = [l for l in lines if "\u2588" in l]
        assert len(bar_lines) >= 3
