"""Shared fixtures: the package on `sys.path` and settings rooted at `tmp_path`.

The backend is run as a plain directory (`run.sh` execs uvicorn from
`companion_backend/`), not as an installed distribution, so the tests put that
same directory on `sys.path` and import `backend.*` exactly as the server does.
"""

from __future__ import annotations
import sys
from pathlib import Path

import pytest


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from backend.config import INSTANCE_DIR, Settings  # noqa: E402  (needs the path insert above)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings whose `data_dir` is an empty per-test directory."""
    return Settings(
        reachy_host="10.0.0.5",
        reachy_ssh_user="pollen",
        data_dir=tmp_path / "data",
        instance_dir=INSTANCE_DIR,
    )
