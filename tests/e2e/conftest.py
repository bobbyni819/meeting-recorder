"""E2E test configuration and fixtures."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.e2e

# Auto-skip if e2e dependencies are not installed
try:
    import sounddevice  # noqa: F401
    import soundfile  # noqa: F401
    import playwright  # noqa: F401
except ImportError as _e:
    pytest.skip(
        "E2E dependencies not installed (pip install -e '.[e2e]')",
        allow_module_level=True,
    )


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
