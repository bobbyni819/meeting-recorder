"""Shared UI color theme constants and utility functions for all Tkinter windows."""

from meeting_recorder.audio.level_monitor import MIN_DB

# Background colors
BG_COLOR = "#1a1a2e"
BG_HEADER = "#16213e"
BG_PANEL = "#0f1a2e"
BG_CONTROLS = "#0f3460"
BG_CARD = "#1e2a4a"
BG_CARD_HOVER = "#243352"

# Text colors
TEXT_COLOR = "#e0e0e0"
TEXT_DIM = "#888888"
TEXT_BRIGHT = "#ffffff"

# Status colors
RED_DOT = "#e74c3c"
RED_DOT_OFF = "#5a2020"
GREEN = "#2ecc71"
GREEN_DARK = "#1a8a4a"
AMBER = "#f39c12"
BLUE_ACCENT = "#3498db"
BLUE_DARK = "#2471a3"

# VU meter colors
GREEN_VU = "#2ecc71"
YELLOW_VU = "#f1c40f"
RED_VU = "#e74c3c"
VU_BG = "#2c2c3e"

# Button colors
BUTTON_BG = "#0f3460"
BUTTON_HOVER = "#1a5276"

# Mute state colors
MUTED_COLOR = "#e74c3c"
UNMUTED_COLOR = "#2ecc71"


# ---------------------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------------------

def db_to_fraction(db: float) -> float:
    """Convert dB value to 0.0-1.0 fraction for VU meter display."""
    if db <= MIN_DB:
        return 0.0
    if db >= 0.0:
        return 1.0
    return (db - MIN_DB) / (0.0 - MIN_DB)


def vu_color(fraction: float) -> str:
    """Return VU meter color based on signal level fraction (0.0 to 1.0)."""
    if fraction > 0.80:
        return RED_VU
    if fraction > 0.50:
        return YELLOW_VU
    return GREEN_VU


def format_elapsed(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
