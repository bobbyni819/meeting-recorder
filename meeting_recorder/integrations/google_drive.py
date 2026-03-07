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
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# File extensions to upload (skip large raw audio)
UPLOAD_EXTENSIONS = {
    ".json", ".txt", ".srt",  # Transcripts
    ".mp4",                   # Screen recording
}

# Files to always upload by name (mixed.wav is transient and deleted after transcription)
UPLOAD_FILENAMES = {"metadata.json", "transcript.json", "transcript.txt", "transcript.srt", "transcript_raw.txt", "screen.mp4", "summary.json", "summary.md"}


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
            for file_path in recording_dir.iterdir():
                if not file_path.is_file():
                    continue
                if file_path.name not in UPLOAD_FILENAMES and file_path.suffix not in UPLOAD_EXTENSIONS:
                    continue

                if self._upload_file(file_path, recording_folder_id):
                    uploaded += 1

            logger.info(
                "Uploaded %d files to Google Drive: %s", uploaded, folder_name
            )
            return recording_folder_id

        except Exception:
            logger.exception("Failed to upload recording to Google Drive")
            return None

    def _get_or_create_root_folder(self) -> Optional[str]:
        """Get or create the MeetingRecordings folder in Drive root."""
        try:
            # Search for existing folder
            query = (
                "name = 'MeetingRecordings' and "
                "mimeType = 'application/vnd.google-apps.folder' and "
                "trashed = false"
            )
            results = self._service.files().list(
                q=query, spaces="drive", fields="files(id, name)"
            ).execute()

            files = results.get("files", [])
            if files:
                return files[0]["id"]

            # Create new folder
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

    def _upload_file(self, file_path: Path, parent_id: str) -> bool:
        """Upload a single file to a Google Drive folder."""
        try:
            from googleapiclient.http import MediaFileUpload

            mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

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

            logger.debug("Uploaded: %s", file_path.name)
            return True

        except Exception:
            logger.exception("Failed to upload file: %s", file_path.name)
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
