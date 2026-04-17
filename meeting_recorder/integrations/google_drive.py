"""Google Drive integration for backing up meeting recordings.

Uploads recording folders to a configurable Google Drive folder after
transcription completes. Uses OAuth2 for authentication - first use
opens a browser for consent.

Setup:
1. Go to https://console.cloud.google.com
2. Create a project (or select existing)
3. Enable the Google Drive API
4. Create OAuth2 credentials (Desktop app type)
5. Download the credentials JSON and save as:
   ~/.meeting_recorder/google_credentials.json
6. Enable Google Drive upload in Meeting Recorder settings
7. First upload will open a browser to authorize access

The refresh token is stored at ~/.meeting_recorder/google_token.json
so you only need to authorize once per computer.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TOKEN_FILE = Path.home() / ".meeting_recorder" / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# File extensions to upload.  Video files are intentionally excluded —
# they're large (500MB-2GB) and dominate upload time.  Users who want
# the video can access it locally; Drive stores transcripts/summary/audio.
UPLOAD_EXTENSIONS = {
    ".json", ".txt", ".srt", ".md",  # Transcripts, summaries, notes
    ".wav",                          # Audio (for re-processing)
}

# Files to always upload by name
UPLOAD_FILENAMES = {
    "metadata.json",
    "transcript.json", "transcript.txt", "transcript.srt", "transcript_raw.txt",
    "summary.json", "summary.md",
    "notes.md", "decisions.json", "action_items.json",
    "app_audio.wav", "mic_audio.wav", "mixed.wav",
}

# Files to NEVER upload (keeps screen recording local-only — too large)
UPLOAD_BLOCKLIST = {"screen.mp4", "thumbnail.jpg"}


class GoogleDriveUploader:
    """Handles OAuth2 authentication and file uploads to Google Drive."""

    def __init__(self, credentials_path: Path, folder_id: str = ""):
        """
        Args:
            credentials_path: Path to the OAuth2 credentials JSON file.
            folder_id: Google Drive folder ID to upload into.
                       Empty string creates a top-level "MeetingRecordings" folder.
        """
        self._credentials_path = credentials_path
        self._folder_id = folder_id
        self._service = None

    def authenticate(self) -> bool:
        """Authenticate with Google Drive API.

        Returns True if authentication was successful.
        """
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
        except ImportError:
            logger.error(
                "Google API libraries not installed. Run: "
                "pip install google-api-python-client google-auth-oauthlib"
            )
            return False

        creds = None

        # Load existing token
        if TOKEN_FILE.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            except Exception:
                logger.debug("Failed to load existing token", exc_info=True)

        # Refresh or re-authenticate
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                logger.info("Token refresh failed, re-authenticating...")
                creds = None

        if not creds or not creds.valid:
            if not self._credentials_path.exists():
                logger.error(
                    "Google credentials file not found: %s. "
                    "Download it from Google Cloud Console.",
                    self._credentials_path,
                )
                return False

            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)
            except Exception:
                logger.exception("OAuth2 authentication failed")
                return False

            # Save token for next time
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())

        try:
            from googleapiclient.discovery import build
            self._service = build("drive", "v3", credentials=creds)
            logger.info("Google Drive authenticated successfully.")
            return True
        except Exception:
            logger.exception("Failed to build Drive service")
            return False

    def upload_recording(self, recording_dir: Path) -> Optional[str]:
        """Upload a recording folder to Google Drive.

        Creates a subfolder in the target Drive folder with the recording
        directory name, then uploads selected files into it.

        Args:
            recording_dir: Local path to the recording directory.

        Returns:
            The Google Drive folder ID of the uploaded recording, or None on failure.
        """
        if not self._service:
            if not self.authenticate():
                return None

        try:
            # Ensure the parent folder exists
            parent_id = self._folder_id or self._get_or_create_root_folder()
            if not parent_id:
                return None

            # Create subfolder for this recording
            folder_name = recording_dir.name
            recording_folder_id = self._create_folder(folder_name, parent_id)
            if not recording_folder_id:
                return None

            # Upload files
            uploaded = 0
            failed: list[str] = []
            for file_path in recording_dir.iterdir():
                if not file_path.is_file():
                    continue
                # Skip blocklist (videos, thumbnails — too large to be worth uploading)
                if file_path.name in UPLOAD_BLOCKLIST:
                    continue
                if file_path.name not in UPLOAD_FILENAMES and file_path.suffix not in UPLOAD_EXTENSIONS:
                    continue

                if self._upload_file(file_path, recording_folder_id):
                    uploaded += 1
                else:
                    failed.append(file_path.name)

            if failed:
                logger.warning(
                    "Drive upload partial: %d succeeded, %d failed (%s)",
                    uploaded, len(failed), ", ".join(failed),
                )
            logger.info(
                "Uploaded %d files to Google Drive: %s", uploaded, folder_name
            )
            return recording_folder_id

        except Exception:
            logger.exception("Failed to upload recording to Google Drive")
            return None

    def _get_or_create_root_folder(self) -> Optional[str]:
        """Get or create the MeetingRecordings folder in Drive root.

        Searches specifically in the Drive root first (``'root' in parents``),
        then falls back to a broader search.  This prevents creating a
        duplicate ``MeetingRecordings (1)`` when the folder already exists
        but was created by another app/machine/scope.
        """
        try:
            # Search in Drive root first, then broader.  When multiple
            # matches exist (e.g. left over from before we fixed the OAuth
            # scope), pick the OLDEST one — that's the canonical folder
            # that any other machine with the correct scope would have
            # also found first.  Deterministic ordering ensures every
            # machine converges on the same folder.
            for parent_filter in ("'root' in parents and ", ""):
                query = (
                    f"name = 'MeetingRecordings' and "
                    f"{parent_filter}"
                    f"mimeType = 'application/vnd.google-apps.folder' and "
                    f"trashed = false"
                )
                results = self._service.files().list(
                    q=query,
                    spaces="drive",
                    fields="files(id, name, createdTime)",
                    orderBy="createdTime",
                ).execute()

                files = results.get("files", [])
                if files:
                    if len(files) > 1:
                        logger.warning(
                            "Multiple MeetingRecordings folders found (%d); "
                            "using oldest: %s. Consider consolidating.",
                            len(files), files[0]["id"],
                        )
                    logger.info("Found existing MeetingRecordings folder: %s", files[0]["id"])
                    return files[0]["id"]

            # Create new folder in Drive root
            logger.info("Creating new MeetingRecordings folder in Drive root.")
            return self._create_folder("MeetingRecordings")

        except Exception:
            logger.exception("Failed to get/create root Drive folder")
            return None

    def _create_folder(self, name: str, parent_id: str = None) -> Optional[str]:
        """Create a folder in Google Drive."""
        try:
            metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                metadata["parents"] = [parent_id]

            folder = self._service.files().create(
                body=metadata, fields="id"
            ).execute()
            return folder.get("id")
        except Exception:
            logger.exception("Failed to create Drive folder: %s", name)
            return None

    def _upload_file(self, file_path: Path, parent_id: str, max_retries: int = 3) -> bool:
        """Upload a single file to a Google Drive folder with retries.

        Retries on transient errors (network blips, rate limits, 5xx).
        Skips retry on file-access errors (PermissionError, FileNotFoundError).
        """
        import time

        from googleapiclient.http import MediaFileUpload

        mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        for attempt in range(1, max_retries + 1):
            try:
                metadata = {
                    "name": file_path.name,
                    "parents": [parent_id],
                }
                media = MediaFileUpload(
                    str(file_path),
                    mimetype=mime_type,
                    resumable=True,
                )
                self._service.files().create(
                    body=metadata,
                    media_body=media,
                    fields="id",
                ).execute()
                logger.debug("Uploaded: %s (attempt %d)", file_path.name, attempt)
                return True

            except (PermissionError, FileNotFoundError) as e:
                # File-access errors won't improve with retry — bail
                logger.warning(
                    "Cannot upload %s (file access error, not retrying): %s",
                    file_path.name, e,
                )
                return False

            except Exception as e:
                err = str(e).lower()
                retryable = any(k in err for k in (
                    "429", "500", "502", "503", "504",
                    "rate limit", "resource exhausted", "timeout",
                    "connection", "deadline exceeded", "unavailable",
                    "ssl", "eof", "reset",
                ))
                if not retryable or attempt == max_retries:
                    logger.error(
                        "Failed to upload %s after %d attempt(s): %s",
                        file_path.name, attempt, e,
                    )
                    return False
                wait = 2 ** attempt  # 2s, 4s, 8s
                logger.warning(
                    "Upload of %s failed (attempt %d/%d), retrying in %ds: %s",
                    file_path.name, attempt, max_retries, wait, e,
                )
                time.sleep(wait)

        return False


def is_google_drive_available(credentials_path: Path) -> bool:
    """Check if Google Drive integration can be used.

    Returns True if the credentials file exists and the required
    libraries are installed.
    """
    if not credentials_path.exists():
        return False
    try:
        import google.oauth2.credentials  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
        return True
    except ImportError:
        return False
