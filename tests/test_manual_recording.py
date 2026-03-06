"""Tests for manual (any-window) recording mode."""

from __future__ import annotations


class TestManualMuteSync:
    """MuteSync should not hook any app shortcut for manual recordings."""

    def test_no_app_shortcut_for_manual(self):
        """APP_MUTE_SHORTCUTS should not have a 'manual' entry."""
        from meeting_recorder.audio.mute_sync import APP_MUTE_SHORTCUTS

        assert "manual" not in APP_MUTE_SHORTCUTS
