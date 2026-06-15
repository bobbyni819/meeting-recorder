# Windows Speech Recognition Probe

`probe_windows_speech.py` is a standalone research tool for evaluating the
Windows on-device dictation recognizer through the maintained `winsdk` WinRT
bindings. It is not wired into the app.

## Install

```powershell
py -m pip install winsdk
```

On this machine, `winsdk==1.0.0b10` installed successfully. The legacy `winrt`
package was not needed.

## Run

Probe the newest recorded mic track:

```powershell
py scripts/probe_windows_speech.py
```

Probe a specific WAV:

```powershell
py scripts/probe_windows_speech.py C:\path\to\mic_audio.wav
```

Run a live default-microphone dictation test:

```powershell
py scripts/probe_windows_speech.py C:\path\to\mic_audio.wav --mic-seconds 20
```

If live mic mode fails with `0x80045509`, Windows is rejecting dictation because
the speech privacy policy has not been accepted. Enable the relevant setting
under Windows speech/privacy settings, then rerun the probe.

## Findings

The installed `winsdk` binding exposes
`winsdk.windows.media.speechrecognition.SpeechRecognizer` with dictation grammar
support, `recognize_async()`, and `continuous_recognition_session`. It does not
expose a public file, stream, media source, or audio graph input method that can
feed an arbitrary WAV into `SpeechRecognizer`. The script therefore reports
file-mode as unsupported rather than fabricating a transcript.

The viable evaluation path exposed by this API is microphone-driven continuous
recognition from the default input device. That can still benchmark the same
Windows dictation engine for a live "You" mic track, but it does not directly
transcribe saved meeting audio files.

On this machine, file mode correctly reported unsupported for a real
`mic_audio.wav`. Live mic mode started far enough to reach Windows speech, then
failed with `0x80045509`, meaning the human user must accept/enable Windows
speech privacy settings before collecting live recognition text.

## Viability Assessment

For the live "You" mic preview, Windows speech is worth a live benchmark: it is
low-resource, on-device, and tuned for single-speaker dictation from a mic.

For final meeting transcripts, it is a poor fit through this API. The recognizer
does not diarize, does not handle multiple speakers, and the Python-accessible
WinRT surface does not accept arbitrary WAV input. It should not replace the
current Gemini / Whisper final transcript pipeline without a separate,
reliable audio-injection strategy.
