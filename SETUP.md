# Meeting Recorder - Windows Setup Guide

Complete instructions for installing Meeting Recorder on a fresh Windows machine with NVIDIA GPU support.

---

## Prerequisites

| Requirement | Minimum | Recommended | Notes |
|---|---|---|---|
| **OS** | Windows 10 21H2+ | Windows 11 | Must be x64, not ARM |
| **GPU** | NVIDIA GTX 1060 (6GB) | RTX 3060+ (8GB+) | CUDA required for local transcription |
| **NVIDIA Driver** | 525+ | 560+ | Check with `nvidia-smi` |
| **RAM** | 8 GB | 16 GB+ | large-v3 model uses ~3GB VRAM |
| **Python** | 3.11 | 3.12 | **Do NOT use 3.13** (torch compatibility) |
| **Git** | Any | Latest | For cloning the repo |
| **Microphone** | Any | USB mic | Built-in laptop mic works |

---

## Step 1: Install Python 3.12

1. Download **Python 3.12.x** from https://www.python.org/downloads/
   - Click "Download Python 3.12.x" (the specific patch version doesn't matter)
2. Run the installer
   - **CHECK** "Add python.exe to PATH" at the bottom of the first screen
   - Click "Install Now" (or "Customize" if you want to pick the install location)
3. Verify in a new terminal:
   ```
   python --version
   ```
   Should print `Python 3.12.x`. If it prints 3.13 or something else, you may have multiple Pythons installed — use the full path (e.g., `C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe`).

> **Important**: If the Microsoft Store version of Python launches instead, go to **Settings > Apps > App execution aliases** and turn OFF the "python.exe" and "python3.exe" aliases pointing to the Microsoft Store.

---

## Step 2: Install Git

1. Download from https://git-scm.com/download/win
2. Install with default options
3. Verify: `git --version`

---

## Step 3: Install NVIDIA CUDA Toolkit (if not already installed)

The NVIDIA driver alone is usually sufficient — PyTorch ships its own CUDA runtime. But verify:

```
nvidia-smi
```

If this command works and shows your GPU, you're good. If it doesn't, install the latest NVIDIA driver from https://www.nvidia.com/drivers.

---

## Step 4: Clone the Repository

Open a terminal (PowerShell or Git Bash) and run:

```bash
cd ~/Downloads
git clone https://github.com/bobbyni819/meeting-recorder.git
cd meeting-recorder
```

---

## Step 5: Install PyTorch with CUDA

PyTorch must be installed **before** the project dependencies because it needs the CUDA-specific build. Use the CUDA 12.8 builds — RTX 50-series (Blackwell) GPUs **require** cu128, and older GPUs (GTX 10-series and up) work with it too:

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
```

> On an older GPU with an older driver you can use the CUDA 12.1 builds instead (`--index-url https://download.pytorch.org/whl/cu121`), but do NOT use cu121 with an RTX 50-series card — it lacks Blackwell support and CUDA will silently be unavailable.

Verify CUDA is working:
```bash
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, GPU: {torch.cuda.get_device_name(0)}')"
```

Should print something like: `CUDA: True, GPU: NVIDIA GeForce RTX 3060 Ti`

> **Troubleshooting**: If CUDA is False, your NVIDIA driver may be too old. Update from https://www.nvidia.com/drivers.

---

## Step 6: Install the Project and Dependencies

From the `meeting-recorder` directory:

```bash
# Recommended: everything the default config uses, in one command
pip install -e ".[dev,local,cloud,gemini,gdrive,outlook]"
```

Or pick extras individually:

```bash
# Core dependencies + local transcription (whisper, pyannote)
pip install -e ".[local]"

# Optional: Gemini-powered transcription + summaries (default config backend)
pip install -e ".[gemini]"

# Optional: Outlook calendar integration (auto-names recordings from calendar events)
pip install -e ".[outlook]"

# Optional: Google Drive upload
pip install -e ".[gdrive]"

# Optional: OpenAI Whisper API transcription / OpenAI summaries
pip install -e ".[cloud]"          # or ".[summary-openai]" (same dependency)

# Optional: Anthropic-powered summaries
pip install -e ".[summary-anthropic]"

# Optional: bundled ffmpeg for the decoupled video encoder (h264_nvenc/libx264);
# screen recording falls back to cv2.VideoWriter without it
pip install -e ".[video-encode]"

# Optional: UIAutomation-based Zoom/Teams mute-state detection; mute sync
# falls back to the keyboard-hook + registry paths without it
pip install -e ".[uia]"

# Test dependencies
pip install -e ".[dev]"
```

> The full list of extras lives in `pyproject.toml` under `[project.optional-dependencies]`:
> `local`, `cloud`, `outlook`, `gdrive`, `summary-openai`, `summary-anthropic`, `gemini`, `video-encode`, `uia`, `dev`, `e2e`.

The `[local]` extra installs:
- `faster-whisper` — Whisper speech-to-text (CTranslate2, very fast on GPU)
- `torch` — PyTorch (already installed in step 5)
- `pyannote.audio` — Speaker diarization (who spoke when)

The core package installs:
- `proc-tap` — Per-process audio capture via Windows WASAPI loopback
- `PyAudioWPatch` — Microphone capture
- `pycaw` — Windows Audio Session API (detects which app is playing audio)
- `opencv-python` — Screen recording (mp4v codec)
- `mss` — Screen capture fallback
- `pystray` — System tray icon
- `keyboard` — Global hotkey support

> `scipy` (audio resampling) is pulled in transitively by the `[local]` extra. If you skip `[local]` entirely, install it manually: `pip install scipy`.

---

## Step 7: Accept HuggingFace Gated Model Licenses

Speaker diarization uses pyannote models that require accepting license agreements on HuggingFace:

1. Create a HuggingFace account at https://huggingface.co/join
2. Visit **each** of these model pages and click **"Agree and access repository"**
   (the pipeline loads `pyannote/speaker-diarization-3.1`, which pulls in the other two):
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
   - https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM *(easy to miss!)*
3. Create an access token:
   - Go to https://huggingface.co/settings/tokens
   - Click "New token" > name it anything > **Read** permission > Create
   - Copy the token (starts with `hf_...`)

You'll paste this token into the secrets file in the next step.

> **Common error**: `Cannot access gated repo for url...` means you missed one of the three model pages above. The wespeaker embedding model is the one people most often forget.

---

## Step 8: Configure Settings and API Keys

Configuration is **split into two files** so settings sync across machines but secrets never leave your computer:

| File | Contents | Synced? |
|---|---|---|
| `config.toml` (repo root) | Non-secret settings: backends, model sizes, FPS, hotkeys, features | **Yes** — git-tracked; `git pull` keeps machines in sync |
| `%USERPROFILE%\.meeting_recorder\secrets.toml` | API keys, tokens, machine-specific values (mic device, dashboard position) | **No** — local only, git-ignored |

The non-secret settings need no setup: the repo's `config.toml` already came with the clone and has working defaults (Gemini transcription backend, `large-v3` for local Whisper, diarization on, 30 fps screen recording). Edit that file directly to change behavior.

What you DO need to create is the secrets file.

### Create secrets.toml

Create the config directory (skip if it exists):

```bash
mkdir %USERPROFILE%\.meeting_recorder
```

Create `%USERPROFILE%\.meeting_recorder\secrets.toml` with a text editor (Notepad, VS Code, etc.):

```toml
[transcription]
gemini_api_key = "YOUR_GEMINI_KEY"          # from https://aistudio.google.com/apikey
openai_api_key = ""                         # only if backend = "cloud" (OpenAI Whisper API)

[diarization]
huggingface_token = "hf_YOUR_TOKEN_HERE"    # the Read token from Step 7

[summary]
api_key = ""                                # leave empty: summaries reuse the Gemini key above
```

### Where the keys come from

- **Gemini API key** (free tier works): go to https://aistudio.google.com/apikey and click "Create API key". **One Gemini key powers both transcription and summaries** — the default config uses `backend = "gemini"` and `provider = "gemini"`, and the summary step falls back to `transcription.gemini_api_key` when `[summary] api_key` is empty.
- **HuggingFace token**: created in Step 7 (Read permission). Only needed for speaker diarization.
- **OpenAI key**: only if you switch `backend = "cloud"` or `provider = "openai"` in the repo `config.toml`. Not needed for the default setup.

> **Tip**: `secrets.toml` is also written automatically when you save API keys from the app's Settings window — creating it by hand is just the fastest path on a fresh machine. If you're upgrading an old install that used a combined `%USERPROFILE%\.meeting_recorder\config.toml`, the app auto-migrates it into the split layout on first run.

Verify the keys are picked up:

```bash
python -m meeting_recorder diagnose
```

The **Secrets** section reports each key as `SET` or `EMPTY` (key values are never printed).

---

## Migrating from another machine

Already have Meeting Recorder configured on another computer? Don't re-create the keys — transfer them.

**On the old machine:**

```bash
python -m meeting_recorder export-config
```

This writes `~/meeting_recorder_config.json` containing your API keys **in plaintext**, plus a live Google OAuth token if you've authorized Drive upload there.

> **Security**: transfer the file privately (USB drive, direct copy between machines) — never email it, upload it, or commit it to git. Delete it from **both** machines after importing.

**On the new machine** (after Steps 1–7):

```bash
python -m meeting_recorder import-config meeting_recorder_config.json
python -m meeting_recorder diagnose
```

Notes:
- Machine-specific fields (mic device, dashboard position) are **reset automatically** on import — they don't carry over, and the new machine picks its own defaults.
- Non-secret settings are not in the bundle; they arrive with `git pull` of the repo.
- Double-check `transcription.device` in the repo `config.toml` matches the new machine's hardware: `"cuda"` with an NVIDIA GPU, `"cpu"` otherwise (`compute_type = "int8"` for CPU).
- If the bundle included the Google OAuth token, Drive upload works immediately — no browser re-authorization.

---

## Step 9: First Run — Model Download

The first time you run the app, it will download several models (this only happens once):

| Model | Size | Purpose |
|---|---|---|
| Silero VAD | ~2 MB | Voice activity detection |
| faster-whisper large-v3 | ~3 GB | Speech-to-text transcription |
| pyannote segmentation | ~20 MB | Speaker diarization |
| pyannote embedding | ~80 MB | Speaker identification |

Run in a terminal to see download progress:

```bash
cd ~/Downloads/meeting-recorder
python -m meeting_recorder
```

The app will:
1. Load config
2. Download/cache models (first run only, may take 5-10 minutes)
3. Show a system tray icon (a small icon near the clock)
4. Begin scanning for Zoom, Teams, or Webex meetings (if `auto_start = true`)

> **Auto-recording**: Auto-record is **off by default** (`recording.auto_start = false` in the repo `config.toml`). When enabled — via the tray menu ("Auto-Record Meetings"), Settings, or the config file — the app scans for meeting processes every 5 seconds and starts recording automatically when an active meeting is detected. You can always start manually with `Ctrl+Shift+R`.

> **Diagnostic check**: Run `python -m meeting_recorder diagnose` to verify your setup — it checks config validity, API key presence (SET/EMPTY, never values), GPU availability, model downloads, microphone access, and screen capture.

---

## Step 10: Verify Everything Works

1. **Start a test meeting** (Zoom or Teams, even a solo meeting works)
2. **The app should auto-detect** the meeting and start recording
   - You'll see a dashboard overlay appear in the top-right corner
   - The system tray icon tooltip will show "Recording..."
3. **Speak for 10-20 seconds**, then end the meeting
4. **The app auto-stops** when the meeting process exits
5. **Check the output** in `~/MeetingRecordings/` — you should see:
   - `app_audio.wav` — the meeting's audio (other participants)
   - `mic_audio.wav` — your microphone audio
   - `mixed.wav` — both tracks combined
   - `screen.mp4` — screen recording of the meeting window
   - `transcript.txt` / `transcript.json` / `transcript.srt` — transcription
   - `metadata.json` — recording metadata

---

## Usage

### Running the App

```bash
# Console mode (see logs in real-time, useful for debugging)
python -m meeting_recorder

# Background mode (no console window, runs silently in system tray)
pythonw launch.pyw
```

### Hotkeys (while recording)

| Hotkey | Action |
|---|---|
| `Ctrl+Shift+R` | Start/stop recording manually |
| `Ctrl+Shift+P` | Pause/resume recording |
| `Ctrl+Shift+U` | Toggle your mic mute |
| `Ctrl+Shift+D` | Show/hide the dashboard overlay |

All four are configurable in the `[hotkey]` section of the repo `config.toml` or in Settings > Hotkeys.

### Meeting App Mute Sync

The app detects when you mute/unmute in your meeting app:
- **Zoom**: Hooks `Alt+A` (Zoom's mute shortcut)
- **Teams**: Hooks `Ctrl+Shift+M` (Teams' mute shortcut)

> **Note**: Mute sync only works via keyboard shortcuts, not by clicking the mute button with your mouse. This is a known limitation because Zoom uses custom rendering that's not accessible via Windows UI Automation.

### Teams-Specific Behavior

Teams meetings run inside WebView2 (`msedgewebview2.exe`) child processes, which don't expose per-process audio through the WASAPI loopback that `proc-tap` uses. The recorder **automatically switches to desktop audio capture** when it detects Teams:

- **Desktop audio** captures all system sound via WASAPI loopback on the default output device. Your system volume must be **non-zero** (it doesn't need to be loud — even 2% works, but 0% = silence).
- The dashboard shows **"Desktop Audio"** in amber text when desktop capture is active, so you can confirm it's working.
- This happens automatically — no manual intervention needed.

**Zoom** works differently: Zoom exposes per-process audio normally, so the recorder uses direct `proc-tap` capture. System volume doesn't matter for Zoom recordings.

### Recording Any Window

You can record any application — not just Zoom, Teams, or Webex:

- **If no meeting app is running**, press `Ctrl+Shift+R` and a window picker dialog will appear. Select the window you want to record and click "Record".
- **"Record Window..." tray menu item** — right-click the system tray icon and choose "Record Window..." to always open the picker, even if a meeting app is running. Useful for recording Discord calls, browser tabs, presentations, etc.
- **Audio strategy**: The recorder tries per-process audio capture first. If the selected app produces no audio for 3 seconds, it automatically switches to desktop audio (same as Teams).
- **Mute sync is disabled** for manually-selected windows since there's no standard mute shortcut to hook.

---

## Troubleshooting

### "CUDA not available" or transcription is very slow

- Make sure you installed the CUDA version of PyTorch (Step 5)
- Run `nvidia-smi` to verify your GPU is detected
- Update your NVIDIA driver if it's older than version 525

### "Cannot access gated repo" during first run

- You missed accepting a model license in Step 7
- Most commonly it's the `pyannote/wespeaker-voxceleb-resnet34-LM` embedding model
- Go to each URL, click "Agree and access repository", then retry

### "AudioDecoder is not defined" or pyannote errors

- pyannote.audio 4.x is incompatible — make sure you have version 3.x:
  ```bash
  pip install "pyannote.audio>=3.1,<4.0"
  ```

### No audio from Teams meetings

- Teams uses desktop audio capture (not per-process) — your **system volume must be non-zero**
- Check that the dashboard shows "Desktop Audio" in amber during a Teams recording
- If it shows "App Audio", the recorder didn't detect Teams — restart the recorder after joining the meeting
- Make sure your speakers/headphones are set as the default output device in Windows Sound settings

### No audio captured / empty WAV files

- Make sure the meeting app (Zoom/Teams) is playing audio through your default output device
- ProcTap captures per-process audio — if the wrong PID is selected, check the log file (`meeting_recorder.log`) for which process was detected

### App crashes immediately with no output

- If running with `pythonw.exe`, there's no console output — check `meeting_recorder.log` in the project directory
- Common cause: missing dependencies — run `pip install -e ".[local]"` again

### Screen recording is black

- Some apps (especially Zoom in GPU-accelerated mode) don't work with PrintWindow API
- The app automatically falls back to `mss` region capture, but if the window is on a different monitor it may capture the wrong area
- Check the log for "PrintWindow returned blank frame; falling back to mss"

### Dashboard doesn't appear

- Make sure `[dashboard] enabled = true` in config
- Press `Ctrl+Shift+D` to toggle it
- The dashboard requires tkinter — this is included with the standard Python installer but NOT with some minimal installs

---

## File Locations

| Item | Path |
|---|---|
| Settings (non-secret, git-tracked) | `config.toml` in the repo root |
| Secrets (API keys, tokens — local only) | `%USERPROFILE%\.meeting_recorder\secrets.toml` |
| Legacy combined config | `%USERPROFILE%\.meeting_recorder\config.toml` (auto-migrated to the split layout on first run) |
| Recordings | `%USERPROFILE%\MeetingRecordings\` |
| Log file | `meeting_recorder.log` (in working directory) |
| Whisper model cache | `%USERPROFILE%\.cache\huggingface\hub\` |
| Silero VAD cache | `%USERPROFILE%\.cache\torch\hub\` |
| Voice profiles DB | `%USERPROFILE%\.meeting_recorder\voice_profiles.db` |

---

## Updating

```bash
cd ~/Downloads/meeting-recorder
git pull
pip install -e ".[dev,local,cloud,gemini,gdrive,outlook]"
```

`git pull` also brings the latest non-secret settings (`config.toml`) — your `secrets.toml` is untouched.

---

## GPU Memory Requirements by Model Size

| Model | VRAM | Speed (RTX 3060 Ti) | Accuracy |
|---|---|---|---|
| `tiny` | ~1 GB | ~30x realtime | Low |
| `base` | ~1 GB | ~20x realtime | Fair |
| `small` | ~2 GB | ~12x realtime | Good |
| `medium` | ~5 GB | ~5x realtime | Very good |
| `large-v3` | ~3 GB* | ~6x realtime | Best |

*large-v3 with CTranslate2 (faster-whisper) uses less VRAM than the original Whisper.

If your GPU has less than 4GB VRAM, use `model_size = "small"` in the config.
If you have no GPU, set `device = "cpu"` and `compute_type = "int8"`.
