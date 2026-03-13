"""Config export/import for multi-machine setup.

Export bundles the local secrets and optional Google OAuth token into a
single portable JSON file.  Import applies the bundle on a new machine.

Non-secret settings (model choices, FPS, features) live in the repo's
``config.toml`` and sync via git — this tool only transfers secrets and
machine-specific values that can't go in the repo.

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

from meeting_recorder.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    SECRETS_FILE,
    BUNDLED_CONFIG,
    _LOCAL_ONLY_FIELDS,
    _deep_merge,
)

TOKEN_FILE = CONFIG_DIR / "google_token.json"

# Fields that are machine-specific and should be reset on import
MACHINE_SPECIFIC = {
    ("audio", "mic_device"),           # different hardware per machine
    ("dashboard", "position_x"),       # different monitor layout
    ("dashboard", "position_y"),
}


def export_config(dest: str | None = None) -> int:
    """Export secrets + optional Google token to a portable bundle file.

    Args:
        dest: Output file path. Defaults to ~/meeting_recorder_config.json.

    Returns:
        0 on success, 1 on error.
    """
    # Gather secrets from secrets.toml (preferred) or legacy config.toml
    secrets_data: dict = {}
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "rb") as f:
            secrets_data = tomllib.load(f)
    elif CONFIG_FILE.exists():
        # Legacy: extract secrets from old combined config
        with open(CONFIG_FILE, "rb") as f:
            full = tomllib.load(f)
        for section, fields in _LOCAL_ONLY_FIELDS.items():
            if section not in full:
                continue
            for key in fields:
                val = full.get(section, {}).get(key)
                if val and val not in ("", -1, 0):
                    secrets_data.setdefault(section, {})[key] = val
    else:
        print("No secrets file found. Run the app once first.")
        return 1

    bundle: dict = {
        "version": 2,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "secrets": secrets_data,
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

    print(f"\nSecrets exported to: {dest_path}")
    print(f"\nCopy this file to your other machine(s) and run:")
    print(f"  python -m meeting_recorder import-config {dest_path.name}")

    # Summarize what's included
    has_keys = []
    if secrets_data.get("transcription", {}).get("gemini_api_key"):
        has_keys.append("Gemini")
    if secrets_data.get("transcription", {}).get("openai_api_key"):
        has_keys.append("OpenAI")
    if secrets_data.get("diarization", {}).get("huggingface_token"):
        has_keys.append("HuggingFace")
    if secrets_data.get("summary", {}).get("api_key"):
        has_keys.append("Summary API")
    if has_keys:
        print(f"API keys included: {', '.join(has_keys)}")
    if "google_token" in bundle:
        print("Google Drive: OAuth token included (no re-auth needed)")

    print(f"\nNon-secret settings (models, FPS, features) sync via git.")
    print(f"Just 'git pull' on the other machine for those.")

    return 0


def import_config(source: str, *, overwrite: bool = False) -> int:
    """Import secrets from a portable bundle file.

    Args:
        source: Path to the bundle JSON file.
        overwrite: If True, skip interactive confirmation prompts (for GUI use).

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

    # Support both v1 (combined config) and v2 (secrets only) bundles
    version = bundle.get("version", 1)

    if version >= 2:
        # v2: bundle contains only secrets
        if "secrets" not in bundle:
            print("Invalid bundle: missing 'secrets' section.")
            return 1
        secrets_data = bundle["secrets"]
    else:
        # v1 legacy: bundle contains full config, extract secrets
        if "config" not in bundle:
            print("Invalid bundle: missing 'config' section.")
            return 1
        config_data = bundle["config"]
        secrets_data = {}
        for section, fields in _LOCAL_ONLY_FIELDS.items():
            if section not in config_data:
                continue
            for key in fields:
                val = config_data.get(section, {}).get(key)
                if val and val not in ("", -1, 0):
                    secrets_data.setdefault(section, {})[key] = val

    # Reset machine-specific fields (mic, dashboard position)
    for section, key in MACHINE_SPECIFIC:
        if section in secrets_data and key in secrets_data[section]:
            del secrets_data[section][key]
            # Clean up empty sections
            if not secrets_data[section]:
                del secrets_data[section]

    # Check if secrets file already exists
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if SECRETS_FILE.exists() and not overwrite:
        print(f"Existing secrets found at {SECRETS_FILE}")
        resp = input("Overwrite? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return 0

    # Write secrets
    with open(SECRETS_FILE, "wb") as f:
        tomli_w.dump(secrets_data, f)
    print(f"Secrets written to: {SECRETS_FILE}")

    # Import Google token
    if "google_token" in bundle:
        if TOKEN_FILE.exists() and not overwrite:
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
    print(f"\nImported secrets exported at: {exported_at}")

    # Summarize
    has_keys = []
    if secrets_data.get("transcription", {}).get("gemini_api_key"):
        has_keys.append("Gemini")
    if secrets_data.get("diarization", {}).get("huggingface_token"):
        has_keys.append("HuggingFace")
    if has_keys:
        print(f"API keys imported: {', '.join(has_keys)}")

    # Remind about next steps
    print("\nNext steps on this machine:")
    print("  1. git pull — to get the latest non-secret settings")
    print("  2. Check transcription.device matches your GPU (cuda/cpu)")
    if "google_token" not in bundle:
        print("  3. Google Drive: first upload will open browser to authorize")
    print(f"\n  Secrets file: {SECRETS_FILE}")
    print(f"  Repo config:  {BUNDLED_CONFIG}")
    print("  Verify setup: python -m meeting_recorder diagnose")

    return 0
