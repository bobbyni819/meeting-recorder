"""Windows tray and hotkey adapters."""

from __future__ import annotations

from typing import Callable

from meeting_recorder.platform_support.base import HotkeyBackend, TrayBackend


class WindowsTrayBackend(TrayBackend):
    """Adapter around the existing pystray-based tray icon."""

    def __init__(self, *args, **kwargs):
        from meeting_recorder.ui.tray import TrayIcon

        self._tray = TrayIcon(*args, **kwargs)

    def run(self) -> None:
        self._tray.run()

    def stop(self) -> None:
        self._tray.stop()

    def set_state(self, state: str, status_text: str = "") -> None:
        self._tray.set_state(state, status_text=status_text)

    @property
    def wrapped(self) -> object:
        """Underlying existing Windows tray object."""
        return self._tray


class WindowsHotkeyBackend(HotkeyBackend):
    """Adapter around the existing ``keyboard`` global-hotkey package."""

    def __init__(self):
        self._handles: list[object] = []

    def register(self, combo: str, callback: Callable[[], None]) -> object:
        import keyboard

        handle = keyboard.add_hotkey(combo, callback)
        self._handles.append(handle)
        return handle

    def unregister(self, handle_or_combo: object) -> None:
        import keyboard

        keyboard.remove_hotkey(handle_or_combo)
        try:
            self._handles.remove(handle_or_combo)
        except ValueError:
            pass

    def start(self) -> None:
        return None

    def stop(self) -> None:
        import keyboard

        for handle in list(self._handles):
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._handles.clear()

