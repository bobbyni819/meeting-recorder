"""Minimal mic-only WAV recorder for dictation mode.

Intentionally independent of ``CaptureManager`` — no mute sync, no VAD,
no screen capture, no ring buffer.  A dictation clip is short and solo,
so we just pipe the mic straight into a WAV file at 16kHz mono int16
(what Gemini prefers).
"""

from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from meeting_recorder.audio.resampling import resample_to_16khz_mono

logger = logging.getLogger(__name__)

_CHUNK_SAMPLES_16K = 1024  # ~64 ms of 16kHz mono audio per ring write


class DictationRecorder:
    """Records mic audio straight to a 16kHz mono int16 WAV file.

    Usage:
        rec = DictationRecorder(output_path=Path("clip.wav"))
        rec.start()
        ...
        duration = rec.stop()   # seconds written
    """

    def __init__(
        self,
        output_path: Path,
        device_index: Optional[int] = None,
        target_sample_rate: int = 16000,
    ):
        self.output_path = Path(output_path)
        self.device_index = device_index
        self.target_sample_rate = target_sample_rate
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._samples_written = 0
        self._error: Optional[str] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Dictation recorder already running.")
            return
        self._stop_event.clear()
        self._samples_written = 0
        self._error = None
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="dictation-recorder",
            daemon=True,
        )
        self._thread.start()
        logger.info("Dictation recording started → %s", self.output_path)

    def stop(self) -> float:
        """Stop recording and return duration in seconds."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.error("Dictation recorder thread did not terminate")
            self._thread = None
        duration = self._samples_written / self.target_sample_rate
        logger.info(
            "Dictation recording stopped: %.1fs written → %s",
            duration, self.output_path,
        )
        return duration

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _capture_loop(self) -> None:
        stream = None
        p = None
        wav = None
        try:
            import pyaudiowpatch as pyaudio
            from meeting_recorder.audio._pyaudio_lock import pyaudio_init_lock

            with pyaudio_init_lock:
                p = pyaudio.PyAudio()

            if self.device_index is not None:
                info = p.get_device_info_by_index(self.device_index)
            else:
                info = p.get_default_input_device_info()

            native_rate = int(info["defaultSampleRate"])
            native_channels = min(int(info["maxInputChannels"]), 2)
            logger.info(
                "Dictation mic: %s (native %dHz %dch)",
                info["name"], native_rate, native_channels,
            )

            native_chunk = int(_CHUNK_SAMPLES_16K * native_rate / self.target_sample_rate)

            stream = p.open(
                format=pyaudio.paInt16,
                channels=native_channels,
                rate=native_rate,
                input=True,
                input_device_index=int(info["index"]),
                frames_per_buffer=native_chunk,
            )

            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            wav = wave.open(str(self.output_path), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.target_sample_rate)

            while not self._stop_event.is_set():
                try:
                    data = stream.read(native_chunk, exception_on_overflow=False)
                except IOError as e:
                    logger.warning("Dictation mic read error: %s", e)
                    continue

                raw = np.frombuffer(data, dtype=np.int16)
                mono16 = resample_to_16khz_mono(
                    raw,
                    source_rate=native_rate,
                    target_rate=self.target_sample_rate,
                    source_channels=native_channels,
                    target_length=_CHUNK_SAMPLES_16K,
                )
                wav.writeframes(mono16.tobytes())
                self._samples_written += len(mono16)

        except ImportError:
            self._error = "pyaudiowpatch not installed"
            logger.error(self._error)
        except Exception as e:
            self._error = str(e)
            logger.exception("Dictation capture failed")
        finally:
            if wav is not None:
                try:
                    wav.close()
                except Exception:
                    pass
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
            logger.info("Dictation capture thread exiting.")
