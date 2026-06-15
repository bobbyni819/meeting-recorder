"""Tests for the per-machine performance profile."""

from __future__ import annotations

from unittest import mock

import pytest

from meeting_recorder.performance import (
    HardwareInfo,
    _auto_tier_name,
    _TIER_PRESETS,
    resolve_tier,
)


def _hw(cuda=False, vram=0.0, cores=4, ram=16.0, name=""):
    return HardwareInfo(
        has_cuda=cuda, gpu_name=name, vram_gb=vram, cpu_cores=cores, ram_gb=ram,
    )


class TestAutoTierName:
    def test_rtx_5060_is_full(self):
        # RTX 5060: CUDA, 8GB, modern multi-core
        assert _auto_tier_name(_hw(cuda=True, vram=8.0, cores=16)) == "full"

    def test_rtx_3060_is_full(self):
        # RTX 3060 (12GB) runs large-v3 fine on GPU
        assert _auto_tier_name(_hw(cuda=True, vram=12.0, cores=8)) == "full"

    def test_small_gpu_is_balanced(self):
        # Tiny GPU (<6GB) -> can't comfortably hold large-v3
        assert _auto_tier_name(_hw(cuda=True, vram=4.0, cores=16)) == "balanced"

    def test_many_core_cpu_only_is_balanced(self):
        assert _auto_tier_name(_hw(cuda=False, cores=12)) == "balanced"

    def test_weak_machine_is_light(self):
        assert _auto_tier_name(_hw(cuda=False, cores=4)) == "light"


class TestResolveTier:
    def test_explicit_profiles_bypass_detection(self):
        for name in ("light", "balanced", "full"):
            tier = resolve_tier(name, hw=_hw())
            assert tier.name == name

    def test_explicit_is_case_insensitive(self):
        assert resolve_tier("FULL", hw=_hw()).name == "full"

    def test_auto_uses_hardware(self):
        tier = resolve_tier("auto", hw=_hw(cuda=True, vram=12.0, cores=16))
        assert tier.name == "full"

    def test_unknown_falls_back_to_auto(self):
        tier = resolve_tier("turbo", hw=_hw(cuda=False, cores=2))
        assert tier.name == "light"

    def test_empty_is_auto(self):
        tier = resolve_tier("", hw=_hw(cuda=False, cores=2))
        assert tier.name == "light"


class TestTierContracts:
    def test_light_disables_live_transcription(self):
        """The light tier must not run live transcription (CPU protection)."""
        assert _TIER_PRESETS["light"].live_transcription is False
        assert _TIER_PRESETS["light"].live_transcript_mic is False

    def test_light_uses_cv2_video(self):
        """Light skips software H.264 to avoid burning a weak CPU."""
        assert _TIER_PRESETS["light"].video_encoder == "cv2"

    def test_light_fallback_model_is_small(self):
        assert _TIER_PRESETS["light"].fallback_model_size == "small"

    def test_full_uses_large_model_and_nvenc(self):
        assert _TIER_PRESETS["full"].fallback_model_size == "large-v3"
        assert _TIER_PRESETS["full"].video_encoder == "nvenc"

    def test_live_model_size_by_tier(self):
        assert _TIER_PRESETS["light"].live_model_size == "tiny"
        assert _TIER_PRESETS["balanced"].live_model_size == "small"
        assert _TIER_PRESETS["full"].live_model_size == "small"

    def test_cuda_resolution_preserves_live_model_size(self):
        tier = resolve_tier("full", hw=_hw(cuda=True, vram=12.0, cores=16))
        assert tier.live_device == "cuda"
        assert tier.live_compute_type == "float16"
        assert tier.live_model_size == "small"

    def test_insights_always_enabled(self):
        """Concept extraction is sub-millisecond; on for every tier."""
        assert all(t.live_insights for t in _TIER_PRESETS.values())


class TestPipelineModelCap:
    """The Gemini->local fallback caps model size by tier."""

    def _pipeline(self, profile):
        from meeting_recorder.config import Config
        from meeting_recorder.transcription.pipeline import TranscriptionPipeline

        cfg = Config()
        cfg.performance.profile = profile
        cfg.transcription.model_size = "large-v3"
        return TranscriptionPipeline(cfg)

    def test_light_caps_to_small(self):
        p = self._pipeline("light")
        assert p._fallback_model_size("large-v3", "cpu") == "small"

    def test_full_keeps_large(self):
        p = self._pipeline("full")
        assert p._fallback_model_size("large-v3", "cuda") == "large-v3"

    def test_never_upgrades(self):
        """A user who configured 'tiny' is never bumped up by the tier."""
        p = self._pipeline("full")
        assert p._fallback_model_size("tiny", "cuda") == "tiny"


class TestConfigIsLocalOnly:
    def test_performance_profile_in_local_only_fields(self):
        """The profile must NOT sync via git (per-machine hardware)."""
        from meeting_recorder.config import _LOCAL_ONLY_FIELDS

        assert "performance" in _LOCAL_ONLY_FIELDS
        assert "profile" in _LOCAL_ONLY_FIELDS["performance"]

    def test_profile_stripped_from_repo_config_on_save(self):
        from meeting_recorder.config import _split_secrets

        data = {"performance": {"profile": "light"}}
        secrets = _split_secrets(data)
        # Non-default value moves to secrets, repo keeps the "auto" default
        assert secrets["performance"]["profile"] == "light"
        assert data["performance"]["profile"] == "auto"
