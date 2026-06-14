"""macOS global hotkey backend using pynput."""

from __future__ import annotations

import logging
from typing import Callable

from meeting_recorder.platform_support.base import HotkeyBackend

logger = logging.getLogger(__name__)

_MODIFIERS = {
    "ctrl": "<ctrl>",
    "control": "<ctrl>",
    "shift": "<shift>",
    "alt": "<alt>",
    "option": "<alt>",
    "cmd": "<cmd>",
    "command": "<cmd>",
    "meta": "<cmd>",
    "super": "<cmd>",
    "win": "<cmd>",
}


class MacHotkeyBackend(HotkeyBackend):
    """Register global hotkeys with pynput.keyboard.GlobalHotKeys."""

    def __init__(self):
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._listener = None
        self._running = False

    def register(self, combo: str, callback: Callable[[], None]) -> object:
        translated = _translate_combo(combo)
        self._callbacks[translated] = callback
        if self._running:
            self._restart_listener()
        return translated

    def unregister(self, handle_or_combo: object) -> None:
        translated = str(handle_or_combo)
        if translated not in self._callbacks:
            translated = _translate_combo(translated)
        self._callbacks.pop(translated, None)
        if self._running:
            self._restart_listener()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_listener()

    def stop(self) -> None:
        self._running = False
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                logger.debug("pynput hotkey listener stop failed", exc_info=True)
        self._callbacks.clear()

    def _restart_listener(self) -> None:
        listener = self._listener
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                logger.debug("pynput hotkey listener restart stop failed", exc_info=True)
        self._start_listener()

    def _start_listener(self) -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise RuntimeError(
                "pynput is required for macOS global hotkeys. "
                "Install with: pip install -e '.[macos]'"
            ) from exc
        self._listener = keyboard.GlobalHotKeys(dict(self._callbacks))
        self._listener.start()


def _translate_combo(combo: str) -> str:
    parts = [part.strip().lower() for part in combo.replace("-", "+").split("+")]
    translated: list[str] = []
    for part in parts:
        if not part:
            continue
        translated.append(_MODIFIERS.get(part, part))
    if not translated:
        raise ValueError("hotkey combo cannot be empty")
    return "+".join(translated)

