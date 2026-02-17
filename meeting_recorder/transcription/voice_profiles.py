"""Voice embedding speaker profiles for cross-meeting speaker identification.

Stores speaker voice embeddings in a SQLite database and matches
new speakers against known profiles using cosine similarity.
Embeddings are extracted from audio segments using pyannote's
speaker embedding model.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".meeting_recorder" / "speaker_profiles.db"

# Cosine similarity threshold for a match
DEFAULT_MATCH_THRESHOLD = 0.75


@dataclass
class SpeakerProfile:
    """A stored speaker voice profile."""
    name: str
    embedding: np.ndarray  # shape: (embedding_dim,)
    sample_count: int = 1  # how many recordings contributed to this embedding


@dataclass
class EmbeddingMatch:
    """Result of matching an embedding against stored profiles."""
    name: str
    similarity: float
    is_match: bool  # similarity >= threshold


class VoiceProfileDB:
    """SQLite database for speaker voice embeddings.

    Stores named speaker profiles with their averaged embeddings.
    Supports enrollment (adding/updating profiles) and matching
    (finding the best-matching profile for a new embedding).
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
            self._ensure_schema()
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._conn or self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS speaker_profiles (
                name TEXT PRIMARY KEY,
                embedding_json TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    def enroll(self, name: str, embedding: np.ndarray) -> None:
        """Add or update a speaker profile.

        If the speaker already exists, their embedding is updated as a
        running average weighted by sample count.

        Args:
            name: Speaker name (case-preserved, matched case-insensitively).
            embedding: Voice embedding vector as numpy array.
        """
        conn = self._connect()

        # Check if profile exists
        row = conn.execute(
            "SELECT embedding_json, sample_count FROM speaker_profiles WHERE name = ? COLLATE NOCASE",
            (name,)
        ).fetchone()

        if row is not None:
            # Update with running average
            existing = np.array(json.loads(row["embedding_json"]))
            count = row["sample_count"]
            updated = (existing * count + embedding) / (count + 1)
            conn.execute("""
                UPDATE speaker_profiles
                SET embedding_json = ?, sample_count = ?, updated_at = datetime('now')
                WHERE name = ? COLLATE NOCASE
            """, (json.dumps(updated.tolist()), count + 1, name))
        else:
            conn.execute("""
                INSERT INTO speaker_profiles (name, embedding_json, embedding_dim, sample_count)
                VALUES (?, ?, ?, 1)
            """, (name, json.dumps(embedding.tolist()), len(embedding)))

        conn.commit()
        logger.info("Enrolled speaker profile: %s", name)

    def match(
        self,
        embedding: np.ndarray,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> Optional[EmbeddingMatch]:
        """Find the best matching stored profile for an embedding.

        Args:
            embedding: Voice embedding to match.
            threshold: Minimum cosine similarity for a match.

        Returns:
            EmbeddingMatch if any profile exceeds threshold, None otherwise.
        """
        conn = self._connect()
        rows = conn.execute("SELECT name, embedding_json FROM speaker_profiles").fetchall()

        if not rows:
            return None

        best_name = ""
        best_similarity = -1.0

        for row in rows:
            stored = np.array(json.loads(row["embedding_json"]))
            sim = cosine_similarity(embedding, stored)
            if sim > best_similarity:
                best_similarity = sim
                best_name = row["name"]

        if best_similarity >= threshold:
            return EmbeddingMatch(
                name=best_name,
                similarity=best_similarity,
                is_match=True,
            )

        if best_name:
            return EmbeddingMatch(
                name=best_name,
                similarity=best_similarity,
                is_match=False,
            )

        return None

    def get_profile(self, name: str) -> Optional[SpeakerProfile]:
        """Get a speaker profile by name."""
        conn = self._connect()
        row = conn.execute(
            "SELECT name, embedding_json, sample_count FROM speaker_profiles WHERE name = ? COLLATE NOCASE",
            (name,)
        ).fetchone()

        if row is None:
            return None

        return SpeakerProfile(
            name=row["name"],
            embedding=np.array(json.loads(row["embedding_json"])),
            sample_count=row["sample_count"],
        )

    def list_profiles(self) -> list[str]:
        """List all enrolled speaker names."""
        conn = self._connect()
        rows = conn.execute("SELECT name FROM speaker_profiles ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def delete_profile(self, name: str) -> bool:
        """Delete a speaker profile. Returns True if deleted."""
        conn = self._connect()
        cursor = conn.execute(
            "DELETE FROM speaker_profiles WHERE name = ? COLLATE NOCASE", (name,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors.

    Returns:
        Similarity between -1.0 and 1.0 (1.0 = identical).
    """
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def extract_embedding(
    audio_path: Path,
    start: float = 0.0,
    end: float = 0.0,
) -> Optional[np.ndarray]:
    """Extract a speaker embedding from an audio segment.

    Uses pyannote's speaker embedding model to compute a voice
    embedding from a segment of audio.

    Args:
        audio_path: Path to audio file.
        start: Segment start time in seconds (0 = beginning).
        end: Segment end time in seconds (0 = entire file).

    Returns:
        Embedding as numpy array, or None if extraction fails.
    """
    try:
        from pyannote.audio import Inference
        import torch
        import torchaudio

        # Load audio segment
        waveform, sample_rate = torchaudio.load(str(audio_path))

        if start > 0 or end > 0:
            start_frame = int(start * sample_rate)
            end_frame = int(end * sample_rate) if end > 0 else waveform.shape[1]
            waveform = waveform[:, start_frame:end_frame]

        if waveform.shape[1] < sample_rate:  # Less than 1 second
            logger.debug("Audio segment too short for embedding extraction")
            return None

        # Use pyannote's embedding model
        inference = Inference("pyannote/embedding", use_auth_token=False)

        # Convert to format expected by pyannote
        embedding = inference({"waveform": waveform, "sample_rate": sample_rate})

        return np.array(embedding)

    except ImportError:
        logger.debug("pyannote.audio not available for embedding extraction")
        return None
    except Exception:
        logger.debug("Embedding extraction failed", exc_info=True)
        return None
