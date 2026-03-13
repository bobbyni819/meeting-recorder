"""Voice profiles management window.

Provides a UI for viewing, renaming, and deleting speaker voice profiles
stored in the VoiceProfileDB. These profiles enable automatic speaker
identification across meetings.
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from typing import Optional

from meeting_recorder.ui.theme import (
    BG_COLOR, BG_HEADER, BG_PANEL, BG_CARD, BG_CARD_HOVER,
    TEXT_COLOR, TEXT_DIM, TEXT_BRIGHT,
    GREEN, AMBER, RED_DOT, BLUE_ACCENT,
    BUTTON_BG, BUTTON_HOVER,
)

logger = logging.getLogger(__name__)


class VoiceProfilesWindow:
    """Window for managing speaker voice profiles."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path
        self._window: Optional[tk.Toplevel] = None
        self._profile_cards: list[tk.Frame] = []
        self._content_frame: Optional[tk.Frame] = None

    def show(self, parent: Optional[tk.Tk] = None) -> None:
        """Show the voice profiles window."""
        if self._window is not None:
            self._window.lift()
            return

        self._window = tk.Toplevel(parent) if parent else tk.Tk()
        self._window.title("Voice Profiles")
        self._window.geometry("460x500")
        self._window.configure(bg=BG_COLOR)
        self._window.protocol("WM_DELETE_WINDOW", self.close)

        self._build_ui()

    def close(self) -> None:
        if self._window:
            self._window.destroy()
            self._window = None
            self._content_frame = None
            self._profile_cards = []

    def _get_db(self):
        """Get a VoiceProfileDB instance."""
        from meeting_recorder.transcription.voice_profiles import VoiceProfileDB
        return VoiceProfileDB(self._db_path)

    def _build_ui(self) -> None:
        """Build the profiles management UI."""
        window = self._window

        # Header
        header = tk.Frame(window, bg=BG_HEADER, height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="Voice Profiles",
            font=("Segoe UI", 11, "bold"), fg=TEXT_BRIGHT, bg=BG_HEADER,
        ).pack(padx=16, pady=8, side=tk.LEFT)

        # Info bar
        info = tk.Frame(window, bg=BG_PANEL, padx=12, pady=6)
        info.pack(fill=tk.X)
        tk.Label(
            info,
            text="Speaker profiles enable automatic identification across meetings.\n"
                 "Profiles are built from diarized recordings with speaker maps.",
            font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG_PANEL,
            justify=tk.LEFT, wraplength=420,
        ).pack(anchor=tk.W)

        # Scrollable content
        canvas = tk.Canvas(window, bg=BG_COLOR, highlightthickness=0)
        scrollbar = tk.Scrollbar(window, orient=tk.VERTICAL, command=canvas.yview)
        self._content_frame = tk.Frame(canvas, bg=BG_COLOR)
        self._content_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._content_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._refresh_profiles()

    def _refresh_profiles(self) -> None:
        """Refresh the profile list from the database."""
        if not self._content_frame:
            return

        # Clear existing cards
        for card in self._profile_cards:
            card.destroy()
        self._profile_cards = []

        try:
            db = self._get_db()
            profiles = db.list_profiles_detailed()
            db.close()
        except Exception:
            logger.exception("Failed to load voice profiles")
            profiles = []

        if not profiles:
            empty_label = tk.Label(
                self._content_frame,
                text="No voice profiles enrolled yet.\n\n"
                     "Profiles are created automatically when you\n"
                     "map speaker labels to names in a recording's\n"
                     "detail view (with diarization enabled).",
                font=("Segoe UI", 10), fg=TEXT_DIM, bg=BG_COLOR,
                justify=tk.CENTER,
            )
            empty_label.pack(pady=40)
            self._profile_cards.append(empty_label)
            return

        # Count header
        count_label = tk.Label(
            self._content_frame,
            text=f"{len(profiles)} profile{'s' if len(profiles) != 1 else ''}",
            font=("Segoe UI", 9, "bold"), fg=TEXT_DIM, bg=BG_COLOR,
        )
        count_label.pack(padx=16, pady=(12, 4), anchor=tk.W)
        self._profile_cards.append(count_label)

        for profile in profiles:
            card = self._build_profile_card(profile)
            self._profile_cards.append(card)

        # Bottom padding
        pad = tk.Frame(self._content_frame, bg=BG_COLOR, height=20)
        pad.pack()
        self._profile_cards.append(pad)

    def _build_profile_card(self, profile: dict) -> tk.Frame:
        """Build a card for a single voice profile."""
        card = tk.Frame(self._content_frame, bg=BG_CARD, padx=12, pady=8)
        card.pack(fill=tk.X, padx=12, pady=3)

        # Top row: name + sample count
        top = tk.Frame(card, bg=BG_CARD)
        top.pack(fill=tk.X)

        name = profile["name"]
        samples = profile["sample_count"]
        created = profile.get("created_at", "")[:10]  # YYYY-MM-DD
        updated = profile.get("updated_at", "")[:10]

        tk.Label(
            top, text=name, font=("Segoe UI", 10, "bold"),
            fg=TEXT_BRIGHT, bg=BG_CARD, anchor=tk.W,
        ).pack(side=tk.LEFT)

        # Sample count badge
        badge_color = GREEN if samples >= 3 else AMBER if samples >= 2 else TEXT_DIM
        tk.Label(
            top, text=f"{samples} sample{'s' if samples != 1 else ''}",
            font=("Segoe UI", 8), fg=badge_color, bg=BG_CARD,
        ).pack(side=tk.RIGHT, padx=4)

        # Date info
        date_text = f"Created {created}"
        if updated and updated != created:
            date_text += f"  •  Updated {updated}"
        tk.Label(
            card, text=date_text, font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BG_CARD, anchor=tk.W,
        ).pack(fill=tk.X)

        # Action buttons
        actions = tk.Frame(card, bg=BG_CARD)
        actions.pack(fill=tk.X, pady=(4, 0))

        rename_btn = tk.Label(
            actions, text="  Rename  ", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2",
        )
        rename_btn.pack(side=tk.LEFT, padx=(0, 4))
        rename_btn.bind("<Button-1>", lambda e, n=name: self._rename_profile(n))
        rename_btn.bind("<Enter>", lambda e, b=rename_btn: b.configure(bg=BUTTON_HOVER, fg=TEXT_COLOR))
        rename_btn.bind("<Leave>", lambda e, b=rename_btn: b.configure(bg=BUTTON_BG, fg=TEXT_DIM))

        delete_btn = tk.Label(
            actions, text="  Delete  ", font=("Segoe UI", 8),
            fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2",
        )
        delete_btn.pack(side=tk.LEFT)
        delete_btn.bind("<Button-1>", lambda e, n=name, b=delete_btn: self._delete_profile(n, b))
        delete_btn.bind("<Enter>", lambda e, b=delete_btn: b.configure(bg=BUTTON_HOVER, fg=TEXT_COLOR))
        delete_btn.bind("<Leave>", lambda e, b=delete_btn: b.configure(bg=BUTTON_BG, fg=TEXT_DIM))

        # Hover effect on card
        for w in (card, top):
            w.bind("<Enter>", lambda e, c=card: c.configure(bg=BG_CARD_HOVER))
            w.bind("<Leave>", lambda e, c=card: c.configure(bg=BG_CARD))

        return card

    def _rename_profile(self, old_name: str) -> None:
        """Open a rename dialog for a profile."""
        if not self._window:
            return

        dialog = tk.Toplevel(self._window)
        dialog.title("Rename Profile")
        dialog.geometry("300x120")
        dialog.configure(bg=BG_COLOR)
        dialog.transient(self._window)
        dialog.grab_set()

        tk.Label(
            dialog, text=f'Rename "{old_name}" to:',
            font=("Segoe UI", 10), fg=TEXT_COLOR, bg=BG_COLOR,
        ).pack(padx=16, pady=(12, 4), anchor=tk.W)

        entry = tk.Entry(
            dialog, font=("Segoe UI", 10),
            bg=BG_PANEL, fg=TEXT_BRIGHT, insertbackground=TEXT_BRIGHT,
        )
        entry.pack(fill=tk.X, padx=16, pady=4)
        entry.insert(0, old_name)
        entry.select_range(0, tk.END)
        entry.focus_set()

        status = tk.Label(
            dialog, text="", font=("Segoe UI", 8), fg=RED_DOT, bg=BG_COLOR,
        )
        status.pack(padx=16, anchor=tk.W)

        def do_rename(event=None):
            new_name = entry.get().strip()
            if not new_name:
                status.configure(text="Name cannot be empty.")
                return
            if new_name == old_name:
                dialog.destroy()
                return
            try:
                db = self._get_db()
                ok = db.rename_profile(old_name, new_name)
                db.close()
                if ok:
                    dialog.destroy()
                    self._refresh_profiles()
                else:
                    status.configure(text=f'"{new_name}" already exists.')
            except Exception:
                logger.exception("Rename failed")
                status.configure(text="Rename failed — check logs.")

        entry.bind("<Return>", do_rename)

        btn_frame = tk.Frame(dialog, bg=BG_COLOR)
        btn_frame.pack(fill=tk.X, padx=16, pady=(0, 8))

        cancel_btn = tk.Label(
            btn_frame, text="  Cancel  ", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BUTTON_BG, cursor="hand2",
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(4, 0))
        cancel_btn.bind("<Button-1>", lambda e: dialog.destroy())

        save_btn = tk.Label(
            btn_frame, text="  Save  ", font=("Segoe UI", 9),
            fg=TEXT_BRIGHT, bg=BLUE_ACCENT, cursor="hand2",
        )
        save_btn.pack(side=tk.RIGHT)
        save_btn.bind("<Button-1>", lambda e: do_rename())

    def _delete_profile(self, name: str, btn: tk.Label) -> None:
        """Delete a profile with inline confirmation (click twice)."""
        if btn.cget("text").strip() == "Confirm Delete":
            try:
                db = self._get_db()
                db.delete_profile(name)
                db.close()
                self._refresh_profiles()
            except Exception:
                logger.exception("Delete failed")
            return

        # First click — ask for confirmation
        btn.configure(text=" Confirm Delete ", fg=RED_DOT)

        def reset():
            try:
                btn.configure(text="  Delete  ", fg=TEXT_DIM)
            except tk.TclError:
                pass  # Widget destroyed

        if self._window:
            self._window.after(3000, reset)
