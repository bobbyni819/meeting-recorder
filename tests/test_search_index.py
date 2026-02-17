"""Tests for RecordingIndex SQLite FTS5 search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.search.index import RecordingIndex, SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metadata(
    recording_dir: Path,
    *,
    app_name: str = "Zoom",
    start_time: str = "2025-06-15T10:00:00",
    meeting_subject: str = "Weekly Standup",
    meeting_organizer: str = "Alice",
    meeting_attendees: list[str] | None = None,
    duration_seconds: float = 600.0,
    speaker_count: int = 2,
    segment_count: int = 10,
) -> Path:
    """Create a metadata.json file in the given recording directory."""
    recording_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "app_name": app_name,
        "start_time": start_time,
        "meeting_subject": meeting_subject,
        "meeting_organizer": meeting_organizer,
        "meeting_attendees": meeting_attendees or ["Alice", "Bob"],
        "duration_seconds": duration_seconds,
        "speaker_count": speaker_count,
        "segment_count": segment_count,
    }
    path = recording_dir / "metadata.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def _make_transcript(
    recording_dir: Path,
    segments: list[dict] | None = None,
) -> Path:
    """Create a transcript.json file in the given recording directory."""
    if segments is None:
        segments = [
            {"speaker": "Alice", "text": "Hello everyone, let's begin.", "start": 0.0, "end": 2.5},
            {"speaker": "Bob", "text": "Sure, sounds good.", "start": 3.0, "end": 5.0},
            {"speaker": "Alice", "text": "First topic is the deployment pipeline.", "start": 5.5, "end": 8.0},
        ]
    data = {"segments": segments}
    path = recording_dir / "transcript.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _make_recording(
    base_dir: Path,
    name: str,
    *,
    app_name: str = "Zoom",
    start_time: str = "2025-06-15T10:00:00",
    meeting_subject: str = "Weekly Standup",
    meeting_organizer: str = "Alice",
    meeting_attendees: list[str] | None = None,
    transcript_segments: list[dict] | None = None,
    include_transcript: bool = True,
) -> Path:
    """Create a full recording directory with metadata and optional transcript."""
    recording_dir = base_dir / name
    recording_dir.mkdir(parents=True, exist_ok=True)
    _make_metadata(
        recording_dir,
        app_name=app_name,
        start_time=start_time,
        meeting_subject=meeting_subject,
        meeting_organizer=meeting_organizer,
        meeting_attendees=meeting_attendees,
    )
    if include_transcript:
        _make_transcript(recording_dir, segments=transcript_segments)
    return recording_dir


@pytest.fixture
def index(tmp_path: Path) -> RecordingIndex:
    """Create a RecordingIndex backed by a temporary database."""
    db = tmp_path / "test.db"
    idx = RecordingIndex(db_path=db)
    return idx


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestEnsureSchema:
    """Test that ensure_schema creates the expected tables."""

    def test_creates_recordings_table(self, index: RecordingIndex):
        conn = index._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recordings'"
        ).fetchall()
        assert len(rows) == 1

    def test_creates_fts_table(self, index: RecordingIndex):
        conn = index._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recordings_fts'"
        ).fetchall()
        assert len(rows) == 1

    def test_idempotent(self, index: RecordingIndex):
        """Calling ensure_schema twice should not raise."""
        index._connect()
        index.ensure_schema()
        index.ensure_schema()


# ---------------------------------------------------------------------------
# Indexing tests
# ---------------------------------------------------------------------------

class TestIndexRecording:
    """Test index_recording()."""

    def test_index_with_metadata_and_transcript(self, index: RecordingIndex, tmp_path: Path):
        rec = _make_recording(tmp_path, "rec_001")
        assert index.index_recording(rec) is True

        # Verify data in DB
        conn = index._connect()
        row = conn.execute("SELECT * FROM recordings WHERE recording_dir = ?", (str(rec),)).fetchone()
        assert row is not None
        assert row["subject"] == "Weekly Standup"
        assert row["app_name"] == "Zoom"
        assert "Alice" in row["speakers"]

    def test_index_without_metadata_returns_false(self, index: RecordingIndex, tmp_path: Path):
        rec = tmp_path / "no_meta"
        rec.mkdir()
        assert index.index_recording(rec) is False

    def test_index_with_metadata_but_no_transcript(self, index: RecordingIndex, tmp_path: Path):
        rec = _make_recording(tmp_path, "rec_no_transcript", include_transcript=False)
        assert index.index_recording(rec) is True

        conn = index._connect()
        row = conn.execute("SELECT * FROM recordings WHERE recording_dir = ?", (str(rec),)).fetchone()
        assert row is not None
        assert row["transcript_text"] == ""

    def test_index_with_txt_transcript(self, index: RecordingIndex, tmp_path: Path):
        rec = _make_recording(tmp_path, "rec_txt", include_transcript=False)
        (rec / "transcript.txt").write_text("Hello from text file.", encoding="utf-8")
        assert index.index_recording(rec) is True

        conn = index._connect()
        row = conn.execute("SELECT * FROM recordings WHERE recording_dir = ?", (str(rec),)).fetchone()
        assert row["transcript_text"] == "Hello from text file."

    def test_upsert_updates_existing(self, index: RecordingIndex, tmp_path: Path):
        rec = _make_recording(tmp_path, "rec_upsert", meeting_subject="Original Subject")
        assert index.index_recording(rec) is True

        # Update metadata and re-index
        _make_metadata(rec, meeting_subject="Updated Subject")
        assert index.index_recording(rec) is True

        conn = index._connect()
        row = conn.execute("SELECT * FROM recordings WHERE recording_dir = ?", (str(rec),)).fetchone()
        assert row["subject"] == "Updated Subject"

        # Verify only one row exists
        count = conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------

class TestSearch:
    """Test search()."""

    def test_search_by_keyword_finds_match(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_a", meeting_subject="Deployment Review")
        index.index_recording(tmp_path / "rec_a")

        results = index.search(query="deployment")
        assert len(results) >= 1
        assert any("Deployment" in r.subject for r in results)

    def test_search_by_keyword_no_match(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_b", meeting_subject="Budget Meeting")
        index.index_recording(tmp_path / "rec_b")

        results = index.search(query="xyznonexistent")
        assert len(results) == 0

    def test_search_by_speaker_filter(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_c", transcript_segments=[
            {"speaker": "Carol", "text": "Testing speaker filter.", "start": 0.0, "end": 2.0},
        ])
        _make_recording(tmp_path, "rec_d", transcript_segments=[
            {"speaker": "Dave", "text": "Another recording.", "start": 0.0, "end": 2.0},
        ])
        index.index_recording(tmp_path / "rec_c")
        index.index_recording(tmp_path / "rec_d")

        results = index.search(speaker="Carol")
        assert len(results) == 1
        assert "Carol" in results[0].speakers

    def test_search_by_date_from(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_old", start_time="2024-01-01T10:00:00")
        _make_recording(tmp_path, "rec_new", start_time="2025-06-01T10:00:00")
        index.index_recording(tmp_path / "rec_old")
        index.index_recording(tmp_path / "rec_new")

        results = index.search(date_from="2025-01-01")
        assert len(results) == 1
        assert results[0].date.startswith("2025-")

    def test_search_by_date_to(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_early", start_time="2024-01-15T10:00:00")
        _make_recording(tmp_path, "rec_late", start_time="2025-12-15T10:00:00")
        index.index_recording(tmp_path / "rec_early")
        index.index_recording(tmp_path / "rec_late")

        results = index.search(date_to="2024-12-31")
        assert len(results) == 1
        assert results[0].date.startswith("2024-")

    def test_search_by_date_range(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_jan", start_time="2025-01-15T10:00:00")
        _make_recording(tmp_path, "rec_mar", start_time="2025-03-15T10:00:00")
        _make_recording(tmp_path, "rec_jul", start_time="2025-07-15T10:00:00")
        index.index_recording(tmp_path / "rec_jan")
        index.index_recording(tmp_path / "rec_mar")
        index.index_recording(tmp_path / "rec_jul")

        results = index.search(date_from="2025-02-01", date_to="2025-06-30")
        assert len(results) == 1
        assert "2025-03" in results[0].date

    def test_search_by_attendee_filter(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_e", meeting_attendees=["Eve", "Frank"])
        _make_recording(tmp_path, "rec_f", meeting_attendees=["Grace", "Heidi"])
        index.index_recording(tmp_path / "rec_e")
        index.index_recording(tmp_path / "rec_f")

        results = index.search(attendee="Eve")
        assert len(results) == 1
        assert "Eve" in results[0].attendees

    def test_search_by_subject_filter(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_g", meeting_subject="Sprint Retro")
        _make_recording(tmp_path, "rec_h", meeting_subject="Design Review")
        index.index_recording(tmp_path / "rec_g")
        index.index_recording(tmp_path / "rec_h")

        results = index.search(subject="Retro")
        assert len(results) == 1
        assert "Retro" in results[0].subject

    def test_search_combined_query_and_speaker(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_i", meeting_subject="API Discussion", transcript_segments=[
            {"speaker": "Ivan", "text": "Let's discuss the API design.", "start": 0.0, "end": 3.0},
        ])
        _make_recording(tmp_path, "rec_j", meeting_subject="API Discussion", transcript_segments=[
            {"speaker": "Judy", "text": "The API needs rate limiting.", "start": 0.0, "end": 3.0},
        ])
        index.index_recording(tmp_path / "rec_i")
        index.index_recording(tmp_path / "rec_j")

        results = index.search(query="API", speaker="Ivan")
        assert len(results) == 1
        assert "Ivan" in results[0].speakers

    def test_search_with_limit(self, index: RecordingIndex, tmp_path: Path):
        for i in range(5):
            _make_recording(
                tmp_path, f"rec_lim_{i}",
                start_time=f"2025-06-{15 + i:02d}T10:00:00",
            )
            index.index_recording(tmp_path / f"rec_lim_{i}")

        results = index.search(subject="Standup", limit=3)
        assert len(results) == 3

    def test_empty_query_with_speaker_filter(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_k", transcript_segments=[
            {"speaker": "Karl", "text": "Working on tests.", "start": 0.0, "end": 2.0},
        ])
        index.index_recording(tmp_path / "rec_k")

        results = index.search(speaker="Karl")
        assert len(results) == 1

    def test_special_characters_in_query(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_special")
        index.index_recording(tmp_path / "rec_special")

        # These should not raise, even if they return no results
        results = index.search(query="hello OR world")
        assert isinstance(results, list)

        # Invalid FTS syntax should return empty, not crash
        results = index.search(query='"""')
        assert isinstance(results, list)

    def test_unicode_in_transcript(self, index: RecordingIndex, tmp_path: Path):
        _make_recording(tmp_path, "rec_unicode", transcript_segments=[
            {"speaker": "Takeshi", "text": "日本語のテストです。", "start": 0.0, "end": 3.0},
            {"speaker": "Maria", "text": "Prueba en espanol con acentos.", "start": 3.0, "end": 6.0},
        ])
        index.index_recording(tmp_path / "rec_unicode")

        # Verify Unicode text is stored correctly
        conn = index._connect()
        row = conn.execute(
            "SELECT transcript_text FROM recordings WHERE recording_dir = ?",
            (str(tmp_path / "rec_unicode"),),
        ).fetchone()
        assert "日本語" in row["transcript_text"]

        # FTS search using Latin text from the same recording
        results = index.search(query="Prueba")
        assert len(results) >= 1
        assert "日本語" in results[0].snippet or "Prueba" in results[0].snippet

    def test_snippet_contains_query_context(self, index: RecordingIndex, tmp_path: Path):
        long_text = "A" * 100 + " deployment pipeline " + "B" * 100
        _make_recording(tmp_path, "rec_snippet", transcript_segments=[
            {"speaker": "Alice", "text": long_text, "start": 0.0, "end": 10.0},
        ])
        index.index_recording(tmp_path / "rec_snippet")

        results = index.search(query="deployment")
        assert len(results) >= 1
        assert "deployment" in results[0].snippet.lower()


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------

class TestIndexAll:
    """Test index_all()."""

    def test_indexes_multiple_recordings(self, index: RecordingIndex, tmp_path: Path):
        base = tmp_path / "recordings"
        base.mkdir()
        _make_recording(base, "meeting_1")
        _make_recording(base, "meeting_2")
        _make_recording(base, "meeting_3")

        count = index.index_all(base)
        assert count == 3

    def test_empty_directory_returns_zero(self, index: RecordingIndex, tmp_path: Path):
        base = tmp_path / "empty_recordings"
        base.mkdir()
        count = index.index_all(base)
        assert count == 0

    def test_nonexistent_directory_returns_zero(self, index: RecordingIndex, tmp_path: Path):
        base = tmp_path / "does_not_exist"
        count = index.index_all(base)
        assert count == 0

    def test_skips_dirs_without_metadata(self, index: RecordingIndex, tmp_path: Path):
        base = tmp_path / "mixed"
        base.mkdir()
        _make_recording(base, "valid_meeting")
        (base / "random_dir").mkdir()
        (base / "random_dir" / "notes.txt").write_text("not a recording", encoding="utf-8")

        count = index.index_all(base)
        assert count == 1


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

class TestSync:
    """Test sync()."""

    def test_sync_adds_new_recordings(self, index: RecordingIndex, tmp_path: Path):
        base = tmp_path / "sync_dir"
        base.mkdir()
        _make_recording(base, "existing")
        index.index_recording(base / "existing")

        _make_recording(base, "new_one")
        added, removed = index.sync(base)
        assert added == 1
        assert removed == 0

    def test_sync_removes_deleted_recordings(self, index: RecordingIndex, tmp_path: Path):
        base = tmp_path / "sync_del"
        base.mkdir()
        rec = _make_recording(base, "to_delete")
        index.index_recording(rec)

        # Remove the recording from disk
        import shutil
        shutil.rmtree(rec)

        added, removed = index.sync(base)
        assert added == 0
        assert removed == 1


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------

class TestRemoveRecording:
    """Test remove_recording()."""

    def test_remove_existing_recording(self, index: RecordingIndex, tmp_path: Path):
        rec = _make_recording(tmp_path, "to_remove")
        index.index_recording(rec)

        index.remove_recording(str(rec))

        conn = index._connect()
        row = conn.execute("SELECT * FROM recordings WHERE recording_dir = ?", (str(rec),)).fetchone()
        assert row is None

    def test_remove_nonexistent_does_not_error(self, index: RecordingIndex):
        # Should not raise
        index.remove_recording("/nonexistent/path")


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------

class TestSearchResult:
    """Test SearchResult dataclass."""

    def test_default_rank(self):
        result = SearchResult(
            recording_dir="/tmp/rec",
            date="2025-06-15",
            subject="Test",
            app_name="Zoom",
            organizer="Alice",
            attendees="Alice, Bob",
            speakers="Alice",
            snippet="Hello",
        )
        assert result.rank == 0.0

    def test_all_fields_set(self):
        result = SearchResult(
            recording_dir="/tmp/rec",
            date="2025-06-15",
            subject="Test",
            app_name="Zoom",
            organizer="Alice",
            attendees="Alice, Bob",
            speakers="Alice",
            snippet="Hello",
            rank=-1.5,
        )
        assert result.recording_dir == "/tmp/rec"
        assert result.date == "2025-06-15"
        assert result.rank == -1.5


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------

class TestClose:
    """Test close()."""

    def test_close_active_connection(self, index: RecordingIndex):
        index._connect()
        index.close()
        assert index._conn is None

    def test_close_without_connection(self, index: RecordingIndex):
        """Closing without ever connecting should not error."""
        index.close()

    def test_close_twice(self, index: RecordingIndex):
        """Closing twice should not error."""
        index._connect()
        index.close()
        index.close()
