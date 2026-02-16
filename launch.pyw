"""Windowless launcher for Meeting Recorder.

This file is used by shortcuts to launch the app without showing a console window.
The .pyw extension tells Windows to use pythonw.exe (no console).
"""

import subprocess
import sys
import os

# Change to the project directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Launch the meeting recorder module
subprocess.Popen(
    [sys.executable, "-m", "meeting_recorder"],
    cwd=os.path.dirname(os.path.abspath(__file__)),
    creationflags=subprocess.CREATE_NO_WINDOW,
)
