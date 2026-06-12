"""Tests for UIA-based mute detection (mocked uiautomation tree)."""

from __future__ import annotations

import sys
import time
import types
from types import SimpleNamespace
from unittest import mock

import pytest

from meeting_recorder.audio import uia_mute_detector
from meeting_recorder.audio.uia_mute_detector import (
    classify_mute_button_name,
    detect_mute_state,
)


# ---------------------------------------------------------------------------
# Fake UIA tree helpers
# ---------------------------------------------------------------------------

class FakeControl:
    """Minimal stand-in for a uiautomation Control."""

    def __init__(
        self,
        name: str = "",
        control_type: str = "PaneControl",
        children=None,
        pid: int = 0,
        class_name: str = "",
        toggle_state=None,
    ):
        self.Name = name
        self.ControlTypeName = control_type
        self.ProcessId = pid
        self.ClassName = class_name
        self._children = list(children or [])
        self._toggle_state = toggle_state

    def GetChildren(self):
        return list(self._children)

    def GetPattern(self, pattern_id):
        if self._toggle_state is None:
            return None
        return SimpleNamespace(ToggleState=self._toggle_state)


def _fake_uia_module(root: FakeControl) -> types.ModuleType:
    mod = types.ModuleType("uiautomation")
    mod.GetRootControl = lambda: root
    mod.PatternId = SimpleNamespace(TogglePattern=10015)
    return mod


def _detect_with_tree(root: FakeControl, pids: set[int], **kwargs):
    """Run detect_mute_state against a fake UIA tree."""
    fake = _fake_uia_module(root)
    with mock.patch.dict(sys.modules, {"uiautomation": fake}), \
            mock.patch.object(uia_mute_detector, "_ensure_com_initialized"):
        return detect_mute_state(pids, **kwargs)


def _window(pid: int, children, name="Zoom Meeting", class_name="ZPContentViewWndClass"):
    return FakeControl(
        name=name, control_type="WindowControl",
        children=children, pid=pid, class_name=class_name,
    )


# ---------------------------------------------------------------------------
# classify_mute_button_name decision table
# ---------------------------------------------------------------------------

class TestClassifyMuteButtonName:
    """Name -> mute-state decision table."""

    @pytest.mark.parametrize("name,expected", [
        # Zoom action labels (button offers the opposite action)
        ("Mute my microphone", False),
        ("Unmute my microphone", True),
        ("mute audio", False),
        ("Unmute audio", True),
        ("MUTE MY MICROPHONE", False),  # case-insensitive
        # Zoom state labels
        ("currently unmuted", False),
        ("currently muted", True),
        ("Mute my audio, currently unmuted", False),
        # Teams labels
        ("Mute mic", False),
        ("Unmute mic", True),
        ("Mute mic (Ctrl+Shift+M)", False),
        ("Unmute mic (Ctrl+Shift+M)", True),
        ("Mic off", True),
        ("Mic is off", True),
        ("Microphone off", True),
        ("Mic on", False),
        ("Microphone is on", False),
        # Bare action labels
        ("Mute", False),
        ("Unmute", True),
        (" mute ", False),
    ])
    def test_conclusive_names(self, name, expected):
        assert classify_mute_button_name(name) is expected

    @pytest.mark.parametrize("name", [
        None,
        "",
        "Mute All",            # host bulk action, no mic context
        "Mute John Doe",       # per-participant action
        "Volume",
        "Start Video",
        "Leave Meeting",
        "Mic",                 # bare toggle: needs TogglePattern instead
        "Computer audio",      # audio context but no mute keyword
    ])
    def test_inconclusive_names(self, name):
        assert classify_mute_button_name(name) is None


# ---------------------------------------------------------------------------
# detect_mute_state against fake trees
# ---------------------------------------------------------------------------

class TestDetectMuteState:
    """End-to-end detection on mocked UIA trees."""

    def test_zoom_unmuted(self):
        button = FakeControl("Mute my microphone", "ButtonControl")
        toolbar = FakeControl("toolbar", children=[button])
        root = FakeControl(children=[_window(100, [toolbar])])
        assert _detect_with_tree(root, {100}) is False

    def test_zoom_muted(self):
        button = FakeControl("Unmute my microphone", "ButtonControl")
        toolbar = FakeControl("toolbar", children=[button])
        root = FakeControl(children=[_window(100, [toolbar])])
        assert _detect_with_tree(root, {100}) is True

    def test_teams_mic_toggle_on_is_unmuted(self):
        button = FakeControl("Mic", "ButtonControl", toggle_state=1)
        root = FakeControl(children=[_window(200, [button], name="Meeting", class_name="")])
        assert _detect_with_tree(root, {200}) is False

    def test_teams_mic_toggle_off_is_muted(self):
        button = FakeControl("Mic", "ButtonControl", toggle_state=0)
        root = FakeControl(children=[_window(200, [button], name="Meeting", class_name="")])
        assert _detect_with_tree(root, {200}) is True

    def test_teams_mic_toggle_indeterminate_is_none(self):
        button = FakeControl("Mic", "ButtonControl", toggle_state=2)
        root = FakeControl(children=[_window(200, [button], name="Meeting", class_name="")])
        assert _detect_with_tree(root, {200}) is None

    def test_non_button_control_ignored(self):
        text = FakeControl("Unmute my microphone", "TextControl")
        root = FakeControl(children=[_window(100, [text])])
        assert _detect_with_tree(root, {100}) is None

    def test_checkbox_control_accepted(self):
        cb = FakeControl("Mute mic", "CheckBoxControl")
        root = FakeControl(children=[_window(100, [cb])])
        assert _detect_with_tree(root, {100}) is False

    def test_window_of_other_pid_skipped(self):
        button = FakeControl("Unmute my microphone", "ButtonControl")
        root = FakeControl(children=[_window(999, [button])])
        assert _detect_with_tree(root, {100}) is None

    def test_no_windows_returns_none(self):
        root = FakeControl(children=[])
        assert _detect_with_tree(root, {100}) is None

    def test_empty_pids_returns_none(self):
        root = FakeControl(children=[_window(100, [])])
        assert _detect_with_tree(root, set()) is None

    def test_depth_limit_excludes_deep_button(self):
        button = FakeControl("Unmute my microphone", "ButtonControl")
        node = button
        for _ in range(5):
            node = FakeControl("nested", children=[node])
        root = FakeControl(children=[_window(100, [node])])
        # Button sits at depth 7 from the window; limit of 3 misses it.
        assert _detect_with_tree(root, {100}, max_depth=3) is None
        assert _detect_with_tree(root, {100}, max_depth=10) is True

    def test_budget_exhausted_returns_none(self):
        button = FakeControl("Unmute my microphone", "ButtonControl")
        root = FakeControl(children=[_window(100, [button])])
        assert _detect_with_tree(root, {100}, budget_seconds=-1.0) is None

    def test_meeting_window_searched_before_others(self):
        """Zoom in-meeting window outranks an unrelated settings window."""
        settings_btn = FakeControl("Mute my microphone", "ButtonControl")
        meeting_btn = FakeControl("Unmute my microphone", "ButtonControl")
        settings_win = _window(100, [settings_btn], name="Settings", class_name="")
        meeting_win = _window(100, [meeting_btn])
        root = FakeControl(children=[settings_win, meeting_win])
        # The ZPContentView window's (muted) answer wins.
        assert _detect_with_tree(root, {100}) is True

    def test_first_conclusive_button_wins(self):
        mute_btn = FakeControl("Mute my microphone", "ButtonControl")
        video_btn = FakeControl("Start Video", "ButtonControl")
        root = FakeControl(children=[_window(100, [video_btn, mute_btn])])
        assert _detect_with_tree(root, {100}) is False

    def test_root_enumeration_failure_returns_none(self):
        mod = types.ModuleType("uiautomation")

        def boom():
            raise RuntimeError("UIA unavailable")

        mod.GetRootControl = boom
        mod.PatternId = SimpleNamespace(TogglePattern=10015)
        with mock.patch.dict(sys.modules, {"uiautomation": mod}), \
                mock.patch.object(uia_mute_detector, "_ensure_com_initialized"):
            assert detect_mute_state({100}) is None

    def test_uiautomation_import_failure_returns_none(self):
        with mock.patch.dict(sys.modules, {"uiautomation": None}):
            assert detect_mute_state({100}) is None

    def test_control_property_errors_skipped(self):
        class BrokenControl:
            """Control whose property reads raise (stale COM element)."""

            Name = "x"

            @property
            def ControlTypeName(self):
                raise RuntimeError("COM error")

            def GetChildren(self):
                return []

        good = FakeControl("Unmute my microphone", "ButtonControl")
        root = FakeControl(children=[_window(100, [BrokenControl(), good])])
        assert _detect_with_tree(root, {100}) is True

    def test_runs_within_budget(self):
        """A wide tree is cut off by the time budget, not hung."""
        wide = [FakeControl(f"btn{i}", "ButtonControl") for i in range(2000)]
        root = FakeControl(children=[_window(100, wide)])
        start = time.monotonic()
        result = _detect_with_tree(root, {100}, budget_seconds=0.05)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 1.0
