"""Transcribe a recording directory with faster-whisper (local) on CUDA.

Standalone path that doesn't depend on meeting_recorder's config plumbing —
useful when Gemini API is 503'ing and we just want a transcript NOW.

Usage: python whisper_transcribe.py <recording_dir> [model_size]
"""
from __future__ import annotations
import json
import sys
import time
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("whisper_transcribe")

if len(sys.argv) < 2:
    print("Usage: python whisper_transcribe.py <recording_dir> [model_size=large-v3]")
    sys.exit(1)

rec_dir = Path(sys.argv[1])
model_size = sys.argv[2] if len(sys.argv) > 2 else "large-v3"

if not rec_dir.exists():
    print(f"Recording dir not found: {rec_dir}")
    sys.exit(1)

# Prefer app_audio (Zoom side, captures all speakers) over mic_audio (just Bobby)
audio = None
for cand in ("mixed.wav", "app_audio.wav", "mic_audio.wav"):
    p = rec_dir / cand
    if p.exists() and p.stat().st_size > 1024:
        audio = p
        break

if audio is None:
    print(f"No audio file found in {rec_dir}")
    sys.exit(1)

log.info(f"Audio: {audio} ({audio.stat().st_size / 1e6:.1f} MB)")

# Read meta for attendees
meta_path = rec_dir / "metadata.json"
attendees: list[str] = []
organizer = ""
if meta_path.exists():
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    attendees = meta.get("meeting_attendees", [])
    organizer = meta.get("meeting_organizer", "")
    log.info(f"Subject: {meta.get('meeting_subject', '?')} | organizer: {organizer} | {len(attendees)} attendees")

from faster_whisper import WhisperModel

log.info(f"Loading {model_size} on cuda (float16)...")
t0 = time.time()
try:
    model = WhisperModel(model_size, device="cuda", compute_type="float16")
    log.info(f"Model loaded in {time.time() - t0:.1f}s")
except Exception as e:
    log.warning(f"cuda/float16 load failed: {e}; falling back to int8_float32")
    model = WhisperModel(model_size, device="cuda", compute_type="int8_float32")
    log.info(f"Model loaded (fallback) in {time.time() - t0:.1f}s")

log.info(f"Transcribing {audio.name} ...")
t0 = time.time()
segments_iter, info = model.transcribe(
    str(audio),
    language="en",
    vad_filter=True,
    beam_size=5,
    word_timestamps=False,
)

segments = []
for i, seg in enumerate(segments_iter):
    segments.append({
        "id": i,
        "start": round(seg.start, 2),
        "end": round(seg.end, 2),
        "text": seg.text.strip(),
    })
    if (i + 1) % 50 == 0:
        log.info(f"  segment {i+1}, t={seg.end:.1f}s ({seg.end / (info.duration or 1) * 100:.0f}%)")

elapsed = time.time() - t0
log.info(f"Transcription complete: {len(segments)} segments in {elapsed:.1f}s (audio = {info.duration:.0f}s, {info.duration/elapsed:.1f}x realtime)")

# Outputs
out_json = rec_dir / "transcript.json"
out_txt = rec_dir / "transcript.txt"

out_json.write_text(json.dumps({
    "model": model_size,
    "duration_seconds": info.duration,
    "language": info.language,
    "attendees": attendees,
    "organizer": organizer,
    "segments": segments,
}, indent=2, ensure_ascii=False), encoding="utf-8")

# Plain text with timestamps
out_txt.write_text(
    "\n".join(f"[{s['start']:7.1f}s] {s['text']}" for s in segments),
    encoding="utf-8",
)

log.info(f"Wrote {out_json}")
log.info(f"Wrote {out_txt}")
log.info("Done.")
