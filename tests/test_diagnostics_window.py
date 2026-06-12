"""Tests for the diagnostics window and structured check results."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from meeting_recorder.diagnose import (
    CheckCategory,
    CheckResult,
    _is_rate_limit_error,
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
        assert len(results) == 9  # 9 check categories
        for cat in results:
            assert isinstance(cat, CheckCategory)
            assert cat.name
            assert isinstance(cat.results, list)

    def test_category_names(self):
        results = run_diagnostics_structured()
        names = [c.name for c in results]
        assert "Configuration" in names
        assert "Secrets" in names
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


class TestRateLimitClassification:
    def test_429_resource_exhausted_is_rate_limit(self):
        e = Exception("429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}")
        assert _is_rate_limit_error(e)

    def test_resource_exhausted_alone_is_rate_limit(self):
        assert _is_rate_limit_error(Exception("RESOURCE_EXHAUSTED: quota"))

    def test_invalid_key_is_not_rate_limit(self):
        assert not _is_rate_limit_error(Exception("400 API key not valid"))

    def test_network_error_is_not_rate_limit(self):
        assert not _is_rate_limit_error(Exception("Connection refused"))

    def test_gemini_429_reported_as_warn_not_fail(self):
        """Free-tier 429 means the key is valid — must not fail diagnostics."""
        from meeting_recorder.config import Config
        from meeting_recorder.diagnose import _check_api_structured

        config = Config()
        config.transcription.gemini_api_key = "test-key"
        config.transcription.openai_api_key = ""
        config.summary.provider = "gemini"
        config.summary.api_key = ""

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception(
            "429 RESOURCE_EXHAUSTED. Quota exceeded for free tier."
        )
        with patch.object(Config, "load", return_value=config), \
             patch("google.genai.Client", return_value=mock_client):
            cat = _check_api_structured()

        gemini_results = [r for r in cat.results if "Gemini" in r.message]
        assert gemini_results
        assert gemini_results[0].status == "warn"
        assert "rate-limited" in gemini_results[0].message


class TestGpuCheckRegression:
    def test_gpu_check_uses_valid_device_properties_attr(self):
        """Regression: total_mem is not a torch attribute (total_memory is)."""
        pytest.importorskip("torch")
        from meeting_recorder.diagnose import _check_gpu_structured

        cat = _check_gpu_structured()
        # With torch installed the only valid outcomes are ok (CUDA) or
        # warn (CPU-only) — an AttributeError would surface as fail.
        assert cat.status in ("ok", "warn")
        assert not any("total_mem" in r.message for r in cat.results)


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
