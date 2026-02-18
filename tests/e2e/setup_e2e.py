"""E2E test environment diagnostics.

Run: python -m tests.e2e.setup_e2e
"""

from __future__ import annotations

import sys


def check(label: str, passed: bool, detail: str = "") -> bool:
    """Print a check result."""
    icon = "[OK]" if passed else "[!!]"
    color = "\033[92m" if passed else "\033[91m"
    reset = "\033[0m"
    msg = f"  {color}{icon}{reset} {label}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return passed


def main() -> int:
    print("\nE2E Test Environment Diagnostics")
    print("=" * 50)

    all_ok = True

    # 1. Check meeting_recorder is importable
    print("\n[Core]")
    try:
        import meeting_recorder  # noqa: F401
        check("meeting_recorder", True, "importable")
    except ImportError as e:
        check("meeting_recorder", False, str(e))
        all_ok = False

    # 2. Check sounddevice
    print("\n[Audio Dependencies]")
    try:
        import sounddevice as sd
        check("sounddevice", True, f"v{sd.__version__}")
    except ImportError:
        check("sounddevice", False, "pip install sounddevice")
        all_ok = False

    # 3. Check soundfile
    try:
        import soundfile as sf
        check("soundfile", True, f"v{sf.__version__}")
    except ImportError:
        check("soundfile", False, "pip install soundfile")
        all_ok = False

    # 4. Check VB-Cable
    print("\n[Virtual Audio]")
    try:
        from tests.e2e.virtual_audio import find_vbcable_device
        device = find_vbcable_device()
        if device is not None:
            check("VB-Cable", True, f"device index {device}")
        else:
            check("VB-Cable", False, "Not found. Install from https://vb-audio.com/Cable/")
            all_ok = False
    except Exception as e:
        check("VB-Cable", False, str(e))
        all_ok = False

    # 5. Check Playwright
    print("\n[Browser Automation]")
    try:
        import playwright  # noqa: F401
        check("playwright", True, "installed")
    except ImportError:
        check("playwright", False, "pip install playwright")
        all_ok = False

    # Check Chromium browser
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        check("Chromium", True, "available")
    except Exception as e:
        check("Chromium", False, f"Run: playwright install chromium ({e})")
        all_ok = False

    # 6. Check E2E_MEETING_LINK
    print("\n[Environment]")
    import os
    link = os.environ.get("E2E_MEETING_LINK", "")
    if link:
        check("E2E_MEETING_LINK", True, link[:60] + ("..." if len(link) > 60 else ""))
    else:
        check("E2E_MEETING_LINK", False, "Set env var with meeting URL for full pipeline tests")
        # Not a hard failure - some tests work without it

    # Summary
    print("\n" + "=" * 50)
    if all_ok:
        print("[OK] All prerequisites met. Ready to run E2E tests.")
        return 0
    else:
        print("[!!] Some prerequisites missing. See above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
