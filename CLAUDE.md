# Meeting Recorder — Claude Code Context

## What this project does
Automatically records meeting audio (app + mic), screen, transcribes with Whisper large-v3,
diarizes speakers with pyannote, generates AI summaries, and uploads to Google Drive.
Runs as a Windows system-tray app (`launch.pyw`).

## Key files
- `meeting_recorder/app.py` — top-level orchestrator (tray, hotkeys, recording lifecycle)
- `meeting_recorder/config.py` — all settings; split config (see below)
- `meeting_recorder/audio/capture_manager.py` — coordinates app audio + mic + screen capture threads
- `meeting_recorder/audio/app_audio.py` — per-process audio via ProcTap (WASAPI loopback)
- `meeting_recorder/audio/mic_audio.py` — mic capture via PyAudioWPatch + Silero VAD
- `meeting_recorder/audio/resampling.py` — resample to 16kHz mono int16; NoiseGate class
- `meeting_recorder/video/screen_capture.py` — window capture via PrintWindow API (falls back to mss)
- `meeting_recorder/video/window_finder.py` — Win32 EnumWindows helpers
- `meeting_recorder/ui/dashboard.py` — floating overlay (Tkinter GameBarDashboard)
- `meeting_recorder/transcription/` — faster-whisper pipeline + pyannote diarization
- `SETUP.md` — full install guide for a new Windows machine

## Audio pipeline
```
ProcTap (48kHz stereo float32)  →  resample_to_16khz_mono  →  NoiseGate  →  RingBuffer  →  WAV writer
PyAudioWPatch mic (44.1kHz 2ch) →  resample_to_16khz_mono  →  Silero VAD →  RingBuffer  →  WAV writer
```
- Silero VAD needs **exactly 512 samples at 16kHz** per chunk
- `proctap.ProcessAudioCapture` is the correct class (not `ProcTap`)
- Mute sync hooks **Alt+A** (Zoom) and **Ctrl+Shift+M** (Teams) keyboard shortcuts
- Mute sync starts **UNMUTED** by default — starting muted silences all mic audio

## Screen capture
- Uses Win32 `PrintWindow` API first (captures only the window, no overlays)
- Falls back to `mss` region grab if PrintWindow returns a blank frame
- Teams / Zoom often have multiple processes; the meeting window may be a different PID
  than the one `process_finder` selects — use the **⊙ Window** picker in the dashboard
  to switch both screen and audio capture to the correct window mid-recording
- `switch_screen_window(hwnd)` hot-swaps both screen capture AND audio PID atomically

## Record Anything mode
- If no meeting app (Zoom/Teams/Webex) is detected, `start_recording()` opens a window picker
- "Record Window..." tray menu item always opens the picker regardless of meeting app state
- Selected window uses `app_key="manual"` — disables mute sync, skips Outlook calendar
- Audio: tries ProcTap per-process first, auto-switches to desktop audio if silent for 3 seconds
- `_silence_auto_switch()` thread in capture_manager.py handles the detection
- Health warnings (silence, volume, write errors) surfaced via `_on_health_warning(key)` callback

## Thread safety
- `_transcriber_lock` in capture_manager.py protects `_live_transcriber` (writer loop + stop)
- `_metadata_lock` in app.py protects all `metadata.save()` calls
- Ring buffer tracks overflow count, logs warning every 100 drops
- Writer thread sets `_write_error` flag and fires health warning on exception

## Pause/Resume
- `Ctrl+Shift+P` (configurable) toggles pause during recording
- Paused state: audio capture continues but data is discarded (not written to WAV)
- Screen capture still grabs frames for live preview but doesn't write to video
- `CaptureManager.pause()` / `resume()` / `toggle_pause()` / `is_paused`
- `_pause_lock` in capture_manager.py protects pause state transitions
- `elapsed_seconds` excludes paused duration via `_total_paused_seconds` tracking
- Dashboard shows amber "⏸ PAUSED" indicator and pause button toggles play/pause icon
- Tray menu "Pause / Resume" item, enabled only during recording

## Voice profiles
- `VoiceProfileDB` in `transcription/voice_profiles.py` stores speaker embeddings in SQLite
- Auto-enrolled during transcription when diarization identifies speakers
- Cross-meeting speaker identification via cosine similarity (threshold 0.75)
- Settings > Speakers tab: list, rename, delete voice profiles
- `list_profiles_detailed()` returns name, sample_count, timestamps
- `rename_profile()` renames with conflict detection

## Re-process recording
- `reprocess_recording(path)` in app.py re-runs transcription + summary on existing audio
- Available via "Re-process" button in recording detail view and right-click context menu
- `reprocess_all_failed()` batch re-processes all recordings with status "error"
- Uses current config (not original recording's config) — useful after switching backends
- Guards against concurrent re-processing (checks if post-processing thread is alive)

## Search index
- SQLite FTS5 index in `~/.meeting_recorder/recordings.db`
- Auto-syncs on startup via background thread (`_sync_search_index`)
- `RecordingIndex.sync()` adds new recordings, removes deleted ones
- Each post-processed recording indexed individually via `_index_recording`

## Known pitfalls
- `pythonw.exe` sets `sys.stdout/stderr = None` — redirect to devnull before `torch.hub.load`
- `torch.hub.load` deadlocks in background threads when pystray + keyboard hooks are active;
  pre-load the VAD model on the main thread before `pystray` starts
- `stop_recording()` is protected by `_stop_lock` to prevent double-invocation from concurrent
  callers (Stop button + process-exit auto-stop); do not remove this lock
- Teams calendar window (main PID) produces no audio — always use the meeting window PID

## Meeting auto-detection
- `recording.auto_start = true` enables background scanner thread
- `_meeting_scanner_loop()` polls every 5 seconds when idle
- Auto-starts recording when window score >= 50 (active meeting, not idle lobby)
- Toggleable from tray menu ("Auto-Record Meetings") — persists to config on toggle
- Scanner pauses during recording and resumes after post-processing completes
- Disk space check before recording: refuses at < 100 MB, warns at < 1 GB

## Recording retention
- `[retention]` config section: `enabled`, `max_age_days` (default 90), `max_total_gb` (default 0)
- `RecordingStore.cleanup()` deletes by age and/or total size, oldest-first
- Runs at app startup and after each post-processing completes
- Never deletes the active recording directory (`exclude` parameter)
- Disabled by default — user must set `retention.enabled = true`

## Config validation
- `Config.validate()` checks backend, provider, fps, vad threshold, retention values
- Logs warnings for invalid values; does not raise
- Called automatically in `MeetingRecorderApp.run()` at startup

## Split config (multi-machine sync)
Config is split into two layers so non-secret settings sync via git:
- **`config.toml`** (repo root, git-tracked) — model choices, FPS, features, all non-secret settings
- **`~/.meeting_recorder/secrets.toml`** (local only, git-ignored) — API keys, tokens, mic device, dashboard position

`Config.load()` reads bundled config first, then overlays secrets.toml.
`Config.save()` splits: secrets → secrets.toml, everything else → repo config.toml.
On first run after upgrade, auto-migrates from legacy `~/.meeting_recorder/config.toml` → split files.

Secret/local fields: `transcription.openai_api_key`, `transcription.gemini_api_key`,
`diarization.huggingface_token`, `summary.api_key`, `audio.mic_device`,
`dashboard.position_x`, `dashboard.position_y`.

## Config defaults (current)
- `recording.auto_start = false` (manual window picker; auto-detect available via toggle)
- `transcription.model_size = "large-v3"` (base is too inaccurate)
- `diarization.enabled = true` (requires HuggingFace token + 3 gated model acceptances)
- `screen_recording.enabled = true`, `fps = 30`
- `retention.enabled = false`, `max_age_days = 90`, `max_total_gb = 0`
- Output: `~/MeetingRecordings/`

## Development
```bash
# Install
pip install -e ".[dev,audio,video,transcription,diarization,summary,search,integrations]"

# Run tests
python -m pytest tests/ -q

# Launch
python -m meeting_recorder        # console (shows logs)
pythonw launch.pyw                # background (tray only)
```

## Dependencies requiring manual steps
- **PyTorch + CUDA**: `pip install torch --index-url https://download.pytorch.org/whl/cu121`
- **pyannote.audio**: pin to `3.4.0` (4.x has breaking API changes)
- **HuggingFace gated models**: must accept on hf.co before first run:
  - `pyannote/speaker-diarization-3.1`
  - `pyannote/segmentation-3.0`
  - `pyannote/wespeaker-voxceleb-resnet34-LM`
