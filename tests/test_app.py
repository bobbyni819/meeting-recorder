"""Tests for MeetingRecorderApp orchestrator (all external deps mocked).

The app module transitively imports pystray (via TrayIcon), which may not be
available in the test environment.  We inject mock modules into sys.modules
before importing the module under test.
"""

from __future__ import annotations

import copy
import sys
import threading
import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest

from meeting_recorder.config import Config
from meeting_recorder.audio.process_finder import MeetingProcess

# Inject mock modules for native UI packages that may not be installed
# in the test environment.  Unlike mock.patch.dict, this persists so the
# module stays in sys.modules for the lifetime of the test process.
for _mod_name in ("pystray", "PIL", "PIL.Image", "winotify"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

from meeting_recorder.app import MeetingRecorderApp  # noqa: E402
import meeting_recorder.app as _app_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dummy_wav(path: Path, duration_s: float = 0.5, rate: int = 16000) -> None:
    """Create a minimal valid WAV file (mono 16-bit silence)."""
    import wave
    n_frames = int(rate * duration_s)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n_frames)


def _make_app(config: Config | None = None):
    """Create a MeetingRecorderApp with heavy deps stubbed out."""
    cfg = config or Config()

    with (
        mock.patch.object(_app_mod, "TrayIcon"),
        mock.patch.object(_app_mod, "RecordingStore"),
        mock.patch.object(_app_mod, "TranscriptionPipeline"),
    ):
        app = MeetingRecorderApp(cfg)

    return app


def _make_process():
    """Create a mock MeetingProcess."""
    return MeetingProcess(pid=1234, name="zoom.exe", app_key="zoom", display_name="Zoom")


# ---------------------------------------------------------------------------
# Recording lifecycle
# ---------------------------------------------------------------------------

class TestStartRecording:
    def test_start_recording_creates_capture_manager(self, tmp_path):
        """Verify CaptureManager is instantiated with correct args."""
        app = _make_app()
        process = _make_process()
        rec_dir = tmp_path / "rec"
        rec_dir.mkdir()

        mock_cm_instance = MagicMock()
        mock_cm_instance.mute_sync = None
        mock_cm_instance.is_recording = True

        with (
            mock.patch.object(_app_mod, "CaptureManager", return_value=mock_cm_instance) as MockCM,
            mock.patch.object(_app_mod, "find_current_meeting", return_value=None),
            mock.patch.object(_app_mod, "RecordingMetadata") as MockMeta,
            mock.patch.object(_app_mod, "GameBarDashboard"),
        ):
            meta_inst = MagicMock()
            meta_inst.meeting_subject = ""
            meta_inst.meeting_attendees = []
            MockMeta.create.return_value = meta_inst
            app._recording_store.create_recording_dir = MagicMock(return_value=rec_dir)
            app._start_recording_for_process(process)

        MockCM.assert_called_once()
        call_kwargs = MockCM.call_args[1]
        assert call_kwargs["pid"] == 1234
        mock_cm_instance.start.assert_called_once()

    def test_start_recording_failure_resets_state(self):
        """If _start_recording_for_process raises, state is cleaned up.

        Patches _pick_window_for_recording: start_recording() always goes
        through the window picker now, and the real picker opens a blocking
        Tk window that would hang the test run.
        """
        app = _make_app()

        with mock.patch.object(
            app, "_pick_window_for_recording", return_value=_make_process()
        ):
            with mock.patch.object(
                app, "_start_recording_for_process", side_effect=RuntimeError("boom")
            ):
                app.start_recording()

        assert app._capture_manager is None
        assert app._current_recording_dir is None
        assert app._current_metadata is None
        assert app._current_process is None


class TestStopRecording:
    def test_stop_recording_spawns_post_process_thread(self):
        """Verify stop_recording starts a post-processing thread.

        _post_process blocks on an event until the assertion has run:
        the post thread clears _post_thread when it finishes, so an
        instantly-returning mock races the assert.
        """
        app = _make_app()

        mock_cm = MagicMock()
        mock_cm.is_recording = True
        mock_cm.elapsed_seconds = 30.0
        app._capture_manager = mock_cm
        app._current_recording_dir = Path("/tmp/rec")
        app._current_metadata = MagicMock()
        app._recording_config = Config()

        started = threading.Event()
        release = threading.Event()

        def blocking_post_process(*args, **kwargs):
            started.set()
            release.wait(timeout=5.0)

        with mock.patch.object(
            app, "_post_process", side_effect=blocking_post_process
        ):
            app.stop_recording()
            assert started.wait(timeout=2.0), "post-process thread never ran"
            post_thread = app._post_thread
            assert post_thread is not None
            release.set()
            post_thread.join(timeout=2.0)

        mock_cm.stop.assert_called_once()

    def test_stop_recording_lock_prevents_double_stop(self):
        """Two concurrent stop calls should only produce one post-process thread."""
        app = _make_app()

        mock_cm = MagicMock()
        mock_cm.is_recording = True
        mock_cm.elapsed_seconds = 10.0
        app._capture_manager = mock_cm
        app._current_recording_dir = Path("/tmp/rec")
        app._current_metadata = MagicMock()
        app._recording_config = Config()

        post_calls = []

        def track_post_process(*args):
            post_calls.append(1)

        with mock.patch.object(app, "_post_process", side_effect=track_post_process):
            t1 = threading.Thread(target=app.stop_recording)
            t2 = threading.Thread(target=app.stop_recording)
            t1.start()
            t2.start()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)

        # Wait for post thread if it exists
        if app._post_thread:
            app._post_thread.join(timeout=2.0)

        # Only one should have spawned a post-process call
        assert len(post_calls) == 1


class TestAutoStop:
    def test_auto_stop_triggers_full_stop_with_post_processing(self):
        """When _on_capture_auto_stopped fires, stop_recording should run fully.

        This tests the flow: monitor detects process exit -> _on_stopped ->
        _on_capture_auto_stopped -> stop_recording -> capture_manager.stop +
        post-processing thread spawned.
        """
        app = _make_app()

        mock_cm = MagicMock()
        mock_cm.is_recording = True
        mock_cm.elapsed_seconds = 15.0
        app._capture_manager = mock_cm
        app._current_recording_dir = Path("/tmp/rec")
        app._current_metadata = MagicMock()
        app._recording_config = Config()

        post_calls = []

        def track_post_process(*args):
            post_calls.append(args)

        with mock.patch.object(app, "_post_process", side_effect=track_post_process):
            with mock.patch.object(app, "_close_dashboard"):
                app._on_capture_auto_stopped()

        # Wait for post-processing thread
        if app._post_thread:
            app._post_thread.join(timeout=2.0)

        # capture_manager.stop() should have been called
        mock_cm.stop.assert_called_once()
        # Post-processing should have been spawned
        assert len(post_calls) == 1
        # State should be cleaned up
        assert app._capture_manager is None

    def test_auto_stop_when_already_stopped_is_noop(self):
        """If recording was already stopped, auto-stop should be a no-op."""
        app = _make_app()
        assert app._capture_manager is None

        # Should not raise
        app._on_capture_auto_stopped()


class TestConfigSnapshot:
    def test_config_snapshot_used_for_post_process(self):
        """Verify post-processing receives a snapshot, not the live config."""
        app = _make_app()

        mock_cm = MagicMock()
        mock_cm.is_recording = True
        mock_cm.elapsed_seconds = 5.0
        app._capture_manager = mock_cm
        app._current_recording_dir = Path("/tmp/rec")
        app._current_metadata = MagicMock()

        # Set up config and start recording to take snapshot
        app.config.output.formats = ["json", "txt"]
        app._recording_config = copy.deepcopy(app.config)

        post_args = []

        def capture_post_process(*args):
            post_args.append(args)

        with mock.patch.object(app, "_post_process", side_effect=capture_post_process):
            app.stop_recording()

        if app._post_thread:
            app._post_thread.join(timeout=2.0)

        assert len(post_args) == 1
        # Third arg is the config snapshot
        cfg_snapshot = post_args[0][2]
        assert cfg_snapshot is not None
        assert cfg_snapshot.output.formats == ["json", "txt"]

        # Modify live config — snapshot should be independent
        app.config.output.formats = ["srt"]
        assert cfg_snapshot.output.formats == ["json", "txt"]


class TestToggleAudioMode:
    def test_toggle_to_desktop_calls_switch_to_desktop(self):
        """Toggling when in app mode should call switch_to_desktop_audio."""
        app = _make_app()
        mock_cm = MagicMock()
        mock_cm.is_desktop_audio = False
        app._capture_manager = mock_cm

        app._toggle_audio_mode()

        mock_cm.switch_to_desktop_audio.assert_called_once()
        mock_cm.switch_to_app_audio.assert_not_called()

    def test_toggle_to_app_uses_current_process_pid(self):
        """Toggling back to app mode should use _current_process.pid."""
        app = _make_app()
        mock_cm = MagicMock()
        mock_cm.is_desktop_audio = True
        app._capture_manager = mock_cm
        app._current_process = _make_process()

        app._toggle_audio_mode()

        mock_cm.switch_to_app_audio.assert_called_once_with(1234)

    def test_toggle_to_app_falls_back_to_manager_pid(self):
        """When _current_process is None, fall back to _capture_manager.pid."""
        app = _make_app()
        mock_cm = MagicMock()
        mock_cm.is_desktop_audio = True
        mock_cm.pid = 5678
        app._capture_manager = mock_cm
        app._current_process = None

        app._toggle_audio_mode()

        mock_cm.switch_to_app_audio.assert_called_once_with(5678)

    def test_toggle_noop_when_no_capture_manager(self):
        """_toggle_audio_mode should do nothing when not recording."""
        app = _make_app()
        assert app._capture_manager is None

        # Should not raise
        app._toggle_audio_mode()


class TestQuit:
    def test_quit_triggers_stop_recording_with_post_processing(self):
        """quit() should call stop_recording() so post-processing runs."""
        app = _make_app()

        mock_cm = MagicMock()
        mock_cm.is_recording = True
        mock_cm.elapsed_seconds = 10.0
        app._capture_manager = mock_cm
        app._current_recording_dir = Path("/tmp/rec")
        app._current_metadata = MagicMock()
        app._recording_config = Config()

        post_calls = []

        def track_post_process(*args):
            post_calls.append(1)

        with mock.patch.object(app, "_post_process", side_effect=track_post_process):
            with mock.patch.object(app, "_close_dashboard"):
                with mock.patch.object(app, "_unregister_hotkey"):
                    app.quit()

        # Wait for post thread
        if app._post_thread:
            app._post_thread.join(timeout=2.0)

        # stop_recording() should have been called, which:
        # 1. Stops capture manager
        mock_cm.stop.assert_called_once()
        # 2. Spawns post-processing
        assert len(post_calls) == 1
        # 3. Cleans up state
        assert app._capture_manager is None

    def test_quit_waits_for_post_processing(self):
        """quit() should wait for post-processing thread to finish."""
        app = _make_app()

        done = threading.Event()

        def slow_post():
            done.wait(timeout=5.0)

        thread = threading.Thread(target=slow_post)
        thread.start()
        app._post_thread = thread

        # Simulate quit in a separate thread so it doesn't block test forever
        def do_quit():
            with mock.patch.object(app, "_close_dashboard"):
                with mock.patch.object(app, "_unregister_hotkey"):
                    with mock.patch.object(_app_mod, "notifications"):
                        app.quit()

        quit_thread = threading.Thread(target=do_quit)
        quit_thread.start()

        # Let quit() start waiting, then unblock post-processing
        time.sleep(0.1)
        done.set()
        quit_thread.join(timeout=5.0)
        thread.join(timeout=2.0)

        assert not quit_thread.is_alive()


class TestPostProcessMetadata:
    def test_summary_fields_persisted_after_parallel_tasks(self, tmp_path):
        """Verify metadata.save() is called after parallel tasks (summary, Drive upload)
        complete, so fields like has_summary are actually persisted to disk.

        Before the fix, metadata.finalize() saved with has_summary=False, then
        _generate_summary set has_summary=True in memory, but there was no
        final save — the summary fields were lost on disk.
        """
        import json
        from meeting_recorder.storage.metadata import RecordingMetadata

        app = _make_app()

        # Create real metadata (so we can check disk state)
        metadata = RecordingMetadata.create(
            app_name="Zoom", app_pid=100, sample_rate=16000,
            channels=1, language="en", transcription_backend="local",
        )
        rec_dir = tmp_path / "rec"
        rec_dir.mkdir()
        # Create valid dummy audio files (pipeline expects them, WAV validation
        # requires a proper header with non-zero duration)
        _make_dummy_wav(rec_dir / "app_audio.wav")
        _make_dummy_wav(rec_dir / "mic_audio.wav")

        # Configure the pipeline mock — last_speaker_mapping must be None
        # (not a truthy MagicMock) to avoid setting mock objects on metadata.
        app._pipeline.process.return_value = []
        app._pipeline.last_speaker_mapping = None

        # Mock dependencies so _post_process can run
        with (
            mock.patch.object(_app_mod, "mix_tracks_streaming"),
            mock.patch.object(_app_mod, "save_all_formats"),
            mock.patch.object(app, "_index_recording"),
            mock.patch.object(app, "_tray"),
            mock.patch.object(_app_mod, "notifications"),
        ):
            # Simulate _generate_summary setting metadata fields
            def fake_summary(recording_dir, segments, meta, summary_config):
                meta.has_summary = True
                meta.summary_provider = "gemini"
                meta.summary_model = "gemini-2.0-flash"

            with mock.patch.object(app, "_generate_summary", side_effect=fake_summary):
                cfg = Config()
                cfg.summary.enabled = True
                app._post_process(rec_dir, metadata, cfg)

        # Verify the metadata was saved to disk WITH the summary fields
        disk_data = json.loads((rec_dir / "metadata.json").read_text(encoding="utf-8"))
        assert disk_data["has_summary"] is True
        assert disk_data["summary_provider"] == "gemini"
        assert disk_data["summary_model"] == "gemini-2.0-flash"
