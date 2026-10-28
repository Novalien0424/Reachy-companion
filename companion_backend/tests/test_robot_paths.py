"""The robot-side instance directory, as each transport is handed it.

The app keeps its files in the robot user's `~/.config/reachy_companion`. scp and
a remote shell resolve `~` differently: scp expands a leading `~/` itself (SFTP
and legacy protocol alike), while a shell expands it only unquoted -- and every
path in the promote and discard scripts is quoted. These tests pin the scp
argument and run the shell scripts the module builds through a real `sh`.
"""

from __future__ import annotations
import os
import shutil
import hashlib
import subprocess
import dataclasses
from typing import Any
from pathlib import Path
from collections.abc import Callable, Sequence

import pytest

from reachy_companion import faces, people
from backend import robot
from backend.config import INSTANCE_DIR, Settings


def test_scp_addresses_the_instance_dir_under_the_robot_users_home(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `~/` reaches scp literally, unexpanded on this machine, for both directions."""
    calls: list[list[str]] = []

    def run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    monkeypatch.setattr(robot.subprocess, "run", run)
    source = tmp_path / "faces.json"
    source.write_text("{}", encoding="utf-8")

    robot._download(settings, faces.FACES_FILENAME, tmp_path / "fetched.json")
    robot._upload(settings, source, robot.FACES_TMP_NAME)

    assert calls[0] == ["scp", f"pollen@10.0.0.5:~/.config/reachy_companion/{faces.FACES_FILENAME}", str(tmp_path / "fetched.json")]
    assert calls[1] == ["scp", str(source), f"pollen@10.0.0.5:~/.config/reachy_companion/{robot.FACES_TMP_NAME}"]
    assert str(Path.home()) not in " ".join(calls[0] + calls[1])


def _local_shell(home: Path, cwd: Path) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run every ssh script through a local `sh`, with HOME standing in for the robot user's."""
    real_run = subprocess.run

    def run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "ssh"
        return real_run(["sh", "-c", argv[-1]], env={**os.environ, "HOME": str(home)}, cwd=cwd, **kwargs)

    return run


@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("sha256sum") is None, reason="needs sh and sha256sum")
def test_the_promote_and_discard_scripts_reach_the_dir_through_home(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The scripts land in `$HOME/.config/reachy_companion`, never in a directory named `~`."""
    home = tmp_path / "home"
    instance = home / ".config" / "reachy_companion"
    instance.mkdir(parents=True)
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.setattr(robot.subprocess, "run", _local_shell(home, elsewhere))

    # A first push: both files absent on the robot, both staged.
    (instance / robot.FACES_TMP_NAME).write_text("faces", encoding="utf-8")
    (instance / robot.PEOPLE_TMP_NAME).write_text("people", encoding="utf-8")
    assert robot._promote(settings, None, None) is True
    assert (instance / faces.FACES_FILENAME).read_text(encoding="utf-8") == "faces"
    assert (instance / people.PEOPLE_FILENAME).read_text(encoding="utf-8") == "people"

    # The guard reads the same files: a hash that no longer matches is a race.
    (instance / robot.FACES_TMP_NAME).write_text("faces 2", encoding="utf-8")
    (instance / robot.PEOPLE_TMP_NAME).write_text("people 2", encoding="utf-8")
    people_sha = hashlib.sha256(b"people").hexdigest()
    assert robot._promote(settings, hashlib.sha256(b"stale").hexdigest(), people_sha) is False
    assert not (instance / robot.FACES_TMP_NAME).exists()

    (instance / robot.FACES_TMP_NAME).write_text("faces 3", encoding="utf-8")
    robot._discard_staged(settings)
    assert not (instance / robot.FACES_TMP_NAME).exists()

    assert not (elsewhere / "~").exists() and not (tmp_path / "~").exists()


@pytest.mark.parametrize(
    "directory",
    ["", "relative/dir", "~", "~user/x", "/a b", "/a'b", '/a"b', "/a$b", "/a`b", "~/a;b", "/a\\b"],
)
def test_the_remote_dir_refuses_anything_but_a_plain_absolute_or_home_path(settings: Settings, directory: str) -> None:
    """The directory reaches scp's argv and a shell script, so it is allowlisted."""
    bad = dataclasses.replace(settings, instance_dir=directory)
    with pytest.raises(robot.RobotError):
        robot._remote_dir(bad)
    with pytest.raises(robot.RobotError):
        robot._remote_shell_dir(bad)


def test_the_shell_form_rewrites_only_a_leading_home(settings: Settings) -> None:
    """`~/` becomes `$HOME/`; an absolute directory passes through untouched."""
    assert INSTANCE_DIR == "~/.config/reachy_companion"
    assert robot._remote_shell_dir(settings) == "$HOME/.config/reachy_companion"
    absolute = dataclasses.replace(settings, instance_dir="/srv/reachy_companion/")
    assert robot._remote_dir(absolute) == "/srv/reachy_companion"
    assert robot._remote_shell_dir(absolute) == "/srv/reachy_companion"
