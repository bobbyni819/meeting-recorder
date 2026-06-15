"""Probe Windows on-device speech recognition from Python.

This is a standalone research script. It is intentionally not imported by, or
connected to, the meeting_recorder application.

The WinRT SpeechRecognizer API exposed by winsdk is microphone-oriented. This
script first probes whether the installed binding exposes any supported
file-audio recognition path. If it does not, it reports that limitation clearly
instead of faking a file transcript. Use --mic-seconds to evaluate the same
Windows dictation engine live from the default input device.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import time
import wave
from pathlib import Path
from typing import Any


def _latest_default_wav() -> Path | None:
    root = Path.home() / "MeetingRecordings"
    if not root.exists():
        return None
    candidates = [p for p in root.rglob("mic_audio.wav") if p.stat().st_size > 1024]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate) if rate else None
    except wave.Error:
        return None


def _wav_description(path: Path) -> str:
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            rate = wav.getframerate()
            sample_width = wav.getsampwidth() * 8
            duration = wav.getnframes() / float(rate) if rate else 0.0
    except wave.Error as exc:
        return f"unreadable WAV header: {exc}"
    return f"{rate} Hz, {channels} channel(s), {sample_width}-bit, {duration:.2f}s"


def _enum_name(value: Any) -> str:
    for name in dir(type(value)):
        if name.startswith("_"):
            continue
        try:
            if getattr(type(value), name) == value:
                return name
        except Exception:
            pass
    return str(value)


def _describe_winrt_error(exc: BaseException) -> str:
    message = str(exc).strip() or repr(exc)
    lower = message.lower()
    hints: list[str] = []
    if "0x80045509" in lower or "-2147199735" in lower:
        hints.append(
            "Accept/enable the Windows speech privacy policy before starting dictation."
        )
    if "privacy" in lower or "denied" in lower or "access" in lower:
        hints.append(
            "Check Windows Settings > Privacy & security > Microphone and Speech."
        )
    if "speech" in lower and ("disabled" in lower or "not enabled" in lower):
        hints.append("Enable online speech recognition / speech services in Windows Settings.")
    if "microphone" in lower:
        hints.append("Check that the default input device is available and not muted.")
    if hints:
        message = message + "\n" + "\n".join(f"Hint: {hint}" for hint in hints)
    return message


def _load_speech_types():
    try:
        from winsdk.windows.media.speechrecognition import (
            SpeechContinuousRecognitionMode,
            SpeechRecognitionResultStatus,
            SpeechRecognitionScenario,
            SpeechRecognitionTopicConstraint,
            SpeechRecognizer,
        )
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "winsdk is not installed. Install with: py -m pip install winsdk"
        ) from exc

    return {
        "SpeechContinuousRecognitionMode": SpeechContinuousRecognitionMode,
        "SpeechRecognitionResultStatus": SpeechRecognitionResultStatus,
        "SpeechRecognitionScenario": SpeechRecognitionScenario,
        "SpeechRecognitionTopicConstraint": SpeechRecognitionTopicConstraint,
        "SpeechRecognizer": SpeechRecognizer,
    }


async def _create_dictation_recognizer() -> Any:
    types = _load_speech_types()
    recognizer = types["SpeechRecognizer"]()
    constraint = types["SpeechRecognitionTopicConstraint"](
        types["SpeechRecognitionScenario"].DICTATION,
        "dictation",
        "dictation",
    )
    recognizer.constraints.append(constraint)
    compile_result = await recognizer.compile_constraints_async()
    status = compile_result.status
    if status != types["SpeechRecognitionResultStatus"].SUCCESS:
        recognizer.close()
        raise RuntimeError(f"dictation grammar compile failed: {_enum_name(status)}")
    return recognizer


async def probe_file_recognition(path: Path) -> dict[str, Any]:
    """Try to find a supported SpeechRecognizer file-input path.

    winsdk 1.0.0b10 exposes StorageFile and AudioGraph file nodes, but the
    SpeechRecognizer object has no public method or constructor accepting a
    StorageFile, stream, AudioGraph node, or MediaSource. The probe is kept in
    code so future WinRT binding updates can be noticed quickly.
    """
    types = _load_speech_types()
    recognizer = await _create_dictation_recognizer()
    try:
        methods = {
            name: getattr(recognizer, name)
            for name in dir(recognizer)
            if not name.startswith("_")
        }
        file_like_methods = [
            name
            for name in methods
            if any(token in name.lower() for token in ("file", "stream", "source", "audio"))
        ]
        recognize = methods.get("recognize_async")
        signature_note = "unavailable"
        if recognize is not None:
            try:
                signature_note = str(inspect.signature(recognize))
            except (TypeError, ValueError):
                signature_note = "no Python signature exposed"

        return {
            "supported": False,
            "text": "",
            "reason": (
                "The installed winsdk SpeechRecognizer binding exposes only microphone "
                "recognition entry points. It has no public file/stream/audio-source "
                "input method for an arbitrary WAV."
            ),
            "details": {
                "path": str(path),
                "recognize_async_signature": signature_note,
                "file_like_methods": file_like_methods,
                "recognizer_methods": sorted(methods),
                "status_enum_success": types["SpeechRecognitionResultStatus"].SUCCESS,
            },
        }
    finally:
        recognizer.close()


async def recognize_from_microphone(seconds: float) -> dict[str, Any]:
    types = _load_speech_types()
    recognizer = await _create_dictation_recognizer()
    session = recognizer.continuous_recognition_session
    results: list[str] = []
    statuses: list[str] = []

    def on_result_generated(sender: Any, args: Any) -> None:
        result = args.result
        statuses.append(_enum_name(result.status))
        if result.status == types["SpeechRecognitionResultStatus"].SUCCESS:
            text = result.text.strip()
            if text:
                results.append(text)

    def on_completed(sender: Any, args: Any) -> None:
        if hasattr(args, "status"):
            statuses.append(f"completed:{_enum_name(args.status)}")

    result_token = session.add_result_generated(on_result_generated)
    completed_token = session.add_completed(on_completed)
    started_at = time.perf_counter()
    try:
        await session.start_async(types["SpeechContinuousRecognitionMode"].DEFAULT)
        await asyncio.sleep(seconds)
        await session.stop_async()
    finally:
        try:
            session.remove_result_generated(result_token)
            session.remove_completed(completed_token)
        finally:
            recognizer.close()

    elapsed = time.perf_counter() - started_at
    return {
        "text": " ".join(results).strip(),
        "elapsed": elapsed,
        "statuses": statuses,
    }


def _print_file_result(path: Path, result: dict[str, Any], elapsed: float) -> None:
    duration = _wav_duration_seconds(path)
    print("Mode: file")
    print(f"Audio: {path}")
    print(f"WAV: {_wav_description(path)}")
    print(f"File-based recognition supported: {result['supported']}")
    if result["supported"]:
        rtf = elapsed / duration if duration else None
        print(f"Recognized text: {result.get('text', '')}")
        print(f"Elapsed seconds: {elapsed:.3f}")
        print(f"RTF: {rtf:.3f}" if rtf is not None else "RTF: unavailable")
    else:
        print("Recognized text: <none>")
        print(f"Elapsed seconds: {elapsed:.3f}")
        print("RTF: unavailable")
        print(f"Limitation: {result['reason']}")
        print(
            "Use --mic-seconds N to run a live default-microphone probe with the "
            "same Windows dictation recognizer."
        )


def _parse_args() -> argparse.Namespace:
    default_wav = _latest_default_wav()
    parser = argparse.ArgumentParser(
        description="Probe Windows on-device speech recognition via winsdk."
    )
    parser.add_argument(
        "wav",
        nargs="?",
        type=Path,
        default=default_wav,
        help="16 kHz mono WAV to test; defaults to latest ~/MeetingRecordings/**/mic_audio.wav.",
    )
    parser.add_argument(
        "--mic-seconds",
        type=float,
        default=0.0,
        help="Also recognize live speech from the default microphone for N seconds.",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = _parse_args()
    if args.wav is None:
        print("No WAV path supplied and no ~/MeetingRecordings/**/mic_audio.wav was found.")
        print("Pass a 16 kHz mono WAV path, or use --mic-seconds N for live mic recognition.")
        return 2

    wav_path = args.wav.expanduser().resolve()
    if not wav_path.exists():
        print(f"WAV not found: {wav_path}")
        return 2

    try:
        started_at = time.perf_counter()
        file_result = await probe_file_recognition(wav_path)
        elapsed = time.perf_counter() - started_at
        _print_file_result(wav_path, file_result, elapsed)
    except Exception as exc:
        print("Mode: file")
        print(f"Audio: {wav_path}")
        print("Recognized text: <none>")
        print("Elapsed seconds: unavailable")
        print("RTF: unavailable")
        print("Windows speech probe failed:")
        print(_describe_winrt_error(exc))
        return 1

    if args.mic_seconds > 0:
        print()
        print("Mode: microphone")
        print(f"Listening seconds: {args.mic_seconds:.1f}")
        try:
            mic_result = await recognize_from_microphone(args.mic_seconds)
        except Exception as exc:
            print("Recognized text: <none>")
            print("Elapsed seconds: unavailable")
            print("RTF: not applicable")
            print("Windows microphone recognition failed:")
            print(_describe_winrt_error(exc))
            return 1

        print(f"Recognized text: {mic_result['text'] or '<none>'}")
        print(f"Elapsed seconds: {mic_result['elapsed']:.3f}")
        print("RTF: not applicable")
        if mic_result["statuses"]:
            print(f"Statuses: {', '.join(mic_result['statuses'])}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
