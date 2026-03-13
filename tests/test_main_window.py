"""Tests for the MainWindow application window."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from meeting_recorder.audio.level_monitor import MIN_DB
from meeting_recorder.ui.main_window import (
    MainWindow,
    _format_elapsed,
    _format_duration_short,
    _db_to_fraction,
    _vu_color,
    GREEN_VU,
    YELLOW_VU,
    RED_VU,
    BG_COLOR,
    BG_CARD,
    WIN_WIDTH,
    WIN_HEIGHT,
)


# ---------------------------------------------------------------------------
# _format_elapsed
# ---------------------------------------------------------------------------

class TestFormatElapsed:
    def test_zero(self):
        assert _format_elapsed(0) == "00:00:00"

    def test_seconds_only(self):
        assert _format_elapsed(45) == "00:00:45"

    def test_minutes_and_seconds(self):
        assert _format_elapsed(125) == "00:02:05"

    def test_hours(self):
        assert _format_elapsed(3661) == "01:01:01"

    def test_float_truncates(self):
        assert _format_elapsed(59.9) == "00:00:59"

    def test_large_value(self):
        assert _format_elapsed(86400) == "24:00:00"


# ---------------------------------------------------------------------------
# _format_duration_short
# ---------------------------------------------------------------------------

class TestFormatDurationShort:
    def test_seconds_only(self):
        assert _format_duration_short(5) == "5s"

    def test_minutes_and_seconds(self):
        assert _format_duration_short(125) == "2m 05s"

    def test_hours_and_minutes(self):
        assert _format_duration_short(3661) == "1h 01m"

    def test_zero(self):
        assert _format_duration_short(0) == "0s"


# ---------------------------------------------------------------------------
# _db_to_fraction
# ---------------------------------------------------------------------------

class TestDbToFraction:
    def test_silence(self):
        assert _db_to_fraction(MIN_DB) == 0.0

    def test_below_min(self):
        assert _db_to_fraction(MIN_DB - 20) == 0.0

    def test_full_scale(self):
        assert _db_to_fraction(0.0) == 1.0

    def test_above_zero_clamps(self):
        assert _db_to_fraction(5.0) == 1.0

    def test_midpoint(self):
        mid = MIN_DB / 2.0
        result = _db_to_fraction(mid)
        assert 0.49 < result < 0.51

    def test_monotonic(self):
        values = [MIN_DB, -50, -40, -30, -20, -10, 0]
        fractions = [_db_to_fraction(db) for db in values]
        for i in range(len(fractions) - 1):
            assert fractions[i] <= fractions[i + 1]


# ---------------------------------------------------------------------------
# _vu_color
# ---------------------------------------------------------------------------

class TestVuColor:
    def test_low_is_green(self):
        assert _vu_color(0.0) == GREEN_VU
        assert _vu_color(0.49) == GREEN_VU

    def test_mid_is_yellow(self):
        assert _vu_color(0.51) == YELLOW_VU
        assert _vu_color(0.79) == YELLOW_VU

    def test_high_is_red(self):
        assert _vu_color(0.81) == RED_VU
        assert _vu_color(1.0) == RED_VU

    def test_boundary_50(self):
        assert _vu_color(0.50) == GREEN_VU

    def test_boundary_80(self):
        assert _vu_color(0.80) == YELLOW_VU


# ---------------------------------------------------------------------------
# MainWindow — instantiation and state (no Tk needed)
# ---------------------------------------------------------------------------

class TestMainWindowInit:
    """Test MainWindow construction and state before any Tk window is created."""

    def test_default_state(self):
        mw = MainWindow()
        assert not mw._is_recording
        assert not mw._is_paused
        assert mw._window is None
        assert not mw.is_visible

    def test_callbacks_stored(self):
        cb_start = mock.Mock()
        cb_stop = mock.Mock()
        cb_pause = mock.Mock()
        mw = MainWindow(
            on_start=cb_start,
            on_stop=cb_stop,
            on_pause=cb_pause,
            auto_start=True,
            hotkey_recording="ctrl+r",
            hotkey_pause="ctrl+p",
        )
        assert mw._on_start is cb_start
        assert mw._on_stop is cb_stop
        assert mw._on_pause is cb_pause
        assert mw._auto_start is True
        assert mw._hotkey_recording == "ctrl+r"
        assert mw._hotkey_pause == "ctrl+p"

    def test_all_callbacks_default_none(self):
        mw = MainWindow()
        assert mw._on_start is None
        assert mw._on_stop is None
        assert mw._on_pause is None
        assert mw._on_toggle_mute is None
        assert mw._on_record_window is None
        assert mw._on_search is None
        assert mw._on_settings is None
        assert mw._on_open_recordings is None
        assert mw._on_open_recording is None
        assert mw._on_list_recent is None
        assert mw._on_list_windows is None
        assert mw._on_pick_window is None
        assert mw._on_toggle_audio_mode is None
        assert mw._on_toggle_auto_start is None


# ---------------------------------------------------------------------------
# Thread-safe update methods (no-op when window is None)
# ---------------------------------------------------------------------------

class TestMainWindowUpdatesNoWindow:
    """All update methods should be safe to call when no Tk window exists."""

    def setup_method(self):
        self.mw = MainWindow()

    def test_set_recording_state_no_window(self):
        self.mw.set_recording_state(True, "Teams")
        assert self.mw._is_recording is True
        assert self.mw._recording_app_name == "Teams"

    def test_set_recording_state_false(self):
        self.mw.set_recording_state(True, "Zoom")
        self.mw.set_recording_state(False)
        assert self.mw._is_recording is False

    def test_update_audio_levels_no_crash(self):
        self.mw.update_audio_levels(-30.0, -20.0, -25.0, -15.0)

    def test_update_elapsed_no_crash(self):
        self.mw.update_elapsed(123.4)

    def test_update_paused_no_crash(self):
        self.mw.update_paused(True)
        assert self.mw._is_paused is True
        self.mw.update_paused(False)
        assert self.mw._is_paused is False

    def test_update_mute_state_no_crash(self):
        self.mw.update_mute_state(True)

    def test_update_transcript_no_crash(self):
        self.mw.update_transcript("Hello world")

    def test_update_screen_preview_no_crash(self):
        self.mw.update_screen_preview(None)

    def test_update_status_bar_no_crash(self):
        self.mw.update_status_bar("Processing...")

    def test_refresh_history_no_crash(self):
        self.mw.refresh_history()


# ---------------------------------------------------------------------------
# hide / close when no window
# ---------------------------------------------------------------------------

class TestMainWindowHideClose:
    def test_hide_no_window(self):
        mw = MainWindow()
        mw.hide()
        assert not mw.is_visible

    def test_close_no_window(self):
        mw = MainWindow()
        mw.close()
        assert not mw.is_visible
        assert mw._window is None

    def test_close_sets_window_none(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw.close()
        assert mw._window is None


# ---------------------------------------------------------------------------
# Recording state tracking
# ---------------------------------------------------------------------------

class TestRecordingStateTracking:
    def test_state_tracks_app_name(self):
        mw = MainWindow()
        mw.set_recording_state(True, "Microsoft Teams")
        assert mw._recording_app_name == "Microsoft Teams"

    def test_state_clears_on_stop(self):
        mw = MainWindow()
        mw.set_recording_state(True, "Zoom")
        mw.set_recording_state(False)
        assert not mw._is_recording

    def test_pause_state_independent(self):
        mw = MainWindow()
        mw.set_recording_state(True, "Test")
        mw.update_paused(True)
        assert mw._is_paused
        mw.update_paused(False)
        assert not mw._is_paused


# ---------------------------------------------------------------------------
# Audio level gating in update_audio_levels
# ---------------------------------------------------------------------------

class TestAudioLevelGating:
    """update_audio_levels should be gated by recording state and visibility."""

    def test_skips_when_not_recording(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._is_recording = False
        mw.update_audio_levels(-30.0, -20.0, -25.0, -15.0)
        mw._window.after.assert_not_called()

    def test_skips_when_not_visible(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = False
        mw._is_recording = True
        mw.update_audio_levels(-30.0, -20.0, -25.0, -15.0)
        mw._window.after.assert_not_called()

    def test_calls_after_when_recording_and_visible(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._is_recording = True
        mw.update_audio_levels(-30.0, -20.0, -25.0, -15.0)
        mw._window.after.assert_called_once()


# ---------------------------------------------------------------------------
# Transcript truncation
# ---------------------------------------------------------------------------

class TestTranscriptTruncation:
    def test_short_text_passes_through(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._is_recording = True
        mw.update_transcript("Short text")
        mw._window.after.assert_called_once()

    def test_long_text_truncated(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._is_recording = True
        long_text = "x" * 500
        mw.update_transcript(long_text)
        mw._window.after.assert_called_once()

    def test_skips_when_not_recording(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._is_recording = False
        mw.update_transcript("Test")
        mw._window.after.assert_not_called()


# ---------------------------------------------------------------------------
# _fire helper
# ---------------------------------------------------------------------------

class TestFire:
    def test_fire_with_callback(self):
        mw = MainWindow()
        called = []
        mw._fire(lambda: called.append(True))
        import time
        time.sleep(0.1)
        assert called == [True]

    def test_fire_with_none(self):
        mw = MainWindow()
        mw._fire(None)  # should not raise


# ---------------------------------------------------------------------------
# _read_file helper
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        assert MainWindow._read_file(f) == "hello world"

    def test_strips_whitespace(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("  content  \n\n", encoding="utf-8")
        assert MainWindow._read_file(f) == "content"

    def test_missing_file_returns_empty(self, tmp_path):
        assert MainWindow._read_file(tmp_path / "nope.txt") == ""

    def test_binary_file_returns_empty(self, tmp_path):
        f = tmp_path / "bad.txt"
        f.write_bytes(b"\xff\xfe\x00\x01")
        # Might succeed or fail depending on encoding — should not crash
        result = MainWindow._read_file(f)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Detail view state management (no Tk)
# ---------------------------------------------------------------------------

class TestDetailViewState:
    def test_detail_does_not_show_when_recording(self):
        mw = MainWindow()
        mw._is_recording = True
        # Should not crash even without Tk
        mw._show_recording_detail(Path("/fake"))

    def test_close_detail_without_frame(self):
        mw = MainWindow()
        mw._close_detail()  # should not crash


# ---------------------------------------------------------------------------
# Auto-start and audio mode updates (no Tk)
# ---------------------------------------------------------------------------

class TestReprocessCallback:
    def test_stores_callback(self):
        cb = lambda path: None
        mw = MainWindow(on_reprocess=cb)
        assert mw._on_reprocess is cb

    def test_default_no_callback(self):
        mw = MainWindow()
        assert mw._on_reprocess is None


class TestAutoStartUpdate:
    def test_updates_state(self):
        mw = MainWindow(auto_start=False)
        assert not mw._auto_start
        mw.update_auto_start(True)
        assert mw._auto_start
        mw.update_auto_start(False)
        assert not mw._auto_start

    def test_no_crash_without_window(self):
        mw = MainWindow()
        mw.update_auto_start(True)
        mw.update_auto_start(False)

    def test_toggle_click_fires_callback(self):
        called = []
        mw = MainWindow(auto_start=False, on_toggle_auto_start=lambda v: called.append(v))
        mw._toggle_auto_start_click()
        import time
        time.sleep(0.1)
        assert mw._auto_start is True
        assert called == [True]

    def test_toggle_click_toggles_off(self):
        called = []
        mw = MainWindow(auto_start=True, on_toggle_auto_start=lambda v: called.append(v))
        mw._toggle_auto_start_click()
        import time
        time.sleep(0.1)
        assert mw._auto_start is False
        assert called == [False]


class TestAudioModeUpdate:
    def test_no_crash_without_window(self):
        mw = MainWindow()
        mw.update_audio_mode(True)
        mw.update_audio_mode(False)

    def test_skips_when_not_visible(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = False
        mw.update_audio_mode(True)
        mw._window.after.assert_not_called()


# ---------------------------------------------------------------------------
# VU Peak Hold
# ---------------------------------------------------------------------------

class TestVuPeakHold:
    def test_peak_tracks_max(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._is_recording = True
        # First call sets peak
        mw.update_audio_levels(-10.0, -10.0, -20.0, -20.0)
        assert mw._app_peak_frac > 0.0
        assert mw._mic_peak_frac > 0.0

    def test_peak_decays(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._is_recording = True
        # Set high peak
        mw.update_audio_levels(0.0, 0.0, 0.0, 0.0)
        peak_after_high = mw._app_peak_frac
        # Send silence — peak should decay
        for _ in range(10):
            mw.update_audio_levels(MIN_DB, MIN_DB, MIN_DB, MIN_DB)
        assert mw._app_peak_frac < peak_after_high

    def test_peak_resets_on_new_recording(self):
        mw = MainWindow()
        mw._app_peak_frac = 0.5
        mw._mic_peak_frac = 0.7
        mw.set_recording_state(True, "Test")
        assert mw._app_peak_frac == 0.0
        assert mw._mic_peak_frac == 0.0


# ---------------------------------------------------------------------------
# Disk space update
# ---------------------------------------------------------------------------

class TestDiskSpaceUpdate:
    def test_counter_increments(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        assert mw._disk_update_counter == 0
        mw.update_elapsed(10.0)
        assert mw._disk_update_counter == 1

    def test_disk_update_fires_at_50(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw._disk_update_counter = 49
        mw.update_elapsed(10.0)
        # Counter should reset and fire disk update
        assert mw._disk_update_counter == 0
        # Two after calls: one for elapsed, one for disk
        assert mw._window.after.call_count == 2


# ---------------------------------------------------------------------------
# Window title during recording
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Health warning banner
# ---------------------------------------------------------------------------

class TestHealthWarning:
    def test_show_warning_no_crash_without_window(self):
        mw = MainWindow()
        mw.show_warning("Test warning")  # should not crash

    def test_show_warning_schedules_after(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = True
        mw.show_warning("Audio is silent")
        # Called twice: once for display_warning, once for badge update
        assert mw._window.after.call_count == 2

    def test_show_warning_skips_when_not_visible(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_visible = False
        mw.show_warning("Test")
        mw._window.after.assert_not_called()

    def test_show_warning_logs_to_notification_store(self):
        mw = MainWindow()
        mw.show_warning("Audio is silent")
        assert len(mw.notification_store) == 1
        assert mw.notification_store.entries[0].message == "Audio is silent"
        assert mw.notification_store.entries[0].level == "warn"

    def test_add_notification(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw.add_notification("info", "Recording started", source="recorder")
        assert len(mw.notification_store) == 1
        assert mw.notification_store.entries[0].source == "recorder"
        mw._window.after.assert_called_once()

    def test_notification_store_unread(self):
        mw = MainWindow()
        mw.show_warning("a")
        mw.show_warning("b")
        assert mw.notification_store.unread_count == 2
        mw.notification_store.mark_read()
        assert mw.notification_store.unread_count == 0


# ---------------------------------------------------------------------------
# Window title during recording
# ---------------------------------------------------------------------------

class TestWindowTitle:
    def test_title_updates_during_recording(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_recording = True
        mw._elapsed_label = mock.Mock()
        mw._set_elapsed("01:23:45")
        mw._window.title.assert_called_with("Meeting Recorder \u2014 01:23:45")

    def test_title_not_updated_when_not_recording(self):
        mw = MainWindow()
        mw._window = mock.Mock()
        mw._is_recording = False
        mw._elapsed_label = mock.Mock()
        mw._set_elapsed("00:00:00")
        mw._window.title.assert_not_called()


# ---------------------------------------------------------------------------
# Detail view — _build_details_text
# ---------------------------------------------------------------------------

class TestBuildDetailsText:
    def test_files_present(self, tmp_path):
        """Files that exist show checkmark and size."""
        (tmp_path / "app_audio.wav").write_bytes(b"\x00" * 2048)
        (tmp_path / "transcript.txt").write_text("hello", encoding="utf-8")
        meta = {"status": "completed"}
        text = MainWindow._build_details_text(tmp_path, meta)
        assert "\u2713" in text  # checkmark for existing files
        assert "App Audio" in text
        assert "2 KB" in text or "2.0 KB" in text
        assert "\u2717" in text  # cross for missing files

    def test_speaker_map(self, tmp_path):
        meta = {
            "speaker_map": {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"},
            "speaker_map_method": "calendar",
        }
        text = MainWindow._build_details_text(tmp_path, meta)
        assert "SPEAKER MAP" in text
        assert "Alice" in text
        assert "Bob" in text
        assert "calendar" in text

    def test_google_drive_link(self, tmp_path):
        meta = {"google_drive_folder_id": "abc123"}
        text = MainWindow._build_details_text(tmp_path, meta)
        assert "GOOGLE DRIVE" in text
        assert "abc123" in text
        assert "drive.google.com" in text

    def test_processing_info(self, tmp_path):
        meta = {
            "transcription_backend": "gemini",
            "status": "completed",
            "segment_count": 42,
            "speaker_count": 3,
            "has_summary": True,
            "summary_provider": "anthropic",
            "summary_model": "claude-3",
        }
        text = MainWindow._build_details_text(tmp_path, meta)
        assert "gemini" in text
        assert "42" in text
        assert "anthropic" in text
        assert "claude-3" in text

    def test_empty_meta(self, tmp_path):
        """Empty metadata should not crash."""
        text = MainWindow._build_details_text(tmp_path, {})
        assert "FILES" in text
        assert "TECHNICAL" in text

    def test_speaker_stats_from_transcript(self, tmp_path):
        """Speaker stats calculated from transcript.json segments."""
        transcript = {
            "segments": [
                {"speaker": "Alice", "start": 0.0, "end": 60.0, "text": "hello"},
                {"speaker": "Bob", "start": 60.0, "end": 90.0, "text": "hi"},
                {"speaker": "Alice", "start": 90.0, "end": 120.0, "text": "bye"},
            ]
        }
        (tmp_path / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        text = MainWindow._build_details_text(tmp_path, {})
        assert "SPEAKER STATS" in text
        assert "Alice" in text
        assert "Bob" in text
        # Alice: 90s = 1:30, Bob: 30s = 0:30
        assert "1:30" in text
        assert "0:30" in text

    def test_speaker_stats_absent_without_transcript(self, tmp_path):
        """No SPEAKER STATS section when no transcript.json exists."""
        text = MainWindow._build_details_text(tmp_path, {})
        assert "SPEAKER STATS" not in text

    def test_quality_scores_displayed(self, tmp_path):
        """Quality scores section shows when metadata has scores."""
        meta = {
            "quality_scores": {
                "overall_score": 85,
                "audio_score": 90,
                "audio_details": {"app_rms_db": -18.5, "app_peak_db": -3.0, "app_silence_ratio": 0.1},
                "transcript_score": 80,
                "transcript_details": {"word_count": 500, "wpm": 130, "large_gaps": 1},
                "video_score": 100,
                "video_details": {},
            }
        }
        text = MainWindow._build_details_text(tmp_path, meta)
        assert "QUALITY" in text
        assert "85/100" in text
        assert "90/100" in text
        assert "Excellent" in text or "Good" in text

    def test_quality_scores_absent_when_empty(self, tmp_path):
        """No QUALITY section when no scores in metadata."""
        text = MainWindow._build_details_text(tmp_path, {})
        assert "QUALITY" not in text

    def test_attendance_verification(self, tmp_path):
        """Attendance section shows who spoke and who didn't."""
        meta = {
            "meeting_attendees": ["Alice Smith", "Bob Jones", "Charlie Brown"],
            "speaker_map": {
                "SPEAKER_00": "Alice",
                "SPEAKER_01": "Bob",
            },
        }
        text = MainWindow._build_details_text(tmp_path, meta)
        assert "ATTENDANCE" in text
        assert "Alice Smith" in text
        assert "didn't speak" in text  # Charlie didn't speak
        assert "2/3" in text  # 2 out of 3 spoke

    def test_attendance_absent_without_data(self, tmp_path):
        """No ATTENDANCE section without both attendees and speaker map."""
        text = MainWindow._build_details_text(tmp_path, {"meeting_attendees": ["Alice"]})
        assert "ATTENDANCE" not in text


class TestMeetingNotes:
    def test_basic_notes(self, tmp_path):
        rec = tmp_path / "2026-03-12_14-30-00_Standup"
        rec.mkdir()
        meta = {
            "meeting_subject": "Daily Standup",
            "duration_seconds": 1800,
            "app_name": "Zoom",
            "meeting_organizer": "Alice",
            "meeting_attendees": ["Alice", "Bob", "Charlie"],
        }
        (rec / "summary.md").write_text("Key discussion points here.", encoding="utf-8")

        notes = MainWindow._generate_meeting_notes(rec, meta)
        assert "# Daily Standup" in notes
        assert "2026-03-12" in notes
        assert "30m" in notes
        assert "Zoom" in notes
        assert "Alice" in notes
        assert "(organizer)" in notes
        assert "Bob" in notes
        assert "Key discussion points" in notes
        assert "Meeting Recorder" in notes

    def test_notes_without_summary(self, tmp_path):
        rec = tmp_path / "2026-03-12_10-00-00_Test"
        rec.mkdir()
        meta = {"meeting_subject": "Test Meeting"}
        notes = MainWindow._generate_meeting_notes(rec, meta)
        assert "# Test Meeting" in notes
        assert "Summary" not in notes

    def test_notes_without_subject(self, tmp_path):
        rec = tmp_path / "2026-03-12_10-00-00_ProjectReview"
        rec.mkdir()
        meta = {}
        notes = MainWindow._generate_meeting_notes(rec, meta)
        assert "# ProjectReview" in notes


# ---------------------------------------------------------------------------
# History filter
# ---------------------------------------------------------------------------

class TestHistoryFilter:
    def test_filter_excludes_non_matching(self, tmp_path):
        """Filter text should exclude recordings that don't match."""
        # Create two recording dirs with metadata
        rec1 = tmp_path / "2026-03-01_10-00-00_standup"
        rec1.mkdir()
        (rec1 / "metadata.json").write_text(
            json.dumps({"meeting_subject": "Daily Standup", "app_name": "Zoom"}),
            encoding="utf-8")

        rec2 = tmp_path / "2026-03-02_14-00-00_review"
        rec2.mkdir()
        (rec2 / "metadata.json").write_text(
            json.dumps({"meeting_subject": "Code Review", "app_name": "Teams"}),
            encoding="utf-8")

        mw = MainWindow(on_list_recent=lambda: [rec1, rec2])
        mw._history_frame = mock.Mock()
        mw._history_frame.winfo_children.return_value = []
        mw._stats_label = mock.Mock()

        # Set up filter var as a simple mock
        mw._filter_var = mock.Mock()
        mw._filter_var.get.return_value = "standup"

        # Track which cards are built
        built = []
        mw._build_history_card = lambda p, m=None: built.append(p)

        mw._refresh_history()
        assert len(built) == 1
        assert built[0] == rec1

    def test_filter_matches_app_name(self, tmp_path):
        """Filter should match against app_name."""
        rec1 = tmp_path / "2026-03-01_rec"
        rec1.mkdir()
        (rec1 / "metadata.json").write_text(
            json.dumps({"app_name": "Teams"}), encoding="utf-8")

        mw = MainWindow(on_list_recent=lambda: [rec1])
        mw._history_frame = mock.Mock()
        mw._history_frame.winfo_children.return_value = []
        mw._stats_label = mock.Mock()
        mw._filter_var = mock.Mock()
        mw._filter_var.get.return_value = "zoom"

        built = []
        mw._build_history_card = lambda p, m=None: built.append(p)
        mw._refresh_history()
        assert len(built) == 0  # Teams doesn't match "zoom"

    def test_no_filter_shows_all(self, tmp_path):
        """Empty filter shows all recordings."""
        rec1 = tmp_path / "2026-03-01_rec"
        rec1.mkdir()
        (rec1 / "metadata.json").write_text("{}", encoding="utf-8")

        rec2 = tmp_path / "2026-03-02_rec"
        rec2.mkdir()
        (rec2 / "metadata.json").write_text("{}", encoding="utf-8")

        mw = MainWindow(on_list_recent=lambda: [rec1, rec2])
        mw._history_frame = mock.Mock()
        mw._history_frame.winfo_children.return_value = []
        mw._stats_label = mock.Mock()

        built = []
        mw._build_history_card = lambda p, m=None: built.append(p)

        mw._refresh_history()
        assert len(built) == 2


# ---------------------------------------------------------------------------
# Keyboard navigation
# ---------------------------------------------------------------------------

class TestKeyboardNavigation:
    def test_nav_down_selects_first(self):
        """Down arrow with no selection selects first card."""
        mw = MainWindow()
        mw._is_recording = False
        mw._history_card_paths = [Path("a"), Path("b"), Path("c")]
        mw._select_card = mock.Mock()
        mw._nav_history(1)
        mw._select_card.assert_called_once_with(0)

    def test_nav_up_selects_last(self):
        """Up arrow with no selection selects last card."""
        mw = MainWindow()
        mw._is_recording = False
        mw._history_card_paths = [Path("a"), Path("b"), Path("c")]
        mw._select_card = mock.Mock()
        mw._nav_history(-1)
        mw._select_card.assert_called_once_with(2)

    def test_nav_down_advances(self):
        """Down arrow from index 0 moves to index 1."""
        mw = MainWindow()
        mw._is_recording = False
        mw._history_card_paths = [Path("a"), Path("b"), Path("c")]
        mw._selected_card_idx = 0
        mw._select_card = mock.Mock()
        mw._nav_history(1)
        mw._select_card.assert_called_once_with(1)

    def test_nav_clamps_at_end(self):
        """Down arrow at last item stays at last."""
        mw = MainWindow()
        mw._is_recording = False
        mw._history_card_paths = [Path("a"), Path("b")]
        mw._selected_card_idx = 1
        mw._select_card = mock.Mock()
        mw._nav_history(1)
        mw._select_card.assert_called_once_with(1)

    def test_nav_clamps_at_start(self):
        """Up arrow at first item stays at first."""
        mw = MainWindow()
        mw._is_recording = False
        mw._history_card_paths = [Path("a"), Path("b")]
        mw._selected_card_idx = 0
        mw._select_card = mock.Mock()
        mw._nav_history(-1)
        mw._select_card.assert_called_once_with(0)

    def test_nav_ignored_when_recording(self):
        """Navigation does nothing during recording."""
        mw = MainWindow()
        mw._is_recording = True
        mw._history_card_paths = [Path("a")]
        mw._select_card = mock.Mock()
        mw._nav_history(1)
        mw._select_card.assert_not_called()

    def test_nav_ignored_when_empty(self):
        """Navigation does nothing with no cards."""
        mw = MainWindow()
        mw._is_recording = False
        mw._history_card_paths = []
        mw._select_card = mock.Mock()
        mw._nav_history(1)
        mw._select_card.assert_not_called()

    def test_open_selected_card(self):
        """Enter key opens the selected card's detail view."""
        mw = MainWindow()
        mw._selected_card_idx = 1
        mw._history_card_paths = [Path("a"), Path("b")]
        mw._show_recording_detail = mock.Mock()
        mw._open_selected_card()
        mw._show_recording_detail.assert_called_once_with(Path("b"))

    def test_open_selected_no_selection(self):
        """Enter key does nothing with no selection."""
        mw = MainWindow()
        mw._selected_card_idx = -1
        mw._history_card_paths = [Path("a")]
        mw._show_recording_detail = mock.Mock()
        mw._open_selected_card()
        mw._show_recording_detail.assert_not_called()

    def test_refresh_resets_selection(self, tmp_path):
        """Refreshing history resets selection index."""
        rec = tmp_path / "2026-03-01_rec"
        rec.mkdir()
        (rec / "metadata.json").write_text("{}", encoding="utf-8")

        mw = MainWindow(on_list_recent=lambda: [rec])
        mw._history_frame = mock.Mock()
        mw._history_frame.winfo_children.return_value = []
        mw._stats_label = mock.Mock()
        mw._selected_card_idx = 5
        mw._build_history_card = mock.Mock()
        mw._refresh_history()
        assert mw._selected_card_idx == -1
        assert len(mw._history_card_paths) == 1


# ---------------------------------------------------------------------------
# Window geometry persistence
# ---------------------------------------------------------------------------

class TestGeometryPersistence:
    def test_save_and_load(self, tmp_path, monkeypatch):
        geo_file = tmp_path / "window_geometry.txt"
        monkeypatch.setattr(MainWindow, "_GEOMETRY_FILE", geo_file)

        mw = MainWindow()
        mw._window = mock.Mock()
        mw._window.geometry.return_value = "560x700+100+200"
        mw._save_geometry()

        assert geo_file.exists()
        loaded = MainWindow._load_geometry()
        assert loaded == "560x700+100+200"

    def test_load_missing_file(self, tmp_path, monkeypatch):
        geo_file = tmp_path / "nonexistent.txt"
        monkeypatch.setattr(MainWindow, "_GEOMETRY_FILE", geo_file)
        assert MainWindow._load_geometry() == ""

    def test_validate_geometry_on_screen(self):
        """On-screen geometry is returned unchanged."""
        # Mock ctypes to report a 1920x1080 virtual screen
        with mock.patch("ctypes.windll") as mock_windll:
            metrics = {76: 0, 77: 0, 78: 1920, 79: 1080}
            mock_windll.user32.GetSystemMetrics.side_effect = lambda x: metrics[x]
            result = MainWindow._validate_geometry_on_screen("560x700+100+50")
            assert result == "560x700+100+50"

    def test_validate_geometry_off_screen_right(self):
        """Window far to the right of all monitors returns size only."""
        with mock.patch("ctypes.windll") as mock_windll:
            metrics = {76: 0, 77: 0, 78: 1920, 79: 1080}
            mock_windll.user32.GetSystemMetrics.side_effect = lambda x: metrics[x]
            result = MainWindow._validate_geometry_on_screen("560x700+5000+200")
            assert result == "560x700"

    def test_validate_geometry_off_screen_above(self):
        """Window above all monitors returns size only."""
        with mock.patch("ctypes.windll") as mock_windll:
            metrics = {76: 0, 77: 0, 78: 1920, 79: 1080}
            mock_windll.user32.GetSystemMetrics.side_effect = lambda x: metrics[x]
            result = MainWindow._validate_geometry_on_screen("560x700+100+-2000")
            assert result == "560x700"

    def test_validate_geometry_multi_monitor(self):
        """Position valid on second monitor (negative x) passes."""
        with mock.patch("ctypes.windll") as mock_windll:
            # Two monitors: -1920 to 1920 wide
            metrics = {76: -1920, 77: 0, 78: 3840, 79: 1080}
            mock_windll.user32.GetSystemMetrics.side_effect = lambda x: metrics[x]
            result = MainWindow._validate_geometry_on_screen("560x700+-1500+200")
            assert result == "560x700+-1500+200"

    def test_load_invalid_content(self, tmp_path, monkeypatch):
        geo_file = tmp_path / "window_geometry.txt"
        geo_file.write_text("garbage", encoding="utf-8")
        monkeypatch.setattr(MainWindow, "_GEOMETRY_FILE", geo_file)
        assert MainWindow._load_geometry() == ""


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

class TestTags:
    def test_filter_matches_tags(self, tmp_path):
        """Filter should match recordings by tag."""
        rec1 = tmp_path / "2026-03-01_rec"
        rec1.mkdir()
        (rec1 / "metadata.json").write_text(
            json.dumps({"tags": ["important", "follow-up"]}), encoding="utf-8")

        mw = MainWindow(on_list_recent=lambda: [rec1])
        mw._history_frame = mock.Mock()
        mw._history_frame.winfo_children.return_value = []
        mw._stats_label = mock.Mock()
        mw._filter_var = mock.Mock()
        mw._filter_var.get.return_value = "important"

        built = []
        mw._build_history_card = lambda p, m=None: built.append(p)
        mw._refresh_history()
        assert len(built) == 1

    def test_filter_excludes_untagged(self, tmp_path):
        """Filter by tag excludes untagged recordings."""
        rec1 = tmp_path / "2026-03-01_rec"
        rec1.mkdir()
        (rec1 / "metadata.json").write_text(
            json.dumps({"tags": []}), encoding="utf-8")

        mw = MainWindow(on_list_recent=lambda: [rec1])
        mw._history_frame = mock.Mock()
        mw._history_frame.winfo_children.return_value = []
        mw._stats_label = mock.Mock()
        mw._filter_var = mock.Mock()
        mw._filter_var.get.return_value = "important"

        built = []
        mw._build_history_card = lambda p, m=None: built.append(p)
        mw._refresh_history()
        assert len(built) == 0

    def test_tags_in_metadata(self):
        """Tags field should exist in RecordingMetadata."""
        from meeting_recorder.storage.metadata import RecordingMetadata
        meta = RecordingMetadata()
        assert meta.tags == []
        meta.tags = ["project-x", "urgent"]
        assert meta.tags == ["project-x", "urgent"]

    def test_tags_serialization(self, tmp_path):
        """Tags should round-trip through save/load."""
        from meeting_recorder.storage.metadata import RecordingMetadata
        rec = tmp_path / "test_rec"
        rec.mkdir()
        meta = RecordingMetadata(tags=["alpha", "beta"])
        meta.save(rec)
        loaded = RecordingMetadata.load(rec)
        assert loaded.tags == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Detail Navigation
# ---------------------------------------------------------------------------

class TestDetailNavigation:
    def test_navigate_forward(self, tmp_path):
        paths = [tmp_path / f"rec{i}" for i in range(3)]
        for p in paths:
            p.mkdir()
        mw = MainWindow()
        mw._history_card_paths = paths
        mw._current_detail_path = paths[0]

        # Track which detail is shown
        shown = []
        mw._show_recording_detail = lambda p: (
            shown.append(p),
            setattr(mw, "_current_detail_path", p),
        )

        mw._navigate_detail(1)
        assert shown[-1] == paths[1]

    def test_navigate_backward(self, tmp_path):
        paths = [tmp_path / f"rec{i}" for i in range(3)]
        for p in paths:
            p.mkdir()
        mw = MainWindow()
        mw._history_card_paths = paths
        mw._current_detail_path = paths[2]

        shown = []
        mw._show_recording_detail = lambda p: (
            shown.append(p),
            setattr(mw, "_current_detail_path", p),
        )

        mw._navigate_detail(-1)
        assert shown[-1] == paths[1]

    def test_navigate_at_start_does_nothing(self, tmp_path):
        paths = [tmp_path / f"rec{i}" for i in range(3)]
        for p in paths:
            p.mkdir()
        mw = MainWindow()
        mw._history_card_paths = paths
        mw._current_detail_path = paths[0]

        shown = []
        mw._show_recording_detail = lambda p: shown.append(p)

        mw._navigate_detail(-1)
        assert len(shown) == 0

    def test_navigate_at_end_does_nothing(self, tmp_path):
        paths = [tmp_path / f"rec{i}" for i in range(3)]
        for p in paths:
            p.mkdir()
        mw = MainWindow()
        mw._history_card_paths = paths
        mw._current_detail_path = paths[2]

        shown = []
        mw._show_recording_detail = lambda p: shown.append(p)

        mw._navigate_detail(1)
        assert len(shown) == 0

    def test_navigate_no_detail_open(self):
        mw = MainWindow()
        mw._history_card_paths = []
        mw._current_detail_path = None
        mw._navigate_detail(1)  # Should not error

    def test_close_detail_clears_path(self):
        mw = MainWindow()
        mw._current_detail_path = Path("/fake")
        mw._detail_frame = None
        mw._idle_frame = None
        mw._close_detail()
        assert mw._current_detail_path is None
