"""Tests for active-speaker event capture and alignment.

The live UIA capture can't be unit-tested (needs a real meeting), but the
alignment logic that turns (timestamp, name) events into speaker labels is
fully testable and is what actually decides the transcript labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.audio import speaker_events as se
from meeting_recorder.transcription.local_whisper import TranscriptSegment


def _seg(start, end, text, speaker):
    return TranscriptSegment(start=start, end=end, text=text, speaker=speaker)


class TestExtractName:
    def test_pulls_name_before_hint(self):
        assert se._extract_name("Alice Smith is speaking", "is speaking") == "Alice Smith"

    def test_rejects_empty(self):
        assert se._extract_name("is speaking", "is speaking") is None

    def test_rejects_pure_digits(self):
        assert se._extract_name("123 is speaking", "is speaking") is None


class TestGenericLabel:
    def test_speaker_n_is_generic(self):
        assert se._is_generic_label("Speaker 1")
        assert se._is_generic_label("Speaker 12")
        assert se._is_generic_label("Unknown")

    def test_real_name_not_generic(self):
        assert not se._is_generic_label("Alice Smith")
        assert not se._is_generic_label("Bobby")


class TestLoadSpeakerEvents:
    def test_loads_and_sorts(self, tmp_path):
        p = tmp_path / "speaker_events.jsonl"
        p.write_text(
            json.dumps({"t": 5.0, "speaker": "Bob"}) + "\n"
            + json.dumps({"t": 1.0, "speaker": "Alice"}) + "\n",
            encoding="utf-8",
        )
        events = se.load_speaker_events(tmp_path)
        assert events == [(1.0, "Alice"), (5.0, "Bob")]

    def test_missing_file_returns_empty(self, tmp_path):
        assert se.load_speaker_events(tmp_path) == []

    def test_garbage_lines_skipped(self, tmp_path):
        p = tmp_path / "speaker_events.jsonl"
        p.write_text("not json\n" + json.dumps({"t": 2.0, "speaker": "Z"}), encoding="utf-8")
        # Malformed first line makes the whole parse bail safely -> empty
        assert se.load_speaker_events(tmp_path) == []


class TestBuildSpeakerNameMap:
    def test_maps_generic_label_to_dominant_name(self):
        # Alice speaks 0-10, Bob 10-20 (per the event stream)
        events = [(0.0, "Alice"), (10.0, "Bob")]
        segments = [
            _seg(1.0, 4.0, "hi", "Speaker 1"),
            _seg(5.0, 9.0, "more", "Speaker 1"),
            _seg(11.0, 15.0, "hello", "Speaker 2"),
        ]
        mapping = se.build_speaker_name_map(segments, events)
        assert mapping == {"Speaker 1": "Alice", "Speaker 2": "Bob"}

    def test_does_not_map_already_named_speakers(self):
        events = [(0.0, "Alice")]
        segments = [_seg(1.0, 5.0, "hi", "Bobby")]  # already a real name
        assert se.build_speaker_name_map(segments, events) == {}

    def test_low_confidence_not_mapped(self):
        # Speaker 1's segments split evenly between two captured names
        events = [(0.0, "Alice"), (5.0, "Bob")]
        segments = [
            _seg(1.0, 4.0, "a", "Speaker 1"),   # Alice window
            _seg(6.0, 9.0, "b", "Speaker 1"),   # Bob window
        ]
        # 50/50 -> below the 0.6 confidence threshold -> unmapped
        assert se.build_speaker_name_map(segments, events) == {}

    def test_no_events_no_mapping(self):
        segments = [_seg(0.0, 5.0, "a", "Speaker 1")]
        assert se.build_speaker_name_map(segments, []) == {}

    def test_confidence_threshold_configurable(self):
        events = [(0.0, "Alice"), (5.0, "Bob")]
        segments = [
            _seg(1.0, 4.0, "a", "Speaker 1"),   # Alice 3s
            _seg(6.0, 7.0, "b", "Speaker 1"),   # Bob 1s -> Alice 75%
        ]
        assert se.build_speaker_name_map(segments, events, min_confidence=0.7) == {
            "Speaker 1": "Alice"
        }
        assert se.build_speaker_name_map(segments, events, min_confidence=0.8) == {}


class TestCaptureLifecycle:
    def test_start_stop_without_uia_is_safe(self, tmp_path, monkeypatch):
        """Capture must no-op cleanly when UIA libs aren't usable."""
        import sys

        # Force the comtypes import inside the loop to fail
        monkeypatch.setitem(sys.modules, "comtypes", None)
        cap = se.SpeakerEventCapture(
            pids={1234}, output_path=tmp_path / "speaker_events.jsonl",
            poll_interval=0.05,
        )
        cap.start()
        cap.stop()
        # No events, no file, no exception
        assert not (tmp_path / "speaker_events.jsonl").exists()
