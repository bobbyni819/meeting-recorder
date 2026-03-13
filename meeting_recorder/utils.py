"""Cross-platform utility functions."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def open_in_explorer(path: str) -> None:
    """Open a file or folder in the system file manager."""
    if not os.path.exists(path):
        logger.warning("Path does not exist, cannot open: %s", path)
        return
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
