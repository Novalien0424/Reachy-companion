"""Backend settings: where the robot is, and where this Mac keeps its data.

The robot connection details already live in the repo-root `.env` that the
deploy skill reads, so this module reads that same file rather than inventing a
second source of truth. The loader is deliberately tiny — `KEY=value`, `#`
comments, optional quotes — because the backend adds no dependencies beyond
what `reachy_companion/.venv` already carries.
"""

from __future__ import annotations
import os
from typing import Final
from pathlib import Path
from dataclasses import dataclass


# .../companion_backend/backend/config.py -> backend -> companion_backend -> repo root
PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
REPO_ROOT: Final[Path] = PACKAGE_ROOT.parent

# Where the app's instance files live on the robot: the deploy skill installs
# `reachy_companion` into the daemon's shared venv, and `faces.v1.json` /
# `people.v1.json` sit beside the installed package.
INSTANCE_DIR: Final[str] = "/venvs/apps_venv/lib/python3.12/site-packages/reachy_companion"

HOST_ENV: Final[str] = "REACHY_HOST"
SSH_USER_ENV: Final[str] = "REACHY_SSH_USER"
DATA_DIR_ENV: Final[str] = "COMPANION_BACKEND_DATA"


@dataclass(frozen=True)
class Settings:
    """Everything the backend needs to reach the robot and find its own data."""

    reachy_host: str
    reachy_ssh_user: str
    data_dir: Path
    instance_dir: str


def _read_env_file(path: Path) -> dict[str, str]:
    """Return `KEY=value` pairs from a dotenv-style file; a missing file is empty."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # A `.env` we cannot read is the same as one that is not there: the
        # backend still starts, it just cannot reach the robot.
        return {}

    values: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_settings(env_path: Path | None = None) -> Settings:
    """Load settings from the process environment, falling back to the repo-root `.env`."""
    file_values = _read_env_file(env_path if env_path is not None else REPO_ROOT / ".env")

    def value_for(key: str) -> str:
        return os.environ.get(key) or file_values.get(key, "")

    raw_data_dir = value_for(DATA_DIR_ENV)
    data_dir = Path(raw_data_dir).expanduser() if raw_data_dir else PACKAGE_ROOT / "data"

    return Settings(
        reachy_host=value_for(HOST_ENV),
        reachy_ssh_user=value_for(SSH_USER_ENV),
        data_dir=data_dir,
        instance_dir=INSTANCE_DIR,
    )
