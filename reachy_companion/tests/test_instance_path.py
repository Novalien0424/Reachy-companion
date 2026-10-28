"""Tests for the instance directory resolution and its one-time migration.

Ported from upstream `tests/test_instance_path.py` (5ab905d), adapted to our own
directory (`~/.config/reachy_companion`), our override variable and our item
list; the tests below the port cover what upstream does not carry.
"""

import os
import sys
import json
import stat
import shutil
import logging
import subprocess
import importlib.util
from types import ModuleType, SimpleNamespace
from pathlib import Path

import pytest

import reachy_companion.main as main_mod
import reachy_companion.config as config_mod
from reachy_companion import (
    faces,
    memory,
    people,
    persona,
    tool_spaces,
    face_snapshot,
    profile_toolsets,
    startup_settings,
)
from reachy_companion.config import INSTANCE_PATH_ENV, resolve_instance_path
from reachy_companion.hanova import media_store


POSIX_MODES = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


@pytest.fixture(autouse=True)
def _config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the default instance directory at a temporary config home."""
    config_home = tmp_path / "config"
    monkeypatch.setattr(config_mod, "user_config_dir", lambda name: str(config_home / name))
    monkeypatch.delenv(INSTANCE_PATH_ENV, raising=False)
    return config_home


def _legacy(tmp_path: Path, memory: bool = False) -> Path:
    """Build a package directory holding data the old layout wrote there."""
    legacy = tmp_path / "site-packages"
    (legacy / "user_personalities" / "guide").mkdir(parents=True)
    (legacy / "user_personalities" / "guide" / "profile.md").write_text("+++\n+++\nBe a guide.")
    if memory:
        (legacy / "memory.v1.json").write_text('{"version": 1, "facts": []}')
    return legacy


# --- upstream's tests ---------------------------------------------------------


def test_defaults_to_our_own_config_dir(tmp_path: Path, _config_home: Path) -> None:
    """Without an override the data lives in our own config directory, not the daemon's."""
    resolved = resolve_instance_path(tmp_path / "site-packages")

    assert resolved == _config_home / "reachy_companion"
    assert resolved.is_dir()


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REACHY_COMPANION_INSTANCE_PATH overrides the default and is created."""
    monkeypatch.setenv(INSTANCE_PATH_ENV, str(tmp_path / "mine"))

    assert resolve_instance_path(tmp_path / "site-packages") == tmp_path / "mine"
    assert (tmp_path / "mine").is_dir()


def test_migrates_legacy_data_once(tmp_path: Path) -> None:
    """Data written in the package directory moves over on the first run."""
    legacy = _legacy(tmp_path, memory=True)

    resolved = resolve_instance_path(legacy)

    assert (resolved / "memory.v1.json").read_text() == '{"version": 1, "facts": []}'
    assert (resolved / "user_personalities" / "guide" / "profile.md").exists()

    assert not list(resolved.glob(".*.migrating"))

    # A second run must not overwrite live data with the stale legacy copy.
    (resolved / "memory.v1.json").write_text('{"version": 1, "facts": ["kept"]}')
    resolve_instance_path(legacy)
    assert "kept" in (resolved / "memory.v1.json").read_text()


def test_an_interrupted_migration_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy that dies halfway must not pass for a finished migration."""
    legacy = _legacy(tmp_path)
    target = tmp_path / "instance"
    monkeypatch.setenv(INSTANCE_PATH_ENV, str(target))

    real_copytree = shutil.copytree

    def _dies_halfway(src: Path, dst: Path, **kwargs: object) -> None:
        Path(dst).mkdir(parents=True)
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", _dies_halfway)
    resolve_instance_path(legacy)
    assert not (target / "user_personalities").exists()

    monkeypatch.setattr(shutil, "copytree", real_copytree)
    resolve_instance_path(legacy)
    assert (target / "user_personalities" / "guide" / "profile.md").exists()


def test_a_migration_that_fails_between_items_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Memory landing must not make the next start skip the personalities."""
    legacy = _legacy(tmp_path, memory=True)
    target = tmp_path / "instance"
    monkeypatch.setenv(INSTANCE_PATH_ENV, str(target))

    real_copytree = shutil.copytree

    def _fails(src: Path, dst: Path, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", _fails)
    resolve_instance_path(legacy)
    assert (target / "memory.v1.json").exists()
    assert not (target / ".migrated").exists()

    monkeypatch.setattr(shutil, "copytree", real_copytree)
    resolve_instance_path(legacy)
    assert (target / "user_personalities" / "guide" / "profile.md").exists()
    assert (target / ".migrated").exists()


def test_data_deleted_after_migration_stays_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Once migrated, the legacy copy never comes back."""
    legacy = _legacy(tmp_path)
    target = tmp_path / "instance"
    monkeypatch.setenv(INSTANCE_PATH_ENV, str(target))

    resolve_instance_path(legacy)
    shutil.rmtree(target / "user_personalities")
    resolve_instance_path(legacy)

    assert not (target / "user_personalities").exists()


def test_unusable_path_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad override warns and keeps the previous directory, never crashes."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv(INSTANCE_PATH_ENV, str(blocker / "nope"))

    legacy = tmp_path / "site-packages"
    assert resolve_instance_path(legacy) == legacy


# --- ours -----------------------------------------------------------------------

# Every item, in the shape the robot holds it. Directories carry one file each.
_FULL_LEGACY: dict[str, str] = {
    ".env": "OPENAI_API_KEY=sk-test\n",
    "persona.md": "You are Reachy.\n",
    "memory.v1.json": '{"version": 1, "facts": [{"text": "likes tea"}]}',
    "faces.v1.json": '{"version": 1, "faces": [{"id": "f_1_abcdef"}]}',
    "people.v1.json": '{"version": 1, "people": [{"name": "Lena"}]}',
    "face_snapshots/f_1_abcdef.jpg": "jpeg bytes",
    "startup_settings.json": '{"voice": "coral"}',
    "profile_toolsets.json": '{"profiles": {}}',
    "installed_tool_spaces.json": '{"spaces": []}',
    "user_personalities/guide/profile.md": "+++\n+++\nBe a guide.",
    "google-workspace-mcp/account.json": '{"refresh_token": "r"}',
    "google-oauth.json": '{"refreshToken": "r"}',
    "nas-video-index.json": '{"videos": []}',
}


def _full_legacy(tmp_path: Path) -> Path:
    legacy = tmp_path / "site-packages"
    for relative, content in _FULL_LEGACY.items():
        path = legacy / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return legacy


def test_the_item_list_names_every_store_the_code_writes() -> None:
    """Each writer's own filename constant is migrated, so a rename cannot drift past the list."""
    owned = {
        memory.MEMORY_FILENAME,
        faces.FACES_FILENAME,
        people.PEOPLE_FILENAME,
        face_snapshot.SNAPSHOT_DIRNAME,
        persona.PERSONA_FILENAME,
        startup_settings.STARTUP_SETTINGS_FILENAME,
        profile_toolsets.PROFILE_TOOLSETS_FILENAME,
        tool_spaces.INSTALLED_TOOL_SPACES_FILENAME,
        config_mod.USER_PERSONALITIES_DIRNAME,
        ".env",
    }
    assert owned <= set(config_mod._INSTANCE_DATA_NAMES)
    assert {top.split("/")[0] for top in _FULL_LEGACY} == set(config_mod._INSTANCE_DATA_NAMES)
    # The media cache is regenerable and deliberately starts empty.
    assert media_store.MEDIA_DIRNAME not in config_mod._INSTANCE_DATA_NAMES


def test_every_item_migrates_and_the_cache_does_not(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The whole robot-side inventory arrives byte for byte; `hanova_media/` stays behind."""
    legacy = _full_legacy(tmp_path)
    (legacy / "hanova_media" / "music").mkdir(parents=True)
    (legacy / "hanova_media" / "music" / "track.mp3").write_bytes(b"audio")

    with caplog.at_level(logging.INFO, logger=config_mod.__name__):
        resolved = resolve_instance_path(legacy)

    for relative, content in _FULL_LEGACY.items():
        assert (resolved / relative).read_text(encoding="utf-8") == content, relative
    assert not (resolved / "hanova_media").exists()
    assert (resolved / ".migrated").exists()
    assert f"(migrated {len(config_mod._INSTANCE_DATA_NAMES)} items)" in caplog.text
    # Copies, never moves: the legacy data stays until a reinstall replaces it.
    assert all((legacy / relative).exists() for relative in _FULL_LEGACY)


@POSIX_MODES
def test_modes_survive_and_the_directory_is_private(tmp_path: Path) -> None:
    """Credential files keep 600 and credential directories 700; the instance dir is 700."""
    legacy = _full_legacy(tmp_path)
    for name in (".env", "google-oauth.json", "nas-video-index.json", "google-workspace-mcp/account.json"):
        (legacy / name).chmod(0o600)
    (legacy / "google-workspace-mcp").chmod(0o700)

    resolved = resolve_instance_path(legacy)

    def mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    assert mode(resolved) == 0o700
    assert mode(resolved / "google-workspace-mcp") == 0o700
    for name in (".env", "google-oauth.json", "nas-video-index.json", "google-workspace-mcp/account.json"):
        assert mode(resolved / name) == 0o600, name


@POSIX_MODES
def test_an_existing_directory_is_tightened_to_700(tmp_path: Path, _config_home: Path) -> None:
    """A directory someone created group- or world-readable is closed before data lands in it."""
    target = _config_home / "reachy_companion"
    target.mkdir(parents=True)
    target.chmod(0o755)

    resolve_instance_path(tmp_path / "site-packages")

    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_a_start_before_the_restore_does_not_shut_legacy_data_out(tmp_path: Path) -> None:
    """Nothing to carry writes no marker, so files restored into the old place later still move."""
    legacy = tmp_path / "site-packages"
    legacy.mkdir()

    resolved = resolve_instance_path(legacy)
    assert not (resolved / ".migrated").exists()

    (legacy / "persona.md").write_text("You are Reachy.\n")
    resolve_instance_path(legacy)

    assert (resolved / "persona.md").read_text() == "You are Reachy.\n"
    assert (resolved / ".migrated").exists()


def test_the_migrated_env_points_at_the_new_directory(tmp_path: Path) -> None:
    """Credential paths into the legacy directory follow the data; everything else is untouched."""
    legacy = _full_legacy(tmp_path)
    original = (
        "OPENAI_API_KEY=sk-test\n"
        f"GOOGLE_CREDS_DIR={legacy}/google-workspace-mcp\n"
        f'HERMES_DRIVE_SECRETS="{legacy}/google-oauth.json"\n'
        f"HANOVA_NAS_INDEX_PATH='{legacy}/nas-video-index.json'\n"
        f"PERSONA_FILE={legacy}\n"
        f"NOT_OURS={legacy}_backup/x\n"
        f"NESTED=/elsewhere{legacy}/x\n"
    )
    (legacy / ".env").write_text(original, encoding="utf-8")

    resolved = resolve_instance_path(legacy)

    assert (resolved / ".env").read_text(encoding="utf-8") == (
        "OPENAI_API_KEY=sk-test\n"
        f"GOOGLE_CREDS_DIR={resolved}/google-workspace-mcp\n"
        f'HERMES_DRIVE_SECRETS="{resolved}/google-oauth.json"\n'
        f"HANOVA_NAS_INDEX_PATH='{resolved}/nas-video-index.json'\n"
        f"PERSONA_FILE={resolved}\n"
        f"NOT_OURS={legacy}_backup/x\n"
        f"NESTED=/elsewhere{legacy}/x\n"
    )
    assert (legacy / ".env").read_text(encoding="utf-8") == original


def test_the_startup_line_names_the_directory_and_the_migration(
    tmp_path: Path, _config_home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One INFO line per start: `~`-relative, and whether this start migrated anything."""
    monkeypatch.setattr(Path, "home", lambda: _config_home.parent)
    legacy = _legacy(tmp_path, memory=True)

    with caplog.at_level(logging.INFO, logger=config_mod.__name__):
        resolve_instance_path(legacy)
        resolve_instance_path(legacy)

    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("instance: ")]
    assert lines == [
        "instance: ~/config/reachy_companion (migrated 2 items)",
        "instance: ~/config/reachy_companion (already migrated)",
    ]


def test_the_app_entry_runs_on_the_resolved_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ReachyCompanion.run` hands `run()` the resolved directory as a string, not the package dir."""
    package_dir = tmp_path / "site-packages" / "reachy_companion"
    package_dir.mkdir(parents=True)
    calls: list[str] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(main_mod.asyncio, "new_event_loop", lambda: None)
    monkeypatch.setattr(main_mod.asyncio, "set_event_loop", lambda loop: None)
    monkeypatch.setattr(main_mod, "parse_args", lambda: (SimpleNamespace(debug=False), []))
    monkeypatch.setattr(main_mod, "setup_logger", lambda debug: calls.append("logger"))

    def _resolve(legacy: Path) -> Path:
        calls.append("resolve")
        assert legacy == package_dir
        return tmp_path / "instance"

    monkeypatch.setattr(config_mod, "resolve_instance_path", _resolve)
    monkeypatch.setattr(main_mod, "run", lambda args, **kwargs: captured.update(kwargs))

    app = object.__new__(main_mod.ReachyCompanion)
    app.settings_app = None
    monkeypatch.setattr(app, "_get_instance_path", lambda: package_dir / "main.py", raising=False)
    app.run(reachy_mini=None, stop_event=None)

    assert calls == ["logger", "resolve"]
    assert captured["instance_path"] == str(tmp_path / "instance")


def _load_voice_audition() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "voice_audition.py"
    spec = importlib.util.spec_from_file_location("voice_audition_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_voice_audition_script_edits_the_robot_users_instance_dir(tmp_path: Path) -> None:
    """The remote script resolves `~` against the robot user's HOME, where it runs."""
    voice_audition = _load_voice_audition()
    assert voice_audition.INST == "~/.config/reachy_companion"

    home = tmp_path / "home"
    instance = home / ".config" / "reachy_companion"
    instance.mkdir(parents=True)
    (instance / ".env").write_text("VOICEFX_ENABLED=1\n", encoding="utf-8")
    (instance / "persona.md").write_text("You are Reachy.\n", encoding="utf-8")

    script = voice_audition.APPLY_REMOTE.format(
        inst=voice_audition.INST, mark_begin=voice_audition.ENV_MARK_BEGIN, mark_end=voice_audition.ENV_MARK_END
    )
    spec = json.dumps({"voice": "coral", "fx": {"VOICEFX_COMB_MIX": "0.0"}, "enabled_line": []})
    done = subprocess.run(
        [sys.executable, "-c", script],
        input=spec,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        cwd=tmp_path,
        timeout=60,
    )

    assert done.returncode == 0, done.stderr
    assert "VOICEFX_COMB_MIX=0.0" in (instance / ".env").read_text(encoding="utf-8")
    assert (instance / "persona.md").read_text(encoding="utf-8").startswith('+++\nvoice = "coral"\n+++')
    assert not (tmp_path / "~").exists()
