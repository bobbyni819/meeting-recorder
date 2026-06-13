"""Tests for Zoom local caption import."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from meeting_recorder.transcription import vtt_import


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestZoomCaptionParsing:
    def test_webvtt_name_prefix_extracts_speaker(self, tmp_path):
        caption = _write(
            tmp_path,
            "zoom.vtt",
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nBobby Ni: Hello everyone\n",
        )

        segs = vtt_import.parse_vtt(caption)

        assert len(segs) == 1
        assert segs[0].speaker == "Bobby Ni"
        assert segs[0].text == "Hello everyone"

    def test_srt_numbered_name_prefix_extracts_speaker(self, tmp_path):
        caption = _write(
            tmp_path,
            "closed_caption.txt",
            "1\n00:00:01,000 --> 00:00:03,000\nFaye Guo: First caption\n\n"
            "2\n00:00:05,000 --> 00:00:06,000\nAlex Liu: Second caption\n",
        )

        segs = vtt_import.parse_vtt(caption)

        assert [(s.speaker, s.text) for s in segs] == [
            ("Faye Guo", "First caption"),
            ("Alex Liu", "Second caption"),
        ]

    def test_no_speaker_labels_stay_empty(self, tmp_path):
        caption = _write(
            tmp_path,
            "closed_caption.txt",
            "1\n00:00:01,000 --> 00:00:03,000\nHello everyone\n\n"
            "2\n00:00:05,000 --> 00:00:06,000\nNo labels in this cue\n",
        )

        segs = vtt_import.parse_vtt(caption)

        assert [s.speaker for s in segs] == ["", ""]
        assert [s.text for s in segs] == ["Hello everyone", "No labels in this cue"]

    def test_dot_and_comma_millisecond_separators(self, tmp_path):
        caption = _write(
            tmp_path,
            "mixed.txt",
            "WEBVTT\n\n00:00:01.250 --> 00:00:02.500\nBobby Ni: Dot ms\n\n"
            "00:00:04,750 --> 00:00:05,125\nFaye Guo: Comma ms\n",
        )

        segs = vtt_import.parse_vtt(caption)

        assert abs(segs[0].start - 1.250) < 0.001
        assert abs(segs[0].end - 2.500) < 0.001
        assert abs(segs[1].start - 4.750) < 0.001
        assert abs(segs[1].end - 5.125) < 0.001

    def test_multi_speaker_merging_reuses_consecutive_logic(self, tmp_path):
        caption = _write(
            tmp_path,
            "zoom.vtt",
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\nBobby Ni: First\n\n"
            "00:00:02.500 --> 00:00:03.000\nBobby Ni: Second\n\n"
            "00:00:03.200 --> 00:00:04.000\nFaye Guo: Other speaker\n",
        )

        segs = vtt_import.parse_vtt(caption)

        assert len(segs) == 2
        assert segs[0].speaker == "Bobby Ni"
        assert segs[0].text == "First Second"
        assert segs[1].speaker == "Faye Guo"
        assert segs[1].text == "Other speaker"

    def test_malformed_cues_are_skipped_without_raising(self, tmp_path):
        caption = _write(
            tmp_path,
            "bad.txt",
            "WEBVTT\n\n"
            "not a timing line\nBobby Ni: Bad cue\n\n"
            "00:00:04.000 --> nope\nFaye Guo: Bad timing\n\n"
            "00:00:06.000 --> 00:00:07.000\nAlex Liu: Good cue\n",
        )

        segs = vtt_import.parse_vtt(caption)

        assert len(segs) == 1
        assert segs[0].speaker == "Alex Liu"
        assert segs[0].text == "Good cue"


class TestZoomCaptionDiscovery:
    def test_find_zoom_caption_files_newest_first_and_missing_dir(self, tmp_path):
        old_dir = tmp_path / "Meeting 1"
        new_dir = tmp_path / "Meeting 2"
        old_dir.mkdir()
        new_dir.mkdir()
        old = _write(old_dir, "closed_caption.txt", "old")
        ignored = _write(old_dir, "notes.txt", "ignored")
        newest = _write(new_dir, "meeting_saved_closed_captions.txt", "newest")
        vtt = _write(new_dir, "transcript.vtt", "middle")
        os.utime(old, (100, 100))
        os.utime(vtt, (200, 200))
        os.utime(newest, (300, 300))

        found = vtt_import.find_zoom_caption_files(tmp_path)

        assert found == [newest, vtt, old]
        assert ignored not in found
        assert vtt_import.find_zoom_caption_files(tmp_path / "missing") == []


class TestZoomCaptionMatching:
    def _caption(
        self, zoom_dir: Path, folder_name: str, file_name: str = "captions.vtt"
    ) -> Path:
        meeting_dir = zoom_dir / folder_name
        meeting_dir.mkdir(parents=True)
        return _write(meeting_dir, file_name, "WEBVTT\n")

    def test_picks_caption_matching_metadata_start_time(self, tmp_path):
        zoom_dir = tmp_path / "Zoom"
        old = self._caption(zoom_dir, "2026-06-13 08.00.00 Morning")
        target = self._caption(zoom_dir, "2026-06-13 10.02.00 Target")
        late = self._caption(zoom_dir, "2026-06-13 12.30.00 Late")
        rec = tmp_path / "2026-06-13_09-00-00_Recording"
        rec.mkdir()
        (rec / "metadata.json").write_text(
            json.dumps({"start_time": "2026-06-13T10:00:00"}),
            encoding="utf-8",
        )

        found = vtt_import.find_zoom_caption_for_recording(rec, zoom_dir)

        assert found == target
        assert found not in (old, late)

    def test_falls_back_to_recording_dir_timestamp_when_metadata_empty_or_missing(
        self, tmp_path
    ):
        zoom_dir = tmp_path / "Zoom"
        target = self._caption(zoom_dir, "2026-06-13 14.05.00 Target")
        self._caption(zoom_dir, "2026-06-13 11.00.00 Other")
        empty_meta_rec = tmp_path / "2026-06-13_14-00-00_Empty_Metadata"
        missing_meta_rec = tmp_path / "2026-06-13_14-00-00_Missing_Metadata"
        empty_meta_rec.mkdir()
        missing_meta_rec.mkdir()
        (empty_meta_rec / "metadata.json").write_text(
            json.dumps({"start_time": ""}),
            encoding="utf-8",
        )

        assert (
            vtt_import.find_zoom_caption_for_recording(empty_meta_rec, zoom_dir)
            == target
        )
        assert (
            vtt_import.find_zoom_caption_for_recording(missing_meta_rec, zoom_dir)
            == target
        )

    def test_returns_none_when_closest_caption_outside_max_skew(self, tmp_path):
        zoom_dir = tmp_path / "Zoom"
        self._caption(zoom_dir, "2026-06-13 13.00.00 Too_Late")
        rec = tmp_path / "2026-06-13_10-00-00_Recording"
        rec.mkdir()

        found = vtt_import.find_zoom_caption_for_recording(
            rec, zoom_dir, max_skew_minutes=30
        )

        assert found is None

    def test_returns_none_when_recording_time_unavailable(self, tmp_path):
        zoom_dir = tmp_path / "Zoom"
        self._caption(zoom_dir, "2026-06-13 10.00.00 Meeting")
        rec = tmp_path / "recording_without_timestamp"
        rec.mkdir()

        assert vtt_import.find_zoom_caption_for_recording(rec, zoom_dir) is None

    def test_parse_leading_timestamp_accepts_recording_and_zoom_formats(self):
        assert vtt_import._parse_leading_timestamp(
            "2026-06-13_10-00-00_Foo"
        ).isoformat() == "2026-06-13T10:00:00"
        assert vtt_import._parse_leading_timestamp(
            "2026-06-13 10.00.00 Foo"
        ).isoformat() == "2026-06-13T10:00:00"
        assert vtt_import._parse_leading_timestamp("garbage") is None


class TestImportZoomCaptionToRecording:
    def test_import_writes_canonical_outputs_metadata_and_raw_copy(
        self, tmp_path, monkeypatch
    ):
        rec = tmp_path / "2026-06-12_08-00-00_Meeting_Zoom"
        rec.mkdir()
        (rec / "metadata.json").write_text(
            json.dumps({"app_name": "Zoom", "status": "error", "segment_count": 0}),
            encoding="utf-8",
        )
        caption = _write(
            tmp_path,
            "closed_caption.txt",
            "1\n00:00:01,000 --> 00:00:03,000\nBobby Ni: Hello everyone\n\n"
            "2\n00:00:05,000 --> 00:00:06,000\nFaye Guo: Hi Bobby\n",
        )
        monkeypatch.setattr(
            "meeting_recorder.search.index.RecordingIndex",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no index")),
        )

        result = vtt_import.import_zoom_caption_to_recording(rec, caption)

        assert result["segments"] == 2
        assert result["speakers"] == ["Bobby Ni", "Faye Guo"]
        data = json.loads((rec / "transcript.json").read_text(encoding="utf-8"))
        assert set(data["segments"][0].keys()) == {"start", "end", "text", "speaker"}
        assert (rec / "transcript.txt").exists()
        assert (rec / "transcript.srt").exists()
        assert (rec / "zoom_caption.txt").read_text(encoding="utf-8") == (
            caption.read_text(encoding="utf-8")
        )
        meta = json.loads((rec / "metadata.json").read_text(encoding="utf-8"))
        assert meta["status"] == "completed"
        assert meta["transcription_source"] == "zoom_caption"
        assert meta["speaker_count"] == 2
        assert meta["segment_count"] == 2

    def test_empty_zoom_caption_raises(self, tmp_path):
        rec = tmp_path / "recording"
        rec.mkdir()
        caption = _write(tmp_path, "closed_caption.txt", "WEBVTT\n")

        with pytest.raises(ValueError):
            vtt_import.import_zoom_caption_to_recording(rec, caption)
