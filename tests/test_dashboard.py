"""Tests for the GameBarDashboard overlay and supporting utilities."""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from meeting_recorder.audio.level_monitor import MIN_DB
from meeting_recorder.config import Config, DashboardConfig, HotkeyConfig
from meeting_recorder.ui.dashboard import (
    GameBarDashboard,
    DashboardContext,
    db_to_fraction,
    vu_color,
    _format_elapsed,
    GREEN_VU,
    YELLOW_VU,
    RED_VU,
    EXPANDED_WIDTH,
    EXPANDED_HEIGHT,
    COLLAPSED_WIDTH,
    COLLAPSED_HEIGHT,
    PREVIEW_HEIGHT,
)


# ---------------------------------------------------------------------------
# db_to_fraction
# ---------------------------------------------------------------------------

class TestDbToFraction:
    """Test the dB-to-fraction conversion for VU meters."""

    def test_silence_returns_zero(self):
        assert db_to_fraction(MIN_DB) == 0.0

    def test_below_min_db_returns_zero(self):
        assert db_to_fraction(MIN_DB - 10) == 0.0

    def test_full_scale_returns_one(self):
        assert db_to_fraction(0.0) == 1.0

    def test_above_zero_clamps_to_one(self):
        assert db_to_fraction(3.0) == 1.0

    def test_midpoint(self):
        mid_db = MIN_DB / 2.0  # -30 dB
        result = db_to_fraction(mid_db)
        assert 0.49 < result < 0.51

    def test_quarter(self):
        quarter_db = MIN_DB * 0.75  # -45 dB
        result = db_to_fraction(quarter_db)
        assert 0.24 < result < 0.26

    def test_three_quarter(self):
        three_q_db = MIN_DB * 0.25  # -15 dB
        result = db_to_fraction(three_q_db)
        assert 0.74 < result < 0.76

    def test_monotonic_increase(self):
        """Higher dB values should map to higher fractions."""
        values = [MIN_DB, -50, -40, -30, -20, -10, -5, 0]
        fractions = [db_to_fraction(db) for db in values]
        for i in range(len(fractions) - 1):
            assert fractions[i] <= fractions[i + 1]


# ---------------------------------------------------------------------------
# vu_color
# ---------------------------------------------------------------------------

class TestVuColor:
    """Test VU meter color thresholds."""

    def test_zero_is_green(self):
        assert vu_color(0.0) == GREEN_VU

    def test_low_is_green(self):
        assert vu_color(0.3) == GREEN_VU

    def test_at_50_boundary_is_green(self):
        assert vu_color(0.50) == GREEN_VU

    def test_just_above_50_is_yellow(self):
        assert vu_color(0.51) == YELLOW_VU

    def test_mid_yellow(self):
        assert vu_color(0.65) == YELLOW_VU

    def test_at_80_boundary_is_yellow(self):
        assert vu_color(0.80) == YELLOW_VU

    def test_just_above_80_is_red(self):
        assert vu_color(0.81) == RED_VU

    def test_full_scale_is_red(self):
        assert vu_color(1.0) == RED_VU


# ---------------------------------------------------------------------------
# DashboardContext dataclass
# ---------------------------------------------------------------------------

class TestDashboardContext:
    """Test DashboardContext defaults and values."""

    def test_defaults(self):
        ctx = DashboardContext()
        assert ctx.app_name == "Meeting"
        assert ctx.meeting_subject == ""
        assert ctx.is_muted is True
        assert ctx.show_screen_preview is False

    def test_custom_values(self):
        ctx = DashboardContext(
            app_name="Zoom",
            meeting_subject="Weekly Standup",
            is_muted=False,
        )
        assert ctx.app_name == "Zoom"
        assert ctx.meeting_subject == "Weekly Standup"
        assert ctx.is_muted is False


# ---------------------------------------------------------------------------
# DashboardConfig
# ---------------------------------------------------------------------------

class TestDashboardConfig:
    """Test DashboardConfig defaults and round-trip through Config."""

    def test_defaults(self):
        cfg = DashboardConfig()
        assert cfg.enabled is True
        assert cfg.auto_show is True
        assert cfg.auto_hide is True
        assert cfg.opacity == 0.92
        assert cfg.position == "top-right"
        assert cfg.position_x == -1
        assert cfg.position_y == -1
        assert cfg.start_collapsed is False
        assert cfg.show_transcript is True
        assert cfg.show_screen_preview is True
        assert cfg.transcript_font_size == 13
        assert cfg.transcript_lines == 7
        assert cfg.transcript_pool_lines == 2000

    def test_config_has_dashboard(self):
        cfg = Config()
        assert isinstance(cfg.dashboard, DashboardConfig)

    def test_from_dict_empty_gives_defaults(self):
        cfg = Config._from_dict({})
        assert cfg.dashboard.enabled is True
        assert cfg.dashboard.opacity == 0.92

    def test_from_dict_with_dashboard(self):
        data = {
            "dashboard": {
                "enabled": False,
                "opacity": 0.8,
                "position": "bottom-left",
                "start_collapsed": True,
                "transcript_font_size": 15,
                "transcript_pool_lines": 1500,
            }
        }
        cfg = Config._from_dict(data)
        assert cfg.dashboard.enabled is False
        assert cfg.dashboard.opacity == 0.8
        assert cfg.dashboard.position == "bottom-left"
        assert cfg.dashboard.start_collapsed is True
        # Defaults preserved for missing keys
        assert cfg.dashboard.auto_show is True
        assert cfg.dashboard.show_transcript is True
        assert cfg.dashboard.transcript_font_size == 15
        assert cfg.dashboard.transcript_pool_lines == 1500

    def test_unknown_keys_ignored(self):
        data = {
            "dashboard": {
                "enabled": True,
                "unknown_key": "value",
            }
        }
        cfg = Config._from_dict(data)
        assert cfg.dashboard.enabled is True


# ---------------------------------------------------------------------------
# HotkeyConfig.toggle_dashboard
# ---------------------------------------------------------------------------

class TestHotkeyDashboard:
    """Test that the dashboard hotkey is present in HotkeyConfig."""

    def test_default_dashboard_hotkey(self):
        cfg = HotkeyConfig()
        assert cfg.toggle_dashboard == "ctrl+shift+d"

    def test_from_dict_with_dashboard_hotkey(self):
        data = {"hotkey": {"toggle_dashboard": "ctrl+alt+d"}}
        cfg = Config._from_dict(data)
        assert cfg.hotkey.toggle_dashboard == "ctrl+alt+d"


# ---------------------------------------------------------------------------
# GameBarDashboard state management (no Tk, mocking tkinter)
# ---------------------------------------------------------------------------

class TestDashboardStateMethods:
    """Test dashboard state without actually creating a Tk window."""

    def test_initial_state(self):
        dash = GameBarDashboard()
        assert dash.is_visible is False
        assert dash.is_collapsed is False

    def test_position_xy_default(self):
        dash = GameBarDashboard(position_x=100, position_y=200)
        # No window created, falls back to constructor values
        assert dash.position_xy == (100, 200)

    def test_hide_without_show_safe(self):
        dash = GameBarDashboard()
        dash.hide()  # should not raise
        assert dash.is_visible is False

    def test_close_without_show_safe(self):
        dash = GameBarDashboard()
        dash.close()  # should not raise
        assert dash.is_visible is False

    def test_update_methods_safe_without_window(self):
        """All update methods should be no-ops when window is None."""
        dash = GameBarDashboard()
        # None of these should raise
        dash.update_audio_levels(-20, -15, -30, -25)
        dash.update_elapsed(42.5)
        dash.update_mute_state(True)
        dash.update_transcript("Hello world")
        dash.update_screen_preview(None)

    def test_update_screen_preview_safe_without_window(self):
        """update_screen_preview should be a no-op when window is None."""
        import numpy as np
        dash = GameBarDashboard()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        dash.update_screen_preview(frame)  # should not raise

    def test_inline_transcript_font_bump_persists_config(self):
        dash = GameBarDashboard(transcript_font_size=13)
        dash._transcript_label = mock.Mock()
        cfg = Config()

        with mock.patch("meeting_recorder.config.Config.load", return_value=cfg), \
                mock.patch.object(cfg, "save") as save:
            dash._bump_transcript_font(1)

        assert dash._transcript_font_size == 14
        dash._transcript_label.configure.assert_any_call(font=("Segoe UI", 14))
        assert cfg.dashboard.transcript_font_size == 14
        assert cfg.dashboard.transcript_pool_lines == 2000
        save.assert_called_once()

    def test_inline_transcript_tail_keeps_visible_latest_lines(self):
        dash = GameBarDashboard(transcript_font_size=13, transcript_lines=3)
        text = " ".join(f"word{i}" for i in range(80))
        strip = dash._inline_transcript_tail(text)
        assert strip.startswith("...")
        assert "word79" in strip
        assert strip.count("\n") <= 2

    def test_toggle_transcript_window_passes_recording_tail_path(self, tmp_path):
        dash = GameBarDashboard(transcript_pool_lines=1234)
        dash._window = mock.Mock()
        dash._context = DashboardContext(recording_dir=tmp_path)
        instance = mock.Mock()
        instance.is_visible = False

        with mock.patch(
            "meeting_recorder.ui.live_transcript_window.LiveTranscriptWindow",
            return_value=instance,
        ) as cls:
            dash._toggle_transcript_window()

        cls.assert_called_once()
        kwargs = cls.call_args.kwargs
        assert kwargs["transcript_path"] == tmp_path / "live_transcript.txt"
        assert kwargs["transcript_pool_lines"] == 1234
        instance.show.assert_called_once()

    def test_transcript_controls_created_when_tk_available(self):
        tk = pytest.importorskip("tkinter")
        try:
            root = tk.Tk()
        except tk.TclError:
            pytest.skip("no display for Tk")
        root.withdraw()
        try:
            dash = GameBarDashboard(show_transcript=True)
            parent = tk.Frame(root)
            dash._build_expanded(parent)
            assert dash._transcript_expand_btn is not None
            assert "Expand" in dash._transcript_expand_btn.cget("text")
            assert dash._transcript_font_inc_btn is not None
            assert "A+" in dash._transcript_font_inc_btn.cget("text")
            assert dash._transcript_font_dec_btn is not None
            assert "A-" in dash._transcript_font_dec_btn.cget("text")
        finally:
            root.destroy()


# ---------------------------------------------------------------------------
# Mute button affordances (manual toggle + resume auto-sync)
# ---------------------------------------------------------------------------

class TestMuteButtonAffordances:
    """Left-click toggles mute; right-click resumes auto-sync."""

    def test_handle_mute_toggle_calls_callback(self):
        called = []
        dash = GameBarDashboard(on_toggle_mute=lambda: called.append(True))
        dash._handle_mute_toggle()
        assert called == [True]

    def test_handle_resume_auto_sync_calls_callback(self):
        called = []
        dash = GameBarDashboard(on_resume_auto_sync=lambda: called.append(True))
        dash._handle_resume_auto_sync()
        assert called == [True]

    def test_resume_does_not_call_toggle(self):
        """Right-click must not also fire the manual toggle."""
        events = []
        dash = GameBarDashboard(
            on_toggle_mute=lambda: events.append("toggle"),
            on_resume_auto_sync=lambda: events.append("resume"),
        )
        dash._handle_resume_auto_sync()
        assert events == ["resume"]

    def test_handle_resume_safe_without_callback(self):
        dash = GameBarDashboard()
        dash._handle_resume_auto_sync()  # should not raise

    def test_right_click_returns_break(self):
        """Stops the window-level context-menu binding from also firing."""
        dash = GameBarDashboard(on_resume_auto_sync=lambda: None)
        assert dash._on_mute_right_click(None) == "break"

    def test_tooltip_methods_safe_without_window(self):
        dash = GameBarDashboard()
        dash._show_mute_tooltip()  # no window -> no-op
        dash._hide_mute_tooltip()
        assert dash._mute_tooltip is None


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

class TestFormatElapsed:
    """Test the elapsed time formatter."""

    def test_zero(self):
        assert _format_elapsed(0) == "00:00:00"

    def test_seconds_only(self):
        assert _format_elapsed(45) == "00:00:45"

    def test_minutes_and_seconds(self):
        assert _format_elapsed(754) == "00:12:34"

    def test_hours(self):
        assert _format_elapsed(3661) == "01:01:01"

    def test_fractional_truncates(self):
        assert _format_elapsed(59.9) == "00:00:59"


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

class TestLayoutConstants:
    """Verify layout dimensions are reasonable."""

    def test_expanded_dimensions(self):
        assert EXPANDED_WIDTH == 380
        assert EXPANDED_HEIGHT == 280

    def test_collapsed_dimensions(self):
        assert COLLAPSED_WIDTH == 380
        assert COLLAPSED_HEIGHT == 44

    def test_collapsed_smaller_than_expanded(self):
        assert COLLAPSED_HEIGHT < EXPANDED_HEIGHT

    def test_preview_height_reasonable(self):
        assert 100 < PREVIEW_HEIGHT < 200
