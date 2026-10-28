"""Settings loading: the repo-root `.env`, the data-dir override, and the defaults."""

from __future__ import annotations
import os
from pathlib import Path

import pytest

from backend import config


def test_load_settings_reads_the_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`KEY=value` lines load; comments, blanks and quotes are handled."""
    for key in ("REACHY_HOST", "REACHY_SSH_USER", config.DATA_DIR_ENV):
        monkeypatch.delenv(key, raising=False)

    env_path = tmp_path / ".env"
    env_path.write_text(
        "# a comment\n"
        "\n"
        "REACHY_HOST=10.0.0.5\n"
        'REACHY_SSH_USER="pollen"\n'
        "REACHY_SSH_PASSWORD=secret\n"
        "  IGNORED_NO_EQUALS  \n",
        encoding="utf-8",
    )

    settings = config.load_settings(env_path=env_path)

    assert settings.reachy_host == "10.0.0.5"
    assert settings.reachy_ssh_user == "pollen"
    assert settings.instance_dir == config.INSTANCE_DIR
    assert settings.data_dir == config.PACKAGE_ROOT / "data"


def test_process_environment_wins_over_the_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit export overrides the file, the usual precedence."""
    env_path = tmp_path / ".env"
    env_path.write_text("REACHY_HOST=from-file\nREACHY_SSH_USER=from-file\n", encoding="utf-8")
    monkeypatch.setenv("REACHY_HOST", "from-environ")
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "elsewhere"))

    settings = config.load_settings(env_path=env_path)

    assert settings.reachy_host == "from-environ"
    assert settings.reachy_ssh_user == "from-file"
    assert settings.data_dir == tmp_path / "elsewhere"


def test_a_missing_env_file_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend still starts without a `.env` — the UI just cannot reach the robot."""
    for key in ("REACHY_HOST", "REACHY_SSH_USER", config.DATA_DIR_ENV):
        monkeypatch.delenv(key, raising=False)

    settings = config.load_settings(env_path=tmp_path / "absent.env")

    assert settings.reachy_host == ""
    assert settings.reachy_ssh_user == ""


def test_the_default_env_path_is_the_repository_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings()` with no argument reads the repo-root `.env` this repo ships."""
    assert config.REPO_ROOT == config.PACKAGE_ROOT.parent
    assert config.PACKAGE_ROOT.name == "companion_backend"

    monkeypatch.setattr(os, "environ", {})
    settings = config.load_settings()
    assert settings.instance_dir == "~/.config/reachy_companion"
    assert isinstance(settings.reachy_host, str)


def test_the_instance_dir_is_the_apps_own_default_on_the_robot() -> None:
    """The backend reaches the directory the app resolves on Linux, in the robot user's home.

    `config.resolve_instance_path` uses `platformdirs.user_config_dir`, which is
    `~/.config/<name>` on the robot; the backend cannot ask platformdirs on the
    Mac, so it states the same path and this pins the two together.
    """
    from reachy_companion import config as app_config

    assert config.INSTANCE_DIR == f"~/.config/{app_config.INSTANCE_DIRNAME}"
