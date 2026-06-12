"""Gemini-based meeting transcription backend.

Uploads meeting audio to the Gemini Files API and uses a single model call
to produce a timestamped, speaker-labelled transcript.  Significantly more
accurate than local Whisper for real conversational meeting audio, and
costs fractions of a cent per hour of audio with gemini-2.5-flash.

Uses the current ``google-genai`` SDK (not the deprecated ``google.generativeai``).
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from meeting_recorder.transcription.local_whisper import TranscriptSegment

logger = logging.getLogger(__name__)

# Server-suggested retry delays embedded in Gemini error payloads, e.g.
# "'retryDelay': '7s'" (google.rpc.RetryInfo as dict/JSON) or the gRPC
# textproto form "retry_delay { seconds: 7 }".
_RETRY_DELAY_PATTERNS = (
    re.compile(r"retry_?delay[\"']?\s*:\s*[\"']?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE),
    re.compile(r"retry_?delay\s*\{\s*seconds:\s*(\d+)", re.IGNORECASE),
)

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


_DICTATION_PROMPT_TEMPLATE = """\
This is a short solo dictation recording (30 seconds to 3 minutes).
Transcribe it verbatim, infer a topic slug, and infer a project.

Return ONLY a single JSON object, no preamble, no markdown fences, with these keys:
- "transcript": string. Verbatim markdown-formatted transcript of everything spoken.
  Break into paragraphs at natural pauses/topic shifts. Do NOT add commentary or
  stage directions. Do NOT add speaker labels. Preserve filler words only if they
  carry meaning.
- "slug": string. A 3-word kebab-case topic summary (lowercase, hyphens, no punctuation).
  Example: "fig4-simpsons-paradox" or "grant-deadline-notes".
- "project": string. Must be exactly one of: {project_choices}. Pick the single
  best match from the transcript content.

Return the JSON object now:
"""


@dataclass
class DictationResult:
    """Structured output from a dictation transcription call."""
    transcript: str
    slug: str
    project: str
    model: str


def _slugify(text: str) -> str:
    """Normalize text to 3-word kebab-case."""
    cleaned = re.sub(r"[^a-z0-9\s-]", "", text.lower().strip())
    words = [w for w in re.split(r"[\s_-]+", cleaned) if w]
    if not words:
        return "untitled"
    return "-".join(words[:3])


def parse_dictation_response(
    raw: str,
    project_choices: list[str],
    default_project: str,
    model: str,
) -> DictationResult:
    """Parse Gemini's JSON response for a dictation clip.

    Tolerates markdown code fences around the JSON, extra commentary, and
    missing/invalid fields — falls back to sensible defaults rather than
    raising so a voice memo is never lost because of a malformed response.
    """
    text = raw.strip()
    # Strip ```json ... ``` fences if Gemini added them despite being told not to
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    transcript = raw.strip()  # fallback: whole response as transcript
    slug = ""
    project = ""

    try:
        # Find the first {...} block if there's extra prose around it
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            obj = json.loads(match.group(0))
            transcript = str(obj.get("transcript", transcript)).strip()
            slug = str(obj.get("slug", "")).strip()
            project = str(obj.get("project", "")).strip()
    except (json.JSONDecodeError, ValueError):
        logger.warning("Dictation response was not valid JSON; using raw text as transcript")

    slug = _slugify(slug) if slug else _slugify(transcript[:60])

    allowed = set(project_choices) | {default_project}
    if project not in allowed:
        project = default_project

    return DictationResult(
        transcript=transcript,
        slug=slug,
        project=project,
        model=model,
    )


class GeminiTranscriber:
    """Transcription backend using Google Gemini audio understanding.

    Uploads the mixed meeting WAV to the Gemini Files API, waits for it to
    become active, then requests a verbatim timestamped transcript.

    The transcript is also saved as ``transcript_raw.txt`` alongside the WAV
    so you always have the unprocessed Gemini output for reference.
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    # Files API processing can take minutes for long recordings; 2 min was
    # not enough.  Poll fast at first, then back off.
    FILE_POLL_TIMEOUT_SECONDS = 600.0
    FILE_POLL_FAST_INTERVAL = 2.0   # for the first minute
    FILE_POLL_SLOW_INTERVAL = 5.0   # after the first minute
    # Free-tier 429s are routine; retry generously but keep the worst-case
    # total wait bounded.
    MAX_TOTAL_RETRY_WAIT = 120.0

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

        # Poll until the file has been processed server-side (usually < 30 s,
        # but long recordings can take several minutes)
        uploaded = self._wait_for_file_active(client, uploaded)

        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(
                f"Gemini file processing did not complete (state={uploaded.state.name}). "
                "Try again or check the Gemini API status."
            )

        # Transcribe with retries for transient API errors
        raw_text = self._transcribe_with_retry(client, uploaded)

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

        return self._parse(raw_text, audio_duration=self._wav_duration(audio_path))

    def transcribe_dictation(
        self,
        audio_path: Path,
        project_choices: list[str],
        default_project: str = "general",
    ) -> DictationResult:
        """Transcribe a short solo dictation clip and return structured output.

        Uses the same upload / FLAC compression / retry machinery as
        ``transcribe()`` but asks Gemini for a JSON blob containing the
        verbatim transcript, a kebab-case slug, and an inferred project.
        """
        from google import genai
        from google.genai import types

        if not project_choices:
            project_choices = [default_project]
        # Ensure default_project is always an allowed choice
        choices = list(project_choices)
        if default_project not in choices:
            choices.append(default_project)

        client = genai.Client(api_key=self.api_key)

        upload_path, mime_type, flac_temp = self._compress_to_flac(audio_path)

        size_mb = upload_path.stat().st_size / 1_000_000
        logger.info(
            "Uploading dictation %s (%.1f MB) to Gemini Files API…",
            upload_path.name, size_mb,
        )

        try:
            uploaded = client.files.upload(
                file=str(upload_path),
                config=types.UploadFileConfig(mime_type=mime_type),
            )
        finally:
            if flac_temp is not None and flac_temp.exists():
                flac_temp.unlink()

        uploaded = self._wait_for_file_active(client, uploaded)

        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(
                f"Gemini file processing did not complete (state={uploaded.state.name})."
            )

        prompt = _DICTATION_PROMPT_TEMPLATE.format(
            project_choices=", ".join(f'"{p}"' for p in choices)
        )
        raw_text = self._transcribe_with_retry(client, uploaded, prompt=prompt)

        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            logger.debug("Could not delete uploaded Gemini file (non-fatal)")

        return parse_dictation_response(raw_text, choices, default_project, self.model)

    def _wait_for_file_active(self, client, uploaded):
        """Poll the Files API until *uploaded* leaves the PROCESSING state.

        Polls every ``FILE_POLL_FAST_INTERVAL`` seconds for the first minute,
        then every ``FILE_POLL_SLOW_INTERVAL`` seconds, up to
        ``FILE_POLL_TIMEOUT_SECONDS`` total.  Returns the final file object
        (which may still be in PROCESSING if the timeout was reached — the
        caller checks for ACTIVE).
        """
        waited = 0.0
        while (
            uploaded.state.name == "PROCESSING"
            and waited < self.FILE_POLL_TIMEOUT_SECONDS
        ):
            interval = (
                self.FILE_POLL_FAST_INTERVAL
                if waited < 60.0
                else self.FILE_POLL_SLOW_INTERVAL
            )
            time.sleep(interval)
            waited += interval
            uploaded = client.files.get(name=uploaded.name)
        return uploaded

    @staticmethod
    def _server_retry_delay(error: Exception) -> float | None:
        """Extract a server-suggested retry delay (seconds) from a Gemini error."""
        text = str(error)
        for pattern in _RETRY_DELAY_PATTERNS:
            m = pattern.search(text)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    continue
        return None

    def _transcribe_with_retry(
        self, client, uploaded, max_retries: int = 5, prompt: str | None = None,
    ):
        """Call Gemini generate_content with retries for transient errors.

        Retries on rate-limit (429 RESOURCE_EXHAUSTED — routine on the free
        tier), server errors (5xx), and network issues, with exponential
        backoff plus jitter (~4/8/16/32 s between attempts).  Honors a
        server-suggested retry delay when the error payload carries one.
        Total wait across all attempts is bounded by ``MAX_TOTAL_RETRY_WAIT``.
        Raises the final error if all attempts fail.
        """
        last_error = None
        contents_prompt = prompt if prompt is not None else _TRANSCRIPTION_PROMPT
        total_waited = 0.0
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    "Transcribing with %s (attempt %d/%d)…",
                    self.model, attempt, max_retries,
                )
                response = client.models.generate_content(
                    model=self.model,
                    contents=[uploaded, contents_prompt],
                )
                return response.text
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                # Retry on rate-limit, server errors, and network issues
                retryable = any(k in err_str for k in (
                    "429", "500", "502", "503",
                    "resource exhausted", "resource_exhausted",
                    "rate limit", "timeout", "connection",
                    "deadline exceeded", "unavailable", "overloaded",
                ))
                if not retryable or attempt >= max_retries:
                    logger.error(
                        "Gemini transcription failed (attempt %d/%d): %s",
                        attempt, max_retries, e,
                    )
                    raise
                wait = 2.0 ** (attempt + 1)  # 4s, 8s, 16s, 32s
                server_delay = self._server_retry_delay(e)
                if server_delay is not None:
                    wait = max(wait, server_delay)
                # Jitter avoids synchronized retries hammering the API
                wait += random.uniform(0, wait * 0.25)
                wait = min(wait, self.MAX_TOTAL_RETRY_WAIT - total_waited)
                if wait <= 0:
                    logger.error(
                        "Gemini retry budget exhausted after %.0fs; giving up: %s",
                        total_waited, e,
                    )
                    raise
                total_waited += wait
                logger.warning(
                    "Gemini API error (attempt %d/%d), retrying in %.0fs: %s",
                    attempt, max_retries, wait, e,
                )
                time.sleep(wait)

        raise last_error  # unreachable, but satisfies type checkers

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

    @staticmethod
    def _wav_duration(audio_path: Path) -> float | None:
        """Read the duration in seconds from a WAV file header, or None."""
        try:
            with wave.open(str(audio_path), "rb") as wf:
                rate = wf.getframerate()
                if rate <= 0:
                    return None
                return wf.getnframes() / float(rate)
        except Exception:
            logger.debug("Could not read WAV duration: %s", audio_path, exc_info=True)
            return None

    def _parse(
        self, raw: str, audio_duration: float | None = None,
    ) -> list[TranscriptSegment]:
        """Parse Gemini's ``[MM:SS] Speaker: text`` output into TranscriptSegments.

        Lines that match the strict ``[MM:SS] Speaker: text`` format become
        first-class segments with timestamps and speaker labels.  Lines that
        do NOT match (narrative, headers, descriptions, off-format text) are
        still preserved — they're appended to the previous segment's text so
        nothing is dropped.  The verbatim raw output is also always saved
        to ``transcript_raw.txt`` alongside this structured output.

        When *audio_duration* is known, segment ends are clamped to it —
        in particular the final segment, whose end would otherwise be a
        fabricated ``start + 60`` placeholder.
        """
        # Matches both [MM:SS] and [H:MM:SS]
        pattern = re.compile(
            r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s+(.+?):\s+(.+)$"
        )

        segments: list[TranscriptSegment] = []
        unmatched_prefix: list[str] = []  # lines before first segment

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if not m:
                # Preserve the line instead of dropping it:
                # - if we already have a segment, append as context
                # - else save for the first segment to pick up
                if segments:
                    segments[-1].text = segments[-1].text + "\n" + line
                else:
                    unmatched_prefix.append(line)
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

            seg_text = text.strip()
            # Prepend any pre-segment narrative to the first segment
            if unmatched_prefix and not segments:
                seg_text = "\n".join(unmatched_prefix) + "\n" + seg_text
                unmatched_prefix = []

            segments.append(TranscriptSegment(
                start=float(start),
                end=float(start + 60),  # placeholder — overwritten by next segment
                text=seg_text,
                speaker=speaker.strip(),
            ))

        # If the whole transcript had no matching lines (edge case), emit
        # one catch-all segment so nothing is dropped.
        if not segments and unmatched_prefix:
            segments.append(TranscriptSegment(
                start=0.0,
                end=audio_duration if audio_duration and audio_duration > 0 else 0.0,
                text="\n".join(unmatched_prefix),
                speaker="",
            ))

        # Clamp ends to the real audio length (fixes the last segment's
        # start+60 placeholder) and guarantee end >= start everywhere.
        if audio_duration is not None and audio_duration > 0:
            for seg in segments:
                if seg.end > audio_duration:
                    seg.end = audio_duration
        for seg in segments:
            if seg.end < seg.start:
                seg.end = seg.start

        logger.info("Parsed %d segments from Gemini transcript", len(segments))
        return segments
