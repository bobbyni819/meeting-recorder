"""macOS application entry points."""

from __future__ import annotations

__all__ = ["MacMenubarApp"]


def __getattr__(name: str):
    if name == "MacMenubarApp":
        from meeting_recorder.macos.app import MacMenubarApp

        return MacMenubarApp
    raise AttributeError(name)
