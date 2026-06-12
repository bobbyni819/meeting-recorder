"""Per-machine performance profile: gate heavy features by hardware tier.

The config file (config.toml) syncs across machines via git, so a feature
flag set there would force the same choice on every machine. Performance
settings are therefore LOCAL-ONLY (stored in secrets.toml alongside the
mic device) and default to "auto", which detects the machine's GPU, CPU
cores, and RAM at startup and picks a tier.

Tiers (least → most capable):
    light    — no usable GPU or few cores. Minimise live/CPU work.
    balanced — a capable GPU (e.g. RTX 3060) or many cores.
    full     — a strong GPU (>=10GB VRAM) and plenty of cores.

Every gated feature ALSO honours its own explicit config flag; the profile
only decides defaults for features the user left on "auto". A profile must
never make a machine do MORE than its hardware comfortably allows, and the
"light" tier must cost no more than the app does today.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)

TIERS = ("light", "balanced", "full")


@dataclass(frozen=True)
class HardwareInfo:
    has_cuda: bool
    gpu_name: str
    vram_gb: float
    cpu_cores: int
    ram_gb: float


@dataclass(frozen=True)
class PerformanceTier:
    """Resolved capability decisions for the current machine."""

    name: str
    # Live transcription preview at all (tiny model).
    live_transcription: bool
    # Feed the user's mic as a second live source.
    live_transcript_mic: bool
    # Local Whisper fallback model size when Gemini is unavailable.
    fallback_model_size: str
    # Preferred video encoder family: "nvenc" | "software" | "cv2".
    video_encoder: str
    # Run live concept extraction (keyword/topic). Always cheap.
    live_insights: bool
    # Device for the live transcription model. "cuda" is ~10x faster than
    # "cpu" for the tiny model and the GPU is idle during a meeting (cloud
    # transcription; the local fallback only runs post-meeting). Resolved
    # from real hardware in resolve_tier — "cuda" only when CUDA exists.
    live_device: str = "cpu"
    # Compute type paired with the device (float16 on GPU, int8 on CPU).
    live_compute_type: str = "int8"
    # Seconds between live transcription passes. Lower = less lag; safe to
    # shorten on GPU where inference is ~100ms.
    live_interval: float = 3.0


# Static tier definitions. "auto" resolves to one of these.
_TIER_PRESETS: dict[str, PerformanceTier] = {
    "light": PerformanceTier(
        name="light",
        live_transcription=False,      # protect a weak CPU
        live_transcript_mic=False,
        fallback_model_size="small",   # large-v3 on CPU could take hours
        video_encoder="cv2",           # no software libx264 CPU burn
        live_insights=True,            # sub-millisecond, always fine
    ),
    "balanced": PerformanceTier(
        name="balanced",
        live_transcription=True,
        live_transcript_mic=True,
        fallback_model_size="medium",
        video_encoder="nvenc",         # GPU present; falls back if probe fails
        live_insights=True,
    ),
    "full": PerformanceTier(
        name="full",
        live_transcription=True,
        live_transcript_mic=True,
        fallback_model_size="large-v3",
        video_encoder="nvenc",
        live_insights=True,
    ),
}


@lru_cache(maxsize=1)
def detect_hardware() -> HardwareInfo:
    """Detect GPU, CPU cores, and RAM. Cached for the process lifetime."""
    has_cuda = False
    gpu_name = ""
    vram_gb = 0.0
    try:
        import torch

        if torch.cuda.is_available():
            has_cuda = True
            gpu_name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    except Exception:
        logger.debug("GPU detection failed", exc_info=True)

    cpu_cores = 0
    ram_gb = 0.0
    try:
        import os

        cpu_cores = os.cpu_count() or 0
    except Exception:
        pass
    try:
        import psutil

        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        logger.debug("RAM detection failed", exc_info=True)

    info = HardwareInfo(
        has_cuda=has_cuda, gpu_name=gpu_name, vram_gb=vram_gb,
        cpu_cores=cpu_cores, ram_gb=ram_gb,
    )
    logger.info(
        "Hardware: GPU=%s (%.1fGB), %d cores, %.1fGB RAM",
        gpu_name or "none", vram_gb, cpu_cores, ram_gb,
    )
    return info


def _auto_tier_name(hw: HardwareInfo) -> str:
    """Pick a tier name from detected hardware.

    A dedicated GPU with >=6GB runs large-v3 Whisper comfortably (true for
    both an RTX 5060 8GB and an RTX 3060 12GB), so either lands on "full".
    A weak/small GPU or a beefy CPU-only box is "balanced"; everything else
    is "light". The user can always pin a tier with performance.profile —
    that manual override is the real lever; auto just sets a safe default.
    """
    if hw.has_cuda and hw.vram_gb >= 6.0 and hw.cpu_cores >= 6:
        return "full"
    if hw.has_cuda or hw.cpu_cores >= 8:
        return "balanced"
    return "light"


def resolve_tier(profile: str, hw: HardwareInfo | None = None) -> PerformanceTier:
    """Resolve a configured profile string to a concrete tier.

    The live-transcription device is filled in from real hardware (CUDA only
    when present), so an explicit profile still gets GPU acceleration on a
    GPU machine and safely falls to CPU on one without.

    Args:
        profile: "auto", "light", "balanced", or "full" (case-insensitive).
        hw: Detected hardware (detected on demand if omitted).
    """
    from dataclasses import replace

    profile = (profile or "auto").strip().lower()
    hw = hw or detect_hardware()
    if profile in _TIER_PRESETS:
        preset = _TIER_PRESETS[profile]
    else:
        if profile != "auto":
            logger.warning(
                "Unknown performance.profile %r — using auto detection", profile,
            )
        name = _auto_tier_name(hw)
        logger.info("Performance profile 'auto' resolved to '%s' tier", name)
        preset = _TIER_PRESETS[name]

    # Live transcription on the GPU when one exists and the tier runs live
    # preview at all; faster inference + a shorter poll interval cut the lag.
    if hw.has_cuda and preset.live_transcription:
        return replace(
            preset, live_device="cuda", live_compute_type="float16",
            live_interval=1.5,
        )
    return preset
