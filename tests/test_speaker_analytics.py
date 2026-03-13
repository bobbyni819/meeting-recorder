"""Tests for speaker analytics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.speaker_analytics import (
    SpeakerStats,
    RecordingAnalytics,
    analyze_speakers,
    format_speaker_analytics,
    _count_turns,
    _compute_coverage,
    _count_words_per_speaker,
)


def _make_rec(
    base: Path,
    meta: dict = None,
    segments: list[dict] = None,
) -> Path:
    rec = base / "2026-03-01_09-00-00_Test"
    rec.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        with open(rec / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)
    if segments is not None:
        with open(rec / "transcript.json", "w", encoding="utf-8") as f:
            json.dump({"segments": segments}, f)
    return rec


class TestAnalyzeSpeakers:
    def test_basic_two_speakers(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600}, segments=[
            {"speaker": "Alice", "start": 0, "end": 200, "text": "Hello world today"},
            {"speaker": "Bob", "start": 200, "end": 400, "text": "Good morning everyone here"},
            {"speaker": "Alice", "start": 400, "end": 600, "text": "Let us discuss the plan now"},
        ])
        result = analyze_speakers(rec)
        assert result is not None
        assert result.duration == 600
        assert len(result.speakers) == 2
        # Alice: 0-200 + 400-600 = 400s; Bob: 200-400 = 200s
        alice = next(s for s in result.speakers if s.name == "Alice")
        bob = next(s for s in result.speakers if s.name == "Bob")
        assert alice.talk_seconds == 400.0
        assert bob.talk_seconds == 200.0
        assert alice.talk_pct == pytest.approx(66.7, abs=0.1)
        assert bob.talk_pct == pytest.approx(33.3, abs=0.1)

    def test_word_count(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 300}, segments=[
            {"speaker": "Alice", "start": 0, "end": 150,
             "text": "one two three four five six seven eight nine ten"},
            {"speaker": "Bob", "start": 150, "end": 300,
             "text": "hello world"},
        ])
        result = analyze_speakers(rec)
        alice = next(s for s in result.speakers if s.name == "Alice")
        bob = next(s for s in result.speakers if s.name == "Bob")
        assert alice.word_count == 10
        assert bob.word_count == 2

    def test_wpm(self, tmp_path):
        # Alice speaks 120 words in 60 seconds = 120 WPM
        words = " ".join(f"word{i}" for i in range(120))
        rec = _make_rec(tmp_path, meta={"duration_seconds": 120}, segments=[
            {"speaker": "Alice", "start": 0, "end": 60, "text": words},
            {"speaker": "Bob", "start": 60, "end": 120, "text": "hi"},
        ])
        result = analyze_speakers(rec)
        alice = next(s for s in result.speakers if s.name == "Alice")
        assert alice.wpm == pytest.approx(120.0, abs=1.0)

    def test_turn_count(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600}, segments=[
            {"speaker": "Alice", "start": 0, "end": 100, "text": "a"},
            {"speaker": "Bob", "start": 100, "end": 200, "text": "b"},
            {"speaker": "Alice", "start": 200, "end": 300, "text": "c"},
            {"speaker": "Bob", "start": 300, "end": 400, "text": "d"},
            {"speaker": "Alice", "start": 400, "end": 500, "text": "e"},
        ])
        result = analyze_speakers(rec)
        alice = next(s for s in result.speakers if s.name == "Alice")
        bob = next(s for s in result.speakers if s.name == "Bob")
        assert alice.turn_count == 3
        assert bob.turn_count == 2
        assert result.turn_count == 5

    def test_silence(self, tmp_path):
        # 600s meeting, only 400s of speech
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600}, segments=[
            {"speaker": "Alice", "start": 0, "end": 200, "text": "a"},
            {"speaker": "Bob", "start": 300, "end": 500, "text": "b"},
        ])
        result = analyze_speakers(rec)
        assert result.silence_seconds == pytest.approx(200.0, abs=1)
        assert result.silence_pct == pytest.approx(33.3, abs=0.1)

    def test_crosstalk(self, tmp_path):
        # Alice: 0-200, Bob: 150-350 → overlap at 150-200 = 50s
        rec = _make_rec(tmp_path, meta={"duration_seconds": 350}, segments=[
            {"speaker": "Alice", "start": 0, "end": 200, "text": "a"},
            {"speaker": "Bob", "start": 150, "end": 350, "text": "b"},
        ])
        result = analyze_speakers(rec)
        assert result.crosstalk_seconds == pytest.approx(50.0, abs=0.1)
        assert result.crosstalk_pct == pytest.approx(14.3, abs=0.5)

    def test_no_crosstalk(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 200}, segments=[
            {"speaker": "Alice", "start": 0, "end": 100, "text": "a"},
            {"speaker": "Bob", "start": 100, "end": 200, "text": "b"},
        ])
        result = analyze_speakers(rec)
        assert result.crosstalk_seconds == 0.0

    def test_longest_turn(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600}, segments=[
            {"speaker": "Alice", "start": 0, "end": 50, "text": "a"},
            {"speaker": "Alice", "start": 100, "end": 300, "text": "b"},
            {"speaker": "Alice", "start": 400, "end": 500, "text": "c"},
        ])
        result = analyze_speakers(rec)
        alice = result.speakers[0]
        assert alice.longest_turn_seconds == 200.0

    def test_speaker_map_resolution(self, tmp_path):
        rec = _make_rec(tmp_path, meta={
            "duration_seconds": 200,
            "speaker_map": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
        }, segments=[
            {"speaker": "SPEAKER_00", "start": 0, "end": 100, "text": "a"},
            {"speaker": "SPEAKER_01", "start": 100, "end": 200, "text": "b"},
        ])
        result = analyze_speakers(rec)
        names = {s.name for s in result.speakers}
        assert "Alice" in names
        assert "Bob" in names
        assert "SPEAKER_00" not in names

    def test_no_transcript(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600})
        assert analyze_speakers(rec) is None

    def test_empty_segments(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600}, segments=[])
        assert analyze_speakers(rec) is None

    def test_no_metadata_uses_segment_end(self, tmp_path):
        rec = _make_rec(tmp_path, segments=[
            {"speaker": "Alice", "start": 0, "end": 100, "text": "a"},
            {"speaker": "Bob", "start": 100, "end": 300, "text": "b"},
        ])
        result = analyze_speakers(rec)
        assert result is not None
        assert result.duration == 300

    def test_provided_meta(self, tmp_path):
        rec = tmp_path / "2026-03-01_09-00-00_Test"
        rec.mkdir()
        with open(rec / "transcript.json", "w", encoding="utf-8") as f:
            json.dump({"segments": [
                {"speaker": "Alice", "start": 0, "end": 100, "text": "hello"},
            ]}, f)
        result = analyze_speakers(rec, meta={"duration_seconds": 200})
        assert result is not None
        assert result.duration == 200

    def test_sorted_by_talk_time(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600}, segments=[
            {"speaker": "Alice", "start": 0, "end": 100, "text": "a"},
            {"speaker": "Bob", "start": 100, "end": 400, "text": "b"},
            {"speaker": "Charlie", "start": 400, "end": 600, "text": "c"},
        ])
        result = analyze_speakers(rec)
        times = [s.talk_seconds for s in result.speakers]
        assert times == sorted(times, reverse=True)

    def test_avg_turn_duration(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 300}, segments=[
            {"speaker": "Alice", "start": 0, "end": 100, "text": "a"},
            {"speaker": "Bob", "start": 100, "end": 200, "text": "b"},
            {"speaker": "Alice", "start": 200, "end": 300, "text": "c"},
        ])
        result = analyze_speakers(rec)
        alice = next(s for s in result.speakers if s.name == "Alice")
        # Alice: 200s total, 2 turns → avg 100s
        assert alice.avg_turn_seconds == 100.0

    def test_single_speaker(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 300}, segments=[
            {"speaker": "Alice", "start": 0, "end": 300, "text": "long monologue here"},
        ])
        result = analyze_speakers(rec)
        assert len(result.speakers) == 1
        assert result.speakers[0].talk_pct == 100.0
        assert result.silence_seconds == 0.0

    def test_zero_duration_segment_skipped(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 200}, segments=[
            {"speaker": "Alice", "start": 0, "end": 100, "text": "a"},
            {"speaker": "Bob", "start": 100, "end": 100, "text": ""},  # zero-length
            {"speaker": "Bob", "start": 100, "end": 200, "text": "b"},
        ])
        result = analyze_speakers(rec)
        bob = next(s for s in result.speakers if s.name == "Bob")
        assert bob.talk_seconds == 100.0


class TestCountTurns:
    def test_single_segment(self):
        assert _count_turns([(0, 100)]) == 1

    def test_continuous_segments(self):
        # Segments with tiny gaps (< 1s) are same turn
        assert _count_turns([(0, 100), (100.5, 200)]) == 1

    def test_separate_turns(self):
        # Gap > 1s = new turn
        assert _count_turns([(0, 100), (102, 200)]) == 2

    def test_many_turns(self):
        segs = [(i * 20, i * 20 + 10) for i in range(5)]  # 5 segments with 10s gaps
        assert _count_turns(segs) == 5

    def test_empty(self):
        assert _count_turns([]) == 0


class TestComputeCoverage:
    def test_full_coverage(self):
        intervals = [(0, 300)]
        speech, silence, crosstalk = _compute_coverage(
            intervals, 300, {"A": [(0, 300)]}
        )
        assert speech == 300
        assert silence == 0
        assert crosstalk == 0

    def test_partial_coverage(self):
        intervals = [(0, 100), (200, 300)]
        speech, silence, crosstalk = _compute_coverage(
            intervals, 400, {"A": [(0, 100), (200, 300)]}
        )
        assert speech == 200
        assert silence == 200

    def test_overlap_crosstalk(self):
        intervals = [(0, 200), (150, 350)]
        speech, silence, crosstalk = _compute_coverage(
            intervals, 350,
            {"A": [(0, 200)], "B": [(150, 350)]}
        )
        assert crosstalk == pytest.approx(50.0)

    def test_empty(self):
        speech, silence, crosstalk = _compute_coverage([], 100, {})
        assert speech == 0
        assert silence == 100
        assert crosstalk == 0


class TestCountWordsPerSpeaker:
    def test_basic(self):
        segments = [
            {"speaker": "Alice", "text": "hello world"},
            {"speaker": "Bob", "text": "one two three"},
        ]
        counts = _count_words_per_speaker(segments, {})
        assert counts["Alice"] == 2
        assert counts["Bob"] == 3

    def test_speaker_map(self):
        segments = [
            {"speaker": "SPEAKER_00", "text": "hello world foo"},
        ]
        counts = _count_words_per_speaker(segments, {"SPEAKER_00": "Alice"})
        assert counts["Alice"] == 3
        assert "SPEAKER_00" not in counts

    def test_empty_text(self):
        segments = [{"speaker": "Alice", "text": ""}]
        counts = _count_words_per_speaker(segments, {})
        assert counts.get("Alice", 0) == 0

    def test_accumulates(self):
        segments = [
            {"speaker": "Alice", "text": "one two"},
            {"speaker": "Alice", "text": "three four five"},
        ]
        counts = _count_words_per_speaker(segments, {})
        assert counts["Alice"] == 5


class TestFormatSpeakerAnalytics:
    def test_format(self, tmp_path):
        rec = _make_rec(tmp_path, meta={"duration_seconds": 600}, segments=[
            {"speaker": "Alice", "start": 0, "end": 300, "text": "word " * 100},
            {"speaker": "Bob", "start": 300, "end": 600, "text": "word " * 50},
        ])
        result = analyze_speakers(rec)
        text = format_speaker_analytics(result)
        assert "SPEAKER ANALYTICS" in text
        assert "Alice" in text
        assert "Bob" in text
        assert "10.0 min" in text  # meeting duration
