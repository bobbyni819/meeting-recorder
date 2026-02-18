# Meeting Recorder — Claude Code Context

## What this project does
Automatically records meeting audio (app + mic), screen, transcribes with Whisper large-v3,
diarizes speakers with pyannote, generates AI summaries, and uploads to Google Drive.
Runs as a Windows system-tray app (`launch.pyw`).

## Key files
- `meeting_recorder/app.py` — top-level orchestrator (tray, hotkeys, recording lifecycle)
- `meeting_recorder/config.py` — all settings; config file lives at `~/.meeting_recorder/config.toml`
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

## Known pitfalls
- `pythonw.exe` sets `sys.stdout/stderr = None` — redirect to devnull before `torch.hub.load`
- `torch.hub.load` deadlocks in background threads when pystray + keyboard hooks are active;
  pre-load the VAD model on the main thread before `pystray` starts
- `stop_recording()` is protected by `_stop_lock` to prevent double-invocation from concurrent
  callers (Stop button + process-exit auto-stop); do not remove this lock
- Teams calendar window (main PID) produces no audio — always use the meeting window PID

## Config defaults (current)
- `transcription.model_size = "large-v3"` (base is too inaccurate)
- `diarization.enabled = true` (requires HuggingFace token + 3 gated model acceptances)
- `screen_recording.enabled = true`, `fps = 15`
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
