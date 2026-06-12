"""Tests for Teams/Zoom WebVTT transcript import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.transcription import vtt_import


_SAMPLE_VTT = """WEBVTT

id/6-0
00:00:03.713 --> 00:00:08.211
<v Faye Guo>And I have been working on the
multi-agent project for a year,</v>

id/6-1
00:00:08.300 --> 00:00:12.352
<v Faye Guo>and I work on parameter extraction.</v>

id/42-0
00:00:15.953 --> 00:00:18.193
<v Alex Liu>Yeah, for sure.</v>

id/49-0
00:00:26.993 --> 00:00:31.990
<v Bobby Ni>Yeah, I just want to add something.</v>
"""


class TestParseVtt:
    def test_parses_segments_with_speakers(self, tmp_path):
        p = tmp_path / "t.vtt"
        p.write_text(_SAMPLE_VTT, encoding="utf-8")
        segs = vtt_import.parse_vtt(p)
        speakers = [s.speaker for s in segs]
        assert "Faye Guo" in speakers
        assert "Alex Liu" in speakers
        assert "Bobby Ni" in speakers

    def test_merges_consecutive_same_speaker(self, tmp_path):
        p = tmp_path / "t.vtt"
        p.write_text(_SAMPLE_VTT, encoding="utf-8")
        segs = vtt_import.parse_vtt(p)
        # Faye's two adjacent cues (gap < 1s) merge into one turn
        faye = [s for s in segs if s.speaker == "Faye Guo"]
        assert len(faye) == 1
        assert "multi-agent project" in faye[0].text
        assert "parameter extraction" in faye[0].text

    def test_timestamps_parsed(self, tmp_path):
        p = tmp_path / "t.vtt"
        p.write_text(_SAMPLE_VTT, encoding="utf-8")
        segs = vtt_import.parse_vtt(p)
        first = segs[0]
        assert abs(first.start - 3.713) < 0.001
        assert first.end >= first.start

    def test_sorted_by_start(self, tmp_path):
        # Cues deliberately out of order
        vtt = (
            "WEBVTT\n\n00:00:10.000 --> 00:00:11.000\n<v B>second</v>\n\n"
            "00:00:01.000 --> 00:00:02.000\n<v A>first</v>\n"
        )
        p = tmp_path / "t.vtt"
        p.write_text(vtt, encoding="utf-8")
        segs = vtt_import.parse_vtt(p)
        assert segs[0].text == "first"
        assert segs[1].text == "second"

    def test_handles_mm_ss_only_timestamps(self, tmp_path):
        vtt = "WEBVTT\n\n01:05.500 --> 01:08.000\n<v X>hi</v>\n"
        p = tmp_path / "t.vtt"
        p.write_text(vtt, encoding="utf-8")
        segs = vtt_import.parse_vtt(p)
        assert abs(segs[0].start - 65.5) < 0.001

    def test_no_voice_tag_yields_empty_speaker(self, tmp_path):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nplain caption text\n"
        p = tmp_path / "t.vtt"
        p.write_text(vtt, encoding="utf-8")
        segs = vtt_import.parse_vtt(p)
        assert segs[0].speaker == ""
        assert segs[0].text == "plain caption text"

    def test_strips_inline_tags(self, tmp_path):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A><b>bold</b> word</v>\n"
        p = tmp_path / "t.vtt"
        p.write_text(vtt, encoding="utf-8")
        segs = vtt_import.parse_vtt(p)
        assert segs[0].text == "bold word"

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "t.vtt"
        p.write_text("WEBVTT\n", encoding="utf-8")
        assert vtt_import.parse_vtt(p) == []


class TestImportToRecording:
    def _recording(self, tmp_path):
        rec = tmp_path / "2026-06-12_08-00-00_Meeting_Teams"
        rec.mkdir()
        meta = {"app_name": "Teams", "status": "error", "segment_count": 0}
        (rec / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        return rec

    def test_writes_canonical_transcript(self, tmp_path, monkeypatch):
        rec = self._recording(tmp_path)
        vtt = tmp_path / "teams.vtt"
        vtt.write_text(_SAMPLE_VTT, encoding="utf-8")
        # Avoid touching the real search index
        monkeypatch.setattr(
            "meeting_recorder.search.index.RecordingIndex",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no index")),
        )
        result = vtt_import.import_vtt_to_recording(rec, vtt)

        assert result["segments"] >= 3
        assert set(result["speakers"]) == {"Faye Guo", "Alex Liu", "Bobby Ni"}
        # Canonical transcript.json schema
        data = json.loads((rec / "transcript.json").read_text(encoding="utf-8"))
        assert "segments" in data
        seg0 = data["segments"][0]
        assert set(seg0.keys()) == {"start", "end", "text", "speaker"}
        # VTT preserved + metadata updated
        assert (rec / "teams_transcript.vtt").exists()
        meta = json.loads((rec / "metadata.json").read_text(encoding="utf-8"))
        assert meta["status"] == "completed"
        assert meta["transcription_source"] == "teams_vtt"
        assert meta["speaker_count"] == 3

    def test_empty_vtt_raises(self, tmp_path, monkeypatch):
        rec = self._recording(tmp_path)
        vtt = tmp_path / "empty.vtt"
        vtt.write_text("WEBVTT\n", encoding="utf-8")
        with pytest.raises(ValueError):
            vtt_import.import_vtt_to_recording(rec, vtt)
