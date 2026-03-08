"""Config export/import for multi-machine setup.

Export bundles the config and optional Google OAuth token into a single
portable JSON file.  Import applies the bundle on a new machine.

Usage:
    python -m meeting_recorder export-config [output_file]
    python -m meeting_recorder import-config <file>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from meeting_recorder.config import CONFIG_DIR, CONFIG_FILE

TOKEN_FILE = CONFIG_DIR / "google_token.json"

# Fields that are machine-specific and should be reset on import
MACHINE_SPECIFIC = {
    ("audio", "mic_device"),           # different hardware per machine
    ("dashboard", "position_x"),       # different monitor layout
    ("dashboard", "position_y"),
}


def export_config(dest: str | None = None) -> int:
    """Export config + optional Google token to a portable bundle file.

    Args:
        dest: Output file path. Defaults to ~/meeting_recorder_config.json.

    Returns:
        0 on success, 1 on error.
    """
    if not CONFIG_FILE.exists():
        print(f"No config file found at {CONFIG_FILE}")
        print("Run the app once first to generate a default config.")
        return 1

    with open(CONFIG_FILE, "rb") as f:
        config_data = tomllib.load(f)

    bundle: dict = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "config": config_data,
    }

    # Include Google OAuth token if it exists
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE) as f:
                bundle["google_token"] = json.load(f)
            print("Including Google OAuth token (already authorized).")
        except Exception:
            print("Warning: Could not read Google token, skipping.")

    # Write bundle
    if dest is None:
        dest = str(Path.home() / "meeting_recorder_config.json")
    dest_path = Path(dest)

    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)

    print(f"\nConfig exported to: {dest_path}")
    print(f"\nCopy this file to your other machine(s) and run:")
    print(f"  python -m meeting_recorder import-config {dest_path.name}")

    # Summarize what's included
    sections = list(config_data.keys())
    print(f"\nIncluded sections: {', '.join(sections)}")

    has_keys = []
    if config_data.get("transcription", {}).get("gemini_api_key"):
        has_keys.append("Gemini")
    if config_data.get("transcription", {}).get("openai_api_key"):
        has_keys.append("OpenAI")
    if config_data.get("diarization", {}).get("huggingface_token"):
        has_keys.append("HuggingFace")
    if config_data.get("summary", {}).get("api_key"):
        has_keys.append("Summary API")
    if has_keys:
        print(f"API keys included: {', '.join(has_keys)}")
    if "google_token" in bundle:
        print("Google Drive: OAuth token included (no re-auth needed)")

    return 0


def import_config(source: str) -> int:
    """Import config from a portable bundle file.

    Args:
        source: Path to the bundle JSON file.

    Returns:
        0 on success, 1 on error.
    """
    source_path = Path(source)
    if not source_path.exists():
        print(f"File not found: {source_path}")
        return 1

    try:
        with open(source_path, encoding="utf-8") as f:
            bundle = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Invalid bundle file: {e}")
        return 1

    if "config" not in bundle:
        print("Invalid bundle: missing 'config' section.")
        return 1

    config_data = bundle["config"]

    # Reset machine-specific fields to defaults
    for section, key in MACHINE_SPECIFIC:
        if section in config_data and key in config_data[section]:
            del config_data[section][key]

    # Check if config already exists
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        print(f"Existing config found at {CONFIG_FILE}")
        resp = input("Overwrite? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return 0

    # Write config
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(config_data, f)
    print(f"Config written to: {CONFIG_FILE}")

    # Import Google token
    if "google_token" in bundle:
        if TOKEN_FILE.exists():
            resp = input("Google OAuth token already exists. Overwrite? [y/N] ").strip().lower()
            if resp != "y":
                print("Kept existing Google token.")
            else:
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    json.dump(bundle["google_token"], f)
                print(f"Google OAuth token written to: {TOKEN_FILE}")
        else:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(bundle["google_token"], f)
            print(f"Google OAuth token written to: {TOKEN_FILE}")

    # Print what was imported
    exported_at = bundle.get("exported_at", "unknown")
    print(f"\nImported config exported at: {exported_at}")

    # Remind about machine-specific setup
    print("\nNext steps on this machine:")
    print("  1. Check audio device: mic_device will use system default")
    print("  2. Check transcription.device matches your GPU (cuda/cpu)")
    print("  3. First run will download ML models (~3GB for whisper large-v3)")
    if "google_token" not in bundle:
        print("  4. Google Drive: first upload will open browser to authorize")
    print(f"\n  Edit config: {CONFIG_FILE}")
    print("  Verify setup: python -m meeting_recorder diagnose")

    return 0
