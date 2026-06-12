"""Retry Gemini transcription for a specific recording directory.

Usage: python scripts/retry_transcribe.py <recording_dir>

Loads the user's config + secrets, instantiates the TranscriptionPipeline,
and runs it against the audio files in <recording_dir>. Writes the resulting
transcript.json + transcript.txt + transcript.srt into the same dir.
"""
from __future__ import annotations
import json
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("retry_transcribe")

if len(sys.argv) < 2:
    print("Usage: python retry_transcribe.py <recording_dir>")
    sys.exit(1)

rec_dir = Path(sys.argv[1])
if not rec_dir.exists():
    print(f"Recording dir not found: {rec_dir}")
    sys.exit(1)

from meeting_recorder.config import Config
from meeting_recorder.transcription.pipeline import TranscriptionPipeline

config = Config.load()

# Load metadata to get attendees + organizer
metadata_path = rec_dir / "metadata.json"
if metadata_path.exists():
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    attendees = meta.get("meeting_attendees", [])
    organizer = meta.get("meeting_organizer", "")
    log.info(f"Subject: {meta.get('meeting_subject', '')}")
    log.info(f"Attendees: {len(attendees)}, organizer: {organizer}")
else:
    attendees = []
    organizer = ""

pipeline = TranscriptionPipeline(config)
log.info(f"Backend: {config.transcription.backend}, model: {config.transcription.gemini_model}")
log.info(f"Starting transcription of {rec_dir}")

segments = pipeline.process(rec_dir, attendees=attendees, organizer=organizer)

log.info(f"Got {len(segments)} segments")

# Write outputs
out_json = rec_dir / "transcript.json"
out_txt = rec_dir / "transcript.txt"
out_srt = rec_dir / "transcript.srt"

# JSON
json_segs = [
    {
        "start": s.start,
        "end": s.end,
        "speaker": getattr(s, "speaker", ""),
        "text": s.text,
    }
    for s in segments
]
out_json.write_text(json.dumps(json_segs, indent=2, ensure_ascii=False), encoding="utf-8")
log.info(f"Wrote {out_json}")

# TXT
out_txt.write_text(
    "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {getattr(s, 'speaker', '')}: {s.text}" for s in segments),
    encoding="utf-8",
)
log.info(f"Wrote {out_txt}")

# SRT
def fmt_srt(t):
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t); ms = int((t - s) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

srt_lines = []
for i, s in enumerate(segments, 1):
    spk = getattr(s, "speaker", "")
    srt_lines.append(f"{i}\n{fmt_srt(s.start)} --> {fmt_srt(s.end)}\n{spk}: {s.text}\n")
out_srt.write_text("\n".join(srt_lines), encoding="utf-8")
log.info(f"Wrote {out_srt}")

log.info("Done.")
