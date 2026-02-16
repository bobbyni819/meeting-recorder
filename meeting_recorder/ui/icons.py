"""Programmatic icon generation for system tray states."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


def create_idle_icon(size: int = 64) -> Image.Image:
    """Create the idle state icon (gray microphone)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(80, 80, 80, 230),
    )

    # Microphone shape
    cx, cy = size // 2, size // 2
    mic_w, mic_h = size // 5, size // 3

    # Mic body (rounded rectangle approximation)
    draw.rounded_rectangle(
        [cx - mic_w, cy - mic_h, cx + mic_w, cy + mic_w],
        radius=mic_w,
        fill=(200, 200, 200),
    )

    # Mic stand
    stand_w = 2
    draw.line(
        [cx, cy + mic_w, cx, cy + mic_w + size // 8],
        fill=(200, 200, 200),
        width=stand_w,
    )
    # Base
    base_w = size // 5
    draw.line(
        [cx - base_w, cy + mic_w + size // 8, cx + base_w, cy + mic_w + size // 8],
        fill=(200, 200, 200),
        width=stand_w,
    )

    return img


def create_recording_icon(size: int = 64) -> Image.Image:
    """Create the recording state icon (red circle with pulse)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Red background circle
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(220, 40, 40, 230),
    )

    # White inner circle (record symbol)
    inner_margin = size // 4
    draw.ellipse(
        [inner_margin, inner_margin, size - inner_margin, size - inner_margin],
        fill=(255, 255, 255, 240),
    )

    return img


def create_processing_icon(size: int = 64) -> Image.Image:
    """Create the processing state icon (blue gear/spinner)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Blue background circle
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(40, 100, 220, 230),
    )

    # Hourglass/processing symbol (three dots)
    cx, cy = size // 2, size // 2
    dot_r = size // 10
    spacing = size // 5
    for dx in [-spacing, 0, spacing]:
        draw.ellipse(
            [cx + dx - dot_r, cy - dot_r, cx + dx + dot_r, cy + dot_r],
            fill=(255, 255, 255, 240),
        )

    return img


def create_error_icon(size: int = 64) -> Image.Image:
    """Create the error state icon (yellow warning)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Yellow background circle
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(220, 180, 40, 230),
    )

    # Exclamation mark
    cx = size // 2
    bar_w = size // 10
    draw.rectangle(
        [cx - bar_w, size // 4, cx + bar_w, size // 2 + size // 8],
        fill=(60, 60, 60),
    )
    dot_y = size // 2 + size // 4
    draw.ellipse(
        [cx - bar_w - 1, dot_y - bar_w, cx + bar_w + 1, dot_y + bar_w],
        fill=(60, 60, 60),
    )

    return img
