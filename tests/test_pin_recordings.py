"""Tests for recording pin/unpin feature."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_recorder.ui.main_window import MainWindow


@pytest.fixture
def rec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "recordings"
    d.mkdir()
    return d


def _make_recording(base: Path, name: str, meta: dict | None = None) -> Path:
    d = base / name
    d.mkdir()
    if meta is not None:
        with open(d / "metadata.json", "w") as f:
            json.dump(meta, f)
    return d


class TestTogglePin:
    def test_pin_creates_metadata(self, rec_dir: Path):
        """Pinning a recording without metadata should create metadata."""
        d = _make_recording(rec_dir, "2026-03-10_09-00-00_Test")
        mw = MainWindow()
        mw._toggle_pin(d)
        with open(d / "metadata.json") as f:
            meta = json.load(f)
        assert meta["pinned"] is True

    def test_pin_existing_metadata(self, rec_dir: Path):
        """Pinning should add pinned=True to existing metadata."""
        d = _make_recording(rec_dir, "2026-03-10_09-00-00_Test", {
            "status": "completed", "app_name": "zoom"
        })
        mw = MainWindow()
        mw._toggle_pin(d)
        with open(d / "metadata.json") as f:
            meta = json.load(f)
        assert meta["pinned"] is True
        assert meta["status"] == "completed"
        assert meta["app_name"] == "zoom"

    def test_unpin(self, rec_dir: Path):
        """Unpinning should set pinned=False."""
        d = _make_recording(rec_dir, "2026-03-10_09-00-00_Test", {"pinned": True})
        mw = MainWindow()
        mw._toggle_pin(d)
        with open(d / "metadata.json") as f:
            meta = json.load(f)
        assert meta["pinned"] is False

    def test_toggle_twice(self, rec_dir: Path):
        """Toggling pin twice should return to unpinned."""
        d = _make_recording(rec_dir, "2026-03-10_09-00-00_Test", {"status": "completed"})
        mw = MainWindow()
        mw._toggle_pin(d)  # pin
        mw._toggle_pin(d)  # unpin
        with open(d / "metadata.json") as f:
            meta = json.load(f)
        assert meta["pinned"] is False


class TestPinnedSortOrder:
    def test_pinned_first(self, rec_dir: Path):
        """Pinned recordings should appear before unpinned in sort."""
        _make_recording(rec_dir, "2026-03-10_09-00-00_A", {"status": "completed"})
        _make_recording(rec_dir, "2026-03-11_09-00-00_B", {"pinned": True, "status": "completed"})
        _make_recording(rec_dir, "2026-03-12_09-00-00_C", {"status": "completed"})

        # Simulate the sorting logic from _refresh_history
        recordings = sorted(
            [d for d in rec_dir.iterdir() if d.is_dir()],
            key=lambda p: p.name, reverse=True,
        )
        pinned = []
        unpinned = []
        for rec_path in recordings:
            meta = {}
            meta_path = rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)
            if meta.get("pinned"):
                pinned.append(rec_path)
            else:
                unpinned.append(rec_path)

        sorted_list = pinned + unpinned
        assert "B" in sorted_list[0].name  # pinned is first
        assert len(sorted_list) == 3

    def test_multiple_pinned_maintain_order(self, rec_dir: Path):
        """Multiple pinned recordings should maintain their relative order."""
        _make_recording(rec_dir, "2026-03-10_09-00-00_A", {"pinned": True})
        _make_recording(rec_dir, "2026-03-11_09-00-00_B", {"pinned": True})
        _make_recording(rec_dir, "2026-03-12_09-00-00_C", {})

        recordings = sorted(
            [d for d in rec_dir.iterdir() if d.is_dir()],
            key=lambda p: p.name, reverse=True,
        )
        pinned = []
        unpinned = []
        for rec_path in recordings:
            meta = {}
            meta_path = rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)
            if meta.get("pinned"):
                pinned.append(rec_path)
            else:
                unpinned.append(rec_path)

        sorted_list = pinned + unpinned
        # B (newer) before A (older) among pinned
        assert "B" in sorted_list[0].name
        assert "A" in sorted_list[1].name
        assert "C" in sorted_list[2].name

    def test_no_pinned(self, rec_dir: Path):
        """Without pinned recordings, order stays as-is."""
        _make_recording(rec_dir, "2026-03-10_09-00-00_A", {"status": "completed"})
        _make_recording(rec_dir, "2026-03-11_09-00-00_B", {"status": "completed"})

        recordings = sorted(
            [d for d in rec_dir.iterdir() if d.is_dir()],
            key=lambda p: p.name, reverse=True,
        )
        pinned = []
        unpinned = []
        for rec_path in recordings:
            meta = {}
            meta_path = rec_path / "metadata.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)
            if meta.get("pinned"):
                pinned.append(rec_path)
            else:
                unpinned.append(rec_path)

        sorted_list = pinned + unpinned
        assert "B" in sorted_list[0].name  # newest first
        assert "A" in sorted_list[1].name
