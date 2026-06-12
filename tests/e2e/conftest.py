"""E2E test configuration and fixtures."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.e2e

# NOTE: do NOT add a module-level pytest.skip here — it aborts collection of
# the entire tests/ package. Missing e2e dependencies are handled by
# collect_ignore in tests/conftest.py instead.


@pytest.fixture
def meeting_link():
    """Read E2E_MEETING_LINK env var; skip test if not set."""
    link = os.environ.get("E2E_MEETING_LINK")
    if not link:
        pytest.skip("E2E_MEETING_LINK not set")
    return link


@pytest.fixture
def e2e_output_dir(tmp_path):
    """Create a temporary directory for recording output."""
    d = tmp_path / "e2e_output"
    d.mkdir()
    return d
