"""Playwright-based meeting bot for E2E testing.

Joins Zoom or Teams meetings as a second participant using Chromium.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Centralized selectors -- update these when platforms change their UI
ZOOM_SELECTORS = {
    "launch_meeting_link": 'a[href*="launch"], a:has-text("Launch Meeting")',
    "join_from_browser": (
        'a:has-text("Join from Your Browser"), '
        'a:has-text("join from your browser")'
    ),
    "name_input": '#input-for-name, #inputname, input[type="text"][autocomplete="off"]',
    "join_button": 'button:has-text("Join"), #joinBtn',
    "join_audio_button": 'button:has-text("Join Audio")',
    "join_computer_audio": 'button:has-text("Join Audio by Computer"), button:has-text("Computer Audio")',
    "ok_button": 'button:has-text("OK")',
    "got_it_button": 'button:has-text("Got it")',
    "agree_button": 'button:has-text("I Agree")',
    "leave_button": 'button:has-text("Leave")',
}

TEAMS_SELECTORS = {
    "continue_browser": (
        'a:has-text("Continue on this browser"):visible, '
        'button:has-text("Continue on this browser"):visible'
    ),
    "name_input": (
        'input[placeholder*="name" i]:visible, '
        'input[data-tid="prejoin-display-name-input"]:visible'
    ),
    "join_button": (
        'button:has-text("Join now"):visible, '
        'button[data-tid="prejoin-join-button"]:visible'
    ),
    "leave_button": (
        'button[data-tid="hangup-button"]:visible, '
        'button:has-text("Leave"):visible'
    ),
}


class MeetingBot:
    """Browser-based meeting bot using Playwright.

    Joins Zoom or Teams web meetings as a participant named "E2E Test Bot".
    Uses Chromium with fake media stream flags to auto-allow mic/camera.
    """

    def __init__(
        self,
        name: str = "E2E Test Bot",
        headless: bool = False,
        audio_file: Optional[str] = None,
    ):
        self._name = name
        self._headless = headless
        self._audio_file = audio_file
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._platform: Optional[str] = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.leave()

    def join(self, url: str, timeout: float = 60.0) -> None:
        """Join a meeting from the given URL.

        Detects platform (Zoom/Teams) from URL and follows the
        appropriate join flow.
        """
        from playwright.sync_api import sync_playwright

        self._platform = self._detect_platform(url)
        logger.info("Joining %s meeting: %s", self._platform, url[:80])

        self._playwright = sync_playwright().start()
        chrome_args = [
            "--use-fake-ui-for-media-stream",
            "--disable-web-security",
            "--no-sandbox",
        ]
        if self._audio_file:
            # Send audio from a WAV file instead of Chromium's silent fake device
            chrome_args.append(f"--use-file-for-fake-audio-capture={self._audio_file}")
        else:
            chrome_args.append("--use-fake-device-for-media-stream")
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=chrome_args,
        )
        self._context = self._browser.new_context(
            permissions=["microphone", "camera"],
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(timeout * 1000)

        if self._platform == "zoom":
            # Use Zoom's web client URL directly to skip the "Launch Meeting"
            # interstitial that tries to open the desktop app and stalls navigation.
            wc_url = self._zoom_web_client_url(url)
            logger.info("Using Zoom web client URL: %s", wc_url)
            try:
                self._page.goto(wc_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception:
                # Zoom pages often timeout due to heavy JS; proceed if page loaded
                logger.debug("Navigation timeout (expected for Zoom), continuing")
            time.sleep(3)
            self._join_zoom()
        elif self._platform == "teams":
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception:
                logger.debug("Navigation timeout, continuing")
            time.sleep(3)
            self._join_teams()
        else:
            raise ValueError(f"Unsupported platform: {self._platform}")

        logger.info("Successfully joined %s meeting as '%s'", self._platform, self._name)

    def leave(self) -> None:
        """Leave the meeting and clean up browser resources."""
        if self._page:
            try:
                selectors = ZOOM_SELECTORS if self._platform == "zoom" else TEAMS_SELECTORS
                leave_sel = selectors.get("leave_button", "")
                if leave_sel:
                    self._click_first_visible(leave_sel, timeout=5000)
            except Exception:
                logger.debug("Could not click leave button", exc_info=True)

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._page = None
        logger.info("Left meeting and cleaned up browser")

    def _join_zoom(self) -> None:
        """Follow Zoom web client join flow."""
        sel = ZOOM_SELECTORS

        # If we still landed on the interstitial (not /wc/ page), handle it
        if "/wc/" not in (self._page.url or ""):
            self._click_first_visible(sel["launch_meeting_link"], timeout=5000, optional=True)
            time.sleep(2)
            self._click_first_visible(sel["join_from_browser"], timeout=15000, optional=True)
            time.sleep(2)

        # Handle consent dialogs
        self._click_first_visible(sel["agree_button"], timeout=5000, optional=True)
        self._click_first_visible(sel["got_it_button"], timeout=5000, optional=True)

        # Enter name
        name_input = self._wait_for_first(sel["name_input"], timeout=15000)
        if name_input:
            name_input.fill("")
            name_input.fill(self._name)
            logger.info("Entered name: %s", self._name)

        # Click Join
        self._click_first_visible(sel["join_button"], timeout=10000)
        logger.info("Clicked Join button, waiting for meeting to load...")
        time.sleep(8)

        # Dismiss any popup dialogs (e.g. "Floating reactions" promo)
        self._click_first_visible(sel["ok_button"], timeout=3000, optional=True)
        self._click_first_visible(sel["got_it_button"], timeout=2000, optional=True)
        time.sleep(1)

        # Click "Join Audio" in the bottom toolbar — this is required for
        # the bot to actually connect its microphone to the meeting.
        self._click_first_visible(sel["join_audio_button"], timeout=10000, optional=True)
        time.sleep(2)
        # Some Zoom versions show a second dialog to choose audio type
        self._click_first_visible(sel["join_computer_audio"], timeout=5000, optional=True)
        time.sleep(1)
        logger.info("Audio join flow completed")

    def _join_teams(self) -> None:
        """Follow Teams web client join flow."""
        sel = TEAMS_SELECTORS

        # Click "Continue on this browser"
        self._click_first_visible(sel["continue_browser"], timeout=20000)
        time.sleep(3)

        # Enter name
        name_input = self._wait_for_first(sel["name_input"], timeout=10000)
        if name_input:
            name_input.fill("")
            name_input.fill(self._name)

        # Click "Join now"
        self._click_first_visible(sel["join_button"], timeout=10000)
        time.sleep(3)

    def _zoom_web_client_url(self, url: str) -> str:
        """Convert a Zoom meeting URL to the web client URL.

        e.g. https://duke.zoom.us/j/9602761835
          -> https://duke.zoom.us/wc/join/9602761835
        """
        import re

        match = re.search(r"(https?://[^/]+)/j/(\d+)", url)
        if match:
            base, meeting_id = match.group(1), match.group(2)
            # Preserve query params (password, etc.)
            query = ""
            if "?" in url:
                query = url[url.index("?"):]
            return f"{base}/wc/join/{meeting_id}{query}"
        return url

    def _detect_platform(self, url: str) -> str:
        """Detect meeting platform from URL."""
        url_lower = url.lower()
        if "zoom.us" in url_lower or "zoom.com" in url_lower:
            return "zoom"
        elif "teams.microsoft.com" in url_lower or "teams.live.com" in url_lower:
            return "teams"
        else:
            raise ValueError(f"Cannot detect platform from URL: {url}")

    def _click_first_visible(
        self, selector: str, timeout: int = 10000, optional: bool = False
    ) -> None:
        """Click the first visible element matching any of the comma-separated selectors."""
        parts = [s.strip() for s in selector.split(",")]
        per_selector_timeout = max(timeout // len(parts), 1000)
        for sel in parts:
            try:
                self._page.wait_for_selector(sel, timeout=per_selector_timeout)
                self._page.click(sel)
                return
            except Exception:
                continue

        if not optional:
            raise TimeoutError(f"No visible element found for: {selector}")

    def _wait_for_first(self, selector: str, timeout: int = 10000):
        """Wait for and return the first visible element matching any selector."""
        parts = [s.strip() for s in selector.split(",")]
        per_selector_timeout = max(timeout // len(parts), 1000)
        for sel in parts:
            try:
                return self._page.wait_for_selector(sel, timeout=per_selector_timeout)
            except Exception:
                continue
        return None
