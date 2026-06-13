"""Tkinter settings dialog for Meeting Recorder configuration."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

from meeting_recorder.config import Config

logger = logging.getLogger(__name__)


class SettingsWindow:
    """Tkinter-based settings dialog."""

    def __init__(self, config: Config, on_save: Optional[callable] = None):
        self.config = config
        self._on_save = on_save
        self._window: Optional[tk.Tk] = None

    def show(self) -> None:
        """Show the settings window."""
        if self._window is not None:
            try:
                self._window.lift()
                return
            except tk.TclError:
                self._window = None

        self._window = tk.Tk()
        self._window.title("Meeting Recorder - Settings")
        self._window.geometry("500x650")
        self._window.resizable(True, True)
        self._window.minsize(450, 500)

        # Apply dark theme
        self._apply_dark_theme()

        # Create notebook (tabs)
        notebook = ttk.Notebook(self._window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Recording tab
        rec_frame = ttk.Frame(notebook, padding=10)
        notebook.add(rec_frame, text="Recording")
        self._build_recording_tab(rec_frame)

        # Audio tab
        audio_frame = ttk.Frame(notebook, padding=10)
        notebook.add(audio_frame, text="Audio")
        self._build_audio_tab(audio_frame)

        # Transcription tab
        trans_frame = ttk.Frame(notebook, padding=10)
        notebook.add(trans_frame, text="Transcription")
        self._build_transcription_tab(trans_frame)

        # Dashboard tab
        dash_frame = ttk.Frame(notebook, padding=10)
        notebook.add(dash_frame, text="Dashboard")
        self._build_dashboard_tab(dash_frame)

        # Speakers tab
        speakers_frame = ttk.Frame(notebook, padding=10)
        notebook.add(speakers_frame, text="Speakers")
        self._build_speakers_tab(speakers_frame)

        # Storage tab
        storage_frame = ttk.Frame(notebook, padding=10)
        notebook.add(storage_frame, text="Storage")
        self._build_storage_tab(storage_frame)

        # Hotkeys tab
        hotkey_frame = ttk.Frame(notebook, padding=10)
        notebook.add(hotkey_frame, text="Hotkeys")
        self._build_hotkeys_tab(hotkey_frame)

        # Integrations tab
        integ_frame = ttk.Frame(notebook, padding=10)
        notebook.add(integ_frame, text="Integrations")
        self._build_integrations_tab(integ_frame)

        # Buttons
        btn_frame = ttk.Frame(self._window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self._close).pack(side=tk.RIGHT)

        self._window.protocol("WM_DELETE_WINDOW", self._close)
        self._window.mainloop()

    def _apply_dark_theme(self) -> None:
        """Apply dark theme to ttk widgets."""
        bg = "#1a1a2e"
        fg = "#e0e0e0"
        field_bg = "#0f1a2e"
        select_bg = "#0f3460"
        self._window.configure(bg=bg)

        style = ttk.Style(self._window)
        style.theme_use("clam")
        style.configure(".", background=bg, foreground=fg, fieldbackground=field_bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TRadiobutton", background=bg, foreground=fg)
        style.configure("TNotebook", background=bg)
        style.configure("TNotebook.Tab", background="#16213e", foreground=fg, padding=(10, 4))
        style.map("TNotebook.Tab",
                   background=[("selected", select_bg)],
                   foreground=[("selected", "#ffffff")])
        style.configure("TButton", background="#0f3460", foreground=fg)
        style.map("TButton", background=[("active", "#1a5276")])
        style.configure("TEntry", fieldbackground=field_bg, foreground=fg)
        style.configure("TCombobox", fieldbackground=field_bg, foreground=fg)
        style.configure("TSpinbox", fieldbackground=field_bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)

    def _build_recording_tab(self, parent: ttk.Frame) -> None:
        """Build the recording settings tab."""
        row = 0

        ttk.Label(parent, text="Output Directory:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._output_dir_var = tk.StringVar(value=self.config.recording.output_dir)
        dir_frame = ttk.Frame(parent)
        dir_frame.grid(row=row, column=1, sticky=tk.EW, pady=5)
        ttk.Entry(dir_frame, textvariable=self._output_dir_var, width=35).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_frame, text="Browse", command=self._browse_output_dir).pack(side=tk.RIGHT, padx=5)

        row += 1
        ttk.Label(parent, text="Language:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._language_var = tk.StringVar(value=self.config.recording.language)
        ttk.Combobox(
            parent, textvariable=self._language_var, width=10,
            values=["en", "es", "fr", "de", "it", "pt", "ja", "ko", "zh", "auto"],
        ).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Your Name:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._user_name_var = tk.StringVar(value=self.config.recording.user_name)
        ttk.Entry(parent, textvariable=self._user_name_var, width=30).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Start/Stop Hotkey:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._hotkey_var = tk.StringVar(value=self.config.hotkey.toggle_recording)
        ttk.Entry(parent, textvariable=self._hotkey_var, width=20).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Pause Hotkey:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._pause_hotkey_var = tk.StringVar(value=self.config.hotkey.toggle_pause)
        ttk.Entry(parent, textvariable=self._pause_hotkey_var, width=20).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        self._auto_start_var = tk.BooleanVar(value=self.config.recording.auto_start)
        ttk.Checkbutton(parent, text="Auto-detect meetings and start recording", variable=self._auto_start_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=5
        )

        row += 1
        self._live_transcription_var = tk.BooleanVar(value=self.config.recording.live_transcription)
        ttk.Checkbutton(parent, text="Live transcription preview (uses CPU, may increase latency)", variable=self._live_transcription_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=5
        )

        row += 1
        self._screen_rec_var = tk.BooleanVar(value=self.config.screen_recording.enabled)
        ttk.Checkbutton(parent, text="Record Meeting Window (screen capture)", variable=self._screen_rec_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=5
        )

        row += 1
        ttk.Label(parent, text="Screen FPS:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._screen_fps_var = tk.DoubleVar(value=self.config.screen_recording.fps)
        ttk.Spinbox(parent, from_=1, to=60, textvariable=self._screen_fps_var, width=5).grid(row=row, column=1, sticky=tk.W, pady=5)

        parent.columnconfigure(1, weight=1)

    def _build_audio_tab(self, parent: ttk.Frame) -> None:
        """Build the audio settings tab."""
        row = 0

        ttk.Label(parent, text="VAD Threshold:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._vad_threshold_var = tk.DoubleVar(value=self.config.vad.threshold)
        scale = ttk.Scale(parent, from_=0.1, to=0.9, variable=self._vad_threshold_var, orient=tk.HORIZONTAL)
        scale.grid(row=row, column=1, sticky=tk.EW, pady=5)

        row += 1
        ttk.Label(parent, text="Min Speech Duration (ms):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._min_speech_var = tk.IntVar(value=self.config.vad.min_speech_duration_ms)
        ttk.Spinbox(parent, from_=100, to=1000, textvariable=self._min_speech_var, width=10).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Min Silence Duration (ms):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._min_silence_var = tk.IntVar(value=self.config.vad.min_silence_duration_ms)
        ttk.Spinbox(parent, from_=100, to=2000, textvariable=self._min_silence_var, width=10).grid(row=row, column=1, sticky=tk.W, pady=5)

        parent.columnconfigure(1, weight=1)

    def _build_transcription_tab(self, parent: ttk.Frame) -> None:
        """Build the transcription settings tab."""
        row = 0

        ttk.Label(parent, text="Backend:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._backend_var = tk.StringVar(value=self.config.transcription.backend)
        ttk.Combobox(
            parent, textvariable=self._backend_var, width=10,
            values=["local", "cloud", "gemini"], state="readonly",
        ).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Model Size:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._model_size_var = tk.StringVar(value=self.config.transcription.model_size)
        ttk.Combobox(
            parent, textvariable=self._model_size_var, width=15,
            values=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        ).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Device:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._device_var = tk.StringVar(value=self.config.transcription.device)
        ttk.Combobox(
            parent, textvariable=self._device_var, width=10,
            values=["cuda", "cpu"], state="readonly",
        ).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Compute Type:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._compute_type_var = tk.StringVar(value=self.config.transcription.compute_type)
        ttk.Combobox(
            parent, textvariable=self._compute_type_var, width=10,
            values=["float16", "int8", "float32"], state="readonly",
        ).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="OpenAI API Key:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._api_key_var = tk.StringVar(value=self.config.transcription.openai_api_key)
        ttk.Entry(parent, textvariable=self._api_key_var, width=40, show="*").grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Gemini API Key:").grid(row=row, column=0, sticky=tk.W, pady=5)
        gemini_frame = ttk.Frame(parent)
        gemini_frame.grid(row=row, column=1, sticky=tk.W, pady=5)
        self._gemini_key_var = tk.StringVar(value=self.config.transcription.gemini_api_key)
        ttk.Entry(gemini_frame, textvariable=self._gemini_key_var, width=32, show="*").pack(side=tk.LEFT)
        self._gemini_test_btn = ttk.Button(gemini_frame, text="Test", width=5, command=self._test_gemini_key)
        self._gemini_test_btn.pack(side=tk.LEFT, padx=(4, 0))

        row += 1
        ttk.Label(parent, text="Gemini Model:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._gemini_model_var = tk.StringVar(value=self.config.transcription.gemini_model)
        ttk.Entry(parent, textvariable=self._gemini_model_var, width=25).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        self._diarization_var = tk.BooleanVar(value=self.config.diarization.enabled)
        ttk.Checkbutton(parent, text="Enable Speaker Diarization", variable=self._diarization_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=5
        )

        row += 1
        ttk.Label(parent, text="HuggingFace Token:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._hf_token_var = tk.StringVar(value=self.config.diarization.huggingface_token)
        ttk.Entry(parent, textvariable=self._hf_token_var, width=40, show="*").grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Min Speakers:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._min_speakers_var = tk.IntVar(value=self.config.diarization.min_speakers)
        ttk.Spinbox(parent, from_=1, to=10, textvariable=self._min_speakers_var, width=5).grid(
            row=row, column=1, sticky=tk.W, pady=5
        )

        row += 1
        ttk.Label(parent, text="Max Speakers:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._max_speakers_var = tk.IntVar(value=self.config.diarization.max_speakers)
        ttk.Spinbox(parent, from_=2, to=20, textvariable=self._max_speakers_var, width=5).grid(
            row=row, column=1, sticky=tk.W, pady=5
        )

        row += 1
        ttk.Label(parent, text="Output Formats:").grid(row=row, column=0, sticky=tk.W, pady=5)
        fmt_frame = ttk.Frame(parent)
        fmt_frame.grid(row=row, column=1, sticky=tk.W, pady=5)
        self._fmt_json_var = tk.BooleanVar(value="json" in self.config.output.formats)
        self._fmt_txt_var = tk.BooleanVar(value="txt" in self.config.output.formats)
        self._fmt_srt_var = tk.BooleanVar(value="srt" in self.config.output.formats)
        ttk.Checkbutton(fmt_frame, text="JSON", variable=self._fmt_json_var).pack(side=tk.LEFT)
        ttk.Checkbutton(fmt_frame, text="TXT", variable=self._fmt_txt_var).pack(side=tk.LEFT)
        ttk.Checkbutton(fmt_frame, text="SRT", variable=self._fmt_srt_var).pack(side=tk.LEFT)

        parent.columnconfigure(1, weight=1)

    def _build_dashboard_tab(self, parent: ttk.Frame) -> None:
        """Build the dashboard overlay settings tab."""
        row = 0

        self._dash_enabled_var = tk.BooleanVar(value=self.config.dashboard.enabled)
        ttk.Checkbutton(parent, text="Enable recording dashboard overlay", variable=self._dash_enabled_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=5
        )

        row += 1
        self._dash_auto_show_var = tk.BooleanVar(value=self.config.dashboard.auto_show)
        ttk.Checkbutton(parent, text="Auto-show when recording starts", variable=self._dash_auto_show_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=2
        )

        row += 1
        self._dash_auto_hide_var = tk.BooleanVar(value=self.config.dashboard.auto_hide)
        ttk.Checkbutton(parent, text="Auto-hide when recording stops", variable=self._dash_auto_hide_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=2
        )

        row += 1
        self._dash_collapsed_var = tk.BooleanVar(value=self.config.dashboard.start_collapsed)
        ttk.Checkbutton(parent, text="Start collapsed (mini indicator)", variable=self._dash_collapsed_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=2
        )

        row += 1
        self._dash_transcript_var = tk.BooleanVar(value=self.config.dashboard.show_transcript)
        ttk.Checkbutton(parent, text="Show transcript preview", variable=self._dash_transcript_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=2
        )

        row += 1
        self._dash_preview_var = tk.BooleanVar(value=self.config.dashboard.show_screen_preview)
        ttk.Checkbutton(parent, text="Show screen capture preview", variable=self._dash_preview_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=2
        )

        row += 1
        ttk.Label(parent, text="Opacity:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._dash_opacity_var = tk.DoubleVar(value=self.config.dashboard.opacity)
        ttk.Scale(parent, from_=0.3, to=1.0, variable=self._dash_opacity_var, orient=tk.HORIZONTAL).grid(
            row=row, column=1, sticky=tk.EW, pady=5
        )

        row += 1
        ttk.Label(parent, text="Position:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._dash_position_var = tk.StringVar(value=self.config.dashboard.position)
        ttk.Combobox(
            parent, textvariable=self._dash_position_var, width=15,
            values=["top-left", "top-right", "bottom-left", "bottom-right", "center"],
            state="readonly",
        ).grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(
            parent,
            text="Hotkey: Ctrl+Shift+D to toggle dashboard\nDragging remembers position across sessions.",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(10, 2))

        parent.columnconfigure(1, weight=1)

    def _build_speakers_tab(self, parent: ttk.Frame) -> None:
        """Build the voice profile management tab."""
        row = 0

        ttk.Label(parent, text="Voice Profiles", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )

        row += 1
        ttk.Label(
            parent,
            text="Speakers are auto-enrolled from recordings when diarization is enabled.\n"
                 "Voice profiles are used to identify speakers across meetings.",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        row += 1
        # Profile list with scrollbar
        list_frame = ttk.Frame(parent)
        list_frame.grid(row=row, column=0, columnspan=2, sticky=tk.NSEW, pady=5)
        parent.rowconfigure(row, weight=1)

        self._speaker_listbox = tk.Listbox(
            list_frame,
            font=("Segoe UI", 9),
            bg="#0f1a2e", fg="#e0e0e0",
            selectbackground="#0f3460", selectforeground="#ffffff",
            activestyle="none", bd=0,
            highlightthickness=1, highlightcolor="#0f3460",
        )
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._speaker_listbox.yview)
        self._speaker_listbox.configure(yscrollcommand=scrollbar.set)
        self._speaker_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        row += 1
        self._speaker_count_label = ttk.Label(parent, text="", foreground="gray")
        self._speaker_count_label.grid(row=row, column=0, sticky=tk.W, pady=(2, 5))

        row += 1
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Button(btn_frame, text="Rename", command=self._rename_speaker).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Delete", command=self._delete_speaker).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Refresh", command=self._refresh_speakers).pack(side=tk.LEFT, padx=(0, 5))

        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=0)

        # Load profiles
        self._speaker_profiles: list[dict] = []
        self._refresh_speakers()

    def _refresh_speakers(self) -> None:
        """Reload the speaker profile list from the database."""
        self._speaker_listbox.delete(0, tk.END)
        try:
            from meeting_recorder.transcription.voice_profiles import VoiceProfileDB
            db = VoiceProfileDB()
            try:
                self._speaker_profiles = db.list_profiles_detailed()
            finally:
                db.close()
        except Exception:
            self._speaker_profiles = []

        for p in self._speaker_profiles:
            samples = p["sample_count"]
            updated = p["updated_at"][:10] if p.get("updated_at") else "?"
            self._speaker_listbox.insert(
                tk.END,
                f"  {p['name']}    ({samples} sample{'s' if samples != 1 else ''}, last: {updated})"
            )

        count = len(self._speaker_profiles)
        self._speaker_count_label.configure(
            text=f"{count} voice profile{'s' if count != 1 else ''} enrolled"
        )

    def _rename_speaker(self) -> None:
        """Rename the selected speaker profile."""
        sel = self._speaker_listbox.curselection()
        if not sel or sel[0] >= len(self._speaker_profiles):
            return

        old_name = self._speaker_profiles[sel[0]]["name"]

        # Simple dialog for new name
        dialog = tk.Toplevel(self._window)
        dialog.title("Rename Speaker")
        dialog.geometry("300x120")
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.grab_set()
        dialog.configure(bg="#1a1a2e")

        tk.Label(
            dialog, text=f"Rename '{old_name}' to:",
            font=("Segoe UI", 9), fg="#e0e0e0", bg="#1a1a2e",
        ).pack(padx=12, pady=(12, 4), anchor=tk.W)

        name_var = tk.StringVar(value=old_name)
        entry = tk.Entry(
            dialog, textvariable=name_var, font=("Segoe UI", 9),
            bg="#0f1a2e", fg="#e0e0e0", insertbackground="#e0e0e0",
        )
        entry.pack(fill=tk.X, padx=12, pady=4)
        entry.select_range(0, tk.END)
        entry.focus_set()

        def _do_rename():
            new_name = name_var.get().strip()
            if not new_name or new_name == old_name:
                dialog.destroy()
                return
            try:
                from meeting_recorder.transcription.voice_profiles import VoiceProfileDB
                db = VoiceProfileDB()
                try:
                    ok = db.rename_profile(old_name, new_name)
                finally:
                    db.close()
                if not ok:
                    messagebox.showwarning("Rename Failed", f"Could not rename: '{new_name}' may already exist.")
                    return
            except Exception as e:
                messagebox.showerror("Error", f"Rename failed: {e}")
                return
            dialog.destroy()
            self._refresh_speakers()

        entry.bind("<Return>", lambda e: _do_rename())

        btn_frame = tk.Frame(dialog, bg="#1a1a2e")
        btn_frame.pack(fill=tk.X, padx=12, pady=8)
        ttk.Button(btn_frame, text="Rename", command=_do_rename).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT)

    def _delete_speaker(self) -> None:
        """Delete the selected speaker profile."""
        sel = self._speaker_listbox.curselection()
        if not sel or sel[0] >= len(self._speaker_profiles):
            return

        name = self._speaker_profiles[sel[0]]["name"]
        if not messagebox.askyesno("Delete Profile", f"Delete voice profile for '{name}'?\n\nThis cannot be undone."):
            return

        try:
            from meeting_recorder.transcription.voice_profiles import VoiceProfileDB
            db = VoiceProfileDB()
            try:
                db.delete_profile(name)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Error", f"Delete failed: {e}")
            return

        self._refresh_speakers()

    def _build_storage_tab(self, parent: ttk.Frame) -> None:
        """Build the storage & retention settings tab."""
        row = 0

        # Recording stats
        ttk.Label(parent, text="Recording Statistics", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )

        row += 1
        stats_text = self._get_recording_stats()
        ttk.Label(parent, text=stats_text, foreground="gray").grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(0, 5)
        )

        row += 1
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=10
        )

        # Retention policy
        row += 1
        ttk.Label(parent, text="Retention Policy", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )

        row += 1
        self._retention_enabled_var = tk.BooleanVar(value=self.config.retention.enabled)
        ttk.Checkbutton(
            parent, text="Auto-delete old recordings",
            variable=self._retention_enabled_var,
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        row += 1
        ttk.Label(parent, text="Max Age (days):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._retention_age_var = tk.IntVar(value=self.config.retention.max_age_days)
        ttk.Spinbox(parent, from_=0, to=3650, textvariable=self._retention_age_var, width=8).grid(
            row=row, column=1, sticky=tk.W, pady=5
        )

        row += 1
        ttk.Label(parent, text="Max Total Size (GB):").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._retention_size_var = tk.DoubleVar(value=self.config.retention.max_total_gb)
        ttk.Spinbox(parent, from_=0, to=10000, increment=10, textvariable=self._retention_size_var, width=8).grid(
            row=row, column=1, sticky=tk.W, pady=5
        )

        row += 1
        ttk.Label(
            parent,
            text="Set 0 for no limit. Age and size limits work together.\n"
                 "Cleanup runs at startup and after each recording.",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(2, 10))

        # Config export/import section
        row += 1
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=10
        )

        row += 1
        ttk.Label(parent, text="Config Transfer", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )

        row += 1
        ttk.Label(
            parent,
            text="Export API keys & secrets to transfer to another machine.\n"
                 "Non-secret settings sync via git.",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(0, 5))

        row += 1
        transfer_frame = ttk.Frame(parent)
        transfer_frame.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Button(transfer_frame, text="Export Config...", command=self._export_config).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(transfer_frame, text="Import Config...", command=self._import_config).pack(side=tk.LEFT, padx=(0, 5))

        parent.columnconfigure(1, weight=1)

    def _get_recording_stats(self) -> str:
        """Calculate recording directory statistics."""
        try:
            from meeting_recorder.storage.recording_store import RecordingStore
            store = RecordingStore(self.config.output_dir)
            recordings = store.list_recordings()
            count = len(recordings)
            if count == 0:
                return "No recordings yet."

            total_bytes = sum(store._dir_size(d) for d in recordings)
            total_gb = total_bytes / (1024 ** 3)

            oldest = recordings[-1].name[:10] if recordings else "?"
            newest = recordings[0].name[:10] if recordings else "?"

            if total_gb >= 1.0:
                size_str = f"{total_gb:.1f} GB"
            else:
                size_str = f"{total_bytes / (1024 ** 2):.0f} MB"

            return f"{count} recording(s)  |  {size_str}  |  {oldest} to {newest}"
        except Exception:
            return "Unable to calculate stats."

    def _build_integrations_tab(self, parent: ttk.Frame) -> None:
        """Build the integrations settings tab."""
        row = 0

        # Outlook section
        ttk.Label(parent, text="Outlook Calendar", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )

        row += 1
        self._outlook_var = tk.BooleanVar(value=self.config.outlook.enabled)
        ttk.Checkbutton(
            parent, text="Auto-detect meeting name from Outlook calendar",
            variable=self._outlook_var,
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        row += 1
        ttk.Label(parent, text="Search Window (min):").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._outlook_buffer_var = tk.IntVar(value=self.config.outlook.buffer_minutes)
        ttk.Spinbox(parent, from_=1, to=30, textvariable=self._outlook_buffer_var, width=5).grid(
            row=row, column=1, sticky=tk.W, pady=2
        )

        row += 1
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=10
        )

        # Google Drive section
        row += 1
        ttk.Label(parent, text="Google Drive Backup", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )

        row += 1
        self._gdrive_var = tk.BooleanVar(value=self.config.google_drive.enabled)
        ttk.Checkbutton(
            parent, text="Upload recordings to Google Drive after transcription",
            variable=self._gdrive_var,
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        row += 1
        ttk.Label(parent, text="Credentials File:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._gdrive_creds_var = tk.StringVar(value=self.config.google_drive.credentials_path)
        creds_frame = ttk.Frame(parent)
        creds_frame.grid(row=row, column=1, sticky=tk.EW, pady=2)
        ttk.Entry(creds_frame, textvariable=self._gdrive_creds_var, width=30).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(creds_frame, text="Browse", command=self._browse_gdrive_creds).pack(side=tk.RIGHT, padx=5)

        row += 1
        ttk.Label(parent, text="Drive Folder ID:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._gdrive_folder_var = tk.StringVar(value=self.config.google_drive.folder_id)
        ttk.Entry(parent, textvariable=self._gdrive_folder_var, width=40).grid(row=row, column=1, sticky=tk.W, pady=2)

        row += 1
        ttk.Label(
            parent,
            text="Leave Folder ID empty to auto-create a\n'MeetingRecordings' folder in your Drive.",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        row += 1
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=10
        )

        # AI Summary section
        row += 1
        ttk.Label(parent, text="AI Meeting Summary", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )

        row += 1
        self._summary_var = tk.BooleanVar(value=self.config.summary.enabled)
        ttk.Checkbutton(
            parent, text="Generate AI summary after transcription",
            variable=self._summary_var,
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        row += 1
        ttk.Label(parent, text="Provider:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._summary_provider_var = tk.StringVar(value=self.config.summary.provider)
        ttk.Combobox(
            parent, textvariable=self._summary_provider_var, width=12,
            values=["openai", "anthropic", "gemini"], state="readonly",
        ).grid(row=row, column=1, sticky=tk.W, pady=2)

        row += 1
        ttk.Label(parent, text="API Key:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._summary_api_key_var = tk.StringVar(value=self.config.summary.api_key)
        ttk.Entry(parent, textvariable=self._summary_api_key_var, width=40, show="*").grid(
            row=row, column=1, sticky=tk.W, pady=2
        )

        row += 1
        ttk.Label(parent, text="Model:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._summary_model_var = tk.StringVar(value=self.config.summary.model)
        ttk.Entry(parent, textvariable=self._summary_model_var, width=25).grid(
            row=row, column=1, sticky=tk.W, pady=2
        )

        row += 1
        ttk.Label(parent, text="Max Transcript Tokens:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._summary_max_tokens_var = tk.IntVar(value=self.config.summary.max_transcript_tokens)
        ttk.Spinbox(parent, from_=0, to=1000000, increment=1000, textvariable=self._summary_max_tokens_var, width=10).grid(
            row=row, column=1, sticky=tk.W, pady=2
        )

        row += 1
        ttk.Label(
            parent,
            text="Leave Model empty for provider default\n(gpt-4o / claude-sonnet-4-20250514 / gemini-2.5-flash).\n0 tokens = no limit (send full transcript).",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        parent.columnconfigure(1, weight=1)

    def _build_hotkeys_tab(self, parent: ttk.Frame) -> None:
        """Build the hotkeys settings tab."""
        row = 0

        ttk.Label(parent, text="Global Hotkeys", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 2)
        )
        row += 1
        ttk.Label(
            parent, text="These work system-wide even when the app is in background.",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        # Start/Stop (already has a var from recording tab)
        row += 1
        ttk.Label(parent, text="Start / Stop Recording:").grid(
            row=row, column=0, sticky=tk.W, pady=4)
        if not hasattr(self, "_hotkey_var"):
            self._hotkey_var = tk.StringVar(value=self.config.hotkey.toggle_recording)
        ttk.Entry(parent, textvariable=self._hotkey_var, width=22).grid(
            row=row, column=1, sticky=tk.W, pady=4)

        # Pause (already has a var from recording tab)
        row += 1
        ttk.Label(parent, text="Pause / Resume:").grid(
            row=row, column=0, sticky=tk.W, pady=4)
        if not hasattr(self, "_pause_hotkey_var"):
            self._pause_hotkey_var = tk.StringVar(value=self.config.hotkey.toggle_pause)
        ttk.Entry(parent, textvariable=self._pause_hotkey_var, width=22).grid(
            row=row, column=1, sticky=tk.W, pady=4)

        # Mute toggle
        row += 1
        ttk.Label(parent, text="Manual Mic Mute:").grid(
            row=row, column=0, sticky=tk.W, pady=4)
        self._mute_hotkey_var = tk.StringVar(value=self.config.hotkey.toggle_mute)
        ttk.Entry(parent, textvariable=self._mute_hotkey_var, width=22).grid(
            row=row, column=1, sticky=tk.W, pady=4)

        # Dashboard toggle
        row += 1
        ttk.Label(parent, text="Show / Hide Dashboard:").grid(
            row=row, column=0, sticky=tk.W, pady=4)
        self._dash_hotkey_var = tk.StringVar(value=self.config.hotkey.toggle_dashboard)
        ttk.Entry(parent, textvariable=self._dash_hotkey_var, width=22).grid(
            row=row, column=1, sticky=tk.W, pady=4)

        row += 1
        ttk.Label(
            parent,
            text='Format: "ctrl+shift+r", "alt+F10", etc.\n'
                 "Changes take effect after restart.",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(4, 8))

        # Separator
        row += 1
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky=tk.EW, pady=10)

        # App-level keyboard shortcuts (read-only info)
        row += 1
        ttk.Label(parent, text="Window Shortcuts (not configurable)", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(5, 4))

        shortcuts = [
            ("Ctrl+F", "Search / filter recordings"),
            ("Ctrl+,", "Open settings"),
            ("F1", "Toggle keyboard help"),
            ("F5", "Refresh recording history"),
            ("Escape", "Close detail / exit bulk select / hide"),
            ("\u2191 / \u2193", "Navigate recording list"),
            ("Enter", "Open selected recording"),
        ]
        for key, desc in shortcuts:
            row += 1
            ttk.Label(parent, text=key, font=("Consolas", 9, "bold")).grid(
                row=row, column=0, sticky=tk.W, pady=1, padx=(10, 0))
            ttk.Label(parent, text=desc).grid(
                row=row, column=1, sticky=tk.W, pady=1)

        parent.columnconfigure(1, weight=1)

    def _export_config(self) -> None:
        """Export config secrets to a portable file."""
        path = filedialog.asksaveasfilename(
            title="Export Config",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="meeting_recorder_config.json",
        )
        if not path:
            return
        try:
            from meeting_recorder.config_transfer import export_config
            result = export_config(path)
            if result == 0:
                messagebox.showinfo("Export Complete", f"Config exported to:\n{path}")
            else:
                messagebox.showwarning("Export Failed", "No secrets file found. Run the app once first.")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")

    def _import_config(self) -> None:
        """Import config secrets from a portable file."""
        path = filedialog.askopenfilename(
            title="Import Config",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        if not messagebox.askyesno(
            "Import Config",
            "This will overwrite your current API keys and secrets.\n\nContinue?",
        ):
            return

        try:
            from meeting_recorder.config_transfer import import_config
            result = import_config(path, overwrite=True)
            if result == 0:
                messagebox.showinfo(
                    "Import Complete",
                    "Config imported successfully.\n\nRestart the app for changes to take effect.",
                )
            else:
                messagebox.showwarning("Import Failed", "Could not import the config file.")
        except Exception as e:
            messagebox.showerror("Error", f"Import failed: {e}")

    def _browse_gdrive_creds(self) -> None:
        """Open file browser for Google Drive credentials JSON."""
        path = filedialog.askopenfilename(
            title="Select Google Drive Credentials JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self._gdrive_creds_var.set(path)

    def _browse_output_dir(self) -> None:
        """Open directory browser for output directory."""
        path = filedialog.askdirectory(title="Select Recording Output Directory")
        if path:
            self._output_dir_var.set(path)

    def _test_gemini_key(self) -> None:
        """Test the Gemini API key with a minimal request."""
        key = self._gemini_key_var.get().strip()
        if not key:
            self._gemini_test_btn.configure(text="No key")
            self._window.after(2000, lambda: self._gemini_test_btn.configure(text="Test"))
            return

        self._gemini_test_btn.configure(text="...", state="disabled")

        def _do_test():
            win = self._window

            def _safe_after(delay, fn):
                try:
                    if win is not None and win.winfo_exists():
                        win.after(delay, fn)
                except (tk.TclError, RuntimeError, AttributeError):
                    pass

            if win is None:
                return

            try:
                from google import genai
                model = self._gemini_model_var.get().strip() or "gemini-2.0-flash"
                with genai.Client(api_key=key) as client:
                    response = client.models.generate_content(
                        model=model, contents="Reply with just the word OK",
                    )
                if response and response.text:
                    _safe_after(0, lambda: self._gemini_test_btn.configure(text="OK!", state="normal"))
                else:
                    _safe_after(0, lambda: self._gemini_test_btn.configure(text="Fail", state="normal"))
            except Exception as e:
                short_err = str(e)[:40]
                logger.warning("Gemini API key test failed: %s", e)
                def _show_fail(msg=short_err):
                    self._gemini_test_btn.configure(text="Fail", state="normal")
                    messagebox.showwarning("Gemini Test Failed", f"API key test failed:\n{msg}")
                _safe_after(0, _show_fail)
            _safe_after(3000, lambda: self._gemini_test_btn.configure(text="Test"))

        import threading
        threading.Thread(target=_do_test, daemon=True).start()

    def _save(self) -> None:
        """Save settings and close."""
        try:
            # Validate numeric ranges
            fps = self._screen_fps_var.get()
            if fps < 1 or fps > 120:
                messagebox.showwarning("Invalid Setting", "FPS must be between 1 and 120.")
                return
            age = self._retention_age_var.get()
            if age < 0:
                messagebox.showwarning("Invalid Setting", "Max age days cannot be negative.")
                return
            size = self._retention_size_var.get()
            if size < 0:
                messagebox.showwarning("Invalid Setting", "Max total GB cannot be negative.")
                return

            self.config.recording.output_dir = self._output_dir_var.get()
            self.config.recording.language = self._language_var.get()
            self.config.recording.user_name = self._user_name_var.get()
            self.config.recording.auto_start = self._auto_start_var.get()
            self.config.recording.live_transcription = self._live_transcription_var.get()
            self.config.hotkey.toggle_recording = self._hotkey_var.get()
            self.config.hotkey.toggle_pause = self._pause_hotkey_var.get()
            if hasattr(self, "_mute_hotkey_var"):
                self.config.hotkey.toggle_mute = self._mute_hotkey_var.get()
            if hasattr(self, "_dash_hotkey_var"):
                self.config.hotkey.toggle_dashboard = self._dash_hotkey_var.get()

            self.config.vad.threshold = self._vad_threshold_var.get()
            self.config.vad.min_speech_duration_ms = self._min_speech_var.get()
            self.config.vad.min_silence_duration_ms = self._min_silence_var.get()

            self.config.transcription.backend = self._backend_var.get()
            self.config.transcription.model_size = self._model_size_var.get()
            self.config.transcription.device = self._device_var.get()
            self.config.transcription.compute_type = self._compute_type_var.get()
            self.config.transcription.openai_api_key = self._api_key_var.get()
            self.config.transcription.gemini_api_key = self._gemini_key_var.get()
            self.config.transcription.gemini_model = self._gemini_model_var.get()

            self.config.diarization.enabled = self._diarization_var.get()
            self.config.diarization.huggingface_token = self._hf_token_var.get()
            self.config.diarization.min_speakers = self._min_speakers_var.get()
            self.config.diarization.max_speakers = self._max_speakers_var.get()

            self.config.screen_recording.enabled = self._screen_rec_var.get()
            self.config.screen_recording.fps = self._screen_fps_var.get()

            self.config.outlook.enabled = self._outlook_var.get()
            self.config.outlook.buffer_minutes = self._outlook_buffer_var.get()

            self.config.google_drive.enabled = self._gdrive_var.get()
            self.config.google_drive.credentials_path = self._gdrive_creds_var.get()
            self.config.google_drive.folder_id = self._gdrive_folder_var.get()

            self.config.summary.enabled = self._summary_var.get()
            self.config.summary.provider = self._summary_provider_var.get()
            self.config.summary.api_key = self._summary_api_key_var.get()
            self.config.summary.model = self._summary_model_var.get()
            self.config.summary.max_transcript_tokens = self._summary_max_tokens_var.get()

            self.config.retention.enabled = self._retention_enabled_var.get()
            self.config.retention.max_age_days = self._retention_age_var.get()
            self.config.retention.max_total_gb = self._retention_size_var.get()

            self.config.dashboard.enabled = self._dash_enabled_var.get()
            self.config.dashboard.auto_show = self._dash_auto_show_var.get()
            self.config.dashboard.auto_hide = self._dash_auto_hide_var.get()
            self.config.dashboard.start_collapsed = self._dash_collapsed_var.get()
            self.config.dashboard.show_transcript = self._dash_transcript_var.get()
            self.config.dashboard.show_screen_preview = self._dash_preview_var.get()
            self.config.dashboard.opacity = self._dash_opacity_var.get()
            self.config.dashboard.position = self._dash_position_var.get()

            formats = []
            if self._fmt_json_var.get():
                formats.append("json")
            if self._fmt_txt_var.get():
                formats.append("txt")
            if self._fmt_srt_var.get():
                formats.append("srt")
            self.config.output.formats = formats

            self.config.save()

            if self._on_save:
                self._on_save(self.config)

            self._close()
            logger.info("Settings saved.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}")

    def _close(self) -> None:
        """Close the settings window."""
        if self._window is not None:
            self._window.destroy()
            self._window = None
