"""CLI diagnostic command for Meeting Recorder.

Runs a series of checks to verify that the system is properly configured
and can record, transcribe, and upload without errors.

Usage:
    python -m meeting_recorder diagnose
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ANSI color codes (used only when terminal supports it)
_SUPPORTS_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _ok(msg: str) -> str:
    if _SUPPORTS_COLOR:
        return f"\033[92m[OK]\033[0m   {msg}"
    return f"[OK]   {msg}"


def _warn(msg: str) -> str:
    if _SUPPORTS_COLOR:
        return f"\033[93m[WARN]\033[0m {msg}"
    return f"[WARN] {msg}"


def _fail(msg: str) -> str:
    if _SUPPORTS_COLOR:
        return f"\033[91m[FAIL]\033[0m {msg}"
    return f"[FAIL] {msg}"


def _header(msg: str) -> str:
    if _SUPPORTS_COLOR:
        return f"\n\033[1m--- {msg} ---\033[0m"
    return f"\n--- {msg} ---"


def run_diagnostics() -> int:
    """Run all diagnostic checks and return exit code (0 = all OK)."""
    print("Meeting Recorder — System Diagnostics\n")
    failures = 0

    # 1. Config
    failures += _check_config()

    # 2. GPU / CUDA
    failures += _check_gpu()

    # 3. VAD model
    failures += _check_vad()

    # 4. Meeting process scan
    failures += _check_meeting_processes()

    # 5. App audio probe
    failures += _check_app_audio()

    # 6. Mic probe
    failures += _check_mic()

    # 7. API connectivity
    failures += _check_api()

    # 8. Screen capture probe
    failures += _check_screen_capture()

    print()
    if failures == 0:
        print(_ok("All checks passed!"))
    else:
        print(_fail(f"{failures} check(s) failed."))
    return 1 if failures > 0 else 0


def _check_config() -> int:
    """Check config loads and key values are valid."""
    print(_header("Configuration"))
    failures = 0
    try:
        from meeting_recorder.config import Config, CONFIG_FILE

        config = Config.load()
        print(_ok(f"Config loaded from {CONFIG_FILE if CONFIG_FILE.exists() else 'defaults'}"))

        # Output dir writable
        output_dir = config.output_dir
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            test_file = output_dir / ".diagnose_test"
            test_file.write_text("test")
            test_file.unlink()
            print(_ok(f"Output dir writable: {output_dir}"))
        except Exception as e:
            print(_fail(f"Output dir not writable: {output_dir} ({e})"))
            failures += 1

        # Check transcription backend config
        tc = config.transcription
        if tc.backend == "gemini" and not tc.gemini_api_key:
            print(_fail("Gemini transcription selected but gemini_api_key is empty"))
            failures += 1
        elif tc.backend == "cloud" and not tc.openai_api_key:
            print(_fail("Cloud transcription selected but openai_api_key is empty"))
            failures += 1
        else:
            print(_ok(f"Transcription backend: {tc.backend}"))

        # Summary config
        sc = config.summary
        if sc.enabled and not sc.api_key:
            # Check Gemini fallback
            if sc.provider == "gemini" and tc.gemini_api_key:
                print(_ok("Summary provider: gemini (using transcription API key)"))
            else:
                print(_warn(f"Summary enabled ({sc.provider}) but api_key is empty"))
        elif sc.enabled:
            print(_ok(f"Summary provider: {sc.provider}"))
        else:
            print(_ok("Summary: disabled"))

    except Exception as e:
        print(_fail(f"Config load failed: {e}"))
        failures += 1

    return failures


def _check_gpu() -> int:
    """Check GPU availability and CUDA support."""
    print(_header("GPU / CUDA"))
    failures = 0
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_mem / (1024 ** 3)
            print(_ok(f"CUDA available: {name} ({vram:.1f} GB VRAM)"))
        else:
            print(_warn(
                "CUDA not available — transcription will use CPU (much slower). "
                "Install PyTorch with CUDA: pip install torch --index-url "
                "https://download.pytorch.org/whl/cu121"
            ))
    except ImportError:
        print(_fail("PyTorch not installed"))
        failures += 1
    except Exception as e:
        print(_fail(f"GPU check failed: {e}"))
        failures += 1
    return failures


def _check_vad() -> int:
    """Check that the Silero VAD model can load."""
    print(_header("Voice Activity Detection"))
    failures = 0
    try:
        from meeting_recorder.audio.vad import VoiceActivityDetector

        vad = VoiceActivityDetector(threshold=0.5)
        vad.load()
        print(_ok("Silero VAD model loaded"))
    except Exception as e:
        print(_fail(f"VAD model failed to load: {e}"))
        failures += 1
    return failures


def _check_meeting_processes() -> int:
    """Scan for meeting processes."""
    print(_header("Meeting Process Scan"))
    failures = 0
    try:
        from meeting_recorder.audio.process_finder import find_meeting_processes

        processes = find_meeting_processes()
        if processes:
            for p in processes:
                print(_ok(f"Found {p.display_name} (PID {p.pid}, {p.name})"))
        else:
            print(_warn("No meeting processes found (Zoom, Teams, Webex not running)"))
    except Exception as e:
        print(_fail(f"Process scan failed: {e}"))
        failures += 1

    return failures


def _check_app_audio() -> int:
    """Probe app audio capture from top meeting candidate."""
    print(_header("App Audio Capture"))
    failures = 0
    try:
        from meeting_recorder.audio.process_finder import find_primary_meeting_process
        from meeting_recorder.audio.app_audio import AppAudioCapture
        from meeting_recorder.audio.ring_buffer import RingBuffer
        import struct

        process = find_primary_meeting_process()
        if process is None:
            print(_warn("No meeting process to probe (skipping audio test)"))
            return 0

        buf = RingBuffer(max_chunks=500)
        capture = AppAudioCapture(
            pid=process.pid,
            ring_buffer=buf,
            sample_rate=16000,
            channels=1,
            chunk_duration_ms=30,
        )

        capture.start()
        time.sleep(2.0)
        capture.stop()

        chunks = buf.get_all()
        total_samples = 0
        sum_sq = 0.0
        for chunk in chunks:
            samples = struct.unpack(f"<{len(chunk) // 2}h", chunk)
            total_samples += len(samples)
            sum_sq += sum(s * s for s in samples)

        if total_samples > 0:
            rms = (sum_sq / total_samples) ** 0.5
            print(_ok(f"Captured {total_samples} samples from PID {process.pid}, RMS={rms:.0f}"))
            if rms < 10:
                print(_warn("RMS very low — meeting may be silent or wrong PID"))
        else:
            print(_warn(f"No audio data captured from PID {process.pid}"))

    except Exception as e:
        print(_fail(f"App audio probe failed: {e}"))
        failures += 1

    return failures


def _check_mic() -> int:
    """Probe microphone capture and VAD."""
    print(_header("Microphone Capture"))
    failures = 0
    try:
        import pyaudiowpatch as pyaudio

        p = pyaudio.PyAudio()
        device_info = p.get_default_input_device_info()
        print(_ok(f"Default mic: {device_info['name']} ({int(device_info['defaultSampleRate'])}Hz)"))

        # Quick 1s capture
        native_rate = int(device_info["defaultSampleRate"])
        native_channels = min(int(device_info["maxInputChannels"]), 2)
        chunk_size = int(native_rate * 0.1)  # 100ms chunks

        stream = p.open(
            format=pyaudio.paInt16,
            channels=native_channels,
            rate=native_rate,
            input=True,
            frames_per_buffer=chunk_size,
        )

        import struct
        total_samples = 0
        sum_sq = 0.0
        for _ in range(10):  # 10 x 100ms = 1s
            data = stream.read(chunk_size, exception_on_overflow=False)
            samples = struct.unpack(f"<{len(data) // 2}h", data)
            total_samples += len(samples)
            sum_sq += sum(s * s for s in samples)

        stream.stop_stream()
        stream.close()
        p.terminate()

        if total_samples > 0:
            rms = (sum_sq / total_samples) ** 0.5
            print(_ok(f"Mic capture OK: {total_samples} samples, RMS={rms:.0f}"))
        else:
            print(_warn("No mic data captured"))

    except ImportError:
        print(_fail("PyAudioWPatch not installed"))
        failures += 1
    except Exception as e:
        print(_fail(f"Mic probe failed: {e}"))
        failures += 1

    return failures


def _check_api() -> int:
    """Check API connectivity for configured providers."""
    print(_header("API Connectivity"))
    failures = 0

    try:
        from meeting_recorder.config import Config

        config = Config.load()

        # Check Gemini API if configured
        gemini_key = config.transcription.gemini_api_key
        if gemini_key:
            try:
                from google import genai

                client = genai.Client(api_key=gemini_key)
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents="Say 'ok' and nothing else.",
                )
                if response.text:
                    print(_ok("Gemini API: connected"))
                else:
                    print(_warn("Gemini API: empty response"))
            except Exception as e:
                print(_fail(f"Gemini API: {e}"))
                failures += 1
        else:
            print(_ok("Gemini API: not configured (skipped)"))

        # Check OpenAI API if configured
        openai_key = config.transcription.openai_api_key or (
            config.summary.api_key if config.summary.provider == "openai" else ""
        )
        if openai_key:
            try:
                import openai

                client = openai.OpenAI(api_key=openai_key)
                client.models.list()
                print(_ok("OpenAI API: connected"))
            except Exception as e:
                print(_fail(f"OpenAI API: {e}"))
                failures += 1
        else:
            print(_ok("OpenAI API: not configured (skipped)"))

    except Exception as e:
        print(_fail(f"API check failed: {e}"))
        failures += 1

    return failures


def _check_screen_capture() -> int:
    """Probe screen capture from the best meeting window, or any visible window."""
    print(_header("Screen Capture"))
    failures = 0
    try:
        from meeting_recorder.audio.process_finder import find_primary_meeting_process
        from meeting_recorder.video.window_finder import (
            find_window_by_pid,
            get_window_rect,
            list_visible_windows,
        )
        from meeting_recorder.video.screen_capture import ScreenCapture

        # Try meeting window first, then fall back to any visible window
        hwnd = None
        source = ""
        process = find_primary_meeting_process()
        if process is not None:
            hwnd = find_window_by_pid(process.pid)
            source = f"meeting ({process.display_name})"

        if hwnd is None:
            windows = list_visible_windows()
            if windows:
                hwnd = windows[0][0]
                source = f"window: {windows[0][1][:40]}"

        if hwnd is None:
            print(_warn("No visible window found for screen capture test (skipped)"))
            return 0

        rect = get_window_rect(hwnd)
        if rect is None:
            print(_warn("Window minimized or invalid (skipped)"))
            return 0

        _, _, width, height = rect
        frame = ScreenCapture._capture_printwindow(hwnd, width, height)
        if frame is not None:
            import numpy as np
            if np.max(frame) >= 5:
                print(_ok(f"Screen capture OK: {width}x{height} from {source} (PrintWindow)"))
            else:
                print(_warn(f"PrintWindow returned blank ({width}x{height}) — will fall back to mss"))
        else:
            print(_warn("PrintWindow failed — will fall back to mss"))

    except ImportError as e:
        print(_warn(f"Screen capture deps missing: {e}"))
    except Exception as e:
        print(_fail(f"Screen capture probe failed: {e}"))
        failures += 1

    return failures
