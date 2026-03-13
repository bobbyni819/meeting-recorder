"""Tests for the diagnostics window and structured check results."""

from __future__ import annotations

import pytest

from meeting_recorder.diagnose import (
    CheckCategory,
    CheckResult,
    run_diagnostics_structured,
)


class TestCheckResult:
    def test_ok(self):
        r = CheckResult("ok", "All good")
        assert r.status == "ok"
        assert r.message == "All good"

    def test_warn(self):
        r = CheckResult("warn", "Not ideal")
        assert r.status == "warn"

    def test_fail(self):
        r = CheckResult("fail", "Broken")
        assert r.status == "fail"


class TestCheckCategory:
    def test_status_all_ok(self):
        cat = CheckCategory(name="Test", results=[
            CheckResult("ok", "a"),
            CheckResult("ok", "b"),
        ])
        assert cat.status == "ok"

    def test_status_with_warn(self):
        cat = CheckCategory(name="Test", results=[
            CheckResult("ok", "a"),
            CheckResult("warn", "b"),
        ])
        assert cat.status == "warn"

    def test_status_with_fail(self):
        cat = CheckCategory(name="Test", results=[
            CheckResult("ok", "a"),
            CheckResult("warn", "b"),
            CheckResult("fail", "c"),
        ])
        assert cat.status == "fail"

    def test_status_empty(self):
        cat = CheckCategory(name="Empty")
        assert cat.status == "ok"

    def test_fail_takes_priority_over_warn(self):
        cat = CheckCategory(name="Test", results=[
            CheckResult("warn", "w"),
            CheckResult("fail", "f"),
        ])
        assert cat.status == "fail"

    def test_name(self):
        cat = CheckCategory(name="GPU / CUDA")
        assert cat.name == "GPU / CUDA"


class TestRunDiagnosticsStructured:
    def test_returns_list_of_categories(self):
        """Structured diagnostics returns a list of CheckCategory objects."""
        results = run_diagnostics_structured()
        assert isinstance(results, list)
        assert len(results) == 8  # 8 check categories
        for cat in results:
            assert isinstance(cat, CheckCategory)
            assert cat.name
            assert isinstance(cat.results, list)

    def test_category_names(self):
        results = run_diagnostics_structured()
        names = [c.name for c in results]
        assert "Configuration" in names
        assert "GPU / CUDA" in names
        assert "Voice Activity Detection" in names
        assert "Meeting Processes" in names
        assert "API Connectivity" in names

    def test_all_results_have_status(self):
        results = run_diagnostics_structured()
        for cat in results:
            for r in cat.results:
                assert r.status in ("ok", "warn", "fail")
                assert r.message  # non-empty

    def test_each_category_has_at_least_one_result(self):
        results = run_diagnostics_structured()
        for cat in results:
            assert len(cat.results) >= 1, f"{cat.name} has no results"


class TestDiagnosticsWindowLifecycle:
    def test_construction(self):
        from meeting_recorder.ui.diagnostics_window import DiagnosticsWindow
        dw = DiagnosticsWindow()
        assert dw._window is None

    def test_close_resets(self):
        from meeting_recorder.ui.diagnostics_window import DiagnosticsWindow
        dw = DiagnosticsWindow()
        dw.close()
        assert dw._window is None

    def test_status_constants(self):
        from meeting_recorder.ui.diagnostics_window import STATUS_ICON, STATUS_COLOR
        assert "ok" in STATUS_ICON
        assert "warn" in STATUS_ICON
        assert "fail" in STATUS_ICON
        assert "ok" in STATUS_COLOR
        assert "warn" in STATUS_COLOR
        assert "fail" in STATUS_COLOR
