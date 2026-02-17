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
    GREEN_VU,
    YELLOW_VU,
    RED_VU,
    EXPANDED_WIDTH,
    EXPANDED_HEIGHT,
    COLLAPSED_WIDTH,
    COLLAPSED_HEIGHT,
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
