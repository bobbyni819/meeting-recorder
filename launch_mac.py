"""Launcher for the macOS Meeting Recorder menu bar app."""

from __future__ import annotations

import logging
import os
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    os.chdir(repo_root)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("meeting_recorder_mac.log", encoding="utf-8", mode="a"),
        ],
    )

    from meeting_recorder.macos.app import MacMenubarApp

    MacMenubarApp().run()


if __name__ == "__main__":
    main()
