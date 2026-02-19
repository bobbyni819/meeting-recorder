"""Play test audio through VB-Cable for semi-automated meeting recorder testing.

Usage:
  python -m tests.e2e.play_test_audio                    # synthetic tones, 30s
  python -m tests.e2e.play_test_audio --tts              # Windows TTS speech, 30s
  python -m tests.e2e.play_test_audio --file speech.wav  # play a WAV file
  python -m tests.e2e.play_test_audio --loop             # loop until Ctrl+C
  python -m tests.e2e.play_test_audio --duration 60      # play for 60 seconds
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Play test audio through VB-Cable for meeting recorder testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                    # synthetic tones, 30s\n"
            "  %(prog)s --tts              # Windows TTS speech, 30s\n"
            "  %(prog)s --file speech.wav  # play a WAV file\n"
            "  %(prog)s --tts --loop       # TTS speech, loop until Ctrl+C\n"
            "  %(prog)s --duration 60      # play for 60 seconds\n"
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--tts",
        action="store_true",
        help="Use Windows TTS (pyttsx3) for realistic speech instead of synthetic tones",
    )
    source.add_argument(
        "--file",
        metavar="WAV",
        help="Play audio from a WAV file",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Loop audio until Ctrl+C",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Audio duration in seconds (default: 30, ignored with --file)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Find VB-Cable ---
    from tests.e2e.virtual_audio import (
        VBCablePlayer,
        find_vbcable_device,
        generate_test_speech,
        generate_tts_speech,
        load_wav_file,
    )

    print("Looking for VB-Cable device...")
    device_idx = find_vbcable_device()
    if device_idx is None:
        print(
            "\nERROR: VB-Cable not found!\n"
            "Install it from: https://vb-audio.com/Cable/\n"
            "Then restart this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    import sounddevice as sd

    dev_info = sd.query_devices(device_idx)
    print(f"Found VB-Cable: device {device_idx} ({dev_info['name']})")

    # --- Generate / load audio ---
    if args.file:
        source_label = f"WAV file: {args.file}"
        print(f"Loading {args.file}...")
        audio = load_wav_file(args.file)
    elif args.tts:
        source_label = "Windows TTS speech"
        print(f"Generating {args.duration:.0f}s of TTS speech...")
        audio = generate_tts_speech(duration=args.duration)
    else:
        source_label = "synthetic tones"
        print(f"Generating {args.duration:.0f}s of synthetic speech-like tones...")
        audio = generate_test_speech(duration=args.duration)

    clip_duration = len(audio) / 44100
    print(f"Audio source: {source_label} ({clip_duration:.1f}s)")

    # --- Play ---
    player = VBCablePlayer(device_index=device_idx)

    if args.loop:
        print(f"Playing through VB-Cable (looping) — press Ctrl+C to stop")
    else:
        print(f"Playing through VB-Cable...")

    start = time.monotonic()
    try:
        player.play_blocking(audio, loop=args.loop)
    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\nDone. Played {elapsed:.1f}s of audio through VB-Cable.")


if __name__ == "__main__":
    main()
