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
git clone https://github.com/bobbyni819/meeting_recorder.git
cd meeting_recorder
```

---

## Step 5: Install PyTorch with CUDA

PyTorch must be installed **before** the project dependencies because it needs the CUDA-specific build. The project uses CUDA 12.1 builds:

```bash
pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
```

Verify CUDA is working:
```bash
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, GPU: {torch.cuda.get_device_name(0)}')"
```

Should print something like: `CUDA: True, GPU: NVIDIA GeForce RTX 3060 Ti`

> **Troubleshooting**: If CUDA is False, your NVIDIA driver may be too old. Update from https://www.nvidia.com/drivers.

---

## Step 6: Install the Project and Dependencies

From the `meeting_recorder` directory:

```bash
# Core dependencies + local transcription (whisper, pyannote)
pip install -e ".[local]"

# Optional: Outlook calendar integration (auto-names recordings from calendar events)
pip install -e ".[outlook]"

# Optional: OpenAI-powered meeting summaries
pip install -e ".[summary-openai]"

# Optional: Google Drive upload
pip install -e ".[gdrive]"

# Optional: All of the above in one command
pip install -e ".[local,outlook,summary-openai,gdrive]"
```

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
- `scipy` — Audio resampling (polyphase anti-aliasing filter)

---

## Step 7: Accept HuggingFace Gated Model Licenses

Speaker diarization uses pyannote models that require accepting license agreements on HuggingFace:

1. Create a HuggingFace account at https://huggingface.co/join
2. Visit **each** of these model pages and click **"Agree and access repository"**:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
   - https://huggingface.co/pyannote/speaker-diarization-community-1 *(easy to miss!)*
3. Create an access token:
   - Go to https://huggingface.co/settings/tokens
   - Click "New token" > name it anything > **Read** permission > Create
   - Copy the token (starts with `hf_...`)

You'll paste this token into the config file in the next step.

> **Common error**: `Cannot access gated repo for url...` means you missed one of the three model pages above. The community-1 model is the one people most often forget.

---

## Step 8: Create the Configuration File

Create the config directory and file:

```bash
mkdir %USERPROFILE%\.meeting_recorder
```

Create `%USERPROFILE%\.meeting_recorder\config.toml` with a text editor (Notepad, VS Code, etc.):

```toml
[recording]
output_dir = "~/MeetingRecordings"
language = "en"
user_name = "Your Name"            # Your name (used to label your mic audio in transcripts)
live_transcription = false          # Set to true for real-time transcript in dashboard

[audio]
sample_rate = 16000
channels = 1
chunk_duration_ms = 30
mic_device = ""                     # Leave empty for default mic

[vad]
threshold = 0.5
min_speech_duration_ms = 250
min_silence_duration_ms = 300

[transcription]
backend = "local"                   # "local" for on-device whisper, "cloud" for OpenAI API
model_size = "large-v3"             # Options: tiny, base, small, medium, large-v3
device = "cuda"                     # "cuda" for GPU, "cpu" for CPU-only (much slower)
compute_type = "float16"            # "float16" for GPU, "int8" for CPU
openai_api_key = ""                 # Only needed if backend = "cloud"

[diarization]
enabled = true
huggingface_token = "hf_YOUR_TOKEN_HERE"   # Paste your HuggingFace token from Step 7
min_speakers = 2
max_speakers = 6

[output]
formats = ["json", "txt", "srt"]

[hotkey]
toggle_recording = "ctrl+shift+r"   # Start/stop recording
toggle_mute = "ctrl+shift+u"        # Toggle your mic mute
toggle_dashboard = "ctrl+shift+d"   # Show/hide the dashboard overlay

[screen_recording]
enabled = true
fps = 30.0                          # 15-30 recommended; higher = larger files

[outlook]
enabled = false                     # Set to true if you have Outlook and installed [outlook] extra
buffer_minutes = 10

[google_drive]
enabled = false
credentials_path = "~/.meeting_recorder/google_credentials.json"
folder_id = ""

[summary]
enabled = false                     # Set to true and provide API key for auto-summaries
provider = "openai"
api_key = ""                        # Your OpenAI API key (sk-...)
model = "gpt-4o-mini"
max_transcript_tokens = 0

[dashboard]
enabled = true
auto_show = true                    # Automatically show dashboard when recording starts
auto_hide = true                    # Automatically hide when recording stops
opacity = 0.92
position = "top-right"
start_collapsed = false
show_transcript = true
show_screen_preview = true          # Live thumbnail of captured window
```

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
cd ~/Downloads/meeting_recorder
python -m meeting_recorder
```

The app will:
1. Load config
2. Download/cache models (first run only, may take 5-10 minutes)
3. Show a system tray icon (a small icon near the clock)
4. Begin scanning for Zoom, Teams, or Webex processes

> **First-run tip**: Start a Zoom or Teams call before running, or the app will just wait in the tray until it detects a meeting process.

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
pythonw -m meeting_recorder
```

### Hotkeys (while recording)

| Hotkey | Action |
|---|---|
| `Ctrl+Shift+R` | Start/stop recording manually |
| `Ctrl+Shift+U` | Toggle your mic mute |
| `Ctrl+Shift+D` | Show/hide the dashboard overlay |

### Meeting App Mute Sync

The app detects when you mute/unmute in your meeting app:
- **Zoom**: Hooks `Alt+A` (Zoom's mute shortcut)
- **Teams**: Hooks `Ctrl+Shift+M` (Teams' mute shortcut)

> **Note**: Mute sync only works via keyboard shortcuts, not by clicking the mute button with your mouse. This is a known limitation because Zoom uses custom rendering that's not accessible via Windows UI Automation.

---

## Troubleshooting

### "CUDA not available" or transcription is very slow

- Make sure you installed the CUDA version of PyTorch (Step 5)
- Run `nvidia-smi` to verify your GPU is detected
- Update your NVIDIA driver if it's older than version 525

### "Cannot access gated repo" during first run

- You missed accepting a model license in Step 7
- Most commonly it's the `pyannote/speaker-diarization-community-1` model
- Go to each URL, click "Agree and access repository", then retry

### "AudioDecoder is not defined" or pyannote errors

- pyannote.audio 4.x is incompatible — make sure you have version 3.x:
  ```bash
  pip install "pyannote.audio>=3.1,<4.0"
  ```

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
| Config file | `%USERPROFILE%\.meeting_recorder\config.toml` |
| Recordings | `%USERPROFILE%\MeetingRecordings\` |
| Log file | `meeting_recorder.log` (in working directory) |
| Whisper model cache | `%USERPROFILE%\.cache\huggingface\hub\` |
| Silero VAD cache | `%USERPROFILE%\.cache\torch\hub\` |
| Voice profiles DB | `%USERPROFILE%\.meeting_recorder\voice_profiles.db` |

---

## Updating

```bash
cd ~/Downloads/meeting_recorder
git pull
pip install -e ".[local]"
```

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
