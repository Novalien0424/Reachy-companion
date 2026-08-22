"""Contract tests for the NAS home-video tools (D-018, R2/R4/R5/R6). No SMB.

Also pins review round 1 findings 15 (validated paths, collision-free names,
atomic staging) and 16 (a conversation-scoped session whose cursor only moves
after a successful cast), and review round 2 findings 3 (tri-state house
gating), 5 (synthetic fixtures only), 10 (single-flight staging) and 11 (a
cursor token, compared and swapped atomically).

**Every identifier below is a synthetic sentinel** (round 2, finding 5). The
previous version used share, folder and place names copied from the operator's
private manifest -- which committed those identifiers to the repository inside
the very test suite written to keep them out of it. The `SENTINEL_*_q4` tokens
are deliberately unmistakable: they cannot be confused with a real NAS layout by
a reader, and the untracked scan in Task 14 Step 9b treats any of them appearing
next to a real value as a failure.
"""

import json
import time
import types
import importlib
from typing import Any

import pytest

from reachy_companion import home_net
from reachy_companion.hanova import nas, settings
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.nas_skip import NasSkip
from reachy_companion.hanova.music_player import PLAYER
from reachy_companion.tools.play_nas_video import PlayNasVideo
from reachy_companion.tools.nas_play_folder import NasPlayFolder
from reachy_companion.tools.nas_video_query import NasVideoQuery


INDEX = {
    "folders": {
        "SENTINEL_TRIP_q4": {
            "year": 2019,
            "place": "SENTINEL_PLACE_q4",
            "country": "SENTINEL_COUNTRY_q4",
            "count": 2,
            "is_travel": True,
        },
    },
    "videos": [
        {
            "path": "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4",
            "cast_path": "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4",
            "cast_ready": True,
            "year": 2019,
            "place": "SENTINEL_PLACE_q4",
            "country": "SENTINEL_COUNTRY_q4",
            "label": "morning",
            "top_folder": "SENTINEL_TRIP_q4",
            "name": "clip01",
            "seq": 1,
        },
        {
            "path": "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4",
            "cast_path": "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4",
            "cast_ready": True,
            "year": 2019,
            "place": "SENTINEL_PLACE_q4",
            "country": "SENTINEL_COUNTRY_q4",
            "label": "evening",
            "top_folder": "SENTINEL_TRIP_q4",
            "name": "clip02",
            "seq": 2,
        },
    ],
}

_TOOL_MODULES = (
    "reachy_companion.tools.nas_video_query",
    "reachy_companion.tools.play_nas_video",
    "reachy_companion.tools.nas_play_folder",
    "reachy_companion.tools.nas_skip",
)


def _deps(tmp_path):
    return types.SimpleNamespace(reachy_mini=None, instance_path=tmp_path)


def _tool(module: str, class_name: str):
    """Resolve a tool class off the module object that is live right now.

    Task 6 carry: `test_external_loading.py` and `test_tool_space_runtime.py` pop
    every `reachy_companion.tools.*` entry out of `sys.modules`, so the class
    captured at collection keeps the *first* copy's globals while every patch
    below -- they all name their target by string -- lands on the second.
    """
    return getattr(importlib.import_module(module), class_name)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """Configure the nas family, put the robot at home, and block SMB and HTTP."""
    # Bind the tool classes to whichever module object is live in THIS test; see
    # `_tool` above and the same idiom in `tests/test_hanova_cast.py`.
    for module, class_name in zip(
        _TOOL_MODULES,
        ("NasVideoQuery", "PlayNasVideo", "NasPlayFolder", "NasSkip"),
    ):
        monkeypatch.setitem(globals(), class_name, _tool(module, class_name))
    index_path = tmp_path / "nas-video-index.json"
    index_path.write_text(json.dumps(INDEX), encoding="utf-8")
    monkeypatch.setenv("HANOVA_NAS_HOST", "nas.example.invalid")
    monkeypatch.setenv("HANOVA_NAS_USER", "u")
    monkeypatch.setenv("HANOVA_NAS_PASSWORD", "p")
    monkeypatch.setenv("HANOVA_NAS_SHARE", "SENTINEL_SHARE_q4")
    monkeypatch.setenv("HANOVA_NAS_SUBPATH", "SENTINEL_SRC_DIR_q4")
    monkeypatch.setenv("HANOVA_NAS_CAST_SUBPATH", "SENTINEL_CAST_DIR_q4")
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(index_path))
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_VIDEO_URL", "tv_show_video_url")
    monkeypatch.setenv("HANOVA_CAST_ENTITY", "media_player.example_tv")
    monkeypatch.setenv("HANOVA_MEDIA_HTTP_BASE", "http://robot.example.invalid:7860")
    monkeypatch.delenv("HANOVA_MEDIA_DIR", raising=False)
    settings.set_media_mount_ready(True)
    home_net.reset_cache()
    GATE.reset()
    GATE.begin_session()
    nas.clear_session()

    async def always_home() -> str:
        return home_net.HOME

    for module in _TOOL_MODULES:
        monkeypatch.setattr(f"{module}.home_state", always_home)
    yield
    nas.clear_session()
    GATE.reset()
    settings.set_media_mount_ready(False)
    home_net.reset_cache()


def _stub_transfer(monkeypatch) -> dict:
    """Replace the SMB fetch and the HA cast with recorders."""
    recorded: dict[str, Any] = {"fetched": [], "cast": []}

    def fake_fetch(cast_path, destination):
        recorded["fetched"].append(cast_path)
        destination.write_bytes(b"MP4")

    async def fake_run_script(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "fetch_cast_file", fake_fetch)
    monkeypatch.setattr(nas, "ha_run_script", fake_run_script)
    return recorded


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert NasVideoQuery.name == "nas_video_query"
    assert PlayNasVideo.name == "play_nas_video"
    assert NasPlayFolder.name == "nas_play_folder"
    assert NasSkip.name == "nas_skip"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream put the owner's name and real place names in these."""
    for tool in (NasVideoQuery(), PlayNasVideo(), NasPlayFolder(), NasSkip()):
        assert "@" not in tool.description
        assert "SENTINEL_PLACE_q4" not in tool.description
        assert len(tool.description) <= 120


def test_filter_index_matches_place_case_insensitively():
    """Voice input has no case, and the index is mixed-language."""
    assert len(nas.filter_index(INDEX, place="sentinel_place_q4")) == 2
    assert nas.filter_index(INDEX, place="nowhere") == []


def test_filter_index_filters_by_year_range():
    """Year filters are how a user says "the trip a few years ago"."""
    assert len(nas.filter_index(INDEX, year=2019)) == 2
    assert nas.filter_index(INDEX, year_from=2020) == []


def test_folder_playlist_is_ordered_by_sequence():
    """Advancing through a trip must follow the recorded order."""
    playlist = nas.folder_playlist(INDEX, "SENTINEL_TRIP_q4")
    assert [video["name"] for video in playlist] == ["clip01", "clip02"]


def test_cast_filename_is_flat_and_safe():
    """The served name must contain no separators the static route could walk."""
    name = nas.cast_filename("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert "/" not in name and name.endswith(".mp4")


# --- path validation and naming (review finding 15) -----------------------
def test_long_paths_that_share_a_prefix_do_not_collide():
    """Finding 15: truncation mapped two different home videos onto one name."""
    prefix = "SENTINEL_CAST_DIR_q4/" + "a" * 200
    first = nas.cast_filename(f"{prefix}/clip01.mp4")
    second = nas.cast_filename(f"{prefix}/clip02.mp4")
    assert first != second


def test_the_served_name_leaks_no_folder_names():
    """A LAN URL is visible to anything on the network, including guests."""
    name = nas.cast_filename("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    for token in ("SENTINEL", "TRIP", "CAST", "clip01", "2019"):
        assert token not in name


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd",
        "SENTINEL_CAST_DIR_q4/../../etc/passwd",
        "/absolute/SENTINEL_CAST_DIR_q4/clip.mp4",
        "SomeOtherFolder/clip.mp4",
        "C:/Windows/clip.mp4",
        "SENTINEL_CAST_DIR_q4/clip.exe",
        "",
    ],
)
def test_paths_outside_the_configured_subtree_are_refused(bad):
    """Finding 15: an index entry is untrusted input, not a path to interpolate."""
    with pytest.raises(nas.NasError):
        nas.cast_filename(bad)


def test_a_valid_path_normalises_rather_than_being_refused():
    """A tidy-but-redundant path from the index is fine."""
    assert nas.validate_cast_path("SENTINEL_CAST_DIR_q4/./SENTINEL_TRIP_q4/clip01.mp4") == (
        "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"
    )


def test_the_configured_cast_subpath_is_actually_used(monkeypatch):
    """Finding 15: the accessor existed and nothing read it."""
    monkeypatch.setenv("HANOVA_NAS_CAST_SUBPATH", "Elsewhere")
    with pytest.raises(nas.NasError):
        nas.validate_cast_path("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert nas.validate_cast_path("Elsewhere/clip01.mp4") == "Elsewhere/clip01.mp4"


# --- HANOVA_NAS_SUBPATH is consumed (round 2, finding 12) -----------------
def test_the_configured_source_subpath_is_actually_used(monkeypatch):
    """Round 2, finding 12: a mandatory prerequisite that nothing read.

    `HANOVA_NAS_SUBPATH` blocked all three casting tools while changing no
    behaviour whatsoever. It now bounds the subtree an index entry's *original*
    path may name.
    """
    assert nas.validate_source_path("SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4") == (
        "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"
    )
    monkeypatch.setenv("HANOVA_NAS_SUBPATH", "SomewhereElse")
    with pytest.raises(nas.NasError):
        nas.validate_source_path("SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")


@pytest.mark.asyncio
async def test_an_index_entry_outside_the_source_subpath_is_never_staged(monkeypatch, tmp_path):
    """The prerequisite has to change behaviour, or it is a dead switch."""
    recorded = _stub_transfer(monkeypatch)
    stray = dict(INDEX["videos"][0])
    stray["path"] = "SomewhereElse/SENTINEL_TRIP_q4/clip01.mp4"
    out = await nas.stage_and_cast(stray, tmp_path)
    assert out["ok"] is False
    assert recorded["fetched"] == [] and recorded["cast"] == []


def test_the_copy_is_staged_privately_and_renamed(monkeypatch, tmp_path):
    """Finding 15: a Chromecast fetching mid-copy must not get a partial file."""
    seen = {}

    class _FakeSmbFile:
        def __init__(self) -> None:
            self._data = b"MP4DATA"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            data, self._data = self._data, b""
            return data

    class _FakeSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            seen["host"] = host

        @staticmethod
        def open_file(path, mode="rb"):
            seen["remote"] = path
            # Prove the served file does not exist yet while the copy runs.
            seen["destination_exists_midway"] = (tmp_path / "out.mp4").exists()
            partials = list(tmp_path.glob("*.part"))
            seen["partial_seen"] = bool(partials)
            return _FakeSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _FakeSmbClient)
    destination = tmp_path / "out.mp4"
    nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)

    assert destination.read_bytes() == b"MP4DATA"
    assert seen["destination_exists_midway"] is False
    assert list(tmp_path.glob("*.part")) == [], "the .part file must be renamed away"
    assert seen["remote"].startswith("\\\\nas.example.invalid\\SENTINEL_SHARE_q4\\")


def test_the_smb_connect_is_bounded(monkeypatch, tmp_path):
    """Finding 1: the library's own default is 60 s of a held single-flight lock."""
    seen = {}

    class _FakeSmbFile:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            return b""

    class _FakeSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            seen["connection_timeout"] = connection_timeout

        @staticmethod
        def open_file(path, mode="rb"):
            return _FakeSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _FakeSmbClient)
    # An empty read means an empty clip, which is its own refusal -- all this
    # test cares about is what reached `register_session` on the way there.
    with pytest.raises(nas.NasError):
        nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", tmp_path / "out.mp4")

    assert seen["connection_timeout"] == nas._SMB_CONNECT_TIMEOUT_S
    assert 0 < nas._SMB_CONNECT_TIMEOUT_S <= 60, "a connect budget above the library default buys nothing"


def test_a_stalled_nas_copy_is_abandoned_rather_than_blocking_forever(monkeypatch, tmp_path):
    """Finding 1: an unbounded read wedged the clip, the lock and the tool call.

    `smbclient` sets its socket back to blocking after connect
    (`transport.py:69`) and neither `open_file` nor `Open.read` takes a timeout,
    so a spun-down NAS or a half-open TCP connection used to stall the copy with
    no deadline at all. The whole copy now runs against a wall-clock budget.
    """
    monkeypatch.setattr(nas, "_SMB_COPY_BUDGET_S", 0.05)

    class _NeverEndingSmbFile:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            time.sleep(0.01)  # a NAS that trickles and never reaches the end
            return b"x" * 1024

    class _StallingSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            return _NeverEndingSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _StallingSmbClient)
    destination = tmp_path / "out.mp4"

    with pytest.raises(nas.NasError) as excinfo:
        nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)

    assert "too long" in str(excinfo.value)
    assert not destination.exists(), "a timed-out copy must never be promoted"
    assert list(tmp_path.glob(f"*{nas.PART_SUFFIX}")) == [], "the staging file must be cleaned up"


def test_a_stalled_copy_releases_the_single_flight_lock(monkeypatch, tmp_path):
    """Finding 1: the lock was held for the whole copy, so a stall pinned it forever."""
    monkeypatch.setattr(nas, "_SMB_COPY_BUDGET_S", 0.05)
    destination = tmp_path / "out.mp4"

    class _NeverEndingSmbFile:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            time.sleep(0.01)
            return b"x" * 1024

    class _StallingSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            return _NeverEndingSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _StallingSmbClient)
    with pytest.raises(nas.NasError):
        nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)

    # The NAS comes back. A second attempt for the SAME clip must be able to run,
    # which it can only do if the first attempt released the destination's lock.
    class _WorkingSmbFile:
        def __init__(self) -> None:
            self._data = b"MP4DATA"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            data, self._data = self._data, b""
            return data

    class _WorkingSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            return _WorkingSmbFile()

    monkeypatch.setattr(nas, "_SMB_COPY_BUDGET_S", 30.0)
    monkeypatch.setitem(__import__("sys").modules, "smbclient", _WorkingSmbClient)
    nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)
    assert destination.read_bytes() == b"MP4DATA", "the lock outlived the stall"


class _WorkingSmbFile:
    """A cooperative SMB handle that yields one payload and then EOF."""

    def __init__(self, payload: bytes = b"MP4DATA") -> None:
        self._data = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, size=-1):
        data, self._data = self._data, b""
        return data


@pytest.mark.asyncio
async def test_a_hung_smb_open_fails_the_tool_call_instead_of_wedging_it(monkeypatch, tmp_path):
    """Fix round 2: `open_file` sits between the two bounded halves, unbounded.

    `smbclient.open_file` performs the tree-connect and create round trips, and
    both call `Connection.receive(request)` with **no timeout**
    (`open.py:1264`, `tree.py:250`) against a socket that was put back into
    blocking mode after connect (`transport.py:69`). A NAS that completes TCP
    connect and session auth but then never answers the open therefore wedged
    the thread between `_SMB_CONNECT_TIMEOUT_S` and `_SMB_COPY_BUDGET_S`, where
    neither could see it. The outer fence turns that into a spoken failure.
    """
    import threading as _threading

    monkeypatch.setattr(nas, "_SMB_COPY_BUDGET_S", 0.05)
    monkeypatch.setattr(nas, "_SMB_FENCE_HEADROOM_S", 0.05)
    release = _threading.Event()

    class _HangingSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            release.wait(timeout=10)  # the create/tree-connect answer never comes
            return _WorkingSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _HangingSmbClient)

    try:
        started = time.monotonic()
        out = await nas.stage_and_cast(dict(INDEX["videos"][0]), tmp_path)
        elapsed = time.monotonic() - started

        assert out["ok"] is False
        assert out["error"] == "the NAS copy took too long and was abandoned"
        assert elapsed < 2.0, "the fence must answer promptly, not wait the hang out"
        nas_dir = tmp_path / "hanova_media" / "nas"
        assert list(nas_dir.glob("*.mp4")) == [], "a fenced fetch may promote nothing"
    finally:
        # Let the leaked worker finish so the suite does not wait on it at teardown.
        release.set()


@pytest.mark.asyncio
async def test_a_fenced_fetch_does_not_block_a_different_clip(monkeypatch, tmp_path):
    """Rung 2's documented leak is per-destination, and no worse than stated.

    The fence does not kill the worker, so the hung clip's `_FETCH_LOCKS` entry
    stays held until the open unblocks. That is survivable only because the locks
    are keyed by destination: every *other* clip must still stage and play.
    """
    import threading as _threading

    monkeypatch.setattr(nas, "_SMB_COPY_BUDGET_S", 0.05)
    monkeypatch.setattr(nas, "_SMB_FENCE_HEADROOM_S", 0.05)
    release = _threading.Event()

    class _PartlyHangingSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            if "clip01" in path:
                release.wait(timeout=10)
            return _WorkingSmbFile()

    async def ok_cast(script_name, data, timeout_s=60.0):
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "ha_run_script", ok_cast)
    monkeypatch.setitem(__import__("sys").modules, "smbclient", _PartlyHangingSmbClient)

    try:
        stuck = await nas.stage_and_cast(dict(INDEX["videos"][0]), tmp_path)
        assert stuck["ok"] is False and stuck["error"] == "the NAS copy took too long and was abandoned"

        other = await nas.stage_and_cast(dict(INDEX["videos"][1]), tmp_path)
        assert other["ok"] is True, "locks are per-destination; a different clip must still play"
    finally:
        release.set()


def test_a_failed_copy_leaves_nothing_behind(monkeypatch, tmp_path):
    """A half-written clip is worse than no clip."""

    class _Boom:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            raise OSError("connection refused")

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _Boom)
    destination = tmp_path / "out.mp4"
    with pytest.raises(nas.NasError):
        nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)
    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_the_smb_error_text_never_reaches_the_caller(monkeypatch, tmp_path):
    """Finding 7: an SMB error quotes the full share path back."""
    sentinel = "SENTINEL_PRIVATE_x7"

    class _Boom:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            raise OSError(f"cannot open {sentinel}")

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _Boom)
    with pytest.raises(nas.NasError) as excinfo:
        nas.fetch_cast_file("SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", tmp_path / "out.mp4")
    assert sentinel not in str(excinfo.value)


def test_load_index_returns_none_when_missing(monkeypatch, tmp_path):
    """A missing index is a configuration fact, not an exception."""
    monkeypatch.setenv("HANOVA_NAS_INDEX_PATH", str(tmp_path / "absent.json"))
    assert nas.load_index() is None


@pytest.mark.asyncio
async def test_nas_video_query_is_unavailable_without_the_index(monkeypatch, tmp_path):
    """R5: the tool is dead without the operator-supplied index, and it says so."""
    monkeypatch.delenv("HANOVA_NAS_INDEX_PATH")
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out == {"status": "unavailable", "reason": "HANOVA_NAS_INDEX_PATH"}


@pytest.mark.asyncio
async def test_nas_video_query_is_away_from_home_off_the_lan(monkeypatch, tmp_path):
    """R4: all nas_* tools are house-bound."""

    async def not_home() -> str:
        return home_net.AWAY

    monkeypatch.setattr("reachy_companion.tools.nas_video_query.home_state", not_home)
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out == {"status": "away_from_home"}


@pytest.mark.parametrize(
    "module,class_name,kwargs",
    [
        ("nas_video_query", "NasVideoQuery", {"place": "sentinel_place_q4"}),
        ("play_nas_video", "PlayNasVideo", {"place": "sentinel_place_q4"}),
        ("nas_play_folder", "NasPlayFolder", {"top_folder": "SENTINEL_TRIP_q4"}),
        ("nas_skip", "NasSkip", {}),
    ],
)
@pytest.mark.asyncio
async def test_every_nas_tool_does_no_work_when_home_is_unknown(monkeypatch, tmp_path, module, class_name, kwargs):
    """Round 2, finding 3: UNKNOWN is not permission, for any of the four.

    Round 1 branched only on AWAY, so an HA outage or a VPN let all four fall
    through and touch the index, the NAS and Home Assistant. The answer must be
    its own status and nothing may happen.
    """

    async def unknown() -> str:
        return home_net.UNKNOWN

    def fail_load():
        raise AssertionError(f"{module} must not read the index on UNKNOWN")

    def fail_fetch(cast_path, destination):
        raise AssertionError(f"{module} must not touch the NAS on UNKNOWN")

    async def fail_cast(script_name, data, timeout_s=60.0):
        raise AssertionError(f"{module} must not touch Home Assistant on UNKNOWN")

    monkeypatch.setattr(f"reachy_companion.tools.{module}.home_state", unknown)
    monkeypatch.setattr(nas, "load_index", fail_load)
    monkeypatch.setattr(nas, "fetch_cast_file", fail_fetch)
    monkeypatch.setattr(nas, "ha_run_script", fail_cast)

    tool_factory = _tool(f"reachy_companion.tools.{module}", class_name)
    out = await tool_factory()(deps=_deps(tmp_path), **kwargs)
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert out["error"]


@pytest.mark.asyncio
async def test_nas_video_query_needs_no_smb_credentials(monkeypatch, tmp_path):
    """Finding 10: it reads a local JSON file and touches nothing else."""
    monkeypatch.delenv("HANOVA_NAS_HOST")
    monkeypatch.delenv("HANOVA_NAS_USER")
    monkeypatch.delenv("HANOVA_NAS_PASSWORD")
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out["ok"] is True and out["count"] == 2


@pytest.mark.asyncio
async def test_nas_video_query_returns_matching_clips(tmp_path):
    """Ground-truth records only: the model must never invent a clip."""
    out = await NasVideoQuery()(deps=_deps(tmp_path), place="sentinel_place_q4")
    assert out["ok"] is True and out["count"] == 2
    assert out["videos"][0]["path"].endswith("clip01.mp4")


@pytest.mark.asyncio
async def test_nas_video_query_with_no_filters_summarises_folders(tmp_path):
    """Answer a bare "what home videos do we have?" with an overview, not 2800 rows."""
    out = await NasVideoQuery()(deps=_deps(tmp_path))
    assert out["ok"] is True
    assert out["folders"][0]["top_folder"] == "SENTINEL_TRIP_q4"


@pytest.mark.asyncio
async def test_play_nas_video_stages_and_casts_a_lan_url(monkeypatch, tmp_path):
    """R6: the TV fetches the robot's own LAN URL, not a path on our disk."""
    recorded = _stub_transfer(monkeypatch)
    out = await PlayNasVideo()(deps=_deps(tmp_path), place="sentinel_place_q4", keyword="morning")
    assert out["ok"] is True and out["status"] == "casting"
    assert recorded["fetched"] == ["SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"]
    script, data = recorded["cast"][0]
    assert script == "tv_show_video_url"
    assert data["url"].startswith("http://robot.example.invalid:7860/hanova-media/nas/")


@pytest.mark.asyncio
async def test_play_nas_video_reuses_a_staged_file(monkeypatch, tmp_path):
    """A second play of the same clip must not re-copy it off the NAS."""
    recorded = _stub_transfer(monkeypatch)
    await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert len(recorded["fetched"]) == 1


@pytest.mark.asyncio
async def test_play_nas_video_reports_no_match(monkeypatch, tmp_path):
    """An unknown request must not silently cast something else."""
    _stub_transfer(monkeypatch)
    out = await PlayNasVideo()(deps=_deps(tmp_path), place="atlantis")
    assert out["ok"] is False and out["error"] == "no_match"


@pytest.mark.asyncio
async def test_play_nas_video_reports_an_smb_failure(monkeypatch, tmp_path):
    """A NAS that is off must produce a spoken answer, not a stack trace."""

    def boom(cast_path, destination):
        raise nas.NasError("the video could not be copied from the NAS")

    monkeypatch.setattr(nas, "fetch_cast_file", boom)
    out = await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert out["ok"] is False
    assert out["error"] == "the video could not be copied from the NAS"


@pytest.mark.asyncio
async def test_an_unlisted_nas_error_text_never_reaches_the_caller(monkeypatch, tmp_path):
    """Round 2, finding 6: `str(exc)` relied on an invariant nothing enforced."""

    def boom(cast_path, destination):
        raise nas.NasError("cannot open \\\\SENTINEL_PRIVATE_x7\\share\\clip.mp4")

    monkeypatch.setattr(nas, "fetch_cast_file", boom)
    out = await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    assert out["ok"] is False
    assert "SENTINEL_PRIVATE_x7" not in out["error"]
    assert out["error"] == "that home video could not be prepared"


@pytest.mark.asyncio
async def test_nas_play_folder_starts_at_the_first_clip(monkeypatch, tmp_path):
    """A whole trip plays in order, starting from the first clip."""
    recorded = _stub_transfer(monkeypatch)
    out = await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert out["ok"] is True and out["remaining"] == 1
    assert recorded["fetched"] == ["SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"]


@pytest.mark.asyncio
async def test_nas_skip_advances_to_the_next_clip(monkeypatch, tmp_path):
    """Move to "the next one" -- the whole of the auto-advance capability we port."""
    recorded = _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is True and out["status"] == "casting"
    assert recorded["fetched"][-1] == "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4"


@pytest.mark.asyncio
async def test_nas_skip_reports_the_end_of_a_trip(monkeypatch, tmp_path):
    """At the end there is nothing to skip to, and that must be said plainly."""
    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    await NasSkip()(deps=_deps(tmp_path))
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "last_clip"


@pytest.mark.asyncio
async def test_nas_skip_without_a_session_reports_nothing_playing(tmp_path):
    """Skipping when nothing is playing is a clean answer."""
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "nothing_playing"


# --- session scoping and cursor discipline (review finding 16) ------------
@pytest.mark.asyncio
async def test_a_failed_skip_does_not_consume_a_clip(monkeypatch, tmp_path):
    """Finding 16: the cursor moved before the cast, so a failure ate a clip."""
    recorded = _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")

    async def failing_cast(script_name, data, timeout_s=60.0):
        return {"ok": False, "error": "Home Assistant returned HTTP 500"}

    monkeypatch.setattr(nas, "ha_run_script", failing_cast)
    failed = await NasSkip()(deps=_deps(tmp_path))
    assert failed["ok"] is False

    async def working_cast(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "ha_run_script", working_cast)
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is True
    assert recorded["fetched"][-1] == "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip02.mp4"


@pytest.mark.asyncio
async def test_the_trip_session_does_not_survive_a_new_conversation(monkeypatch, tmp_path):
    """Finding 16: the session was a process global that outlived its context."""
    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert nas.remaining() == 1

    GATE.begin_session()  # a realtime reconnect

    assert nas.remaining() == 0
    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "nothing_playing"


# --- the cursor token (round 2, finding 11) -------------------------------
def test_peek_next_returns_a_token_identifying_this_advance(monkeypatch):
    """Round 2, finding 11: "the next clip" is not enough to commit against."""
    playlist = list(INDEX["videos"])
    nas.start_session(playlist, 0)
    video, token, error = nas.peek_next()
    assert error is None
    assert video is not None and token is not None
    assert token.expected_index == 0 and token.next_index == 1


def test_only_the_first_of_two_concurrent_skips_can_commit(monkeypatch):
    """Round 2, finding 11: two skips used to consume two clips for one request."""
    nas.start_session(list(INDEX["videos"]), 0)
    _video_a, token_a, _ = nas.peek_next()
    _video_b, token_b, _ = nas.peek_next()
    assert token_a is not None and token_b is not None
    assert token_a.next_index == token_b.next_index == 1

    assert nas.commit_next(token_a) is True
    # The second token observed index 0, which is no longer where the cursor is.
    assert nas.commit_next(token_b) is False
    assert nas.remaining() == 0


def test_a_token_from_a_superseded_playlist_cannot_commit(monkeypatch):
    """Round 2, finding 11: an in-flight cast must not advance a new trip."""
    nas.start_session(list(INDEX["videos"]), 0)
    _video, stale_token, _ = nas.peek_next()
    assert stale_token is not None

    nas.start_session(list(INDEX["videos"]), 0)  # a new trip started meanwhile

    assert nas.commit_next(stale_token) is False
    assert nas.remaining() == 1, "the new trip's cursor is untouched"


def test_a_token_taken_before_a_clear_cannot_commit(monkeypatch):
    """A superseding media action ends the trip; a late cast may not revive it."""
    nas.start_session(list(INDEX["videos"]), 0)
    _video, token, _ = nas.peek_next()
    assert token is not None
    nas.clear_session()
    assert nas.commit_next(token) is False


@pytest.mark.asyncio
async def test_a_session_replacement_during_an_in_flight_cast_is_refused(monkeypatch, tmp_path):
    """The end-to-end version: a new trip starts while a skip is staging."""
    import asyncio as _asyncio

    recorded = _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")

    released = _asyncio.Event()

    async def slow_cast(script_name, data, timeout_s=60.0):
        recorded["cast"].append((script_name, data))
        await released.wait()
        return {"ok": True, "result": []}

    monkeypatch.setattr(nas, "ha_run_script", slow_cast)
    skip_task = _asyncio.create_task(NasSkip()(deps=_deps(tmp_path)))
    await _asyncio.sleep(0)

    # A new trip begins while the skip's cast is still in flight.
    nas.start_session(list(INDEX["videos"]), 0)
    released.set()
    out = await skip_task

    assert out["ok"] is True, "the clip did reach the TV; that part is honest"
    assert nas.remaining() == 1, "the new trip's cursor was not advanced by the old cast"


def _three_clip_playlist():
    """Build a trip long enough that one advance and two advances look different.

    Review finding 2: with the two-clip `INDEX` starting at index 0,
    `remaining()` is 0 whether the cursor ends on 1 (the CAS working) or on 2
    (the double-advance bug) -- `max(0, 2 - 1 - 1)` and `max(0, 2 - 2 - 1)` are
    both 0 -- so the assertion was identical under bug and fix. A third clip
    separates them: 1 versus 0.
    """
    third = dict(INDEX["videos"][1])
    third.update(
        {
            "path": "SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip03.mp4",
            "cast_path": "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip03.mp4",
            "label": "night",
            "name": "clip03",
            "seq": 3,
        }
    )
    return [dict(INDEX["videos"][0]), dict(INDEX["videos"][1]), third]


@pytest.mark.asyncio
async def test_two_concurrent_skips_advance_the_trip_by_exactly_one(monkeypatch, tmp_path):
    """Round 2, finding 11, stated as the user-visible loss: a skipped clip.

    Review finding 2: this used the two-clip playlist, where the old assertion
    (`remaining() == 0`) held just as well when both skips advanced. It now runs
    on three clips and checks the cursor itself, so consuming two clips for one
    user request is a failure rather than an indistinguishable pass.
    """
    import asyncio as _asyncio

    _stub_transfer(monkeypatch)
    nas.start_session(_three_clip_playlist(), 0)

    await _asyncio.gather(
        NasSkip()(deps=_deps(tmp_path)),
        NasSkip()(deps=_deps(tmp_path)),
    )
    assert nas._SESSION["index"] == 1, "two concurrent skips must not consume two clips"
    assert nas.remaining() == 1, "one clip was watched, so two of the three remain"


@pytest.mark.asyncio
async def test_music_supersedes_the_trip_session(monkeypatch, tmp_path):
    """Finding 16: nas_skip must not silently continue a trip nobody is watching."""
    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert nas.remaining() == 1

    nas.clear_session()  # what a superseding media action calls on success

    out = await NasSkip()(deps=_deps(tmp_path))
    assert out["ok"] is False and out["error"] == "nothing_playing"


# --- every superseding media action clears the trip (finding 16, Step 6b) --
#
# The semantics the brief states: the clear happens on the **successful** path of
# `play_music`, `play_video` and `show_on_tv` only -- something else is now on
# the TV, or the "we are watching the trip" context is over, so `nas_skip` must
# not silently continue it. Every early return is left alone on purpose: an
# `unavailable` prerequisite, an `away_from_home` / `home_status_unknown`
# verdict, an empty query, a failed search and a cast Home Assistant refused all
# leave whatever was playing exactly where it was, so the trip is still the
# truth. That is the same rule `play_nas_video` already applies to itself
# ("a failed play leaves whatever was on the TV alone, and therefore leaves the
# trip session alone too").


async def _at_home() -> str:
    return home_net.HOME


def _live_trip() -> None:
    """Put a two-clip trip on the TV so a superseding action has something to end."""
    nas.start_session(list(INDEX["videos"]), 0)
    assert nas.remaining() == 1


def _stub_play_music(monkeypatch, tmp_path, *, played_ok: bool):
    """Configure play_music down to a stubbed PLAYER.play with the given verdict."""
    module = importlib.import_module("reachy_companion.tools.play_music")
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    track = tmp_path / "track.mp3"
    track.write_bytes(b"ID3")
    monkeypatch.setattr(
        module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "abc", "title": "A Song", "error": None},
    )
    monkeypatch.setattr(
        module.ytdlp,
        "download_audio",
        lambda video_id, dest_dir: {"ok": True, "path": str(track), "cached": True, "error": None},
    )

    async def fake_play(deps, *, video_id, title, source_path):
        if not played_ok:
            # What a race with a newer request actually returns (music_player).
            return {"ok": False, "status": "superseded"}
        return {"ok": True, "status": "playing", "title": title, "video_id": video_id}

    monkeypatch.setattr(PLAYER, "play", fake_play)
    return module


def _stub_play_video(monkeypatch, *, cast_ok: bool):
    """Configure play_video down to a stubbed HA cast with the given verdict."""
    module = importlib.import_module("reachy_companion.tools.play_video")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_YOUTUBE", "tv_show_youtube")
    monkeypatch.setattr("reachy_companion.hanova.settings._music_wheels_ready", lambda: (True, ""))
    monkeypatch.setattr("reachy_companion.tools.play_video.home_state", _at_home)
    monkeypatch.setattr(
        module.ytdlp,
        "search",
        lambda query, max_duration_s=None: {"ok": True, "id": "vid123", "title": "A Film", "error": None},
    )

    async def fake_run_script(script_name, data, timeout_s=60.0):
        if not cast_ok:
            return {"ok": False, "error": "Home Assistant returned HTTP 500"}
        return {"ok": True, "result": []}

    monkeypatch.setattr(module, "ha_run_script", fake_run_script)
    return module


def _stub_show_on_tv(monkeypatch, *, cast_ok: bool):
    """Configure show_on_tv down to a stubbed HA cast with the given verdict."""
    module = importlib.import_module("reachy_companion.tools.show_on_tv")
    monkeypatch.setenv("HANOVA_HA_SCRIPT_IMAGE_URL", "tv_show_image_url")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("reachy_companion.tools.show_on_tv.home_state", _at_home)

    async def fake_generate(request, images_dir):
        return {"ok": True, "filename": "drawing.png", "error": None}

    async def fake_run_script(script_name, data, timeout_s=60.0):
        if not cast_ok:
            return {"ok": False, "error": "Home Assistant returned HTTP 500"}
        return {"ok": True, "result": []}

    monkeypatch.setattr(module.images, "generate_image", fake_generate)
    monkeypatch.setattr(module, "ha_run_script", fake_run_script)
    return module


@pytest.mark.asyncio
async def test_play_music_clears_the_trip_session(monkeypatch, tmp_path):
    """Music on the speaker ends the "we are watching the trip" context."""
    module = _stub_play_music(monkeypatch, tmp_path, played_ok=True)
    _live_trip()

    out = await module.PlayMusic()(deps=_deps(tmp_path), query="a song")
    assert out["ok"] is True
    assert nas.remaining() == 0, "music supersedes the trip"

    skipped = await NasSkip()(deps=_deps(tmp_path))
    assert skipped["ok"] is False and skipped["error"] == "nothing_playing"


@pytest.mark.asyncio
async def test_a_play_music_that_never_started_leaves_the_trip_alone(monkeypatch, tmp_path):
    """Nothing reached the speaker, so nothing superseded the trip."""
    module = _stub_play_music(monkeypatch, tmp_path, played_ok=False)
    _live_trip()

    out = await module.PlayMusic()(deps=_deps(tmp_path), query="a song")
    assert out["ok"] is False
    assert nas.remaining() == 1, "a failed play must not consume the trip"


@pytest.mark.asyncio
async def test_play_video_clears_the_trip_session(monkeypatch, tmp_path):
    """Something else is on the TV now, so nas_skip must not continue the trip."""
    module = _stub_play_video(monkeypatch, cast_ok=True)
    _live_trip()

    out = await module.PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is True
    assert nas.remaining() == 0, "a cast video supersedes the trip"

    skipped = await NasSkip()(deps=_deps(tmp_path))
    assert skipped["ok"] is False and skipped["error"] == "nothing_playing"


@pytest.mark.asyncio
async def test_a_refused_play_video_leaves_the_trip_alone(monkeypatch, tmp_path):
    """The TV never accepted it, so the trip is still what is on screen."""
    module = _stub_play_video(monkeypatch, cast_ok=False)
    _live_trip()

    out = await module.PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out["ok"] is False
    assert nas.remaining() == 1, "a refused cast must not consume the trip"


@pytest.mark.asyncio
async def test_show_on_tv_clears_the_trip_session(monkeypatch, tmp_path):
    """A picture on the TV supersedes the trip exactly as a video does."""
    module = _stub_show_on_tv(monkeypatch, cast_ok=True)
    _live_trip()

    out = await module.ShowOnTv()(deps=_deps(tmp_path), request="a red bicycle")
    assert out["ok"] is True
    assert nas.remaining() == 0, "a cast picture supersedes the trip"

    skipped = await NasSkip()(deps=_deps(tmp_path))
    assert skipped["ok"] is False and skipped["error"] == "nothing_playing"


@pytest.mark.asyncio
async def test_a_refused_show_on_tv_leaves_the_trip_alone(monkeypatch, tmp_path):
    """The picture never reached the TV, so the trip is untouched."""
    module = _stub_show_on_tv(monkeypatch, cast_ok=False)
    _live_trip()

    out = await module.ShowOnTv()(deps=_deps(tmp_path), request="a red bicycle")
    assert out["ok"] is False
    assert nas.remaining() == 1, "a refused cast must not consume the trip"


@pytest.mark.asyncio
async def test_a_house_bound_early_return_leaves_the_trip_alone(monkeypatch, tmp_path):
    """R4 verdicts do no work at all -- including no clearing of the trip."""
    module = _stub_play_video(monkeypatch, cast_ok=True)

    async def not_home() -> str:
        return home_net.AWAY

    monkeypatch.setattr("reachy_companion.tools.play_video.home_state", not_home)
    _live_trip()

    out = await module.PlayVideo()(deps=_deps(tmp_path), query="a documentary")
    assert out == {"status": "away_from_home"}
    assert nas.remaining() == 1, "an away verdict changed nothing on the TV"


@pytest.mark.asyncio
async def test_the_shutdown_hook_clears_the_trip_session(monkeypatch, tmp_path):
    """Finding 16: a closing conversation leaves no playlist behind."""
    import types as _types

    import httpx

    from reachy_companion.hanova import music_hooks

    class _Ok:
        status_code = 200

    async def ok_post(self, *args, **kwargs):
        return _Ok()

    monkeypatch.setattr(httpx.AsyncClient, "post", ok_post)
    hook_deps = _types.SimpleNamespace(
        reachy_mini=_types.SimpleNamespace(_daemon_http_url="http://127.0.0.1:8000"),
        instance_path=tmp_path,
    )
    # Round 3, finding 2: the cleanup hook only acts for the LIVE session, so the
    # token has to be minted before the trip exists -- `on_session_started` also
    # clears the trip session (Step 6b).
    token = await music_hooks.on_session_started(hook_deps)

    _stub_transfer(monkeypatch)
    await NasPlayFolder()(deps=_deps(tmp_path), top_folder="SENTINEL_TRIP_q4")
    assert nas.remaining() == 1

    await music_hooks.on_session_shutdown(hook_deps, token)
    assert nas.remaining() == 0


@pytest.mark.asyncio
async def test_two_real_concurrent_fetches_release_together_do_not_corrupt_each_other(monkeypatch, tmp_path):
    """Round 2, finding 10: the real fetch, two threads, one barrier.

    The previous version of this test stubbed out `fetch_cast_file` -- the exact
    routine whose concurrency it claimed to prove -- so it could not have failed
    however broken the staging was. This runs the real one twice against one
    destination, released simultaneously, with a slow SMB source so the copies
    genuinely overlap.
    """
    import asyncio as _asyncio
    import threading as _threading

    payload = b"MP4" + bytes(200_000)
    barrier = _threading.Barrier(2)
    opens = {"n": 0}

    class _SlowSmbFile:
        def __init__(self) -> None:
            self._chunks = [payload[i : i + 8192] for i in range(0, len(payload), 8192)]

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            if not self._chunks:
                return b""
            time.sleep(0.001)  # let the other writer interleave if it can
            return self._chunks.pop(0)

    class _SlowSmbClient:
        @staticmethod
        def register_session(host, username=None, password=None, connection_timeout=None):
            return None

        @staticmethod
        def open_file(path, mode="rb"):
            opens["n"] += 1
            try:
                # Both copies start together if the staging layer lets them. The
                # single-flight lock means the second caller is still waiting for
                # the lock and can never arrive, so a broken barrier is the
                # *expected* outcome, not a failure -- see the note below.
                barrier.wait(timeout=0.5)
            except _threading.BrokenBarrierError:
                pass
            return _SlowSmbFile()

    monkeypatch.setitem(__import__("sys").modules, "smbclient", _SlowSmbClient)
    destination = tmp_path / "clip.mp4"

    async def fetch():
        await _asyncio.to_thread(nas.fetch_cast_file, "SENTINEL_CAST_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4", destination)

    # The single-flight lock means the second caller may never open the file at
    # all; the barrier therefore has a timeout and a second waiter that arrives
    # late is fine. What must hold is the *result*.
    results = await _asyncio.gather(fetch(), fetch(), return_exceptions=True)
    for result in results:
        assert not isinstance(result, BaseException), result

    assert destination.read_bytes() == payload, "one writer truncated the other"
    assert list(tmp_path.glob(f"*{nas.PART_SUFFIX}")) == [], "no staging file survives"
    assert opens["n"] == 1, "single flight: the second caller must find it already staged"


@pytest.mark.asyncio
async def test_concurrent_plays_of_the_same_clip_both_succeed(monkeypatch, tmp_path):
    """The tool-level version: two "play that one" requests must both answer."""
    import asyncio as _asyncio

    _stub_transfer(monkeypatch)
    results = await _asyncio.gather(
        PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"),
        PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4"),
    )
    assert all(result["ok"] for result in results)
    nas_dir = tmp_path / "hanova_media" / "nas"
    assert list(nas_dir.glob(f"*{nas.PART_SUFFIX}")) == []


def test_pruning_never_deletes_a_staging_file(tmp_path):
    """Round 2, finding 10: the LRU must not race an in-progress copy."""
    from reachy_companion.hanova import media_store

    nas_dir = media_store.media_dir("nas", tmp_path)
    (nas_dir / "old.mp4").write_bytes(b"MP4")
    staging = nas_dir / f"new.mp4.abcd1234{nas.PART_SUFFIX}"
    staging.write_bytes(b"partial")

    media_store.prune("nas", tmp_path, keep=0)
    assert staging.exists(), "a .part file is an active writer, not LRU fodder"
    assert not (nas_dir / "old.mp4").exists()


@pytest.mark.asyncio
async def test_a_rejected_index_path_is_reported_not_cast(monkeypatch, tmp_path):
    """Finding 15: a bad index entry stops here, not at the SMB layer."""
    recorded = _stub_transfer(monkeypatch)
    bad = dict(INDEX["videos"][0])
    bad["cast_path"] = "../../etc/passwd"
    out = await nas.stage_and_cast(bad, tmp_path)
    assert out["ok"] is False
    assert recorded["fetched"] == [] and recorded["cast"] == []


@pytest.mark.asyncio
async def test_nas_logs_never_carry_a_clip_path(monkeypatch, caplog, tmp_path):
    """Finding 7: home-video folder names are the most personal data in the port."""
    import logging

    _stub_transfer(monkeypatch)
    caplog.set_level(logging.DEBUG)
    await PlayNasVideo()(deps=_deps(tmp_path), path="SENTINEL_SRC_DIR_q4/SENTINEL_TRIP_q4/clip01.mp4")
    for token in ("SENTINEL_TRIP_q4", "SENTINEL_PLACE_q4", "clip01"):
        assert token not in caplog.text, token


def test_all_four_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"nas_video_query", "play_nas_video", "nas_play_folder", "nas_skip"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
