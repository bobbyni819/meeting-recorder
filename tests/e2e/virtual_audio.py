"""VB-Cable virtual audio device utilities for E2E testing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def find_vbcable_device() -> Optional[int]:
    """Find VB-Cable virtual audio input device index.

    Returns device index or None if not found.
    """
    try:
        import sounddevice as sd
    except ImportError:
        logger.debug("sounddevice not installed")
        return None

    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        name = dev["name"].lower()
        if "cable input" in name and dev["max_output_channels"] > 0:
            logger.info("Found VB-Cable: device %d (%s)", i, dev["name"])
            return i

    logger.debug("VB-Cable not found among %d devices", len(devices))
    return None


def generate_test_speech(
    duration: float = 10.0,
    sample_rate: int = 44100,
) -> np.ndarray:
    """Generate synthetic speech-like audio for testing.

    Creates multi-tone audio with amplitude modulation that resembles
    speech patterns. No external audio files needed.

    Returns float32 numpy array normalized to [-1, 1].
    """
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    # Fundamental frequencies (speech-like range)
    signal = np.zeros_like(t)
    for freq in [150, 250, 400, 800, 1200]:
        signal += np.sin(2 * np.pi * freq * t) / 5

    # Amplitude modulation (syllable-like rhythm, 3-5 Hz)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t)
    # Add slower variation (phrase-like, ~0.5 Hz)
    envelope *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t)

    signal *= envelope

    # Normalize to [-0.8, 0.8] to avoid clipping
    peak = np.max(np.abs(signal))
    if peak > 0:
        signal = signal / peak * 0.8

    return signal.astype(np.float32)


def generate_tts_speech(
    text: str = "Hello, this is a test of the meeting recorder audio capture system. "
    "The quick brown fox jumps over the lazy dog. "
    "Testing one two three four five.",
    duration: float = 10.0,
    sample_rate: int = 44100,
) -> np.ndarray:
    """Generate realistic speech audio using Windows TTS (pyttsx3/SAPI).

    Falls back to synthetic tones if pyttsx3 is unavailable.

    Returns float32 numpy array normalized to [-1, 1].
    """
    try:
        import wave

        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 150)  # moderate speaking speed

        temp_path = Path(__file__).parent / "_tts_temp.wav"
        try:
            engine.save_to_file(text, str(temp_path))
            engine.runAndWait()

            with wave.open(str(temp_path), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
                tts_rate = wf.getframerate()
                n_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()

            # Convert to float32 mono
            if sampwidth == 2:
                audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif sampwidth == 4:
                audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                raise ValueError(f"Unsupported sample width: {sampwidth}")

            if n_channels > 1:
                audio = audio.reshape(-1, n_channels).mean(axis=1)

            # Resample to target rate if needed
            if tts_rate != sample_rate:
                target_len = int(len(audio) * sample_rate / tts_rate)
                audio = np.interp(
                    np.linspace(0, len(audio), target_len, endpoint=False),
                    np.arange(len(audio)),
                    audio,
                ).astype(np.float32)

            # Tile or trim to match requested duration
            target_samples = int(sample_rate * duration)
            if len(audio) < target_samples:
                repeats = (target_samples // len(audio)) + 1
                # Add 0.3s silence between repeats
                silence = np.zeros(int(sample_rate * 0.3), dtype=np.float32)
                segments = []
                for _ in range(repeats):
                    segments.append(audio)
                    segments.append(silence)
                audio = np.concatenate(segments)
            audio = audio[:target_samples]

            # Normalize
            peak = np.max(np.abs(audio))
            if peak > 0:
                audio = audio / peak * 0.8

            logger.info("Generated %.1fs of TTS speech at %dHz", duration, sample_rate)
            return audio
        finally:
            if temp_path.exists():
                temp_path.unlink()

    except Exception as exc:
        logger.warning("pyttsx3 TTS failed (%s), falling back to synthetic tones", exc)
        return generate_test_speech(duration=duration, sample_rate=sample_rate)


def load_wav_file(path: str | Path, sample_rate: int = 44100) -> np.ndarray:
    """Load a WAV file and resample to the target rate.

    Returns float32 numpy array normalized to [-1, 1].
    """
    import soundfile as sf

    audio, file_rate = sf.read(str(path), dtype="float32", always_2d=True)
    # Convert to mono
    audio = audio.mean(axis=1)

    # Resample if needed
    if file_rate != sample_rate:
        target_len = int(len(audio) * sample_rate / file_rate)
        audio = np.interp(
            np.linspace(0, len(audio), target_len, endpoint=False),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)

    # Normalize
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.8

    return audio


class VBCablePlayer:
    """Plays audio through VB-Cable virtual audio device."""

    def __init__(self, device_index: Optional[int] = None):
        self._device_index = device_index or find_vbcable_device()
        if self._device_index is None:
            raise RuntimeError(
                "VB-Cable not found. Install VB-Cable: https://vb-audio.com/Cable/"
            )

    def play_blocking(
        self,
        audio: np.ndarray,
        sample_rate: int = 44100,
        loop: bool = False,
    ) -> None:
        """Play audio through VB-Cable and block until complete.

        If *loop* is True, repeats the audio until interrupted (Ctrl+C).
        """
        import sounddevice as sd

        clip_duration = len(audio) / sample_rate
        logger.info(
            "Playing %.1fs of audio through VB-Cable (device %d)%s",
            clip_duration,
            self._device_index,
            " [looping]" if loop else "",
        )

        if not loop:
            sd.play(audio, samplerate=sample_rate, device=self._device_index)
            sd.wait()
        else:
            try:
                while True:
                    sd.play(audio, samplerate=sample_rate, device=self._device_index)
                    sd.wait()
            except KeyboardInterrupt:
                sd.stop()

        logger.info("Playback complete")

    def play_test_speech(self, duration: float = 10.0) -> None:
        """Generate and play test speech audio."""
        audio = generate_test_speech(duration=duration)
        self.play_blocking(audio)
