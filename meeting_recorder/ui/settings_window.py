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
        self._window.geometry("500x600")
        self._window.resizable(False, False)

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
        ttk.Label(parent, text="OpenAI API Key:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._api_key_var = tk.StringVar(value=self.config.transcription.openai_api_key)
        ttk.Entry(parent, textvariable=self._api_key_var, width=40, show="*").grid(row=row, column=1, sticky=tk.W, pady=5)

        row += 1
        ttk.Label(parent, text="Gemini API Key:").grid(row=row, column=0, sticky=tk.W, pady=5)
        self._gemini_key_var = tk.StringVar(value=self.config.transcription.gemini_api_key)
        ttk.Entry(parent, textvariable=self._gemini_key_var, width=40, show="*").grid(row=row, column=1, sticky=tk.W, pady=5)

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
        ttk.Label(
            parent,
            text="Leave Model empty for provider default\n(gpt-4o / claude-sonnet-4-20250514).",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

        parent.columnconfigure(1, weight=1)

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

    def _save(self) -> None:
        """Save settings and close."""
        try:
            self.config.recording.output_dir = self._output_dir_var.get()
            self.config.recording.language = self._language_var.get()
            self.config.recording.user_name = self._user_name_var.get()
            self.config.recording.auto_start = self._auto_start_var.get()
            self.config.recording.live_transcription = self._live_transcription_var.get()
            self.config.hotkey.toggle_recording = self._hotkey_var.get()
            self.config.hotkey.toggle_pause = self._pause_hotkey_var.get()

            self.config.vad.threshold = self._vad_threshold_var.get()
            self.config.vad.min_speech_duration_ms = self._min_speech_var.get()
            self.config.vad.min_silence_duration_ms = self._min_silence_var.get()

            self.config.transcription.backend = self._backend_var.get()
            self.config.transcription.model_size = self._model_size_var.get()
            self.config.transcription.device = self._device_var.get()
            self.config.transcription.openai_api_key = self._api_key_var.get()
            self.config.transcription.gemini_api_key = self._gemini_key_var.get()
            self.config.transcription.gemini_model = self._gemini_model_var.get()

            self.config.diarization.enabled = self._diarization_var.get()
            self.config.diarization.huggingface_token = self._hf_token_var.get()

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

            self.config.dashboard.enabled = self._dash_enabled_var.get()
            self.config.dashboard.auto_show = self._dash_auto_show_var.get()
            self.config.dashboard.auto_hide = self._dash_auto_hide_var.get()
            self.config.dashboard.start_collapsed = self._dash_collapsed_var.get()
            self.config.dashboard.show_transcript = self._dash_transcript_var.get()
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
