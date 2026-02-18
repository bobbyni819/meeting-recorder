"""Self-service E2E recording test.

Run this script while in an active Zoom/Teams meeting to test the full pipeline:
  python -m tests.e2e.run_e2e_test https://zoom.us/j/YOUR_MEETING_ID

What it does:
  1. Generates a test audio file (speech-like tones)
  2. Launches a browser bot that joins your meeting with that audio as its mic
  3. Starts recording via CaptureManager against the meeting app
  4. Waits for audio to flow, then stops and analyzes results
  5. Prints a clear pass/fail report

You'll need to ADMIT the bot from the waiting room if your meeting has one.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import psutil

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("e2e_test")

AUDIO_FILE = Path(__file__).parent.parent.parent / "test_speech.wav"


def generate_test_audio(path: Path) -> None:
    """Generate a loud multi-tone WAV file for the bot to send."""
    sr = 48000
    duration = 30.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    signal = np.zeros_like(t)
    for freq in [200, 350, 500, 900, 1300]:
        signal += np.sin(2 * np.pi * freq * t) / 5

    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)
    envelope *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.4 * t)
    signal *= envelope

    peak = np.max(np.abs(signal))
    samples = (signal / peak * 0.7 * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())
    logger.info("Generated test audio: %s (%.0fs)", path.name, duration)


def find_zoom_meeting_pid() -> int | None:
    """Find the Zoom process that owns the 'Zoom Meeting' window."""
    user32 = ctypes.windll.user32
    zoom_pids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] and proc.info["name"].lower() == "zoom.exe":
            zoom_pids.add(proc.info["pid"])

    meeting_pid = None

    def callback(hwnd, _):
        nonlocal meeting_pid
        if user32.IsWindowVisible(hwnd):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in zoom_pids:
                length = user32.GetWindowTextLengthW(hwnd) + 1
                buf = ctypes.create_unicode_buffer(length)
                user32.GetWindowTextW(hwnd, buf, length)
                if "Zoom Meeting" in buf.value:
                    meeting_pid = pid.value
        return True

    enum_func = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(enum_func(callback), 0)
    return meeting_pid


def analyze_wav(path: Path) -> dict:
    """Analyze a WAV file and return stats."""
    if not path.exists():
        return {"exists": False}
    with wave.open(str(path), "rb") as wf:
        n_frames = wf.getnframes()
        duration = n_frames / wf.getframerate()
        data = wf.readframes(n_frames)
    if len(data) < 2:
        return {"exists": True, "duration": 0, "max_amp": 0, "active_pct": 0}
    samples = struct.unpack(f"<{len(data) // 2}h", data)
    max_amp = max(abs(s) for s in samples)
    non_zero = sum(1 for s in samples if abs(s) > 50)
    pct = non_zero / len(samples) * 100
    return {
        "exists": True,
        "duration": duration,
        "max_amp": max_amp,
        "active_pct": pct,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m tests.e2e.run_e2e_test <meeting-url>")
        print("Example: python -m tests.e2e.run_e2e_test https://zoom.us/j/1234567890")
        return 1

    meeting_url = sys.argv[1]
    wait_seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 45

    print()
    print("=" * 60)
    print("  E2E Recording Pipeline Test")
    print("=" * 60)

    # 1. Find meeting process
    meeting_pid = find_zoom_meeting_pid()
    if not meeting_pid:
        # Fall back to process finder
        from meeting_recorder.audio.process_finder import find_primary_meeting_process

        process = find_primary_meeting_process()
        if not process:
            print("\n[FAIL] No meeting app found. Start a Zoom/Teams meeting first.")
            return 1
        meeting_pid = process.pid
        logger.info("Using process finder PID: %d", meeting_pid)
    else:
        logger.info("Found Zoom Meeting window PID: %d", meeting_pid)

    # 2. Generate test audio
    if not AUDIO_FILE.exists():
        generate_test_audio(AUDIO_FILE)
    else:
        logger.info("Using existing test audio: %s", AUDIO_FILE)

    # 3. Start recording
    from meeting_recorder.audio.capture_manager import CaptureManager

    output_dir = Path(tempfile.mkdtemp(prefix="e2e_test_"))
    manager = CaptureManager(
        pid=meeting_pid,
        output_dir=output_dir,
        sample_rate=16000,
        channels=1,
        process_name="zoom.exe",
        app_key="zoom",
    )
    manager.start()
    mute_state = "MUTED" if (manager.mute_sync and manager.mute_sync.is_muted) else "UNMUTED"
    logger.info("Recording started (mute: %s)", mute_state)

    # 4. Launch bot
    from tests.e2e.meeting_bot import MeetingBot

    bot = MeetingBot(
        name="Audio Test Bot",
        headless=True,
        audio_file=str(AUDIO_FILE),
    )

    try:
        bot.join(meeting_url, timeout=60.0)
        print()
        print(f"  Bot joined. Admit it from the waiting room if needed!")
        print(f"  Recording for {wait_seconds}s...")
        print()
        for i in range(wait_seconds // 5):
            time.sleep(5)
            elapsed = (i + 1) * 5
            print(f"    {elapsed}s / {wait_seconds}s", flush=True)
        time.sleep(wait_seconds % 5)
    except Exception as e:
        logger.error("Bot join failed: %s", e)
    finally:
        bot.leave()
        time.sleep(2)
        manager.stop()

    # 5. Analyze results
    print()
    print("=" * 60)
    print("  RESULTS")
    print("=" * 60)

    app = analyze_wav(output_dir / "app_audio.wav")
    mic = analyze_wav(output_dir / "mic_audio.wav")

    for label, stats in [("app_audio.wav", app), ("mic_audio.wav", mic)]:
        if not stats["exists"]:
            print(f"  {label}: NOT CREATED [FAIL]")
        elif stats["max_amp"] > 500:
            print(
                f"  {label}: {stats['duration']:.1f}s | "
                f"max_amp={stats['max_amp']} | "
                f"active={stats['active_pct']:.1f}% [PASS - AUDIO CAPTURED]"
            )
        elif stats["max_amp"] > 0:
            print(
                f"  {label}: {stats['duration']:.1f}s | "
                f"max_amp={stats['max_amp']} | "
                f"active={stats['active_pct']:.1f}% [WEAK AUDIO]"
            )
        else:
            print(
                f"  {label}: {stats['duration']:.1f}s | "
                f"silent [NO AUDIO - was bot admitted?]"
            )

    print()
    print(f"  Output: {output_dir}")
    print()

    if app["exists"] and app.get("max_amp", 0) > 500:
        print("  >>> PIPELINE WORKING - external audio captured <<<")
        return 0
    elif app["exists"] and app.get("max_amp", 0) > 0:
        print("  Pipeline running but audio is very quiet.")
        print("  Make sure you admitted the bot and it shows a mic icon.")
        return 0
    else:
        print("  No audio captured. Possible causes:")
        print("  - Bot stuck in waiting room (admit it)")
        print("  - Bot joined but didn't connect audio (check mic icon)")
        print("  - Wrong Zoom process targeted")
        return 1


if __name__ == "__main__":
    sys.exit(main())
