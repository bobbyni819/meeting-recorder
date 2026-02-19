"""Gemini-based meeting transcription backend.

Uploads meeting audio to the Gemini Files API and uses a single model call
to produce a timestamped, speaker-labelled transcript.  Significantly more
accurate than local Whisper for real conversational meeting audio, and
costs fractions of a cent per hour of audio with gemini-2.5-flash.

Uses the current ``google-genai`` SDK (not the deprecated ``google.generativeai``).
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)

_TRANSCRIPTION_PROMPT = """\
Transcribe this meeting audio recording completely and accurately.

Format each speaker turn on its own line as:
[MM:SS] Speaker Name: their words here

Rules:
- Use [MM:SS] timestamps (minutes:seconds from the start of the recording)
- If speakers introduce themselves or are addressed by name, use their real name; \
otherwise use Speaker 1, Speaker 2, etc. — and keep labels consistent throughout
- Transcribe all speech including brief acknowledgments (mm-hmm, yeah, ok, sure)
- Do not add descriptions, commentary, or stage directions
- Do not skip any speech

Begin the transcript immediately with no preamble:
"""


class GeminiTranscriber:
    """Transcription backend using Google Gemini audio understanding.

    Uploads the mixed meeting WAV to the Gemini Files API, waits for it to
    become active, then requests a verbatim timestamped transcript.

    The transcript is also saved as ``transcript_raw.txt`` alongside the WAV
    so you always have the unprocessed Gemini output for reference.
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        api_key: str,
        language: str = "en",
        model: str = "",
    ):
        if not api_key:
            raise ValueError("Gemini API key is required (transcription.gemini_api_key).")
        self.api_key = api_key
        self.language = language
        self.model = model or self.DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Public API — matches the LocalWhisperTranscriber interface
    # ------------------------------------------------------------------

    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        """Upload *audio_path* to Gemini and return parsed TranscriptSegments.

        Also writes ``transcript_raw.txt`` into the same directory as the
        audio file so the verbatim Gemini output is preserved.

        If possible, compresses to FLAC before upload to reduce transfer size.
        """
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        # Try to compress to FLAC for faster upload (WAV -> FLAC is ~3-5x smaller)
        upload_path, mime_type, flac_temp = self._compress_to_flac(audio_path)

        size_mb = upload_path.stat().st_size / 1_000_000
        logger.info(
            "Uploading %s (%.1f MB) to Gemini Files API…", upload_path.name, size_mb
        )

        try:
            uploaded = client.files.upload(
                file=str(upload_path),
                config=types.UploadFileConfig(mime_type=mime_type),
            )
        finally:
            # Clean up temporary FLAC file
            if flac_temp is not None and flac_temp.exists():
                flac_temp.unlink()

        # Poll until the file has been processed server-side (usually < 30 s)
        for _ in range(60):  # up to 2 minutes
            if uploaded.state.name != "PROCESSING":
                break
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)

        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(
                f"Gemini file processing did not complete (state={uploaded.state.name}). "
                "Try again or check the Gemini API status."
            )

        logger.info("File active. Transcribing with %s…", self.model)
        response = client.models.generate_content(
            model=self.model,
            contents=[uploaded, _TRANSCRIPTION_PROMPT],
        )
        raw_text = response.text

        logger.info("Transcript received: %d chars", len(raw_text))

        # Persist the raw text for reference / manual editing
        raw_path = audio_path.parent / "transcript_raw.txt"
        raw_path.write_text(raw_text, encoding="utf-8")
        logger.info("Raw Gemini transcript saved: %s", raw_path.name)

        # Best-effort cleanup of the uploaded file
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            logger.debug("Could not delete uploaded Gemini file (non-fatal)")

        return self._parse(raw_text)

    # ------------------------------------------------------------------
    # Compression
    # ------------------------------------------------------------------

    @staticmethod
    def _compress_to_flac(audio_path: Path) -> tuple[Path, str, Path | None]:
        """Compress WAV to FLAC for smaller uploads.

        Returns:
            (upload_path, mime_type, temp_flac_or_none).  If compression
            fails, returns the original WAV path with no temp file.
        """
        flac_path = audio_path.with_suffix(".flac")

        # Try soundfile first
        try:
            import soundfile as sf
            data, sr = sf.read(str(audio_path))
            sf.write(str(flac_path), data, sr, format="FLAC")
            orig_mb = audio_path.stat().st_size / 1_000_000
            flac_mb = flac_path.stat().st_size / 1_000_000
            logger.info(
                "Compressed to FLAC: %.1f MB -> %.1f MB (%.0f%% reduction)",
                orig_mb, flac_mb, (1 - flac_mb / orig_mb) * 100 if orig_mb > 0 else 0,
            )
            return flac_path, "audio/flac", flac_path
        except ImportError:
            pass
        except Exception:
            logger.debug("soundfile FLAC compression failed", exc_info=True)

        # Try ffmpeg fallback
        try:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(audio_path), str(flac_path)],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0 and flac_path.exists():
                orig_mb = audio_path.stat().st_size / 1_000_000
                flac_mb = flac_path.stat().st_size / 1_000_000
                logger.info(
                    "Compressed to FLAC via ffmpeg: %.1f MB -> %.1f MB",
                    orig_mb, flac_mb,
                )
                return flac_path, "audio/flac", flac_path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        except Exception:
            logger.debug("ffmpeg FLAC compression failed", exc_info=True)

        # Graceful degradation: upload original WAV
        logger.info("FLAC compression unavailable; uploading WAV directly")
        return audio_path, "audio/wav", None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, raw: str) -> list[TranscriptSegment]:
        """Parse Gemini's ``[MM:SS] Speaker: text`` output into TranscriptSegments."""
        # Matches both [MM:SS] and [H:MM:SS]
        pattern = re.compile(
            r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s+(.+?):\s+(.+)$"
        )

        segments: list[TranscriptSegment] = []

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if not m:
                continue

            g1, g2, g3 = m.group(1), m.group(2), m.group(3)
            speaker, text = m.group(4), m.group(5)

            if g3:  # [H:MM:SS]
                start = int(g1) * 3600 + int(g2) * 60 + int(g3)
            else:   # [MM:SS]
                start = int(g1) * 60 + int(g2)

            # Close out the previous segment now that we know where it ends
            if segments:
                segments[-1].end = float(start)

            segments.append(TranscriptSegment(
                start=float(start),
                end=float(start + 60),  # placeholder — overwritten by next segment
                text=text.strip(),
                speaker=speaker.strip(),
            ))

        logger.info("Parsed %d segments from Gemini transcript", len(segments))
        return segments
