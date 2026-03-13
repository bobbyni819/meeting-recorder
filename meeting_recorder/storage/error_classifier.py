"""Classify recording errors and suggest fixes.

Parses error_message strings from metadata and maps them to
known categories with human-readable explanations and remediation steps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ErrorClassification:
    """Classified recording error with remediation."""
    category: str  # e.g. "audio", "transcription", "gpu", "network", "storage"
    title: str  # short human-readable title
    explanation: str  # what went wrong
    suggestions: list[str]  # how to fix it
    retryable: bool  # whether re-processing could fix it


# Patterns: (regex, category, title, explanation, suggestions, retryable)
_PATTERNS: list[tuple[str, str, str, str, list[str], bool]] = [
    # Audio issues
    (
        r"(?i)(corrupt|empty).*(audio|wav)|(audio|wav).*(corrupt|empty)",
        "audio", "Corrupt or empty audio file",
        "The audio file was damaged or no audio data was captured.",
        ["Check that the meeting app was producing audio",
         "Try desktop audio mode instead of per-process capture",
         "Ensure system volume is not muted"],
        False,
    ),
    (
        r"(?i)no audio (device|input|found)",
        "audio", "No audio device found",
        "The system could not find an audio input device.",
        ["Check that your microphone is connected",
         "Verify audio device in Windows Sound settings",
         "Try restarting the application"],
        False,
    ),
    (
        r"(?i)(pyaudio|portaudio|wasapi).*error",
        "audio", "Audio driver error",
        "The audio capture library encountered a driver-level error.",
        ["Restart the application",
         "Check Windows audio service (Windows Audio / AudioEndpointBuilder)",
         "Update audio drivers"],
        True,
    ),
    (
        r"(?i)silence|no speech|empty transcript",
        "audio", "No speech detected",
        "Audio was captured but no speech was detected in the recording.",
        ["Check that system volume was not muted",
         "Ensure the meeting app was producing audio output",
         "Try switching to desktop audio mode",
         "Move closer to the microphone"],
        False,
    ),
    # Transcription issues
    (
        r"(?i)(whisper|faster.?whisper|transcri).*(fail|error|crash)",
        "transcription", "Transcription engine error",
        "The Whisper transcription engine failed to process the audio.",
        ["Re-process the recording (uses current config)",
         "Try a smaller model size (e.g. base or small)",
         "Check GPU memory availability"],
        True,
    ),
    (
        r"(?i)(model|weight).*(not found|missing|download)",
        "transcription", "Model not found",
        "The transcription model could not be loaded or downloaded.",
        ["Check internet connection for model download",
         "Verify model_size in config (large-v3, base, small)",
         "Clear model cache and re-download"],
        True,
    ),
    (
        r"(?i)gemini.*(error|fail|quota|limit|timeout)",
        "transcription", "Gemini API error",
        "The Gemini transcription API returned an error.",
        ["Check your Gemini API key in secrets.toml",
         "Verify API quota has not been exceeded",
         "Try switching to local transcription backend"],
        True,
    ),
    (
        r"(?i)openai.*(error|fail|quota|limit|timeout)",
        "transcription", "OpenAI API error",
        "The OpenAI transcription API returned an error.",
        ["Check your OpenAI API key in secrets.toml",
         "Verify API quota has not been exceeded",
         "Try switching to local transcription backend"],
        True,
    ),
    # GPU / CUDA issues
    (
        r"(?i)(cuda|gpu|vram|out of memory|oom)",
        "gpu", "GPU memory error",
        "The GPU ran out of memory during processing.",
        ["Close other GPU-intensive applications",
         "Use a smaller Whisper model (base or small)",
         "Restart your computer to free GPU memory",
         "Re-process the recording"],
        True,
    ),
    (
        r"(?i)torch.*error|cublas|cudnn",
        "gpu", "PyTorch / CUDA error",
        "A low-level GPU computing error occurred.",
        ["Update PyTorch and CUDA drivers",
         "Restart the application",
         "Try CPU-only mode by setting device to 'cpu'"],
        True,
    ),
    # Diarization issues
    (
        r"(?i)(pyannote|diariz).*(error|fail|token)",
        "diarization", "Speaker diarization error",
        "The speaker diarization pipeline failed.",
        ["Check your HuggingFace token in secrets.toml",
         "Ensure you've accepted the gated model licenses on hf.co",
         "Try disabling diarization in config"],
        True,
    ),
    # Summary issues
    (
        r"(?i)(summary|summariz).*(error|fail)",
        "summary", "Summary generation error",
        "The AI summary could not be generated.",
        ["Check your summary API key in secrets.toml",
         "Verify the summary provider is configured correctly",
         "Re-process the recording"],
        True,
    ),
    # Network issues
    (
        r"(?i)(connection|network|timeout|timed out|connect)",
        "network", "Network connection error",
        "A network request failed due to connectivity issues.",
        ["Check your internet connection",
         "Try again later if the service may be temporarily down",
         "Re-process the recording"],
        True,
    ),
    # Storage issues
    (
        r"(?i)(disk|space|storage|permission|access denied|write)",
        "storage", "Storage error",
        "A file system error occurred, possibly due to insufficient disk space or permissions.",
        ["Check available disk space",
         "Verify write permissions to the output directory",
         "Close other applications that may be locking files"],
        False,
    ),
    # Screen capture issues
    (
        r"(?i)(screen|capture|printwindow|mss).*(error|fail)",
        "video", "Screen capture error",
        "The screen recording component encountered an error.",
        ["Check that the target window is still visible",
         "Try disabling screen recording in config",
         "Restart the application"],
        True,
    ),
]


def classify_error(error_message: str) -> ErrorClassification:
    """Classify an error message into a known category with suggestions.

    Args:
        error_message: The raw error string from metadata.

    Returns:
        ErrorClassification with category, explanation, and fix suggestions.
    """
    if not error_message:
        return ErrorClassification(
            category="unknown",
            title="Unknown error",
            explanation="No error details available.",
            suggestions=["Re-process the recording", "Check the application logs"],
            retryable=True,
        )

    for pattern, category, title, explanation, suggestions, retryable in _PATTERNS:
        if re.search(pattern, error_message):
            return ErrorClassification(
                category=category,
                title=title,
                explanation=explanation,
                suggestions=suggestions,
                retryable=retryable,
            )

    # Fallback for unrecognized errors
    return ErrorClassification(
        category="unknown",
        title="Unexpected error",
        explanation=error_message[:200],
        suggestions=[
            "Re-process the recording",
            "Check meeting_recorder.log for full details",
            "Report the issue if it persists",
        ],
        retryable=True,
    )


def format_error(ec: ErrorClassification) -> str:
    """Format an error classification as readable text."""
    lines = [
        f"[{ec.category.upper()}] {ec.title}",
        f"  {ec.explanation}",
        "",
    ]
    if ec.suggestions:
        lines.append("  Suggestions:")
        for s in ec.suggestions:
            lines.append(f"    - {s}")
    if ec.retryable:
        lines.append("")
        lines.append("  This error may be resolved by re-processing the recording.")
    return "\n".join(lines)
