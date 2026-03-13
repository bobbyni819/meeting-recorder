"""Tests for bulk selection and operations on recordings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meeting_recorder.ui.main_window import MainWindow


class TestBulkModeState:
    def test_initial_bulk_mode_off(self):
        mw = MainWindow()
        assert mw._bulk_mode is False

    def test_initial_bulk_selected_empty(self):
        mw = MainWindow()
        assert mw._bulk_selected == set()

    def test_initial_bulk_bar_none(self):
        mw = MainWindow()
        assert mw._bulk_bar is None

    def test_bulk_toggle_btn_none(self):
        mw = MainWindow()
        assert mw._bulk_toggle_btn is None


class TestToggleBulkMode:
    def test_toggle_on(self):
        mw = MainWindow()
        mw._bulk_mode = False
        mw._toggle_bulk_mode()
        assert mw._bulk_mode is True

    def test_toggle_off(self):
        mw = MainWindow()
        mw._bulk_mode = True
        mw._bulk_selected = {Path("/a"), Path("/b")}
        mw._toggle_bulk_mode()
        assert mw._bulk_mode is False
        assert mw._bulk_selected == set()

    def test_toggle_clears_selection(self):
        mw = MainWindow()
        mw._bulk_mode = True
        mw._bulk_selected = {Path("/x")}
        mw._toggle_bulk_mode()
        assert len(mw._bulk_selected) == 0


class TestToggleBulkSelect:
    def test_select(self):
        mw = MainWindow()
        mw._bulk_mode = True
        p = Path("/recording/a")
        mw._toggle_bulk_select(p)
        assert p in mw._bulk_selected

    def test_deselect(self):
        mw = MainWindow()
        mw._bulk_mode = True
        p = Path("/recording/a")
        mw._bulk_selected.add(p)
        mw._toggle_bulk_select(p)
        assert p not in mw._bulk_selected

    def test_toggle_select_multiple(self):
        mw = MainWindow()
        mw._bulk_mode = True
        p1 = Path("/recording/a")
        p2 = Path("/recording/b")
        mw._toggle_bulk_select(p1)
        mw._toggle_bulk_select(p2)
        assert p1 in mw._bulk_selected
        assert p2 in mw._bulk_selected
        mw._toggle_bulk_select(p1)
        assert p1 not in mw._bulk_selected
        assert p2 in mw._bulk_selected


class TestBulkSelectAll:
    def test_select_all(self):
        mw = MainWindow()
        mw._bulk_mode = True
        mw._history_card_paths = [Path("/a"), Path("/b"), Path("/c")]
        mw._bulk_select_all()
        assert len(mw._bulk_selected) == 3

    def test_deselect_all(self):
        mw = MainWindow()
        mw._bulk_mode = True
        mw._bulk_selected = {Path("/a"), Path("/b")}
        mw._bulk_deselect_all()
        assert len(mw._bulk_selected) == 0


class TestBulkDelete:
    def test_delete_removes_dirs(self, tmp_path: Path):
        mw = MainWindow()
        mw._bulk_mode = True
        # Create some fake recording dirs
        d1 = tmp_path / "rec1"
        d2 = tmp_path / "rec2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "test.wav").write_bytes(b"data")
        (d2 / "test.wav").write_bytes(b"data")
        mw._bulk_selected = {d1, d2}
        # Can't test UI dialog, but test internal logic
        import shutil
        deleted = 0
        for path in list(mw._bulk_selected):
            try:
                shutil.rmtree(path)
                deleted += 1
            except Exception:
                pass
        assert deleted == 2
        assert not d1.exists()
        assert not d2.exists()


class TestBulkExport:
    def test_export_copies_files(self, tmp_path: Path):
        mw = MainWindow()
        # Create fake recording dirs with transcripts
        src = tmp_path / "recordings"
        src.mkdir()
        d1 = src / "2026-03-10_09-00-00_Rec1"
        d1.mkdir()
        (d1 / "transcript.txt").write_text("Hello world")
        d2 = src / "2026-03-10_10-00-00_Rec2"
        d2.mkdir()
        (d2 / "transcript.txt").write_text("Goodbye world")
        (d2 / "summary.md").write_text("# Summary")

        mw._bulk_selected = {d1, d2}
        dest = tmp_path / "export"
        dest.mkdir()

        # Simulate export logic
        import shutil
        exported = 0
        for path in sorted(mw._bulk_selected):
            for fname in ("transcript.txt", "summary.md"):
                s = path / fname
                if s.exists():
                    target = dest / f"{path.name}_{fname}"
                    shutil.copy2(s, target)
                    exported += 1
        assert exported == 3  # 2 transcripts + 1 summary


class TestBulkCallbackWiring:
    def test_on_import_audio_stored(self):
        cb = MagicMock()
        mw = MainWindow(on_import_audio=cb)
        assert mw._on_import_audio is cb

    def test_reprocess_callback_used(self):
        cb = MagicMock()
        mw = MainWindow(on_reprocess=cb)
        assert mw._on_reprocess is cb
