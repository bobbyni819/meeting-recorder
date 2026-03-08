"""Cross-platform utility functions."""

from __future__ import annotations

import os
import subprocess
import sys


def open_in_explorer(path: str) -> None:
    """Open a file or folder in the system file manager."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
