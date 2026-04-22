"""Dictation-mode CLI: hotkey loop that toggles recording."""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from meeting_recorder.config import Config
from meeting_recorder.dictation.pipeline import finalize_recording
from meeting_recorder.dictation.recorder import DictationRecorder

logger = logging.getLogger(__name__)


class _DictationSession:
    """Serialises start/stop over one hotkey; runs finalize off the hot path."""

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self._recorder: DictationRecorder | None = None
        self._started_at: datetime | None = None
        self._temp_audio: Path | None = None

    def toggle(self) -> None:
        with self._lock:
            if self._recorder is None:
                self._start_locked()
            else:
                self._stop_locked()

    def _start_locked(self) -> None:
        self._started_at = datetime.now()
        fd, tmp = tempfile.mkstemp(prefix="dictation-", suffix=".wav")
        import os
        os.close(fd)
        self._temp_audio = Path(tmp)
        self._recorder = DictationRecorder(output_path=self._temp_audio)
        self._recorder.start()
        print(f"● Recording started at {self._started_at.strftime('%H:%M:%S')}…")

    def _stop_locked(self) -> None:
        rec = self._recorder
        started = self._started_at
        temp = self._temp_audio
        self._recorder = None
        self._started_at = None
        self._temp_audio = None
        if rec is None or started is None or temp is None:
            return

        duration = rec.stop()
        print(f"■ Stopped after {duration:.1f}s — transcribing…")

        def _finalize():
            try:
                outcome = finalize_recording(
                    temp_audio=temp,
                    config=self.config,
                    recorded_at=started,
                    duration_seconds=duration,
                )
                if outcome.transcript_path is not None:
                    print(f"✓ Saved: {outcome.transcript_path}")
                else:
                    print(f"✗ Transcription failed; audio kept at {outcome.audio_path}")
                    print(f"  Error: {outcome.error_path}")
            except Exception:
                logger.exception("Dictation finalize failed unexpectedly")
                print("✗ Finalize crashed — see log")

        threading.Thread(target=_finalize, name="dictation-finalize", daemon=True).start()


def run(config: Config) -> int:
    """Blocking hotkey loop. Returns process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not config.transcription.gemini_api_key:
        print("ERROR: transcription.gemini_api_key is not set in "
              "~/.meeting_recorder/secrets.toml")
        return 1

    hotkey = config.dictation.hotkey
    drive_root = Path(config.dictation.drive_root).expanduser()
    print(f"Dictation mode ready. Hotkey: {hotkey}")
    print(f"Output: {drive_root / 'voice-memos'}/YYYY-MM-DD/")
    print("Press the hotkey once to start, again to stop. Ctrl+C to quit.\n")

    session = _DictationSession(config)

    try:
        import keyboard
    except ImportError:
        print("ERROR: 'keyboard' package not installed.")
        return 1

    try:
        keyboard.add_hotkey(hotkey, session.toggle)
    except Exception as e:
        print(f"ERROR: failed to register hotkey '{hotkey}': {e}")
        return 1

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nExiting dictation mode…")
        # If a recording is in progress, finalise it on the way out
        with session._lock:
            if session._recorder is not None:
                session._stop_locked()
        # Let finalize thread print its result before we return
        time.sleep(0.5)
        return 0
    finally:
        try:
            keyboard.remove_hotkey(hotkey)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m meeting_recorder dictate``."""
    config = Config.load()
    return run(config)
