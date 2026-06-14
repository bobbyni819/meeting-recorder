"""Natural-language Q&A over indexed meeting recordings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import punctuation
from typing import Any

from meeting_recorder.config import Config
from meeting_recorder.search.index import RecordingIndex, SearchResult

try:
    from google import genai
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    genai = None


MAX_CONTEXT_CHARS = 24000
DEFAULT_MODEL = "gemini-2.0-flash"

_STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
}


@dataclass
class Source:
    dir_name: str
    date: str
    subject: str
    path: str


@dataclass
class AskResult:
    answer: str
    sources: list[Source]
    used_recordings: int


def ask_meetings(
    question: str,
    *,
    top_k: int = 5,
    config: Any | None = None,
    max_chars_per_source: int = 8000,
) -> AskResult:
    """Answer a question using relevant meeting transcript excerpts."""
    config = config or Config.load()
    api_key = (config.transcription.gemini_api_key or "").strip()
    if not api_key:
        raise ValueError("Set transcription.gemini_api_key to use ask")

    fts_query = _derive_fts_query(question)
    with RecordingIndex() as index:
        results = index.search(fts_query, limit=top_k)

    if not results:
        return AskResult(
            answer="No meetings matched that question.",
            sources=[],
            used_recordings=0,
        )

    sources: list[Source] = []
    blocks: list[str] = []
    total_chars = 0

    for result in results:
        source = _source_from_result(result)
        text = _read_transcript(source.path)
        if max_chars_per_source >= 0:
            text = text[:max_chars_per_source]

        title = source.subject or source.dir_name
        block = f"[Source {len(sources) + 1}] {source.date} — {title}\n{text}"
        if blocks and total_chars + len(block) > MAX_CONTEXT_CHARS:
            break
        if not blocks and len(block) > MAX_CONTEXT_CHARS:
            block = block[:MAX_CONTEXT_CHARS]

        blocks.append(block)
        sources.append(source)
        total_chars += len(block)

    system_prompt = (
        "Answer ONLY from the provided meeting excerpts. Cite supporting "
        "claims inline as [Source N]. If the answer is not found in the "
        "meetings, say that explicitly."
    )
    user_prompt = f"Question: {question}\n\nMeeting excerpts:\n\n" + "\n\n".join(blocks)

    try:
        answer = _generate_answer(
            api_key=api_key,
            model=config.transcription.gemini_model or DEFAULT_MODEL,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except Exception as e:
        return AskResult(
            answer=f"(Gemini error: {e})",
            sources=sources,
            used_recordings=len(sources),
        )

    return AskResult(answer=answer, sources=sources, used_recordings=len(sources))


def format_ask_result(result: AskResult) -> str:
    """Format an AskResult for display."""
    if not result.sources:
        return result.answer

    lines = [result.answer, "Sources:"]
    for index, source in enumerate(result.sources, start=1):
        title = source.subject or source.dir_name
        lines.append(f"- [{index}] {title} ({source.date}) — {source.path}")
    return "\n".join(lines)


def _derive_fts_query(question: str) -> str:
    words = re.findall(r"\b\w+\b", question.translate(str.maketrans("", "", punctuation)).lower())
    content_words = [word for word in words if word not in _STOPWORDS]
    terms = content_words or words
    if not terms:
        return question
    # FTS5 ANDs space-separated terms, which over-restricts a natural-language
    # question (no recording contains every word). OR the terms for recall and
    # let BM25 rank; quote each so FTS5 treats it as a literal (no operator /
    # special-character surprises from a stray "and"/"or"/punctuation).
    return " OR ".join(f'"{word}"' for word in terms)


def _source_from_result(result: SearchResult) -> Source:
    path = str(getattr(result, "path", "") or result.recording_dir)
    return Source(
        dir_name=Path(path).name,
        date=result.date,
        subject=result.subject,
        path=path,
    )


def _read_transcript(path: str) -> str:
    recording_dir = Path(path)
    transcript_txt = recording_dir / "transcript.txt"
    if transcript_txt.exists():
        return transcript_txt.read_text(encoding="utf-8")

    transcript_json = recording_dir / "transcript.json"
    if transcript_json.exists():
        with open(transcript_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments") or []
        return "\n".join(str(segment.get("text", "")) for segment in segments)

    return ""


def _generate_answer(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    if genai is None:
        raise ImportError("google-genai is not installed")

    with genai.Client(api_key=api_key) as client:
        response = client.models.generate_content(
            model=model,
            contents=[system_prompt, user_prompt],
        )
        return response.text
