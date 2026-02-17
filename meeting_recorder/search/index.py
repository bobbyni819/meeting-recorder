"""SQLite FTS5 search index for meeting recordings."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".meeting_recorder" / "recordings.db"


@dataclass
class SearchResult:
    """A single search result."""
    recording_dir: str
    date: str
    subject: str
    app_name: str
    organizer: str
    attendees: str
    speakers: str
    snippet: str
    rank: float = 0.0


class RecordingIndex:
    """SQLite FTS5 search index for meeting recordings.

    Uses WAL journal mode for concurrent reads during recording.
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
            self.ensure_schema()
        return self._conn

    def ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        conn = self._conn or self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recordings (
                recording_dir TEXT PRIMARY KEY,
                date TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                app_name TEXT NOT NULL DEFAULT '',
                organizer TEXT NOT NULL DEFAULT '',
                attendees TEXT NOT NULL DEFAULT '',
                speakers TEXT NOT NULL DEFAULT '',
                transcript_text TEXT NOT NULL DEFAULT '',
                duration_seconds REAL NOT NULL DEFAULT 0,
                speaker_count INTEGER NOT NULL DEFAULT 0,
                segment_count INTEGER NOT NULL DEFAULT 0,
                has_summary INTEGER NOT NULL DEFAULT 0
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS recordings_fts USING fts5(
                recording_dir,
                subject,
                app_name,
                organizer,
                attendees,
                speakers,
                transcript_text,
                content='recordings',
                content_rowid='rowid'
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS recordings_ai AFTER INSERT ON recordings BEGIN
                INSERT INTO recordings_fts(rowid, recording_dir, subject, app_name, organizer, attendees, speakers, transcript_text)
                VALUES (new.rowid, new.recording_dir, new.subject, new.app_name, new.organizer, new.attendees, new.speakers, new.transcript_text);
            END;

            CREATE TRIGGER IF NOT EXISTS recordings_ad AFTER DELETE ON recordings BEGIN
                INSERT INTO recordings_fts(recordings_fts, rowid, recording_dir, subject, app_name, organizer, attendees, speakers, transcript_text)
                VALUES ('delete', old.rowid, old.recording_dir, old.subject, old.app_name, old.organizer, old.attendees, old.speakers, old.transcript_text);
            END;

            CREATE TRIGGER IF NOT EXISTS recordings_au AFTER UPDATE ON recordings BEGIN
                INSERT INTO recordings_fts(recordings_fts, rowid, recording_dir, subject, app_name, organizer, attendees, speakers, transcript_text)
                VALUES ('delete', old.rowid, old.recording_dir, old.subject, old.app_name, old.organizer, old.attendees, old.speakers, old.transcript_text);
                INSERT INTO recordings_fts(rowid, recording_dir, subject, app_name, organizer, attendees, speakers, transcript_text)
                VALUES (new.rowid, new.recording_dir, new.subject, new.app_name, new.organizer, new.attendees, new.speakers, new.transcript_text);
            END;
        """)
        conn.commit()

    def index_recording(self, recording_dir: Path) -> bool:
        """Index a single recording directory.

        Reads metadata.json and transcript files to populate the index.

        Returns True if indexed successfully, False on error.
        """
        conn = self._connect()

        try:
            metadata_path = recording_dir / "metadata.json"
            if not metadata_path.exists():
                logger.warning("No metadata.json in %s, skipping", recording_dir)
                return False

            with open(metadata_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            # Read transcript text
            transcript_text = ""
            transcript_json = recording_dir / "transcript.json"
            transcript_txt = recording_dir / "transcript.txt"

            if transcript_json.exists():
                try:
                    with open(transcript_json, "r", encoding="utf-8") as f:
                        tdata = json.load(f)
                    segments = tdata.get("segments", [])
                    lines = []
                    for seg in segments:
                        speaker = seg.get("speaker", "")
                        text = seg.get("text", "")
                        if speaker:
                            lines.append(f"{speaker}: {text}")
                        else:
                            lines.append(text)
                    transcript_text = "\n".join(lines)
                except (json.JSONDecodeError, KeyError):
                    logger.warning("Failed to parse transcript.json in %s", recording_dir)
            elif transcript_txt.exists():
                transcript_text = transcript_txt.read_text(encoding="utf-8")

            # Extract speakers from transcript
            speakers = set()
            if transcript_json.exists():
                try:
                    with open(transcript_json, "r", encoding="utf-8") as f:
                        tdata = json.load(f)
                    for seg in tdata.get("segments", []):
                        if seg.get("speaker"):
                            speakers.add(seg["speaker"])
                except (json.JSONDecodeError, KeyError):
                    pass

            attendees = meta.get("meeting_attendees", [])
            if isinstance(attendees, list):
                attendees_str = ", ".join(attendees)
            else:
                attendees_str = str(attendees)

            # Upsert into recordings table
            conn.execute("""
                INSERT INTO recordings (
                    recording_dir, date, subject, app_name, organizer,
                    attendees, speakers, transcript_text, duration_seconds,
                    speaker_count, segment_count, has_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recording_dir) DO UPDATE SET
                    date=excluded.date, subject=excluded.subject,
                    app_name=excluded.app_name, organizer=excluded.organizer,
                    attendees=excluded.attendees, speakers=excluded.speakers,
                    transcript_text=excluded.transcript_text,
                    duration_seconds=excluded.duration_seconds,
                    speaker_count=excluded.speaker_count,
                    segment_count=excluded.segment_count,
                    has_summary=excluded.has_summary
            """, (
                str(recording_dir),
                meta.get("start_time", ""),
                meta.get("meeting_subject", ""),
                meta.get("app_name", ""),
                meta.get("meeting_organizer", ""),
                attendees_str,
                ", ".join(sorted(speakers)),
                transcript_text,
                meta.get("duration_seconds", 0),
                meta.get("speaker_count", 0),
                meta.get("segment_count", 0),
                1 if (recording_dir / "summary.json").exists() else 0,
            ))
            conn.commit()
            logger.info("Indexed recording: %s", recording_dir.name)
            return True

        except Exception:
            logger.exception("Failed to index recording: %s", recording_dir)
            return False

    def remove_recording(self, recording_dir: str) -> None:
        """Remove a recording from the index."""
        conn = self._connect()
        conn.execute("DELETE FROM recordings WHERE recording_dir = ?", (recording_dir,))
        conn.commit()

    def search(
        self,
        query: str = "",
        speaker: str = "",
        date_from: str = "",
        date_to: str = "",
        attendee: str = "",
        subject: str = "",
        limit: int = 50,
    ) -> list[SearchResult]:
        """Search recordings with optional filters.

        Args:
            query: FTS5 query string (searches transcript, subject, etc.)
            speaker: Filter by speaker name (substring match)
            date_from: Filter by start date (ISO format, inclusive)
            date_to: Filter by end date (ISO format, inclusive)
            attendee: Filter by attendee name (substring match)
            subject: Filter by meeting subject (substring match)
            limit: Maximum results to return

        Returns:
            List of SearchResult sorted by relevance (if FTS query) or date (descending).
        """
        conn = self._connect()

        if query:
            # FTS5 search with rank
            sql = """
                SELECT r.*, recordings_fts.rank
                FROM recordings_fts
                JOIN recordings r ON r.recording_dir = recordings_fts.recording_dir
                WHERE recordings_fts MATCH ?
            """
            params: list = [query]
        else:
            sql = "SELECT *, 0 as rank FROM recordings r WHERE 1=1"
            params = []

        if speaker:
            sql += " AND r.speakers LIKE ?"
            params.append(f"%{speaker}%")
        if date_from:
            sql += " AND r.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND r.date <= ?"
            params.append(date_to)
        if attendee:
            sql += " AND r.attendees LIKE ?"
            params.append(f"%{attendee}%")
        if subject:
            sql += " AND r.subject LIKE ?"
            params.append(f"%{subject}%")

        if query:
            sql += " ORDER BY rank LIMIT ?"
        else:
            sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            logger.error("Search query error: %s", e)
            return []

        results = []
        for row in rows:
            # Create snippet from transcript
            transcript = row["transcript_text"]
            snippet = ""
            if query and transcript:
                # Simple snippet: find first occurrence
                query_lower = query.lower()
                text_lower = transcript.lower()
                idx = text_lower.find(query_lower)
                if idx >= 0:
                    start = max(0, idx - 50)
                    end = min(len(transcript), idx + len(query) + 50)
                    snippet = ("..." if start > 0 else "") + transcript[start:end] + ("..." if end < len(transcript) else "")
                else:
                    snippet = transcript[:100] + ("..." if len(transcript) > 100 else "")
            elif transcript:
                snippet = transcript[:100] + ("..." if len(transcript) > 100 else "")

            results.append(SearchResult(
                recording_dir=row["recording_dir"],
                date=row["date"],
                subject=row["subject"],
                app_name=row["app_name"],
                organizer=row["organizer"],
                attendees=row["attendees"],
                speakers=row["speakers"],
                snippet=snippet,
                rank=row["rank"],
            ))

        return results

    def index_all(self, base_dir: Path) -> int:
        """Index all recordings in the base directory.

        Returns the number of recordings successfully indexed.
        """
        count = 0
        if not base_dir.exists():
            return count

        for recording_dir in sorted(base_dir.iterdir()):
            if recording_dir.is_dir() and (recording_dir / "metadata.json").exists():
                if self.index_recording(recording_dir):
                    count += 1

        logger.info("Indexed %d recordings from %s", count, base_dir)
        return count

    def sync(self, base_dir: Path) -> tuple[int, int]:
        """Sync the index with the recordings directory.

        Indexes new recordings and removes deleted ones.

        Returns:
            Tuple of (added_count, removed_count).
        """
        conn = self._connect()

        # Get currently indexed recordings
        indexed = {row["recording_dir"] for row in conn.execute("SELECT recording_dir FROM recordings").fetchall()}

        # Get current recordings on disk
        on_disk = set()
        if base_dir.exists():
            for d in base_dir.iterdir():
                if d.is_dir() and (d / "metadata.json").exists():
                    on_disk.add(str(d))

        # Remove stale entries
        removed = 0
        for recording_dir in indexed - on_disk:
            self.remove_recording(recording_dir)
            removed += 1

        # Index new recordings
        added = 0
        for recording_dir in on_disk - indexed:
            if self.index_recording(Path(recording_dir)):
                added += 1

        logger.info("Sync complete: %d added, %d removed", added, removed)
        return added, removed

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
