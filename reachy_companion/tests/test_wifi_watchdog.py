"""Tests for the cron-run Wi-Fi watchdog (wifi-recovery plan W2, D-034).

The module is loaded from its file path, not through the package, because on
the robot it runs as a plain file under the system python. Every command,
probe, clock and log sink is a fake: nothing here touches a real network,
NetworkManager or sudo.

Addresses come from TEST-NET-1 (RFC 5737, documentation only), so no private
LAN address shape enters the repository.
"""

import os
import ast
import sys
import json
import fcntl
import syslog
import subprocess
import dataclasses
import importlib.util
from typing import Any
from pathlib import Path
from dataclasses import field, dataclass
from collections.abc import Sequence

import pytest


MODULE_PATH = Path(__file__).parents[1] / "src" / "reachy_companion" / "wifi_watchdog.py"


def _load_module_from_file() -> Any:
    """Load wifi_watchdog.py the way the robot runs it: as a file, outside the package."""
    spec = importlib.util.spec_from_file_location("wifi_watchdog_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


wd = _load_module_from_file()

GATEWAY = "192.0.2.1"  # TEST-NET-1
OTHER_GATEWAY = "192.0.2.254"  # TEST-NET-1
HOME_CONNECTION = "Sentinel-Home-SSID"  # must never reach a log line
STEP = 120.0  # the cron interval
CONNECT = ["device", "connect", "wlan0"]
RADIO_OFF = ["radio", "wifi", "off"]
RADIO_ON = ["radio", "wifi", "on"]
CYCLE = [RADIO_OFF, RADIO_ON]


@dataclass
class FakeNet:
    """What the fake robot's network looks like right now; tests flip fields between runs."""

    default_route: bool = True
    gateway_answers: bool = True
    dns_ok: bool = True
    nm_state: str | None = "connected"
    active: list[tuple[str, str]] = field(default_factory=lambda: [(HOME_CONNECTION, "802-11-wireless")])
    modes: dict[str, str] = field(default_factory=lambda: {HOME_CONNECTION: "infrastructure"})
    sudo_results: dict[str, Any] = field(default_factory=dict)  # "connect" / "off" / "on" -> CommandResult


class FakeSystem:
    """Fake command runner, resolver, clock, sleep and log sink, with a record of every call."""

    def __init__(self, net: FakeNet | None = None) -> None:
        """Start at a fixed wall-clock time with an empty call record."""
        self.net = net or FakeNet()
        self.clock = 1_800_000_000.0
        self.calls: list[list[str]] = []
        self.sudo: list[list[str]] = []
        self.sleeps: list[float] = []
        self.logs: list[tuple[int, str]] = []
        self.out: list[str] = []
        self.resolves = 0
        self.on_sudo: Any = None  # optional hook called with the nmcli args before a sudo result

    def run(self, argv: Sequence[str], timeout: float) -> Any:
        """Answer one command the watchdog runs."""
        argv = list(argv)
        assert timeout > 0
        self.calls.append(argv)
        ok = wd.CommandResult(0)
        net = self.net
        if argv == ["ip", "route", "show", "default"]:
            route = f"default via {GATEWAY} dev wlan0 proto dhcp src 192.0.2.77 metric 600\n"
            return wd.CommandResult(0, route if net.default_route else "")
        if argv[0] == "ping":
            return ok if net.gateway_answers else wd.CommandResult(1)
        if argv == ["nmcli", "-t", "-f", "STATE", "general"]:
            return wd.CommandResult(0, f"{net.nm_state}\n") if net.nm_state else wd.CommandResult(8)
        if argv == ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"]:
            if net.nm_state is None:
                return wd.CommandResult(8)
            lines = "".join(f"{name.replace(':', chr(92) + ':')}:{kind}:wlan0\n" for name, kind in net.active)
            return wd.CommandResult(0, lines)
        if argv[:5] == ["nmcli", "-g", "802-11-wireless.mode", "connection", "show"]:
            return wd.CommandResult(0, net.modes.get(argv[-1], "") + "\n")
        if argv[:3] == ["sudo", "-n", "/usr/bin/nmcli"]:
            args = argv[3:]
            self.sudo.append(args)
            if self.on_sudo is not None:
                self.on_sudo(args)
            key = {tuple(CONNECT): "connect", tuple(RADIO_OFF): "off", tuple(RADIO_ON): "on"}[tuple(args)]
            result = net.sudo_results.get(key, ok)
            if isinstance(result, list):
                return result.pop(0) if result else ok
            return result
        raise AssertionError(f"unexpected command: {argv}")

    def resolve(self, host: str, port: int, timeout: float) -> bool:
        """Answer the DNS probe."""
        assert (host, port) == ("api.openai.com", 443) and timeout > 0
        self.resolves += 1
        return self.net.dns_ok

    def system(self) -> Any:
        """Bundle the fakes into the watchdog's System adapter."""
        return wd.System(
            run=self.run,
            resolve=self.resolve,
            sleep=self.sleeps.append,
            now=lambda: self.clock,
            log=lambda priority, message: self.logs.append((priority, message)),
            out=self.out.append,
        )

    def messages(self) -> list[str]:
        """Return every log message so far."""
        return [message for _priority, message in self.logs]


def _run(fake: FakeSystem, state_dir: Path, runs: int = 1, step: float = STEP, **kwargs: Any) -> list[list[list[str]]]:
    """Run the watchdog ``runs`` times, ``step`` seconds apart; return the sudo nmcli calls of each run."""
    per_run: list[list[list[str]]] = []
    for _ in range(runs):
        before = len(fake.sudo)
        assert wd.run_once(fake.system(), state_dir, **kwargs) == 0  # cron must never see a failure
        per_run.append(fake.sudo[before:])
        fake.clock += step
    return per_run


def _actions_by_run(per_run: list[list[list[str]]]) -> dict[int, list[list[str]]]:
    """Map 1-based run numbers to the sudo calls made in that run, for runs that made any."""
    return {number: calls for number, calls in enumerate(per_run, start=1) if calls}


def _state(state_dir: Path, name: str = "state.json") -> dict[str, Any]:
    """Read a saved state file."""
    return json.loads((state_dir / name).read_text(encoding="utf-8"))


# --- escalation -----------------------------------------------------------------


def test_each_rung_fires_at_its_failure_count(tmp_path: Path) -> None:
    """2 failures -> device connect, 4 -> radio cycle, then a radio cycle every 6 further failures."""
    fake = FakeSystem(FakeNet(gateway_answers=False))

    per_run = _run(fake, tmp_path, runs=17, step=1000.0)  # wide spacing keeps the hourly cap out of it

    assert _actions_by_run(per_run) == {2: [CONNECT], 4: CYCLE, 10: CYCLE, 16: CYCLE}
    assert fake.sleeps == [5.0, 5.0, 5.0]  # the radio stays off 5 s each cycle
    state = _state(tmp_path)
    assert state["consecutive_failures"] == 17
    assert state["radio_cycles"] == 3
    assert state["radio_off_pending"] is False


def test_no_more_than_four_actions_in_any_rolling_hour(tmp_path: Path) -> None:
    """The 22nd-failure cycle waits until the oldest action is an hour old, then the rhythm resumes."""
    fake = FakeSystem(FakeNet(gateway_answers=False))

    per_run = _run(fake, tmp_path, runs=40)

    assert _actions_by_run(per_run) == {2: [CONNECT], 4: CYCLE, 10: CYCLE, 16: CYCLE, 32: CYCLE, 38: CYCLE}
    action_times = [number * STEP for number in _actions_by_run(per_run)]
    for start in action_times:
        assert sum(1 for t in action_times if start <= t < start + 3600) <= 4
    cap_lines = [message for message in fake.messages() if "hourly cap" in message]
    assert len(cap_lines) == 1  # logged when the cap first holds an action back, not every run


def test_a_hotspot_connection_is_never_touched(tmp_path: Path) -> None:
    """While the robot serves Pollen's `Hotspot` profile there is no route, and the watchdog only stands by."""
    fake = FakeSystem(FakeNet(default_route=False, gateway_answers=False, active=[("Hotspot", "802-11-wireless")]))

    per_run = _run(fake, tmp_path, runs=10)

    assert _actions_by_run(per_run) == {}
    assert [message for message in fake.messages() if "hotspot" in message.lower()] == [fake.messages()[0]]
    assert len(fake.logs) == 1
    assert _state(tmp_path)["status"] == "hotspot"
    assert _state(tmp_path)["consecutive_failures"] == 0

    fake.net.active = [(HOME_CONNECTION, "802-11-wireless")]  # back in client mode, still no link
    per_run = _run(fake, tmp_path, runs=2)
    assert _actions_by_run(per_run) == {2: [CONNECT]}  # a fresh count, not ten saved-up failures


def test_an_access_point_mode_connection_counts_as_the_hotspot(tmp_path: Path) -> None:
    """A renamed hotspot is still recognised by its 802-11-wireless.mode."""
    fake = FakeSystem(
        FakeNet(
            gateway_answers=False,
            active=[("Robot-AP", "802-11-wireless")],
            modes={"Robot-AP": "ap"},
        )
    )

    per_run = _run(fake, tmp_path, runs=6)

    assert _actions_by_run(per_run) == {}
    assert _state(tmp_path)["status"] == "hotspot"


def test_nothing_is_done_while_networkmanager_is_connecting(tmp_path: Path) -> None:
    """A due action waits for NM to finish connecting, and runs as soon as it has stopped."""
    fake = FakeSystem(FakeNet(gateway_answers=False, nm_state="connecting"))

    per_run = _run(fake, tmp_path, runs=5)
    assert _actions_by_run(per_run) == {}
    deferred = [message for message in fake.messages() if "connecting" in message]
    assert len(deferred) == 1

    fake.net.nm_state = "disconnected"
    per_run = _run(fake, tmp_path, runs=2)
    assert _actions_by_run(per_run) == {1: [CONNECT], 2: CYCLE}  # both were due already


def test_unknown_networkmanager_state_defers_actions(tmp_path: Path) -> None:
    """If nmcli cannot report state, the guards cannot be checked, so nothing is done."""
    fake = FakeSystem(FakeNet(gateway_answers=False, nm_state=None))

    per_run = _run(fake, tmp_path, runs=6)

    assert _actions_by_run(per_run) == {}
    assert len([message for message in fake.messages() if "NetworkManager state unavailable" in message]) == 1


def test_gateway_up_but_dns_failing_never_touches_wifi(tmp_path: Path) -> None:
    """An upstream outage is logged once; the radio and NM are left alone."""
    fake = FakeSystem()
    _run(fake, tmp_path)  # healthy first run: "monitoring started"
    fake.net.dns_ok = False

    per_run = _run(fake, tmp_path, runs=10)

    assert _actions_by_run(per_run) == {}
    assert not any(call[0] == "nmcli" for call in fake.calls)
    assert len(fake.logs) == 2 and "DNS" in fake.logs[1][1]
    assert _state(tmp_path)["status"] == "upstream"
    assert _state(tmp_path)["consecutive_failures"] == 0

    fake.net.dns_ok = True
    _run(fake, tmp_path)
    assert len(fake.logs) == 3 and "DNS" in fake.logs[2][1]
    assert _state(tmp_path)["status"] == "up"


def test_no_default_route_counts_as_down(tmp_path: Path) -> None:
    """Without a default route there is nothing to ping: that is a failed probe."""
    fake = FakeSystem(FakeNet(default_route=False))

    per_run = _run(fake, tmp_path, runs=2)

    assert _actions_by_run(per_run) == {2: [CONNECT]}
    assert not any(call[0] == "ping" for call in fake.calls)
    assert "no default route" in fake.messages()[0]


def test_recovery_logs_the_downtime_and_resets_the_outage(tmp_path: Path) -> None:
    """Coming back logs one line with the downtime; the next outage starts from zero."""
    fake = FakeSystem(FakeNet(gateway_answers=False))
    _run(fake, tmp_path, runs=5)  # connect at 2, radio cycle at 4

    fake.net.gateway_answers = True
    _run(fake, tmp_path)

    recovered = fake.messages()[-1]
    assert "recovered after 10m 00s" in recovered
    assert "5 failed probes" in recovered and "2 recovery actions" in recovered
    state = _state(tmp_path)
    assert state["status"] == "up"
    assert state["consecutive_failures"] == 0
    assert state["down_since"] is None
    assert state["connect_done"] is False

    fake.net.gateway_answers = False
    per_run = _run(fake, tmp_path, runs=2)
    assert _state(tmp_path)["consecutive_failures"] == 2
    assert _actions_by_run(per_run) == {2: [CONNECT]}  # the ladder starts again from its first rung


def test_only_transitions_and_an_hourly_still_down_line_are_logged(tmp_path: Path) -> None:
    """Two hours offline: down, the actions, two still-down lines, the cap notice; nothing per run."""
    fake = FakeSystem(FakeNet(gateway_answers=False))

    _run(fake, tmp_path, runs=61)  # 0 .. 7200 s

    messages = fake.messages()
    assert "down detected" in messages[0]
    assert len([message for message in messages if "still down" in message]) == 2
    assert len(messages) < 20


# --- guards around acting -----------------------------------------------------------


def test_the_pending_flag_is_saved_before_the_radio_goes_off(tmp_path: Path) -> None:
    """If the run dies between off and on, the saved state says so."""
    fake = FakeSystem(FakeNet(gateway_answers=False))
    seen: list[bool] = []

    def check_state(args: list[str]) -> None:
        if args == RADIO_OFF:
            seen.append(_state(tmp_path)["radio_off_pending"])

    fake.on_sudo = check_state
    _run(fake, tmp_path, runs=4)

    assert seen == [True]
    assert _state(tmp_path)["radio_off_pending"] is False


def test_a_radio_left_off_is_turned_back_on_first(tmp_path: Path) -> None:
    """When radio-on failed, the next run turns the radio on before anything else."""
    failed = wd.CommandResult(4)
    fake = FakeSystem(FakeNet(gateway_answers=False, sudo_results={"on": [failed, failed, failed]}))

    per_run = _run(fake, tmp_path, runs=4)
    assert per_run[3] == [RADIO_OFF, RADIO_ON, RADIO_ON, RADIO_ON]  # three tries to turn it back on
    assert _state(tmp_path)["radio_off_pending"] is True
    assert syslog.LOG_ERR in [priority for priority, _message in fake.logs]

    per_run = _run(fake, tmp_path)
    assert per_run[0] == [RADIO_ON]
    assert _state(tmp_path)["radio_off_pending"] is False


def test_a_sudo_password_prompt_is_reported_without_raw_stderr(tmp_path: Path) -> None:
    """A refused sudo is named as such; nmcli's own stderr (which can quote an SSID) is never logged."""
    refused = wd.CommandResult(1, "", f"sudo: a password is required for {HOME_CONNECTION}\n")
    fake = FakeSystem(FakeNet(gateway_answers=False, sudo_results={"connect": refused}))

    _run(fake, tmp_path, runs=2)

    assert "sudo refused" in fake.messages()[-1]
    assert HOME_CONNECTION not in " ".join(fake.messages())


def test_logs_and_status_never_carry_an_address_or_a_connection_name(tmp_path: Path) -> None:
    """A full outage, recovery and --status: no gateway IP, no robot IP, no SSID-named profile."""
    fake = FakeSystem(FakeNet(gateway_answers=False))
    _run(fake, tmp_path, runs=12)
    fake.net.dns_ok = False
    fake.net.gateway_answers = True
    _run(fake, tmp_path, runs=2)
    assert wd.main(["--status"], system=fake.system(), state_dir=tmp_path) == 0

    text = " ".join(fake.messages() + fake.out) + (tmp_path / "state.json").read_text(encoding="utf-8")
    for secret in (GATEWAY, "192.0.2.77", HOME_CONNECTION):
        assert secret not in text


# --- state, lock, dry run, CLI ----------------------------------------------------------


def test_a_held_lock_makes_the_run_exit_quietly(tmp_path: Path) -> None:
    """An overlapping run does nothing at all and still exits 0."""
    fake = FakeSystem(FakeNet(gateway_answers=False))
    tmp_path.mkdir(exist_ok=True)
    with open(tmp_path / "lock", "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert wd.run_once(fake.system(), tmp_path) == 0

    assert fake.calls == [] and fake.logs == [] and fake.out == []
    assert not (tmp_path / "state.json").exists()


def test_dry_run_decides_and_logs_but_executes_nothing(tmp_path: Path) -> None:
    """--dry-run walks the rungs in its own state file, prefixes every line, and never calls sudo."""
    fake = FakeSystem(FakeNet(gateway_answers=False))

    per_run = _run(fake, tmp_path, runs=4, dry_run=True)

    assert _actions_by_run(per_run) == {}
    assert fake.sudo == []
    assert all(message.startswith("dry-run: ") for message in fake.messages())
    assert any("would reconnect wlan0" in message for message in fake.messages())
    assert any("would cycle the Wi-Fi radio" in message for message in fake.messages())
    assert all(line.startswith("dry-run: ") for line in fake.out) and len(fake.out) == 4
    assert not (tmp_path / "state.json").exists()  # the cron's real state is untouched
    assert _state(tmp_path, "dry-run-state.json")["consecutive_failures"] == 4


def test_dry_run_reports_a_healthy_link(tmp_path: Path) -> None:
    """The first dry run on a healthy robot says so, once in the log and once on stdout."""
    fake = FakeSystem()

    _run(fake, tmp_path, dry_run=True)

    assert fake.messages() == ["dry-run: monitoring started: Wi-Fi link up"]
    assert "status up" in fake.out[0]


_VALID_STATE = {"version": 1, **dataclasses.asdict(wd.State())}


def test_the_valid_state_fixture_round_trips() -> None:
    """The corrupt cases below differ from a loadable state in exactly one field."""
    assert wd.state_from_json(json.dumps(_VALID_STATE)) == wd.State()


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"version": 1, "status": 5}),
        json.dumps({"version": 99}),
        json.dumps({**_VALID_STATE, "status": "down", "down_since": float("nan")}),  # complete but not finite
        json.dumps({**_VALID_STATE, "consecutive_failures": -3}),
        json.dumps({**_VALID_STATE, "connect_done": 1}),
        "\udcff",
    ],
)
def test_a_corrupt_state_file_is_reset_and_reported_once(tmp_path: Path, content: str) -> None:
    """A broken state file costs one warning and a fresh start, not a crash or a warning per run."""
    (tmp_path / "state.json").write_text(content, encoding="utf-8", errors="surrogateescape")
    fake = FakeSystem()

    _run(fake, tmp_path, runs=3)

    resets = [message for message in fake.messages() if "fresh state" in message]
    assert len(resets) == 1
    assert _state(tmp_path)["status"] == "up"


def test_status_prints_the_saved_state_and_probes_nothing(tmp_path: Path) -> None:
    """--status reads the state file only."""
    fake = FakeSystem(FakeNet(gateway_answers=False))
    _run(fake, tmp_path, runs=3)
    calls_before = list(fake.calls)

    assert wd.main(["--status"], system=fake.system(), state_dir=tmp_path) == 0

    assert fake.calls == calls_before
    report = "\n".join(fake.out)
    assert "status: down" in report
    assert "failed probes in a row: 3" in report
    assert "last probe:" in report and "gateway no answer" in report
    assert "NetworkManager connected, hotspot off" in report


def test_status_names_an_unreadable_networkmanager(tmp_path: Path) -> None:
    """When nmcli could not answer, --status and the dry-run summary say what is holding the action back."""
    fake = FakeSystem(FakeNet(gateway_answers=False, nm_state=None))
    _run(fake, tmp_path, runs=2, dry_run=True)

    assert wd.main(["--status", "--dry-run"], system=fake.system(), state_dir=tmp_path) == 0

    assert fake.out[1].endswith("held back (NetworkManager state unavailable)")
    report = "\n".join(fake.out[2:])
    assert "held back: NetworkManager state unavailable" in report
    assert "NetworkManager unavailable, hotspot unknown" in report


def test_status_without_a_state_file_says_so(tmp_path: Path) -> None:
    """A watchdog that never ran has no state, and --status says that instead of failing."""
    fake = FakeSystem()

    assert wd.main(["--status"], system=fake.system(), state_dir=tmp_path) == 0

    assert "no state yet" in fake.out[0]


def test_gateway_override_is_the_ping_target(tmp_path: Path) -> None:
    """--gateway replaces the routed gateway as the probe target."""
    fake = FakeSystem()

    assert wd.main(["--gateway", OTHER_GATEWAY], system=fake.system(), state_dir=tmp_path) == 0

    pings = [call for call in fake.calls if call[0] == "ping"]
    assert pings == [["ping", "-n", "-c", "2", "-W", "2", OTHER_GATEWAY]]


def test_gateway_override_must_be_an_address() -> None:
    """A malformed override is a usage error, caught before any probe."""
    with pytest.raises(SystemExit) as raised:
        wd.main(["--gateway", "not-an-address"], system=FakeSystem().system(), state_dir=Path("unused"))
    assert raised.value.code == 2


def test_default_gateway_prefers_the_wifi_route() -> None:
    """With several default routes the one on wlan0 is the one probed."""
    output = (
        f"default via {OTHER_GATEWAY} dev eth0 proto dhcp metric 100\n"
        f"default via {GATEWAY} dev wlan0 proto dhcp metric 600\n"
    )
    assert wd.default_gateway(output) == (True, GATEWAY)
    assert wd.default_gateway(f"default via {OTHER_GATEWAY} dev eth0\n") == (True, OTHER_GATEWAY)
    assert wd.default_gateway("default dev wlan0 scope link\n") == (True, None)
    assert wd.default_gateway("") == (False, None)


def test_terse_nmcli_fields_unescape_colons() -> None:
    r"""Terse nmcli output escapes ':' and '\' inside a field."""
    assert wd.split_terse("Home\\:Net:802-11-wireless:wlan0") == ["Home:Net", "802-11-wireless", "wlan0"]
    assert wd.split_terse("back\\\\slash::") == ["back\\slash", "", ""]


def test_the_module_imports_only_the_standard_library() -> None:
    """It must run under /usr/bin/python3 with no venv and without the package __init__."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports would need the package"
            imported.add((node.module or "").split(".")[0])
    assert imported - set(sys.stdlib_module_names) - {"__future__"} == set()


def test_the_file_runs_standalone_under_an_isolated_interpreter(tmp_path: Path) -> None:
    """`python -I wifi_watchdog.py --status` works with nothing but the file (HOME points at a scratch dir)."""
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
    done = subprocess.run(
        [sys.executable, "-I", str(MODULE_PATH), "--status"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "no state yet" in done.stdout

    done = subprocess.run(
        [sys.executable, "-I", str(MODULE_PATH), "--help"], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0 and "--dry-run" in done.stdout


def test_decide_is_pure() -> None:
    """The decision never mutates the state it is given, and gives the same answer twice."""
    state = wd.State(status="down", consecutive_failures=3, down_since=100.0, last_still_down_log=100.0, connect_done=True)
    snapshot = dataclasses.replace(state, action_times=list(state.action_times))
    probe = wd.Probe(True, False, nm_state="connected", hotspot_active=False)

    first = wd.decide(state, probe, 460.0)
    second = wd.decide(state, probe, 460.0)

    assert state == snapshot
    assert first == second
    assert [action.kind for action in first.actions] == [wd.RADIO_CYCLE]
