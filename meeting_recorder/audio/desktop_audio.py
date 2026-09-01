"""System-wide desktop audio capture using WASAPI loopback via PyAudioWPatch."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from meeting_recorder.audio.ring_buffer import RingBuffer
from meeting_recorder.audio.resampling import resample_to_16khz_mono, NoiseGate

logger = logging.getLogger(__name__)


class DesktopAudioCapture:
    """Captures all desktop audio via WASAPI loopback (system-wide).

    Unlike AppAudioCapture (which uses ProcTap to capture a single process),
    this captures everything that comes out of the default speakers —
    guaranteeing meeting audio is recorded regardless of which subprocess
    renders it (e.g. Teams WebView2 audio PIDs).

    Trade-off: also captures notification sounds, music, etc.
    """

    def __init__(
        self,
        ring_buffer: RingBuffer,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
    ):
        self.ring_buffer = ring_buffer
        self.target_sample_rate = sample_rate
        self.target_channels = channels
        self.chunk_duration_ms = chunk_duration_ms
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._noise_gate = NoiseGate()

    def start(self) -> None:
        """Start capturing desktop audio in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Desktop audio capture already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="desktop-audio-capture",
            daemon=True,
        )
        self._thread.start()
        logger.info("Desktop audio capture started (system-wide loopback).")

    def stop(self) -> None:
        """Stop capturing audio."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Desktop audio capture thread did not terminate (zombie).")
            self._thread = None
        logger.info("Desktop audio capture stopped.")

    @staticmethod
    def _find_loopback_device(p) -> dict:
        """Find a WASAPI loopback device, with fallback for NVIDIA HDMI.

        Tries the default loopback first. If that fails (common with NVIDIA
        HDMI audio outputs), enumerates all WASAPI loopback devices and picks
        the first one that isn't an HDMI/DisplayPort output.
        """
        try:
            return p.get_default_wasapi_loopback()
        except LookupError:
            pass

        logger.warning("Default WASAPI loopback not available. Searching for alternatives...")

        # Enumerate all WASAPI loopback devices
        loopback_devices = []
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
            except Exception:
                continue
            # Loopback devices have maxInputChannels > 0 and contain "[Loopback]"
            # or are in the WASAPI host API with loopback flag
            name = info.get("name", "")
            if info.get("maxInputChannels", 0) > 0 and "[Loopback]" in name:
                loopback_devices.append(info)
                logger.info("  Found loopback device: %s (index %d)", name, i)

        if not loopback_devices:
            raise LookupError(
                "No WASAPI loopback devices found. "
                "Ensure a non-HDMI audio output device is available."
            )

        # Prefer non-NVIDIA/non-HDMI devices
        for dev in loopback_devices:
            name_lower = dev["name"].lower()
            if "nvidia" not in name_lower and "hdmi" not in name_lower:
                logger.info("Using fallback loopback device: %s", dev["name"])
                return dev

        # If all devices are HDMI, use the first one anyway
        logger.info("Only HDMI loopback devices available. Using: %s", loopback_devices[0]["name"])
        return loopback_devices[0]

    def _capture_loop(self) -> None:
        """Main capture loop using PyAudioWPatch WASAPI loopback."""
        stream = None
        p = None
        try:
            import pyaudiowpatch as pyaudio
            from meeting_recorder.audio._pyaudio_lock import pyaudio_init_lock

            with pyaudio_init_lock:
                p = pyaudio.PyAudio()
            loopback_device = self._find_loopback_device(p)

            native_rate = int(loopback_device["defaultSampleRate"])
            native_channels = max(int(loopback_device["maxInputChannels"]), 1)

            logger.info(
                "Desktop loopback device: %s (native %dHz %dch)",
                loopback_device["name"],
                native_rate,
                native_channels,
            )

            # Calculate chunk size to roughly match app audio chunk timing
            chunk_samples = int(native_rate * self.chunk_duration_ms / 1000)

            stream = p.open(
                format=pyaudio.paFloat32,
                channels=native_channels,
                rate=native_rate,
                input=True,
                input_device_index=int(loopback_device["index"]),
                frames_per_buffer=chunk_samples,
            )

            logger.info(
                "Desktop loopback stream opened (chunk: %d samples, %dms).",
                chunk_samples, self.chunk_duration_ms,
            )

            consecutive_errors = 0
            while not self._stop_event.is_set():
                try:
                    audio_data = stream.read(chunk_samples, exception_on_overflow=False)
                    consecutive_errors = 0
                except IOError as e:
                    # A dead stream ("Stream closed") raises on every read; an
                    # unbounded `continue` here once spun at full speed and
                    # wrote 164k identical warnings into a 9 GB log. Back off,
                    # and give up after ~30s of continuous failure.
                    consecutive_errors += 1
                    if consecutive_errors <= 3 or consecutive_errors % 100 == 0:
                        logger.warning(
                            "Desktop loopback read error (#%d): %s",
                            consecutive_errors, e,
                        )
                    if consecutive_errors >= 300:
                        logger.error(
                            "Desktop loopback stream failing continuously; "
                            "stopping capture thread."
                        )
                        break
                    self._stop_event.wait(0.1)
                    continue

                audio_f32 = np.frombuffer(audio_data, dtype=np.float32)

                audio_int16 = resample_to_16khz_mono(
                    audio_f32,
                    source_rate=native_rate,
                    target_rate=self.target_sample_rate,
                    source_channels=native_channels,
                )

                audio_int16 = self._noise_gate.process(audio_int16)
                self.ring_buffer.put(audio_int16.tobytes())

        except ImportError:
            logger.error(
                "PyAudioWPatch is not installed. Install with: pip install PyAudioWPatch"
            )
        except Exception:
            logger.exception("Desktop audio capture error")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except OSError as e:
                    logger.debug("Device release during desktop stream cleanup: %s", e)
                except Exception:
                    logger.warning("Unexpected error during desktop stream cleanup", exc_info=True)
            if p is not None:
                try:
                    p.terminate()
                except OSError as e:
                    logger.debug("Device release during PyAudio cleanup: %s", e)
                except Exception:
                    logger.warning("Unexpected error during PyAudio cleanup", exc_info=True)
            logger.info("Desktop audio capture thread exiting.")

    @property
    def is_process_specific(self) -> Optional[bool]:
        """Always False — desktop capture is system-wide by definition."""
        return False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
