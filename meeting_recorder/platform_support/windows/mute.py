"""Windows mute-state detector adapter."""

from __future__ import annotations

from typing import Optional

from meeting_recorder.platform_support.base import MuteDetector


class WindowsMuteDetector(MuteDetector):
    """Adapter around the existing UI Automation mute detector."""

    def read_mute_state(
        self,
        app_key: str,
        target_pids: Optional[set[int]] = None,
    ) -> Optional[bool]:
        if not target_pids:
            return None
        from meeting_recorder.audio.uia_mute_detector import detect_mute_state

        return detect_mute_state(set(target_pids))

