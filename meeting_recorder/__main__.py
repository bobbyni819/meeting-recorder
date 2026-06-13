"""Entry point for Meeting Recorder: python -m meeting_recorder"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import sys
import threading


def _fix_stdio() -> None:
    """Redirect None stdio streams to devnull for pythonw.exe compatibility.

    Under pythonw.exe (no console), sys.stdout/stderr/stdin are None.
    Libraries like torch.hub.load crash when they try to write to stderr.
    """
    devnull = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = devnull
    if sys.stderr is None:
        sys.stderr = devnull
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r")


def _set_app_user_model_id() -> None:
    """Give Windows an explicit app identity so the taskbar shows OUR icon.

    Without this, a pythonw-hosted app inherits Python's taskbar icon and
    grouping. Harmless no-op off Windows or if the call is unavailable.
    """
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "vaultlab.meeting-recorder"
        )
    except Exception:
        pass


def main() -> None:
    """Main entry point for Meeting Recorder."""
    _fix_stdio()
    _set_app_user_model_id()

    # Handle subcommands before full app startup
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "diagnose":
            logging.basicConfig(level=logging.WARNING, format="%(message)s")
            from meeting_recorder.diagnose import run_diagnostics
            sys.exit(run_diagnostics())
        elif cmd == "export-config":
            from meeting_recorder.config_transfer import export_config
            dest = sys.argv[2] if len(sys.argv) > 2 else None
            sys.exit(export_config(dest))
        elif cmd == "import-config":
            if len(sys.argv) < 3:
                print("Usage: python -m meeting_recorder import-config <file>")
                sys.exit(1)
            from meeting_recorder.config_transfer import import_config
            sys.exit(import_config(sys.argv[2]))
        elif cmd == "probe-speakers":
            # Experimental: live-probe what active-speaker names Zoom/Teams
            # expose via UIAutomation. Run this DURING a real meeting to
            # validate (and tune) speaker-event capture before enabling it.
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            import time as _t

            try:
                import psutil
                from meeting_recorder.audio.speaker_events import SpeakerEventCapture
            except ImportError as e:
                print(f"Missing dependency: {e}")
                sys.exit(2)
            pids = {
                p.info["pid"] for p in psutil.process_iter(["pid", "name"])
                if any(k in (p.info["name"] or "").lower()
                       for k in ("zoom", "teams"))
            }
            if not pids:
                print("No Zoom/Teams process found.")
                sys.exit(1)
            print(f"Probing PIDs {pids} for 20s — speak in your meeting now…")
            cap = SpeakerEventCapture(pids=pids, output_path="-")
            seen = []
            cap._write_event = lambda name: (  # capture to stdout instead of file
                seen.append(name) or print(f"  active speaker: {name}")
            )
            cap.start()
            try:
                _t.sleep(20)
            finally:
                cap.stop()
            print(
                f"\nDetected {len(set(seen))} distinct speaker name(s): "
                f"{sorted(set(seen)) or 'none — UI names not exposed this way'}"
            )
            sys.exit(0)
        elif cmd == "probe-mute":
            # Live-probe how the recorder reads your Zoom/Teams mute state via
            # UI Automation. Run this DURING a meeting and toggle your mute a
            # few times (button AND Alt+A / Ctrl+Shift+M) to confirm the
            # recorder follows it. Read-only — changes nothing.
            logging.basicConfig(level=logging.WARNING, format="%(message)s")
            import time as _t

            try:
                import psutil
                from meeting_recorder.audio.uia_mute_detector import detect_mute_state
                from meeting_recorder.audio.mute_sync import (
                    detect_initial_mute_state, get_all_pids_for_process,
                )
            except ImportError as e:
                print(f"Missing dependency: {e}")
                sys.exit(2)

            pids: set[int] = set()
            for name in ("zoom.exe", "ms-teams.exe", "teams.exe", "webex.exe"):
                pids |= get_all_pids_for_process(name)
            # include children (renderer/WebView2 often own the toolbar)
            for pid in list(pids):
                try:
                    for ch in psutil.Process(pid).children(recursive=True):
                        pids.add(ch.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if not pids:
                print("No Zoom/Teams/Webex process found.")
                sys.exit(1)

            secs = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 30
            print(f"Probing {len(pids)} PIDs for {secs}s. Toggle your mute "
                  f"(button AND hotkey) now…\n")
            last = object()
            t_end = _t.monotonic() + secs
            while _t.monotonic() < t_end:
                uia = detect_mute_state(pids)
                reg = None
                for pid in pids:
                    reg = detect_initial_mute_state(pid, include_packaged=True)
                    if reg is not None:
                        break
                state = (uia, reg)
                if state != last:
                    u = {True: "MUTED", False: "UNMUTED", None: "??(blind)"}[uia]
                    r = {True: "muted", False: "in-use", None: "??"}[reg]
                    print(f"  UIA button = {u:11s}   registry mic = {r}")
                    last = state
                _t.sleep(1.0)
            print("\nUIA is the source of truth for soft-mute; registry only "
                  "knows if the mic device is open (stays 'in-use' while soft-muted).")
            sys.exit(0)
        elif cmd == "import-transcript":
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            args = sys.argv[2:]
            if len(args) < 2 or args[0] in ("-h", "--help"):
                print(
                    "Usage: python -m meeting_recorder import-transcript "
                    "<recording-dir> <transcript.vtt>\n\n"
                    "Import a Teams/Zoom WebVTT transcript as the recording's\n"
                    "authoritative transcript (real speaker names, high accuracy).\n"
                    "Rewrites transcript.json/.txt/.srt in the canonical schema and\n"
                    "keeps the original as teams_transcript.vtt."
                )
                sys.exit(0 if len(args) >= 2 else 1)
            from pathlib import Path as _Path

            from meeting_recorder.transcription.vtt_import import (
                import_vtt_to_recording,
            )

            rec_dir = _Path(args[0]).expanduser()
            vtt = _Path(args[1]).expanduser()
            if not rec_dir.is_dir():
                print(f"Not a recording directory: {rec_dir}"); sys.exit(1)
            if not vtt.is_file():
                print(f"VTT file not found: {vtt}"); sys.exit(1)
            try:
                result = import_vtt_to_recording(rec_dir, vtt)
                print(
                    f"Imported {result['segments']} segments "
                    f"({result['duration']:.0f}s). "
                    f"Speakers: {', '.join(result['speakers']) or '(none named)'}"
                )
                sys.exit(0)
            except Exception as e:
                print(f"Import failed: {e}"); sys.exit(1)
        elif cmd == "import-zoom-captions":
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            args = sys.argv[2:]
            if not args or args[0] in ("-h", "--help"):
                print(
                    "Usage: python -m meeting_recorder import-zoom-captions "
                    "<recording-dir> [caption_file]\n\n"
                    "Import Zoom local captions as the recording's authoritative\n"
                    "transcript. If caption_file is omitted, scans ~/Documents/Zoom\n"
                    "and imports the newest closed_caption.txt,\n"
                    "meeting_saved_closed_captions.txt, or .vtt file.\n"
                    "Rewrites transcript.json/.txt/.srt in the canonical schema and\n"
                    "keeps the original as zoom_caption.txt."
                )
                sys.exit(0 if args else 1)
            from pathlib import Path as _Path

            from meeting_recorder.transcription.vtt_import import (
                find_zoom_caption_files,
                import_zoom_caption_to_recording,
            )

            rec_dir = _Path(args[0]).expanduser()
            if not rec_dir.is_dir():
                print(f"Not a recording directory: {rec_dir}"); sys.exit(1)
            if len(args) >= 2:
                caption = _Path(args[1]).expanduser()
            else:
                found = find_zoom_caption_files()
                if not found:
                    print("No Zoom caption files found under ~/Documents/Zoom")
                    sys.exit(1)
                caption = found[0]
                print(f"Using newest Zoom caption file: {caption}")
            if not caption.is_file():
                print(f"Caption file not found: {caption}"); sys.exit(1)
            try:
                result = import_zoom_caption_to_recording(rec_dir, caption)
                print(
                    f"Imported {result['segments']} segments "
                    f"({result['duration']:.0f}s). "
                    f"Speakers: {', '.join(result['speakers']) or '(none named)'}"
                )
                sys.exit(0)
            except Exception as e:
                print(f"Import failed: {e}"); sys.exit(1)
        elif cmd == "search":
            from meeting_recorder.search.cli import main as search_main
            sys.exit(search_main(sys.argv[2:]))
        elif cmd == "stats":
            from meeting_recorder.stats_cli import main as stats_main
            sys.exit(stats_main(sys.argv[2:]))
        elif cmd == "dictate":
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            from meeting_recorder.dictation.cli import main as dictate_main
            sys.exit(dictate_main(sys.argv[2:]))
        elif cmd == "reprocess":
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            args = sys.argv[2:]
            if not args or args[0] in ("-h", "--help"):
                print(
                    "Usage: python -m meeting_recorder reprocess <recording-dir>"
                    " [--backend local|gemini|cloud] [--tail-only]\n\n"
                    "Full re-process (transcribe + summary + index + Drive) of one\n"
                    "recording, headless. --tail-only skips re-transcription and only\n"
                    "re-runs the missing summary / Drive upload from the existing\n"
                    "transcript. --backend overrides the configured transcriber."
                )
                sys.exit(0 if args else 1)
            from pathlib import Path as _Path

            from meeting_recorder.config import Config
            from meeting_recorder import recovery

            rec_dir = _Path(args[0]).expanduser()
            if not rec_dir.is_dir():
                print(f"Not a directory: {rec_dir}")
                sys.exit(1)
            backend = None
            if "--backend" in args:
                backend = args[args.index("--backend") + 1]
            config = Config.load()
            try:
                if "--tail-only" in args:
                    from meeting_recorder.storage.metadata import RecordingMetadata

                    performed = recovery.retry_tail(
                        rec_dir, config, force_summary=True,
                    )
                    meta = RecordingMetadata.load(rec_dir)
                    if performed:
                        print(f"Tail retry complete: {', '.join(performed)}")
                    elif meta.summary_failed or meta.upload_pending:
                        print(
                            "Tail retry FAILED — see traceback above. "
                            "Flags remain set; the startup sweep will retry."
                        )
                        sys.exit(1)
                    else:
                        print("Tail retry complete: nothing to do")
                else:
                    meta = recovery.reprocess_headless(rec_dir, config, backend)
                    print(
                        f"Re-process complete: status={meta.status}, "
                        f"backend={meta.transcription_backend}, "
                        f"{meta.segment_count} segments, "
                        f"summary={'yes' if meta.has_summary else 'no'}"
                    )
                sys.exit(0)
            except Exception as e:
                print(f"Re-process failed: {e}")
                sys.exit(1)
        elif cmd == "archive":
            from meeting_recorder.config import Config
            from meeting_recorder.storage.archive import archive_old_recordings, get_archive_stats
            config = Config.load()
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
            count, saved = archive_old_recordings(config.output_dir, older_than_days=days)
            print(f"Archived {count} recordings, saved {saved / (1024*1024):.1f} MB")
            stats = get_archive_stats(config.output_dir)
            print(f"Total: {stats['total']}, archived: {stats['archived']}, "
                  f"unarchived: {stats['unarchived']}")
            sys.exit(0)
        elif cmd == "probe-echo":
            # Read-only: replay a recording's app_audio.wav (far-end) + mic
            # through the echo gate and report how much it would drop. Use this
            # to validate / tune echo gating on your own recordings before
            # enabling recording.echo_gate.
            import wave
            from pathlib import Path as _Path

            import numpy as _np

            from meeting_recorder.audio.echo_gate import (
                EchoGate,
                streaming_echo_report,
            )

            args = sys.argv[2:]
            if not args or args[0] in ("-h", "--help"):
                print(
                    "Usage: python -m meeting_recorder probe-echo <recording-dir>"
                    " [--threshold 0.5]\n\n"
                    "Replays app_audio.wav (the meeting audio = echo reference)\n"
                    "against mic_audio.wav and reports what fraction of your mic\n"
                    "frames are just the meeting echoing through your speakers.\n"
                    "Read-only — it never modifies the recording."
                )
                sys.exit(0 if args else 1)
            rec_dir = _Path(args[0]).expanduser()
            app_p, mic_p = rec_dir / "app_audio.wav", rec_dir / "mic_audio.wav"
            if not app_p.exists() or not mic_p.exists():
                print(f"Need both app_audio.wav and mic_audio.wav in {rec_dir}")
                sys.exit(1)
            thr = 0.5
            if "--threshold" in args:
                thr = float(args[args.index("--threshold") + 1])

            def _load(p):
                with wave.open(str(p), "rb") as w:
                    sr, ch = w.getframerate(), w.getnchannels()
                    d = _np.frombuffer(w.readframes(w.getnframes()), dtype=_np.int16)
                if ch == 2:
                    d = d.reshape(-1, 2).mean(axis=1).astype(_np.int16)
                return d, sr

            app, sr = _load(app_p)
            mic, _ = _load(mic_p)
            rep = streaming_echo_report(
                app, mic, sample_rate=sr, gate=EchoGate(sample_rate=sr, echo_r2=thr)
            )
            print(f"Recording: {rec_dir.name}")
            print(f"  app(far-end)={len(app)/sr:.0f}s  mic={len(mic)/sr:.0f}s  threshold={thr}")
            print(f"  mic frames: {rep['frames']}  non-silent: {rep['nonsilent']}")
            print(f"  would drop as echo: {rep['dropped']} "
                  f"({rep['drop_pct_of_nonsilent']:.1f}% of non-silent)")
            if rep["example_drop_times_sec"]:
                print(f"  first drops at (s): {rep['example_drop_times_sec']}")
            hi = rep["drop_pct_of_nonsilent"]
            print("  => " + (
                "little/no speaker echo (likely headphones, or a clean setup)"
                if hi < 2 else
                f"meaningful echo detected — enabling recording.echo_gate would "
                f"clean ~{hi:.0f}% of your mic track"
            ))
            sys.exit(0)

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                "meeting_recorder.log", encoding="utf-8", mode="a"
            ),
        ],
    )
    logger = logging.getLogger(__name__)

    # Enable faulthandler to print traceback on segfault/abort to the log file.
    # This catches C-level crashes (e.g. in Tk, audio libs) that bypass Python
    # exception handling entirely.
    _fault_file = open("meeting_recorder.log", "a", encoding="utf-8")
    faulthandler.enable(file=_fault_file)

    # Install global exception hooks to catch unhandled exceptions on ANY thread
    def _excepthook(exc_type, exc_value, exc_tb):
        logger.critical(
            "Unhandled exception on main thread", exc_info=(exc_type, exc_value, exc_tb)
        )
    sys.excepthook = _excepthook

    def _threading_excepthook(args):
        logger.critical(
            "Unhandled exception on thread '%s'",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    threading.excepthook = _threading_excepthook

    # Log on process exit regardless of how it happens
    def _atexit():
        logger.info("Process exiting (atexit). Active threads: %s",
                     [t.name for t in threading.enumerate()])
    atexit.register(_atexit)

    logger.info("=" * 60)
    logger.info("Meeting Recorder starting")
    logger.info("=" * 60)

    try:
        from meeting_recorder.config import Config
        from meeting_recorder.app import MeetingRecorderApp

        config = Config.load()
        logger.info("Config loaded. Output dir: %s", config.output_dir)
        logger.info("Transcription backend: %s", config.transcription.backend)

        app = MeetingRecorderApp(config)

        # Handle Ctrl+C: gracefully stop recording + run post-processing
        import signal

        def _sigint_handler(signum, frame):
            logger.info("Ctrl+C received — shutting down gracefully...")
            app.quit()

        signal.signal(signal.SIGINT, _sigint_handler)

        logger.info("Starting app.run() on main thread (%s)", threading.current_thread().name)
        app.run()
        logger.info("app.run() returned normally.")

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except SystemExit as e:
        logger.info("SystemExit raised with code: %s", e.code)
        raise
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)

    logger.info("Meeting Recorder exited.")


if __name__ == "__main__":
    main()
