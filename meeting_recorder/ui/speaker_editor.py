"""Speaker map editor dialog.

Allows editing the speaker_map in a recording's metadata — renaming
diarized speaker labels (SPEAKER_00, SPEAKER_01, ...) to real names.
Changes are saved to metadata.json and optionally applied to transcript.txt.
"""

from __future__ import annotations

import json
import logging
import re
import tkinter as tk
from pathlib import Path
from typing import Optional

from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL, BG_CARD,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    GREEN, AMBER, RED_DOT, BLUE_ACCENT,
    BUTTON_BG, BUTTON_HOVER,
)

logger = logging.getLogger(__name__)


class SpeakerEditorDialog:
    """Dialog for editing speaker name mappings in a recording."""

    def __init__(self, rec_path: Path, on_saved: Optional[callable] = None):
        self._rec_path = rec_path
        self._on_saved = on_saved
        self._window: Optional[tk.Toplevel] = None
        self._entries: dict[str, tk.Entry] = {}

    def show(self, parent: tk.Tk) -> None:
        """Show the speaker editor dialog."""
        if self._window is not None:
            self._window.lift()
            return

        self._window = tk.Toplevel(parent)
        self._window.title("Edit Speaker Names")
        self._window.geometry("400x350")
        self._window.configure(bg=BG_COLOR)
        self._window.transient(parent)
        self._window.grab_set()
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        meta, speakers = self._load_data()
        self._build_ui(meta, speakers)

    def close(self) -> None:
        if self._window:
            self._window.destroy()
            self._window = None
            self._entries = {}

    def _load_data(self) -> tuple[dict, list[str]]:
        """Load metadata and discover all speakers."""
        meta = {}
        try:
            meta_path = self._rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
        except Exception:
            logger.exception("Failed to load metadata for speaker editor")

        # Collect speaker IDs from transcript.json
        speakers_from_transcript: list[str] = []
        try:
            transcript_path = self._rec_path / "transcript.json"
            if transcript_path.exists():
                with open(transcript_path, "r", encoding="utf-8") as f:
                    tdata = json.load(f)
                seen = set()
                for seg in tdata.get("segments", []):
                    spk = seg.get("speaker", "")
                    if spk and spk not in seen:
                        seen.add(spk)
                        speakers_from_transcript.append(spk)
        except Exception:
            pass

        # Merge: use transcript speakers + any mapped ones not in transcript
        speaker_map = meta.get("speaker_map", {})
        all_speakers = list(speakers_from_transcript)
        for spk_id in speaker_map:
            if spk_id not in all_speakers:
                all_speakers.append(spk_id)

        return meta, all_speakers

    def _build_ui(self, meta: dict, speakers: list[str]) -> None:
        """Build the editor UI."""
        window = self._window

        # Header
        header = tk.Frame(window, bg=BG_HEADER, height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="Edit Speaker Names",
            font=("Segoe UI", 11, "bold"), fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(padx=16, pady=8, side=tk.LEFT)

        speaker_map = meta.get("speaker_map", {})

        if not speakers:
            tk.Label(
                window, text="No speakers found in this recording.\n"
                             "Diarization must be enabled to identify speakers.",
                font=("Segoe UI", 10), fg=TEXT_DIM, bg=BG_COLOR,
                justify=tk.CENTER,
            ).pack(pady=40)
            return

        # Instructions
        tk.Label(
            window,
            text="Map speaker labels to real names. Leave blank to keep the original label.",
            font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_COLOR,
            wraplength=380,
        ).pack(padx=16, pady=(8, 4), anchor=tk.W)

        # Speaker entries
        entries_frame = tk.Frame(window, bg=BG_COLOR)
        entries_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=4)

        self._entries = {}
        for i, spk_id in enumerate(speakers):
            row = tk.Frame(entries_frame, bg=BG_COLOR)
            row.pack(fill=tk.X, pady=3)

            tk.Label(
                row, text=spk_id, font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_COLOR, width=14, anchor=tk.W,
            ).pack(side=tk.LEFT)

            tk.Label(
                row, text="\u2192", font=("Segoe UI", 9),
                fg=TEXT_DIM, bg=BG_COLOR, padx=6,
            ).pack(side=tk.LEFT)

            entry = tk.Entry(
                row, font=("Segoe UI", 10),
                bg=BG_PANEL, fg=TEXT_BRIGHT,
                insertbackground=TEXT_BRIGHT,
                bd=0, highlightthickness=1,
                highlightcolor=BLUE_ACCENT,
            )
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)

            # Pre-fill with existing mapping
            current_name = speaker_map.get(spk_id, "")
            if current_name:
                entry.insert(0, current_name)

            self._entries[spk_id] = entry

            # Focus first entry
            if i == 0:
                entry.focus_set()

        # Bottom buttons
        btn_frame = tk.Frame(window, bg=BG_COLOR)
        btn_frame.pack(fill=tk.X, padx=16, pady=(8, 12))

        status_label = tk.Label(
            btn_frame, text="", font=("Segoe UI", 8), fg=GREEN, bg=BG_COLOR,
        )
        status_label.pack(side=tk.LEFT)

        cancel_btn = tk.Label(
            btn_frame, text="  Cancel  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2",
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(4, 0))
        cancel_btn.bind("<Button-1>", lambda e: self.close())
        cancel_btn.bind("<Enter>", lambda e: cancel_btn.configure(bg=BUTTON_HOVER))
        cancel_btn.bind("<Leave>", lambda e: cancel_btn.configure(bg=BUTTON_BG))

        save_btn = tk.Label(
            btn_frame, text="  Save  ", font=("Segoe UI", 9, "bold"),
            fg=TEXT_BRIGHT, bg=BLUE_ACCENT, cursor="hand2",
        )
        save_btn.pack(side=tk.RIGHT)
        save_btn.bind("<Button-1>", lambda e: self._save(meta, status_label))
        save_btn.bind("<Enter>", lambda e: save_btn.configure(bg="#2980b9"))
        save_btn.bind("<Leave>", lambda e: save_btn.configure(bg=BLUE_ACCENT))

        # Bind Enter to save
        window.bind("<Return>", lambda e: self._save(meta, status_label))

    def _save(self, meta: dict, status_label: tk.Label) -> None:
        """Save the speaker map to metadata.json and update transcript.txt."""
        # Build new speaker map
        new_map: dict[str, str] = {}
        for spk_id, entry in self._entries.items():
            name = entry.get().strip()
            if name:
                new_map[spk_id] = name

        # Update metadata
        meta["speaker_map"] = new_map
        try:
            meta_path = self._rec_path / "metadata.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to save speaker map to metadata")
            status_label.configure(text="Save failed!", fg=RED_DOT)
            return

        # Update transcript.txt if it exists (replace speaker labels)
        self._update_transcript_txt(new_map)

        logger.info("Saved speaker map for %s: %s", self._rec_path.name, new_map)
        status_label.configure(text="\u2713 Saved", fg=GREEN)

        if self._on_saved:
            try:
                self._on_saved()
            except Exception:
                pass

        # Close after a brief delay
        if self._window:
            self._window.after(600, self.close)

    def _update_transcript_txt(self, speaker_map: dict[str, str]) -> None:
        """Apply speaker name mappings to transcript.txt."""
        txt_path = self._rec_path / "transcript.txt"
        if not txt_path.exists() or not speaker_map:
            return

        try:
            text = txt_path.read_text(encoding="utf-8")
            for old_label, new_name in speaker_map.items():
                # Replace patterns like "[SPEAKER_00]" or "SPEAKER_00:"
                text = text.replace(f"[{old_label}]", f"[{new_name}]")
                text = text.replace(f"{old_label}:", f"{new_name}:")
            txt_path.write_text(text, encoding="utf-8")
        except Exception:
            logger.exception("Failed to update transcript.txt with speaker names")
