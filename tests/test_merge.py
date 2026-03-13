"""Tests for recording merge functionality."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.storage.merge import (
    merge_transcripts,
    _load_meta,
    _merge_text_files,
    _merge_summaries,
    _merge_transcript_json,
    _build_merged_metadata,
)


def _make_recording(base: Path, name: str, **kwargs) -> Path:
    """Create a minimal recording directory with optional files."""
    rec = base / name
    rec.mkdir(parents=True, exist_ok=True)

    if "transcript" in kwargs:
        (rec / "transcript.txt").write_text(kwargs["transcript"], encoding="utf-8")
    if "summary" in kwargs:
        (rec / "summary.md").write_text(kwargs["summary"], encoding="utf-8")
    if "notes" in kwargs:
        (rec / "notes.md").write_text(kwargs["notes"], encoding="utf-8")
    if "meta" in kwargs:
        with open(rec / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(kwargs["meta"], f)
    if "transcript_json" in kwargs:
        with open(rec / "transcript.json", "w", encoding="utf-8") as f:
            json.dump(kwargs["transcript_json"], f)

    return rec


class TestMergeTranscripts:
    def test_requires_at_least_two(self, tmp_path: Path):
        """Should raise ValueError with fewer than 2 recordings."""
        rec = _make_recording(tmp_path, "2026-03-10_09-00-00_Test")
        with pytest.raises(ValueError, match="at least 2"):
            merge_transcripts([rec], tmp_path / "output")

    def test_basic_merge(self, tmp_path: Path):
        """Should create merged directory with combined transcript."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_Meeting",
            transcript="Hello from part 1",
            meta={"meeting_subject": "Standup"},
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_Meeting",
            transcript="Hello from part 2",
            meta={},
        )
        output = tmp_path / "output"
        merged = merge_transcripts([r1, r2], output)

        assert merged.exists()
        assert (merged / "transcript.txt").exists()
        text = (merged / "transcript.txt").read_text(encoding="utf-8")
        assert "Hello from part 1" in text
        assert "Hello from part 2" in text

    def test_merged_metadata_created(self, tmp_path: Path):
        """Should create metadata.json in merged directory."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            transcript="Part 1",
            meta={"meeting_subject": "Demo", "duration_seconds": 300},
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_B",
            transcript="Part 2",
            meta={"duration_seconds": 600},
        )
        merged = merge_transcripts([r1, r2], tmp_path / "out")
        meta_path = merged / "metadata.json"
        assert meta_path.exists()

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["duration_seconds"] == 900
        assert meta["meeting_subject"] == "Demo"
        assert "merged" in meta["tags"]

    def test_sorts_chronologically(self, tmp_path: Path):
        """Should sort recordings by name (chronological)."""
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_B",
            transcript="Second",
        )
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            transcript="First",
        )
        # Pass in reverse order
        merged = merge_transcripts([r2, r1], tmp_path / "out")
        text = (merged / "transcript.txt").read_text(encoding="utf-8")
        # First should appear before Second
        assert text.index("First") < text.index("Second")

    def test_merged_name_includes_subject(self, tmp_path: Path):
        """Should include subject in merged directory name."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            transcript="x",
            meta={"meeting_subject": "Weekly Sync"},
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_B",
            transcript="y",
        )
        merged = merge_transcripts([r1, r2], tmp_path / "out")
        assert "Weekly_Sync" in merged.name

    def test_merges_summaries(self, tmp_path: Path):
        """Should merge summary.md files."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            summary="Summary part 1",
            meta={"meeting_subject": "Planning"},
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_B",
            summary="Summary part 2",
            meta={"meeting_subject": "Review"},
        )
        merged = merge_transcripts([r1, r2], tmp_path / "out")
        summary = (merged / "summary.md").read_text(encoding="utf-8")
        assert "# Combined Summary" in summary
        assert "Summary part 1" in summary
        assert "Summary part 2" in summary
        assert "## Part: Planning" in summary
        assert "## Part: Review" in summary

    def test_merges_notes(self, tmp_path: Path):
        """Should merge notes.md files."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            transcript="x", notes="Notes 1",
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_B",
            transcript="y", notes="Notes 2",
        )
        merged = merge_transcripts([r1, r2], tmp_path / "out")
        notes = (merged / "notes.md").read_text(encoding="utf-8")
        assert "Notes 1" in notes
        assert "Notes 2" in notes

    def test_no_transcript_no_crash(self, tmp_path: Path):
        """Should handle recordings without transcripts."""
        r1 = _make_recording(tmp_path, "2026-03-10_09-00-00_A")
        r2 = _make_recording(tmp_path, "2026-03-10_10-00-00_B")
        merged = merge_transcripts([r1, r2], tmp_path / "out")
        assert merged.exists()
        assert (merged / "metadata.json").exists()
        assert not (merged / "transcript.txt").exists()

    def test_three_recordings(self, tmp_path: Path):
        """Should handle more than 2 recordings."""
        recs = []
        for i in range(3):
            recs.append(_make_recording(
                tmp_path, f"2026-03-10_{9+i:02d}-00-00_R{i}",
                transcript=f"Part {i}",
            ))
        merged = merge_transcripts(recs, tmp_path / "out")
        text = (merged / "transcript.txt").read_text(encoding="utf-8")
        assert "Part 0" in text
        assert "Part 1" in text
        assert "Part 2" in text


class TestLoadMeta:
    def test_loads_valid(self, tmp_path: Path):
        """Should load valid metadata."""
        rec = _make_recording(
            tmp_path, "rec", meta={"meeting_subject": "Test"})
        meta = _load_meta(rec)
        assert meta["meeting_subject"] == "Test"

    def test_missing_file(self, tmp_path: Path):
        """Should return empty dict if no metadata.json."""
        rec = tmp_path / "rec"
        rec.mkdir()
        assert _load_meta(rec) == {}

    def test_invalid_json(self, tmp_path: Path):
        """Should return empty dict for corrupted JSON."""
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "metadata.json").write_text("not json", encoding="utf-8")
        assert _load_meta(rec) == {}


class TestMergeTextFiles:
    def test_adds_headers(self, tmp_path: Path):
        """Should add date/time headers between sections."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-30-00_A",
            transcript="Content 1",
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_14-00-00_B",
            transcript="Content 2",
        )
        result = _merge_text_files([r1, r2], "transcript.txt")
        assert "--- 2026-03-10 09:30:00 ---" in result
        assert "--- 2026-03-10 14:00:00 ---" in result
        assert "Content 1" in result
        assert "Content 2" in result

    def test_skips_missing(self, tmp_path: Path):
        """Should skip recordings without the file."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            transcript="Only this",
        )
        r2 = _make_recording(tmp_path, "2026-03-10_10-00-00_B")
        result = _merge_text_files([r1, r2], "transcript.txt")
        assert "Only this" in result
        assert result.count("---") == 2  # One header pair

    def test_empty_file(self, tmp_path: Path):
        """Should skip empty files."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            transcript="",
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_B",
            transcript="Real content",
        )
        result = _merge_text_files([r1, r2], "transcript.txt")
        assert "Real content" in result
        assert result.count("---") == 2


class TestMergeSummaries:
    def test_combined_header(self, tmp_path: Path):
        """Should wrap in Combined Summary header."""
        r1 = _make_recording(
            tmp_path, "2026-03-10_09-00-00_A",
            summary="Sum 1",
            meta={"meeting_subject": "Alpha"},
        )
        r2 = _make_recording(
            tmp_path, "2026-03-10_10-00-00_B",
            summary="Sum 2",
            meta={"meeting_subject": "Beta"},
        )
        result = _merge_summaries([r1, r2])
        assert result.startswith("# Combined Summary")
        assert "## Part: Alpha" in result
        assert "## Part: Beta" in result

    def test_no_summaries(self, tmp_path: Path):
        """Should return empty string if no summaries."""
        r1 = _make_recording(tmp_path, "2026-03-10_09-00-00_A")
        r2 = _make_recording(tmp_path, "2026-03-10_10-00-00_B")
        assert _merge_summaries([r1, r2]) == ""


class TestMergeTranscriptJson:
    def test_adjusts_timestamps(self, tmp_path: Path):
        """Should offset timestamps for subsequent recordings."""
        r1 = _make_recording(
            tmp_path, "r1",
            transcript_json={
                "segments": [
                    {"start": 0.0, "end": 5.0, "text": "Hello"},
                    {"start": 5.0, "end": 10.0, "text": "World"},
                ]
            },
        )
        r2 = _make_recording(
            tmp_path, "r2",
            transcript_json={
                "segments": [
                    {"start": 0.0, "end": 3.0, "text": "Part 2"},
                ]
            },
        )
        result = _merge_transcript_json([r1, r2])
        assert result is not None
        segs = result["segments"]
        assert len(segs) == 3
        # First recording: unchanged
        assert segs[0]["start"] == 0.0
        assert segs[1]["end"] == 10.0
        # Second recording: offset by 10.0 + 2.0 gap = 12.0
        assert segs[2]["start"] == 12.0
        assert segs[2]["end"] == 15.0

    def test_adds_source_tag(self, tmp_path: Path):
        """Should tag segments with source recording name."""
        r1 = _make_recording(
            tmp_path, "rec_a",
            transcript_json={"segments": [{"start": 0, "end": 1, "text": "x"}]},
        )
        r2 = _make_recording(
            tmp_path, "rec_b",
            transcript_json={"segments": [{"start": 0, "end": 1, "text": "y"}]},
        )
        result = _merge_transcript_json([r1, r2])
        assert result["segments"][0]["source"] == "rec_a"
        assert result["segments"][1]["source"] == "rec_b"

    def test_returns_none_if_missing(self, tmp_path: Path):
        """Should return None if any recording lacks transcript.json."""
        r1 = _make_recording(
            tmp_path, "r1",
            transcript_json={"segments": []},
        )
        r2 = _make_recording(tmp_path, "r2")
        assert _merge_transcript_json([r1, r2]) is None

    def test_returns_none_if_invalid(self, tmp_path: Path):
        """Should return None for corrupted JSON."""
        r1 = _make_recording(tmp_path, "r1")
        (r1 / "transcript.json").write_text("bad", encoding="utf-8")
        r2 = _make_recording(
            tmp_path, "r2",
            transcript_json={"segments": []},
        )
        assert _merge_transcript_json([r1, r2]) is None


class TestBuildMergedMetadata:
    def test_total_duration(self, tmp_path: Path):
        """Should sum durations."""
        r1 = _make_recording(
            tmp_path, "r1", meta={"duration_seconds": 100})
        r2 = _make_recording(
            tmp_path, "r2", meta={"duration_seconds": 200})
        merged_dir = tmp_path / "merged"
        merged_dir.mkdir()
        meta = _build_merged_metadata([r1, r2], merged_dir)
        assert meta["duration_seconds"] == 300

    def test_unique_attendees(self, tmp_path: Path):
        """Should merge attendees without duplicates."""
        r1 = _make_recording(
            tmp_path, "r1",
            meta={"meeting_attendees": ["Alice", "Bob"]},
        )
        r2 = _make_recording(
            tmp_path, "r2",
            meta={"meeting_attendees": ["bob", "Charlie"]},
        )
        merged_dir = tmp_path / "merged"
        merged_dir.mkdir()
        meta = _build_merged_metadata([r1, r2], merged_dir)
        names = [a.lower() for a in meta["meeting_attendees"]]
        assert len(names) == 3
        assert "alice" in names
        assert "bob" in names
        assert "charlie" in names

    def test_unique_tags(self, tmp_path: Path):
        """Should merge tags without duplicates, add 'merged'."""
        r1 = _make_recording(
            tmp_path, "r1", meta={"tags": ["engineering", "standup"]})
        r2 = _make_recording(
            tmp_path, "r2", meta={"tags": ["standup", "planning"]})
        merged_dir = tmp_path / "merged"
        merged_dir.mkdir()
        meta = _build_merged_metadata([r1, r2], merged_dir)
        tags_lower = [t.lower() for t in meta["tags"]]
        assert tags_lower.count("standup") == 1
        assert "merged" in tags_lower

    def test_sources_list(self, tmp_path: Path):
        """Should record source recording names."""
        r1 = _make_recording(tmp_path, "rec_a", meta={})
        r2 = _make_recording(tmp_path, "rec_b", meta={})
        merged_dir = tmp_path / "merged"
        merged_dir.mkdir()
        meta = _build_merged_metadata([r1, r2], merged_dir)
        assert meta["merged_from"] == ["rec_a", "rec_b"]

    def test_max_speakers(self, tmp_path: Path):
        """Should take max speaker count."""
        r1 = _make_recording(
            tmp_path, "r1", meta={"speaker_count": 3})
        r2 = _make_recording(
            tmp_path, "r2", meta={"speaker_count": 5})
        merged_dir = tmp_path / "merged"
        merged_dir.mkdir()
        meta = _build_merged_metadata([r1, r2], merged_dir)
        assert meta["speaker_count"] == 5
