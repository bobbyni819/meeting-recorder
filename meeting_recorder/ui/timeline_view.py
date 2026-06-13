"""Speaker timeline visualization.

Renders a horizontal timeline showing when each speaker talked during
a recording. Uses segment data from transcript.json.
"""

from __future__ import annotations

import json
import logging
import tkinter as tk
from pathlib import Path
from typing import Optional

from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
)

logger = logging.getLogger(__name__)

# Speaker colors — distinct enough to tell apart on dark background
SPEAKER_COLORS = [
    "#3498db",  # blue
    "#2ecc71",  # green
    "#e74c3c",  # red
    "#f39c12",  # amber
    "#9b59b6",  # purple
    "#1abc9c",  # teal
    "#e67e22",  # orange
    "#e84393",  # pink
    "#00cec9",  # cyan
    "#6c5ce7",  # indigo
]


def load_timeline_data(rec_path: Path) -> Optional[dict]:
    """Load and prepare timeline data from a recording directory.

    Returns:
        Dict with keys: duration, speakers (list of {name, color, segments}),
        or None if no data available.
    """
    transcript_path = rec_path / "transcript.json"
    meta_path = rec_path / "metadata.json"

    if not transcript_path.exists():
        return None

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            tdata = json.load(f)
    except Exception:
        return None

    segments = tdata.get("segments") or []
    if not segments:
        return None

    # Load speaker map for name resolution
    speaker_map: dict[str, str] = {}
    duration = 0.0
    try:
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            speaker_map = meta.get("speaker_map", {})
            duration = meta.get("duration_seconds", 0)
    except Exception:
        pass

    # If no duration in metadata, use the last segment end time
    if duration <= 0:
        duration = max(seg.get("end", 0) for seg in segments)
    if duration <= 0:
        return None

    # Group segments by speaker
    speaker_segments: dict[str, list[tuple[float, float]]] = {}
    speaker_order: list[str] = []
    for seg in segments:
        spk = seg.get("speaker", "Unknown")
        start = max(0, seg.get("start", 0))
        end = min(duration, seg.get("end", 0))
        if end <= start:
            continue
        if spk not in speaker_segments:
            speaker_segments[spk] = []
            speaker_order.append(spk)
        speaker_segments[spk].append((start, end))

    # Build output
    speakers = []
    for i, spk_id in enumerate(speaker_order):
        display_name = speaker_map.get(spk_id, spk_id)
        speakers.append({
            "id": spk_id,
            "name": display_name,
            "color": SPEAKER_COLORS[i % len(SPEAKER_COLORS)],
            "segments": speaker_segments[spk_id],
        })

    return {
        "duration": duration,
        "speakers": speakers,
    }


class TimelineWindow:
    """Window showing a speaker timeline for a recording."""

    def __init__(self, rec_path: Path):
        self._rec_path = rec_path
        self._window: Optional[tk.Toplevel] = None

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show the timeline window."""
        if self._window is not None:
            self._window.lift()
            return

        data = load_timeline_data(self._rec_path)
        if data is None:
            return

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("Speaker Timeline")
        self._window.geometry("700x300")
        self._window.configure(bg=BG_COLOR)
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui(data)

    def close(self) -> None:
        if self._window:
            self._window.destroy()
            self._window = None

    def _build_ui(self, data: dict) -> None:
        """Build the timeline visualization."""
        window = self._window
        duration = data["duration"]
        speakers = data["speakers"]

        # Header
        header = tk.Frame(window, bg=BG_HEADER, height=36)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        dur_min = int(duration // 60)
        dur_sec = int(duration % 60)
        tk.Label(
            header, text=f"Speaker Timeline  ({dur_min}:{dur_sec:02d})",
            font=("Segoe UI", 10, "bold"), fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(padx=16, pady=6, side=tk.LEFT)

        tk.Label(
            header, text=f"{len(speakers)} speakers",
            font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG_HEADER,
        ).pack(side=tk.RIGHT, padx=16, pady=6)

        # Timeline area
        LABEL_W = 120
        BAR_H = 20
        ROW_PAD = 4
        TIME_AXIS_H = 25

        canvas_h = len(speakers) * (BAR_H + ROW_PAD) + TIME_AXIS_H + 20
        canvas = tk.Canvas(
            window, bg=BG_COLOR, highlightthickness=0,
            height=canvas_h,
        )
        canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Wait for canvas to render to get its width
        window.update_idletasks()
        total_w = canvas.winfo_width()
        if total_w < 200:
            total_w = 660  # fallback

        bar_w = total_w - LABEL_W - 20  # pixels available for timeline bars

        # Draw speaker rows
        y = 10
        for spk in speakers:
            # Label
            canvas.create_text(
                LABEL_W - 8, y + BAR_H // 2,
                text=spk["name"], anchor=tk.E,
                fill=TEXT_COLOR, font=("Segoe UI", 9),
            )

            # Background bar
            x0 = LABEL_W
            canvas.create_rectangle(
                x0, y, x0 + bar_w, y + BAR_H,
                fill=BG_PANEL, outline="",
            )

            # Segment bars
            for start, end in spk["segments"]:
                sx = x0 + int(start / duration * bar_w)
                ex = x0 + int(end / duration * bar_w)
                # Ensure minimum width of 1px
                if ex <= sx:
                    ex = sx + 1
                canvas.create_rectangle(
                    sx, y + 1, ex, y + BAR_H - 1,
                    fill=spk["color"], outline="",
                )

            y += BAR_H + ROW_PAD

        # Time axis
        axis_y = y + 4
        x0 = LABEL_W
        canvas.create_line(x0, axis_y, x0 + bar_w, axis_y, fill=TEXT_DIM)

        # Time labels (aim for ~5-8 labels)
        num_labels = min(8, max(3, int(duration / 60)))
        interval = duration / num_labels
        for i in range(num_labels + 1):
            t = i * interval
            x = x0 + int(t / duration * bar_w)
            canvas.create_line(x, axis_y - 3, x, axis_y + 3, fill=TEXT_DIM)
            m = int(t // 60)
            s = int(t % 60)
            canvas.create_text(
                x, axis_y + 12, text=f"{m}:{s:02d}",
                fill=TEXT_DIM, font=("Segoe UI", 7),
            )

        # Legend at bottom
        legend_y = axis_y + TIME_AXIS_H
        lx = LABEL_W
        for spk in speakers:
            speaking_secs = sum(e - s for s, e in spk["segments"])
            pct = speaking_secs / duration * 100 if duration > 0 else 0
            label = f'{spk["name"]} ({pct:.0f}%)'
            canvas.create_rectangle(lx, legend_y, lx + 10, legend_y + 10,
                                    fill=spk["color"], outline="")
            canvas.create_text(lx + 14, legend_y + 5, text=label,
                               anchor=tk.W, fill=TEXT_DIM, font=("Segoe UI", 7))
            lx += len(label) * 6 + 30  # rough spacing

        # Handle resize
        def _on_resize(event):
            nonlocal total_w, bar_w
            new_w = event.width
            if abs(new_w - total_w) > 10:
                total_w = new_w
                bar_w = total_w - LABEL_W - 20
                canvas.delete("all")
                self._draw_timeline(canvas, data, LABEL_W, bar_w, BAR_H, ROW_PAD, TIME_AXIS_H)

        canvas.bind("<Configure>", _on_resize)

    def _draw_timeline(self, canvas: tk.Canvas, data: dict,
                       label_w: int, bar_w: int, bar_h: int,
                       row_pad: int, time_axis_h: int) -> None:
        """Redraw the timeline (called on resize)."""
        duration = data["duration"]
        speakers = data["speakers"]

        y = 10
        for spk in speakers:
            canvas.create_text(
                label_w - 8, y + bar_h // 2,
                text=spk["name"], anchor=tk.E,
                fill=TEXT_COLOR, font=("Segoe UI", 9),
            )
            x0 = label_w
            canvas.create_rectangle(x0, y, x0 + bar_w, y + bar_h,
                                    fill=BG_PANEL, outline="")
            for start, end in spk["segments"]:
                sx = x0 + int(start / duration * bar_w)
                ex = x0 + int(end / duration * bar_w)
                if ex <= sx:
                    ex = sx + 1
                canvas.create_rectangle(sx, y + 1, ex, y + bar_h - 1,
                                        fill=spk["color"], outline="")
            y += bar_h + row_pad

        axis_y = y + 4
        x0 = label_w
        canvas.create_line(x0, axis_y, x0 + bar_w, axis_y, fill=TEXT_DIM)

        num_labels = min(8, max(3, int(duration / 60)))
        interval = duration / num_labels
        for i in range(num_labels + 1):
            t = i * interval
            x = x0 + int(t / duration * bar_w)
            canvas.create_line(x, axis_y - 3, x, axis_y + 3, fill=TEXT_DIM)
            m = int(t // 60)
            s = int(t % 60)
            canvas.create_text(x, axis_y + 12, text=f"{m}:{s:02d}",
                               fill=TEXT_DIM, font=("Segoe UI", 7))

        legend_y = axis_y + time_axis_h
        lx = label_w
        for spk in speakers:
            speaking_secs = sum(e - s for s, e in spk["segments"])
            pct = speaking_secs / duration * 100 if duration > 0 else 0
            label = f'{spk["name"]} ({pct:.0f}%)'
            canvas.create_rectangle(lx, legend_y, lx + 10, legend_y + 10,
                                    fill=spk["color"], outline="")
            canvas.create_text(lx + 14, legend_y + 5, text=label,
                               anchor=tk.W, fill=TEXT_DIM, font=("Segoe UI", 7))
            lx += len(label) * 6 + 30
