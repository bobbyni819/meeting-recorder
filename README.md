# vaultlab-meetings

Smart Windows meeting recorder with per-app audio capture, voice-activity detection, and local-or-cloud transcription. Originally built standalone; now also serves as the meeting-context layer for [vaultlab](https://github.com/bobbyni819/vaultlab).

> **Install name:** `vaultlab-meetings` (PyPI)
> **Import name:** `meeting_recorder` (Python)

```bash
pip install vaultlab-meetings
```

```python
import meeting_recorder
```

The split is intentional — `meeting-recorder` was already taken on PyPI, so the package ships under the `vaultlab-meetings` name while keeping the original import name for stability across existing scripts.

## What it does

- Captures per-app audio on Windows (Zoom, Teams, browser-based meetings, anything that goes through Windows audio)
- Voice-activity detection trims silence and skips empty stretches
- Local transcription via [faster-whisper](https://github.com/SYSTRAN/faster-whisper) or cloud via OpenAI
- Optional speaker diarization via pyannote.audio
- System-tray app with hotkey controls; saves recordings + transcripts to a configurable folder
- Optional Outlook calendar integration: auto-tags recordings with the meeting that was on your calendar

## Install

Base install (recording only, no transcription backend):

```bash
pip install vaultlab-meetings
```

With local transcription (faster-whisper + torch):

```bash
pip install "vaultlab-meetings[local]"
```

With cloud transcription (OpenAI Whisper API):

```bash
pip install "vaultlab-meetings[cloud]"
```

With Outlook calendar tagging (Windows + Outlook Classic):

```bash
pip install "vaultlab-meetings[outlook]"
```

Combine extras as needed: `pip install "vaultlab-meetings[local,outlook,gdrive]"`.

## Quick start

```bash
meeting-recorder
```

Drops a tray icon. Hotkeys (configurable in `config.toml`):
- Start/stop recording
- Mark a moment (saves a timestamped flag in the transcript)
- Open the recordings folder

Recordings + transcripts land at the path set in `config.toml` (default: `~/MeetingRecordings/`).

## Use from vaultlab

When `vaultlab[meetings]` is installed (which pulls `vaultlab-meetings`), vaultlab's `vaultlab.context.meetings` module wraps this package and ingests transcripts into your KB:

```python
from vaultlab.context.meetings import is_available, start_recording

if is_available():
    start_recording(project="codex-pdac")
```

Transcripts land at `<kb>/Sources/Meetings/<YYYY-MM-DD>-<slug>.md` with frontmatter (date, attendees, project, recording_path, duration, transcription_model) so they're searchable from any vaultlab slash command.

## Platform

Currently **Windows-only**. Per-app audio capture relies on `PyAudioWPatch`, `pycaw`, and `mss`, which are Windows-specific. macOS/Linux support is planned via a different recording backend; PRs welcome.

## License

MIT. See [`LICENSE`](LICENSE).
