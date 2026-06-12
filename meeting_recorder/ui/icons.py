"""Programmatic app / system-tray icons.

Icons are drawn as modern rounded-square ("squircle") glyphs with a vertical
gradient and a clean microphone mark, rendered at 4x and downsampled for
smooth anti-aliased edges. The same art is used for the taskbar/app icon and
the tray, so the app no longer shows the generic Python icon.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

# Supersampling factor for smooth edges.
_SS = 4

# Palette (top, bottom) gradient per state.
_IDLE_TOP, _IDLE_BOTTOM = (99, 102, 241), (67, 56, 202)       # indigo
_REC_TOP, _REC_BOTTOM = (244, 63, 94), (190, 18, 60)          # rose/red
_PROC_TOP, _PROC_BOTTOM = (56, 189, 248), (37, 99, 235)       # sky/blue
_ERR_TOP, _ERR_BOTTOM = (251, 191, 36), (217, 119, 6)         # amber


def _vertical_gradient(size: int, top, bottom) -> Image.Image:
    """A vertical top->bottom gradient image."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel((0, y), tuple(
            int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)
        ))
    return grad.resize((size, size))


def _squircle_base(size: int, top, bottom) -> Image.Image:
    """Rounded-square gradient tile with a subtle inner highlight."""
    s = size * _SS
    radius = int(s * 0.24)
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)

    base = _vertical_gradient(s, top, bottom).convert("RGBA")
    # Soft top highlight for a little depth.
    hi = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    hd.rounded_rectangle(
        [int(s * 0.08), int(s * 0.06), int(s * 0.92), int(s * 0.5)],
        radius=int(s * 0.18), fill=(255, 255, 255, 38),
    )
    base = Image.alpha_composite(base, hi)
    base.putalpha(mask)
    return base.resize((size, size), Image.LANCZOS)


def _draw_mic(img: Image.Image, color=(255, 255, 255, 255)) -> None:
    """Draw a clean centered microphone glyph onto an RGBA image."""
    s = img.size[0] * _SS
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = s // 2
    # Slim, tall capsule so it reads as a microphone (not an egg).
    body_w = int(s * 0.135)
    top = int(s * 0.22)
    bot = int(s * 0.52)
    d.rounded_rectangle(
        [cx - body_w, top, cx + body_w, bot], radius=body_w, fill=color,
    )
    # Cradle arc hugging the lower half of the capsule.
    lw = max(int(s * 0.040), 2)
    arc_w = int(s * 0.215)
    arc_top = int(s * 0.34)
    arc_bot = int(s * 0.60)
    d.arc(
        [cx - arc_w, arc_top, cx + arc_w, arc_bot],
        start=15, end=165, fill=color, width=lw,
    )
    # Stand + base
    d.line([cx, arc_bot - lw // 2, cx, int(s * 0.74)], fill=color, width=lw)
    base_w = int(s * 0.15)
    d.line(
        [cx - base_w, int(s * 0.76), cx + base_w, int(s * 0.76)],
        fill=color, width=lw,
    )
    img.alpha_composite(layer.resize(img.size, Image.LANCZOS))


def create_idle_icon(size: int = 64) -> Image.Image:
    """Idle: indigo squircle with a white microphone."""
    img = _squircle_base(size, _IDLE_TOP, _IDLE_BOTTOM)
    _draw_mic(img)
    return img


def create_recording_icon(size: int = 64) -> Image.Image:
    """Recording: red squircle with a microphone and a record dot."""
    img = _squircle_base(size, _REC_TOP, _REC_BOTTOM)
    _draw_mic(img)
    # Bright record dot, lower-right.
    d = ImageDraw.Draw(img)
    r = max(int(size * 0.13), 4)
    cx, cy = int(size * 0.74), int(size * 0.74)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
    d.ellipse(
        [cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2],
        fill=(244, 63, 94, 255),
    )
    return img


def create_processing_icon(size: int = 64) -> Image.Image:
    """Processing: blue squircle with three dots."""
    img = _squircle_base(size, _PROC_TOP, _PROC_BOTTOM)
    d = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    dot_r = max(size // 12, 2)
    spacing = size // 5
    for dx in (-spacing, 0, spacing):
        d.ellipse(
            [cx + dx - dot_r, cy - dot_r, cx + dx + dot_r, cy + dot_r],
            fill=(255, 255, 255, 240),
        )
    return img


def create_error_icon(size: int = 64) -> Image.Image:
    """Error: amber squircle with an exclamation mark."""
    img = _squircle_base(size, _ERR_TOP, _ERR_BOTTOM)
    d = ImageDraw.Draw(img)
    cx = size // 2
    bar_w = max(size // 14, 2)
    d.rounded_rectangle(
        [cx - bar_w, int(size * 0.28), cx + bar_w, int(size * 0.60)],
        radius=bar_w, fill=(255, 255, 255, 250),
    )
    dot_y = int(size * 0.72)
    d.ellipse(
        [cx - bar_w, dot_y - bar_w, cx + bar_w, dot_y + bar_w],
        fill=(255, 255, 255, 250),
    )
    return img


@lru_cache(maxsize=1)
def app_icon_path() -> str:
    """Write (once) a multi-size .ico for the app/taskbar and return its path.

    Cached in the user config dir so Tk's iconbitmap (which needs a file) gets
    a crisp multi-resolution icon. Falls back to a temp path if the config dir
    is unavailable. Returns "" if writing fails.
    """
    try:
        target_dir = Path.home() / ".meeting_recorder"
        target_dir.mkdir(parents=True, exist_ok=True)
        ico = target_dir / "app_icon.ico"
        if not ico.exists():
            sizes = [16, 24, 32, 48, 64, 128, 256]
            base = create_idle_icon(256)
            base.save(ico, format="ICO", sizes=[(s, s) for s in sizes])
        return str(ico)
    except Exception:
        return ""
