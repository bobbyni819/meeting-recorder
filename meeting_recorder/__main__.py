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


def main() -> None:
    """Main entry point for Meeting Recorder."""
    _fix_stdio()

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
