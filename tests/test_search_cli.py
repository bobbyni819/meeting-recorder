"""Tests for the search CLI."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from meeting_recorder.search.cli import main
from meeting_recorder.search.index import SearchResult


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestSearchCLI:
    """Test the search CLI entry point."""

    @patch("meeting_recorder.search.cli.RecordingIndex")
    @patch("meeting_recorder.search.cli.Config")
    def test_reindex_calls_index_all(self, mock_config_cls, mock_index_cls):
        mock_config = MagicMock()
        mock_config_cls.load.return_value = mock_config

        mock_index = MagicMock()
        mock_index.index_all.return_value = 5
        mock_index_cls.return_value = mock_index

        exit_code = main(["--reindex"])

        assert exit_code == 0
        mock_index.index_all.assert_called_once_with(mock_config.output_dir)
        mock_index.close.assert_called_once()

    def test_no_criteria_exits_with_error(self):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

    @patch("meeting_recorder.search.cli.RecordingIndex")
    @patch("meeting_recorder.search.cli.Config")
    def test_query_produces_output(self, mock_config_cls, mock_index_cls, capsys):
        mock_config_cls.load.return_value = MagicMock()

        mock_index = MagicMock()
        mock_index.search.return_value = [
            SearchResult(
                recording_dir="/tmp/rec_1",
                date="2025-06-15T10:00:00",
                subject="Sprint Planning",
                app_name="Zoom",
                organizer="Alice",
                attendees="Alice, Bob",
                speakers="Alice, Bob",
                snippet="Let's plan the sprint...",
                rank=-1.0,
            ),
        ]
        mock_index_cls.return_value = mock_index

        exit_code = main(["sprint"])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Sprint Planning" in captured.out
        assert "1 result(s) found." in captured.out
        mock_index.search.assert_called_once()
        mock_index.close.assert_called_once()

    @patch("meeting_recorder.search.cli.RecordingIndex")
    @patch("meeting_recorder.search.cli.Config")
    def test_limit_flag_passed_through(self, mock_config_cls, mock_index_cls):
        mock_config_cls.load.return_value = MagicMock()

        mock_index = MagicMock()
        mock_index.search.return_value = []
        mock_index_cls.return_value = mock_index

        main(["test", "--limit", "7"])

        _, kwargs = mock_index.search.call_args
        assert kwargs["limit"] == 7

    def test_help_does_not_error(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
