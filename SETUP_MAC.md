# macOS Setup

This is the Phase 2 macOS backend for Meeting Recorder. It is additive to the
Windows implementation; macOS-specific dependencies are installed through the
`macos` extra.

## Install

From the repo root:

```bash
python3 -m pip install -e '.[macos,local,video-encode]'
```

Use `cloud` instead of `local` if you transcribe through OpenAI:

```bash
python3 -m pip install -e '.[macos,cloud,video-encode]'
```

## System Audio

macOS does not provide Windows-style per-process loopback through PortAudio.
Install a virtual loopback input device:

```bash
brew install blackhole-2ch
```

Then open **Audio MIDI Setup**:

1. Create a **Multi-Output Device** with your speakers/headphones and
   **BlackHole 2ch** checked. Use it as the system output when recording.
2. If your setup needs one named input/output pair, create an **Aggregate
   Device** that includes BlackHole and the relevant physical device.
3. In Meeting Recorder, use the default loopback name `BlackHole`, or pass the
   sounddevice input index/name for your virtual input.

## Permissions

Grant permissions to the exact app launching Python, such as Terminal, iTerm,
PyCharm, VS Code, or a packaged Meeting Recorder app:

- **Microphone**: required for mic capture.
- **Screen Recording**: required for Quartz/mss screen capture.
- **Accessibility**: required for global hotkeys and best-effort mute detection.

After changing these permissions, fully quit and reopen the launcher app.

## Run

```bash
meeting-recorder
```

If running from source without installing scripts:

```bash
python3 -m meeting_recorder
```

## Running the app

From the repo root, run the macOS menu bar app directly:

```bash
python3 launch_mac.py
```

You can also use the package entry point:

```bash
python3 -m meeting_recorder mac
```

On first launch, grant macOS permissions to the app that starts Python
(Terminal, iTerm, VS Code, PyCharm, or a packaged Meeting Recorder app):
Microphone for mic audio, Screen Recording for screen capture, and
Accessibility for global hotkeys and mute-state probing. Fully quit and
reopen that launcher after changing permissions.

For system audio, install and configure BlackHole:

```bash
brew install blackhole-2ch
```

Route your meeting/system output through a Multi-Output or Aggregate Device
that includes BlackHole. If BlackHole is not available, the app continues with
microphone-only capture and leaves the WAV files available for recovery.

## Known Limitations

- System audio currently requires BlackHole or another loopback input device.
- App audio is system-wide through the loopback device, not per-process.
- Mute detection uses the Accessibility tree and needs live tuning against
  current Zoom, Teams, and Webex builds.
- ScreenCaptureKit is the planned future upgrade for native window and system
  audio capture.
