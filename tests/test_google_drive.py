"""Tests for idempotent Google Drive recording uploads."""

from pathlib import Path
from unittest import mock

from meeting_recorder.integrations.google_drive import GoogleDriveUploader


def _uploader() -> GoogleDriveUploader:
    uploader = GoogleDriveUploader(Path("credentials.json"), folder_id="parent")
    uploader._service = mock.MagicMock()
    return uploader


def test_existing_recording_folder_is_reused_and_existing_files_are_skipped(tmp_path):
    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "transcript.txt").write_text("hello", encoding="utf-8")
    uploader = _uploader()

    with mock.patch.object(
        uploader, "_find_child_folder", return_value="existing-folder",
    ), mock.patch.object(
        uploader, "_list_child_names", return_value={"metadata.json"},
    ), mock.patch.object(
        uploader, "_create_folder",
    ) as create_folder, mock.patch.object(
        uploader, "_upload_file", return_value=True,
    ) as upload_file:
        result = uploader.upload_recording(tmp_path)

    create_folder.assert_not_called()
    upload_file.assert_called_once_with(tmp_path / "transcript.txt", "existing-folder")
    assert result is not None
    assert result.folder_id == "existing-folder"
    assert result.uploaded_files == ("transcript.txt",)
    assert result.skipped_files == ("metadata.json",)
    assert result.failed_files == ()
    assert result.complete is True


def test_partial_upload_result_names_failed_files(tmp_path):
    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "summary.md").write_text("summary", encoding="utf-8")
    uploader = _uploader()

    with mock.patch.object(
        uploader, "_find_child_folder", return_value=None,
    ), mock.patch.object(
        uploader, "_create_folder", return_value="new-folder",
    ), mock.patch.object(
        uploader, "_upload_file", side_effect=[True, False],
    ):
        result = uploader.upload_recording(tmp_path)

    assert result is not None
    assert result.folder_id == "new-folder"
    assert result.uploaded_files == ("metadata.json",)
    assert result.failed_files == ("summary.md",)
    assert result.complete is False
