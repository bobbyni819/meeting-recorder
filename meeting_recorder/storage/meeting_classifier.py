"""Meeting type classifier.

Auto-classifies meetings into categories (standup, planning, review, 1-on-1,
all-hands, brainstorm, retrospective, interview, training, demo) based on
subject, transcript content, duration, and speaker count.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Meeting type definitions: keyword patterns + heuristics
_MEETING_TYPES: dict[str, dict] = {
    "standup": {
        "subject_patterns": [r"\bstand[\s-]?up\b", r"\bdaily\b", r"\bscrum\b", r"\bsync\b"],
        "transcript_keywords": ["yesterday", "today", "blocker", "blocked", "working on"],
        "duration_range": (3, 25),  # minutes
        "speaker_range": (2, 15),
    },
    "planning": {
        "subject_patterns": [r"\bplanning\b", r"\bsprint planning\b", r"\bbacklog\b", r"\broadmap\b"],
        "transcript_keywords": ["estimate", "story point", "priority", "sprint", "backlog", "scope"],
        "duration_range": (20, 120),
        "speaker_range": (2, 20),
    },
    "review": {
        "subject_patterns": [r"\breview\b", r"\bdemo\b", r"\bshowcase\b", r"\bwalkthrough\b"],
        "transcript_keywords": ["feedback", "looks good", "change", "approve", "suggestion", "demo"],
        "duration_range": (10, 90),
        "speaker_range": (2, 20),
    },
    "one_on_one": {
        "subject_patterns": [r"\b1[\s:-]?on[\s:-]?1\b", r"\bone[\s:-]?on[\s:-]?one\b", r"\bcatch[\s-]?up\b", r"\bcheck[\s-]?in\b"],
        "transcript_keywords": ["how are you", "career", "growth", "feedback", "goal"],
        "duration_range": (10, 60),
        "speaker_range": (2, 2),
    },
    "all_hands": {
        "subject_patterns": [r"\ball[\s-]?hands\b", r"\btown[\s-]?hall\b", r"\bcompany[\s-]?meeting\b"],
        "transcript_keywords": ["announce", "update", "quarter", "company", "team"],
        "duration_range": (20, 120),
        "speaker_range": (1, 100),
    },
    "brainstorm": {
        "subject_patterns": [r"\bbrainstorm\b", r"\bideation\b", r"\bworkshop\b"],
        "transcript_keywords": ["idea", "what if", "could we", "creative", "explore", "option"],
        "duration_range": (15, 120),
        "speaker_range": (2, 15),
    },
    "retrospective": {
        "subject_patterns": [r"\bretro(?:spective)?\b", r"\bpost[\s-]?mortem\b", r"\blessons[\s-]?learned\b"],
        "transcript_keywords": ["went well", "improve", "start doing", "stop doing", "action item"],
        "duration_range": (15, 90),
        "speaker_range": (2, 15),
    },
    "interview": {
        "subject_patterns": [r"\binterview\b", r"\bscreening\b", r"\bcandidate\b"],
        "transcript_keywords": ["experience", "background", "tell me about", "role", "position", "team"],
        "duration_range": (20, 90),
        "speaker_range": (2, 5),
    },
    "training": {
        "subject_patterns": [r"\btraining\b", r"\bonboarding\b", r"\btutorial\b", r"\blearn\b"],
        "transcript_keywords": ["how to", "step by step", "example", "practice", "exercise"],
        "duration_range": (15, 180),
        "speaker_range": (1, 30),
    },
    "incident": {
        "subject_patterns": [r"\bincident\b", r"\boutage\b", r"\bsev[\s-]?\d\b", r"\bemergency\b", r"\bwar[\s-]?room\b"],
        "transcript_keywords": ["down", "outage", "fix", "rollback", "root cause", "impact", "status"],
        "duration_range": (5, 180),
        "speaker_range": (2, 20),
    },
}

_COMPILED_SUBJECT_PATTERNS = {
    mtype: [re.compile(p, re.IGNORECASE) for p in info["subject_patterns"]]
    for mtype, info in _MEETING_TYPES.items()
}


@dataclass
class MeetingClassification:
    """Classification result for a meeting."""
    meeting_type: str  # primary type
    confidence: float  # 0.0 - 1.0
    scores: dict[str, float]  # all type scores
    signals: list[str]  # what contributed to the classification


def classify_meeting(
    subject: str = "",
    transcript_text: str = "",
    duration_minutes: float = 0,
    speaker_count: int = 0,
    attendee_count: int = 0,
) -> MeetingClassification:
    """Classify a meeting into a type.

    Args:
        subject: Meeting subject line.
        transcript_text: Full transcript text.
        duration_minutes: Meeting duration in minutes.
        speaker_count: Number of distinct speakers.
        attendee_count: Number of attendees (from calendar).

    Returns:
        MeetingClassification with primary type and confidence.
    """
    scores: dict[str, float] = {}
    signals: list[str] = []
    people_count = max(speaker_count, attendee_count)

    # Precompute transcript words (lowercase)
    transcript_lower = transcript_text.lower() if transcript_text else ""
    transcript_words = set(re.findall(r"[a-z]+", transcript_lower))

    for mtype, info in _MEETING_TYPES.items():
        score = 0.0

        # Subject pattern matching (strong signal, +40)
        for pattern in _COMPILED_SUBJECT_PATTERNS[mtype]:
            if pattern.search(subject):
                score += 40.0
                signals.append(f"{mtype}: subject match '{pattern.pattern}'")
                break

        # Transcript keyword matching (+3 each, max 30)
        kw_hits = 0
        for kw in info["transcript_keywords"]:
            if kw in transcript_lower:
                kw_hits += 1
        kw_score = min(kw_hits * 3, 30)
        score += kw_score
        if kw_hits > 0:
            signals.append(f"{mtype}: {kw_hits} keyword hits")

        # Duration fit (+15 if in range, +5 if close)
        dur_min, dur_max = info["duration_range"]
        if duration_minutes > 0:
            if dur_min <= duration_minutes <= dur_max:
                score += 15.0
            elif dur_min - 10 <= duration_minutes <= dur_max + 10:
                score += 5.0

        # Speaker/attendee count fit (+15 if in range)
        spk_min, spk_max = info["speaker_range"]
        if people_count > 0:
            if spk_min <= people_count <= spk_max:
                score += 15.0

        scores[mtype] = score

    # Find best match
    if not scores:
        return MeetingClassification(
            meeting_type="unknown",
            confidence=0.0,
            scores={},
            signals=[],
        )

    best_type = max(scores, key=lambda k: scores[k])
    best_score = scores[best_type]

    # Confidence: normalize to 0-1 range (100 = perfect match)
    confidence = min(1.0, best_score / 100.0)

    # If best score is too low, classify as "general"
    if best_score < 10:
        best_type = "general"
        confidence = 0.0

    return MeetingClassification(
        meeting_type=best_type,
        confidence=round(confidence, 2),
        scores={k: round(v, 1) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
        signals=signals[:10],
    )


def classify_recording(
    rec_path: Path,
    meta: dict | None = None,
) -> MeetingClassification | None:
    """Classify a recording by its type.

    Args:
        rec_path: Recording directory.
        meta: Pre-loaded metadata.

    Returns:
        MeetingClassification or None if insufficient data.
    """
    if meta is None:
        meta_path = rec_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                return None
        else:
            return None

    subject = meta.get("meeting_subject", "")
    duration = meta.get("duration_seconds", 0) / 60.0
    speaker_count = meta.get("speaker_count", 0)
    attendees = meta.get("meeting_attendees") or []

    # Load transcript if available
    transcript = ""
    txt_path = rec_path / "transcript.txt"
    if txt_path.exists():
        try:
            transcript = txt_path.read_text(encoding="utf-8")
        except Exception:
            pass

    return classify_meeting(
        subject=subject,
        transcript_text=transcript,
        duration_minutes=duration,
        speaker_count=speaker_count,
        attendee_count=len(attendees),
    )


def format_classification(cls: MeetingClassification | None) -> str:
    """Format classification as readable text."""
    if cls is None:
        return "Unable to classify meeting."

    type_labels = {
        "standup": "Daily Standup",
        "planning": "Planning Session",
        "review": "Review / Demo",
        "one_on_one": "1-on-1",
        "all_hands": "All-Hands",
        "brainstorm": "Brainstorming",
        "retrospective": "Retrospective",
        "interview": "Interview",
        "training": "Training",
        "incident": "Incident Response",
        "general": "General Meeting",
        "unknown": "Unknown",
    }

    label = type_labels.get(cls.meeting_type, cls.meeting_type.title())
    conf_bar = "\u2588" * int(cls.confidence * 10) + "\u2591" * (10 - int(cls.confidence * 10))

    lines = [
        f"  Type: {label}",
        f"  Confidence: [{conf_bar}] {cls.confidence:.0%}",
    ]

    # Show top 3 alternative types
    top = list(cls.scores.items())[:3]
    if len(top) > 1:
        alts = ", ".join(
            f"{type_labels.get(t, t)} ({s:.0f})"
            for t, s in top[1:]
        )
        lines.append(f"  Also considered: {alts}")

    return "\n".join(lines)
