"""Dictation mode — short solo voice memos transcribed via Gemini.

Additive to meeting mode; shares no runtime state with the tray app.
Entry point: ``python -m meeting_recorder dictate``.
"""

from meeting_recorder.dictation.recorder import DictationRecorder
from meeting_recorder.dictation.pipeline import finalize_recording, render_markdown

__all__ = ["DictationRecorder", "finalize_recording", "render_markdown"]
