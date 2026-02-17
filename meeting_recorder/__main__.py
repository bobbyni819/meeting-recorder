"""Entry point for Meeting Recorder: python -m meeting_recorder"""

from __future__ import annotations

import logging
import os
import sys


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
        app.run()

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)

    logger.info("Meeting Recorder exited.")


if __name__ == "__main__":
    main()
