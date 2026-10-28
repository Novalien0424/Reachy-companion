#!/usr/bin/env python3
"""Wi-Fi watchdog for Reachy Mini Wireless (wifi-recovery plan W2, D-034).

The robot once stayed booted for weeks but fell off the LAN for days, and only
a power cycle brought it back: the daemon checks Wi-Fi at startup and nothing
watches it afterwards. This file is that watcher. It runs from the ``pollen``
user's crontab every two minutes, also while the robot sleeps and the app is
stopped. One run probes, decides, maybe acts, saves its state and exits.

- Healthy means a default route exists and its gateway answers ``ping``. A
  reachable gateway with failing DNS is an upstream problem: logged once,
  Wi-Fi left alone.
- After 2 failed probes in a row it asks NetworkManager to reconnect
  ``wlan0``; after 4 it switches the Wi-Fi radio off for 5 s and on again, and
  repeats that every 6 further failures; at most 4 actions in any rolling hour.
- It never acts while the robot serves its own hotspot or NetworkManager is
  connecting. It never deletes profiles, starts the hotspot, restarts the
  daemon or reboots; its only privilege is the image's NOPASSWD ``nmcli``.
- It logs to syslog as ``reachy-wifi-watchdog``, on transitions only, plus one
  "still down" line an hour. No address or connection name is ever logged.

Standard library only, and no package imports: ``/usr/bin/python3
wifi_watchdog.py`` keeps working while the app's venv is broken or being
reinstalled. ``--dry-run`` decides and logs without acting (in its own state
file); ``--status`` prints the saved state; ``--gateway`` overrides the probe
target. Exit status is 0 whenever the run itself worked, network down or not.
"""

from __future__ import annotations
import os
import sys
import json
import math
import time
import fcntl
import socket
import syslog
import argparse
import tempfile
import ipaddress
import threading
import contextlib
import subprocess
from typing import Any
from pathlib import Path
from dataclasses import field, asdict, replace, dataclass
from collections.abc import Callable, Iterator, Sequence


IDENT = "reachy-wifi-watchdog"
WIFI_DEVICE = "wlan0"
HOTSPOT_CONNECTION_NAME = "Hotspot"  # Pollen's hotspot profile (daemon/app/routers/wifi_config.py)
WIFI_CONNECTION_TYPES = frozenset({"802-11-wireless", "wifi"})
DNS_PROBE_HOST = "api.openai.com"
DNS_PROBE_PORT = 443

CONNECT_AFTER_FAILURES = 2
RADIO_CYCLE_AFTER_FAILURES = 4
RADIO_CYCLE_REPEAT_FAILURES = 6
MAX_ACTIONS_PER_WINDOW = 4
ACTION_WINDOW_SECONDS = 3600.0
STILL_DOWN_LOG_INTERVAL_SECONDS = 3600.0
RADIO_OFF_SECONDS = 5.0
RADIO_ON_ATTEMPTS = 3
RADIO_ON_RETRY_SECONDS = 2.0

PROBE_TIMEOUT_SECONDS = 10.0
DNS_TIMEOUT_SECONDS = 5.0
NMCLI_QUERY_TIMEOUT_SECONDS = 10.0
RADIO_TIMEOUT_SECONDS = 30.0
# `nmcli device connect` waits up to its default 90 s for activation. The argv
# stays exactly as the plan names it, so the NOPASSWD rule matches as checked.
CONNECT_TIMEOUT_SECONDS = 100.0

NMCLI = "/usr/bin/nmcli"  # absolute: the sudoers rule names this path
# cron gives a bare PATH; LC_ALL=C keeps nmcli's STATE words untranslated.
COMMAND_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}

STATE_VERSION = 1
STATUS_UNKNOWN = "unknown"
STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_UPSTREAM = "upstream"  # gateway answers, DNS does not
STATUS_HOTSPOT = "hotspot"
STATUSES = frozenset({STATUS_UNKNOWN, STATUS_UP, STATUS_DOWN, STATUS_UPSTREAM, STATUS_HOTSPOT})

DEVICE_CONNECT = "device_connect"
RADIO_CYCLE = "radio_cycle"
RADIO_ON = "radio_on"

DEFER_NM_UNKNOWN = "NetworkManager state unavailable"
DEFER_NM_CONNECTING = "NetworkManager is connecting"
DEFER_CAP = f"hourly cap of {MAX_ACTIONS_PER_WINDOW} actions reached"

LogLine = tuple[int, str]


# --- values ---------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one command: ``returncode`` is None when it timed out or could not start."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """Report whether the command ran and exited 0."""
        return self.returncode == 0


Runner = Callable[[Sequence[str], float], CommandResult]


@dataclass(frozen=True)
class Probe:
    """What one run observed. NetworkManager is read only when the link is down."""

    has_default_route: bool
    gateway_ok: bool
    dns_ok: bool | None = None  # None: not checked (the link was down)
    nm_state: str | None = None  # None: not read, or nmcli failed
    hotspot_active: bool | None = None  # None: not read, or nmcli failed

    @property
    def link_up(self) -> bool:
        """Report whether the Wi-Fi link counts as working."""
        return self.has_default_route and self.gateway_ok


@dataclass
class ProbeRecord:
    """The last probe as saved in the state file, for ``--status``. Holds no address."""

    at: float
    default_route: bool
    gateway_ok: bool
    dns_ok: bool | None
    nm_state: str | None
    hotspot: bool | None


@dataclass
class State:
    """Everything one run hands to the next."""

    status: str = STATUS_UNKNOWN
    consecutive_failures: int = 0
    down_since: float | None = None
    last_still_down_log: float | None = None
    connect_done: bool = False
    radio_cycles: int = 0
    last_radio_cycle_failure: int | None = None
    outage_actions: int = 0
    deferred_reason: str | None = None
    radio_off_pending: bool = False
    action_times: list[float] = field(default_factory=list)
    last_probe: ProbeRecord | None = None


@dataclass(frozen=True)
class Action:
    """One recovery step the decision asks for."""

    kind: str
    failures: int
    cycle: int = 0

    def describe(self) -> str:
        """Return the step in words, as it appears in the log."""
        if self.kind == DEVICE_CONNECT:
            return f"reconnect {WIFI_DEVICE} (nmcli device connect) after {self.failures} failed probes"
        if self.kind == RADIO_CYCLE:
            return f"cycle the Wi-Fi radio (#{self.cycle}) after {self.failures} failed probes"
        return "turn the Wi-Fi radio back on after an interrupted cycle"


@dataclass(frozen=True)
class Decision:
    """The outcome of :func:`decide`: the next state, the steps to take, the lines to log."""

    state: State
    actions: tuple[Action, ...]
    logs: tuple[LogLine, ...]


# --- the decision (pure) -----------------------------------------------------------------


def decide(state: State, probe: Probe, now: float) -> Decision:
    """Turn the saved state and this run's probe into the next state, actions and log lines.

    Pure: no I/O, no clock. ``now`` is wall-clock seconds; the rolling-hour cap
    ignores action times that lie in the future, so a clock that jumps back
    after an offline boot cannot hold actions back for good.
    """
    previous = state.status
    s = replace(
        state,
        action_times=[t for t in state.action_times if 0.0 <= now - t < ACTION_WINDOW_SECONDS],
        last_probe=ProbeRecord(
            at=now,
            default_route=probe.has_default_route,
            gateway_ok=probe.gateway_ok,
            dns_ok=probe.dns_ok,
            nm_state=probe.nm_state,
            hotspot=probe.hotspot_active,
        ),
    )
    logs: list[LogLine] = []
    # A radio left off by an interrupted cycle comes back first, and alone.
    actions: list[Action] = [Action(RADIO_ON, state.consecutive_failures)] if state.radio_off_pending else []

    if probe.link_up:
        if previous == STATUS_DOWN:
            down_for = now - (s.down_since if s.down_since is not None else now)
            logs.append(
                (
                    syslog.LOG_NOTICE,
                    f"Wi-Fi recovered after {format_duration(down_for)} down "
                    f"({s.consecutive_failures} failed probes, {s.outage_actions} recovery actions)",
                )
            )
        elif previous == STATUS_UNKNOWN:
            logs.append((syslog.LOG_INFO, "monitoring started: Wi-Fi link up"))
        elif previous == STATUS_HOTSPOT:
            logs.append((syslog.LOG_INFO, "hotspot no longer active: Wi-Fi link up"))
        s = _clear_outage(s)
        if probe.dns_ok is False:
            if previous != STATUS_UPSTREAM:
                logs.append(
                    (syslog.LOG_WARNING, "gateway answers but DNS lookups fail: upstream problem, Wi-Fi left alone")
                )
            s.status = STATUS_UPSTREAM
        else:
            if previous == STATUS_UPSTREAM:
                logs.append((syslog.LOG_INFO, "DNS lookups work again"))
            s.status = STATUS_UP
        return Decision(s, tuple(actions), tuple(logs))

    if probe.hotspot_active:
        if previous != STATUS_HOTSPOT:
            logs.append((syslog.LOG_INFO, "robot is serving its hotspot: watchdog standing by"))
        s = _clear_outage(s)
        s.status = STATUS_HOTSPOT
        return Decision(s, tuple(actions), tuple(logs))

    s.consecutive_failures += 1
    if previous != STATUS_DOWN or s.down_since is None:
        cause = "gateway not answering ping" if probe.has_default_route else "no default route"
        logs.append((syslog.LOG_WARNING, f"Wi-Fi down detected: {cause}"))
        s.status = STATUS_DOWN
        s.down_since = now
        s.last_still_down_log = now
    elif now - (s.last_still_down_log or s.down_since) >= STILL_DOWN_LOG_INTERVAL_SECONDS:
        logs.append(
            (
                syslog.LOG_WARNING,
                f"Wi-Fi still down after {format_duration(now - s.down_since)} "
                f"({s.consecutive_failures} failed probes, {len(s.action_times)} recovery actions in the last hour)",
            )
        )
        s.last_still_down_log = now

    due = _due_action(s)
    if due is None or state.radio_off_pending:
        return Decision(s, tuple(actions), tuple(logs))
    held_back_by = _deferral(probe, s)
    if held_back_by is not None:
        if held_back_by != s.deferred_reason:
            logs.append((syslog.LOG_WARNING, f"held back ({held_back_by}): {due.describe()}"))
            s.deferred_reason = held_back_by
        return Decision(s, tuple(actions), tuple(logs))

    s.deferred_reason = None
    s.action_times.append(now)
    s.outage_actions += 1
    if due.kind == DEVICE_CONNECT:
        s.connect_done = True
    else:
        s.radio_cycles = due.cycle
        s.last_radio_cycle_failure = s.consecutive_failures
    actions.append(due)
    return Decision(s, tuple(actions), tuple(logs))


def _clear_outage(state: State) -> State:
    """Return ``state`` with the outage bookkeeping reset; the rolling-hour cap is kept."""
    return replace(
        state,
        consecutive_failures=0,
        down_since=None,
        last_still_down_log=None,
        connect_done=False,
        radio_cycles=0,
        last_radio_cycle_failure=None,
        outage_actions=0,
        deferred_reason=None,
    )


def _due_action(state: State) -> Action | None:
    """Return the rung this many failures has reached and not yet taken, if any.

    A rung stays due until it runs, so a guard or the cap delays it instead of
    skipping it.
    """
    failures = state.consecutive_failures
    if failures >= CONNECT_AFTER_FAILURES and not state.connect_done:
        return Action(DEVICE_CONNECT, failures)
    if failures >= RADIO_CYCLE_AFTER_FAILURES and (
        state.last_radio_cycle_failure is None
        or failures - state.last_radio_cycle_failure >= RADIO_CYCLE_REPEAT_FAILURES
    ):
        return Action(RADIO_CYCLE, failures, cycle=state.radio_cycles + 1)
    return None


def _deferral(probe: Probe, state: State) -> str | None:
    """Return why a due action must wait, or None when it may run now."""
    if probe.nm_state is None or probe.hotspot_active is None:
        return DEFER_NM_UNKNOWN  # the hotspot and connecting guards cannot be checked
    if probe.nm_state == "connecting":
        return DEFER_NM_CONNECTING
    if len(state.action_times) >= MAX_ACTIONS_PER_WINDOW:
        return DEFER_CAP
    return None


def format_duration(seconds: float) -> str:
    """Format a duration for a log line: ``45s``, ``12m 30s``, ``3h 05m``, ``2d 04h``."""
    total = max(int(seconds), 0)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


# --- adapters ----------------------------------------------------------------------------


@dataclass(frozen=True)
class System:
    """The watchdog's view of the outside world; tests pass fakes."""

    run: Runner
    resolve: Callable[[str, int, float], bool]
    sleep: Callable[[float], None]
    now: Callable[[], float]
    log: Callable[[int, str], None]
    out: Callable[[str], None]

    @classmethod
    def real(cls) -> System:
        """Build the adapters a cron run on the robot uses."""
        open_syslog()
        return cls(
            run=run_command,
            resolve=resolve_host,
            sleep=time.sleep,
            now=time.time,
            log=log_to_syslog,
            out=print,
        )


def run_command(argv: Sequence[str], timeout: float) -> CommandResult:
    """Run one command with a timeout; never raises for a missing binary or a hang."""
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=COMMAND_ENV,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(None, timed_out=True)
    except OSError:
        return CommandResult(None)
    return CommandResult(done.returncode, done.stdout, done.stderr)


def resolve_host(host: str, port: int, timeout: float) -> bool:
    """Report whether ``host`` resolves within ``timeout`` seconds.

    ``getaddrinfo`` has no timeout of its own, so it runs in a daemon thread
    the process can leave behind.
    """
    outcome: list[bool] = []

    def lookup() -> None:
        try:
            outcome.append(bool(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except (OSError, UnicodeError):
            outcome.append(False)

    worker = threading.Thread(target=lookup, name="dns-probe", daemon=True)
    worker.start()
    worker.join(timeout)
    return bool(outcome) and outcome[0]


def open_syslog() -> None:
    """Tag every syslog line with the watchdog's identifier and pid."""
    syslog.openlog(IDENT, syslog.LOG_PID, syslog.LOG_USER)


_PRIORITY_NAMES = {
    syslog.LOG_ERR: "error",
    syslog.LOG_WARNING: "warning",
    syslog.LOG_NOTICE: "notice",
    syslog.LOG_INFO: "info",
}


def log_to_syslog(priority: int, message: str) -> None:
    """Send one line to syslog (the journal), and echo it for interactive runs; cron discards stdout."""
    syslog.syslog(priority, message)
    print(f"{_PRIORITY_NAMES.get(priority, 'log')}: {message}")


def default_gateway(route_output: str, device: str = WIFI_DEVICE) -> tuple[bool, str | None]:
    """Parse ``ip route show default``: ``(has a default route, its gateway)``, preferring ``device``."""
    has_route = False
    first: str | None = None
    for line in route_output.splitlines():
        words = line.split()
        if not words or words[0] != "default":
            continue
        has_route = True
        gateway = words[words.index("via") + 1] if "via" in words[:-1] else None
        on_device = "dev" in words[:-1] and words[words.index("dev") + 1] == device
        if on_device and gateway is not None:
            return True, gateway
        if first is None:
            first = gateway
    return has_route, first


def split_terse(line: str) -> list[str]:
    r"""Split one line of ``nmcli -t`` output, where ``\:`` and ``\\`` are escapes."""
    fields: list[str] = []
    current: list[str] = []
    chars = iter(line)
    for char in chars:
        if char == "\\":
            current.append(next(chars, ""))
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def read_network_manager(system: System) -> tuple[str | None, bool | None]:
    """Return ``(NM general state, hotspot active)``; either is None when nmcli cannot say."""
    general = system.run(["nmcli", "-t", "-f", "STATE", "general"], NMCLI_QUERY_TIMEOUT_SECONDS)
    nm_state = (general.stdout.strip() or None) if general.ok else None
    active = system.run(
        ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], NMCLI_QUERY_TIMEOUT_SECONDS
    )
    if not active.ok:
        return nm_state, None
    for line in active.stdout.splitlines():
        fields = split_terse(line)
        if len(fields) < 2:
            continue
        name, kind = fields[0], fields[1]
        if name == HOTSPOT_CONNECTION_NAME:
            return nm_state, True
        if kind in WIFI_CONNECTION_TYPES:
            mode = system.run(
                ["nmcli", "-g", "802-11-wireless.mode", "connection", "show", "id", name],
                NMCLI_QUERY_TIMEOUT_SECONDS,
            )
            if mode.ok and mode.stdout.strip() == "ap":
                return nm_state, True
    return nm_state, False


def gather_probe(system: System, gateway_override: str | None = None) -> Probe:
    """Probe the link: default route, gateway ping; then DNS if it is up, NetworkManager if it is down."""
    route = system.run(["ip", "route", "show", "default"], PROBE_TIMEOUT_SECONDS)
    has_route, gateway = default_gateway(route.stdout) if route.ok else (False, None)
    target = gateway_override or gateway
    gateway_ok = False
    if has_route and target is not None:
        gateway_ok = system.run(["ping", "-n", "-c", "2", "-W", "2", target], PROBE_TIMEOUT_SECONDS).ok
    if has_route and gateway_ok:
        return Probe(True, True, dns_ok=system.resolve(DNS_PROBE_HOST, DNS_PROBE_PORT, DNS_TIMEOUT_SECONDS))
    nm_state, hotspot = read_network_manager(system)
    return Probe(has_route, False, nm_state=nm_state, hotspot_active=hotspot)


# --- acting ------------------------------------------------------------------------------


def _nmcli_as_root(*args: str) -> list[str]:
    """Return the argv for one NOPASSWD nmcli call; ``-n`` fails instead of prompting."""
    return ["sudo", "-n", NMCLI, *args]


def _outcome(result: CommandResult) -> str:
    """Describe a command result without quoting its output, which can name the network."""
    if result.ok:
        return "ok"
    if result.timed_out:
        return "timed out"
    if result.returncode is None:
        return "could not run"
    if "password is required" in result.stderr:
        return "sudo refused (needs a password)"
    return f"failed (exit {result.returncode})"


def _radio_on(system: System) -> CommandResult:
    """Switch the Wi-Fi radio on, retrying: leaving it off would outlive a reboot."""
    result = CommandResult(None)
    for attempt in range(RADIO_ON_ATTEMPTS):
        if attempt:
            system.sleep(RADIO_ON_RETRY_SECONDS)
        result = system.run(_nmcli_as_root("radio", "wifi", "on"), RADIO_TIMEOUT_SECONDS)
        if result.ok:
            break
    return result


def execute(action: Action, state: State, system: System, save: Callable[[State], None]) -> list[LogLine]:
    """Carry out one action, keeping ``state.radio_off_pending`` saved across the radio-off window."""
    if action.kind == DEVICE_CONNECT:
        result = system.run(_nmcli_as_root("device", "connect", WIFI_DEVICE), CONNECT_TIMEOUT_SECONDS)
        priority = syslog.LOG_NOTICE if result.ok else syslog.LOG_WARNING
        return [(priority, f"{action.describe()}: {_outcome(result)}")]

    if action.kind == RADIO_CYCLE:
        # Saved before the radio goes off: if this run dies in between, the next run turns it back on.
        state.radio_off_pending = True
        save(state)
        off = system.run(_nmcli_as_root("radio", "wifi", "off"), RADIO_TIMEOUT_SECONDS)
        system.sleep(RADIO_OFF_SECONDS)
        on = _radio_on(system)  # even if "off" failed: "on" is harmless when the radio is on
        state.radio_off_pending = not on.ok
        save(state)
        message = f"{action.describe()}: off {_outcome(off)}, on {_outcome(on)}"
        if not on.ok:
            return [(syslog.LOG_ERR, f"{message}; the radio may be off, the next run retries")]
        return [(syslog.LOG_NOTICE if off.ok else syslog.LOG_WARNING, message)]

    on = _radio_on(system)
    state.radio_off_pending = not on.ok
    save(state)
    return [(syslog.LOG_NOTICE if on.ok else syslog.LOG_ERR, f"{action.describe()}: {_outcome(on)}")]


# --- state file ----------------------------------------------------------------------------


class StateError(ValueError):
    """The state file does not hold a state this version can use."""


def default_state_dir() -> Path:
    """Return ``~/.local/state/reachy-wifi-watchdog``."""
    return Path.home() / ".local" / "state" / IDENT


def _state_paths(state_dir: Path, dry_run: bool) -> tuple[Path, Path]:
    """Return ``(state file, lock file)``; a dry run keeps its own pair, away from the cron's."""
    if dry_run:
        return state_dir / "dry-run-state.json", state_dir / "dry-run.lock"
    return state_dir / "state.json", state_dir / "lock"


def _is_count_or_time(value: Any) -> bool:
    """Report whether ``value`` is a finite, non-negative number (every number the state holds is)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _field(raw: dict[str, Any], key: str, kinds: tuple[type, ...], optional: bool = False) -> Any:
    """Return ``raw[key]`` if it has one of ``kinds``; a number must also be finite and non-negative."""
    if key not in raw:
        raise StateError(key)
    value = raw[key]
    if value is None and optional:
        return None
    if bool in kinds:
        if not isinstance(value, bool):
            raise StateError(key)
    elif int in kinds:
        if not _is_count_or_time(value) or (float not in kinds and not isinstance(value, int)):
            raise StateError(key)
    elif not isinstance(value, kinds):
        raise StateError(key)
    return value


def _probe_record_from(raw: Any) -> ProbeRecord | None:
    """Rebuild the saved last probe."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise StateError("last_probe")
    return ProbeRecord(
        at=float(_field(raw, "at", (int, float))),
        default_route=_field(raw, "default_route", (bool,)),
        gateway_ok=_field(raw, "gateway_ok", (bool,)),
        dns_ok=_field(raw, "dns_ok", (bool,), optional=True),
        nm_state=_field(raw, "nm_state", (str,), optional=True),
        hotspot=_field(raw, "hotspot", (bool,), optional=True),
    )


def state_from_json(text: str) -> State:
    """Parse a saved state; raise :class:`StateError` (a ValueError) on anything unexpected."""
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        raise StateError("version")
    status = _field(raw, "status", (str,))
    if status not in STATUSES:
        raise StateError("status")
    times = _field(raw, "action_times", (list,))
    if not all(_is_count_or_time(t) for t in times):
        raise StateError("action_times")
    down_since = _field(raw, "down_since", (int, float), optional=True)
    last_still = _field(raw, "last_still_down_log", (int, float), optional=True)
    return State(
        status=status,
        consecutive_failures=_field(raw, "consecutive_failures", (int,)),
        down_since=None if down_since is None else float(down_since),
        last_still_down_log=None if last_still is None else float(last_still),
        connect_done=_field(raw, "connect_done", (bool,)),
        radio_cycles=_field(raw, "radio_cycles", (int,)),
        last_radio_cycle_failure=_field(raw, "last_radio_cycle_failure", (int,), optional=True),
        outage_actions=_field(raw, "outage_actions", (int,)),
        deferred_reason=_field(raw, "deferred_reason", (str,), optional=True),
        radio_off_pending=_field(raw, "radio_off_pending", (bool,)),
        action_times=[float(t) for t in times],
        last_probe=_probe_record_from(_field(raw, "last_probe", (dict,), optional=True)),
    )


def load_state(path: Path) -> tuple[State, bool]:
    """Return ``(state, was_corrupt)``; a missing file is a fresh start, not corruption."""
    try:
        return state_from_json(path.read_text(encoding="utf-8")), False
    except FileNotFoundError:
        return State(), False
    except (OSError, ValueError):  # JSON, UTF-8 and StateError are all ValueErrors
        return State(), True


def save_state(path: Path, state: State) -> None:
    """Write the state atomically: a temp file in the same directory, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps({"version": STATE_VERSION, **asdict(state)}, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


@contextlib.contextmanager
def exclusive_lock(path: Path) -> Iterator[bool]:
    """Hold an exclusive ``flock`` on ``path`` for the block; yields False if another run has it."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        locked = False
    try:
        yield locked
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --- runs -----------------------------------------------------------------------------------


def _describe_probe(record: ProbeRecord | None) -> str:
    """Describe a saved probe in words, with no address in it."""
    if record is None:
        return "none yet"
    parts = [
        f"default route {'yes' if record.default_route else 'no'}",
        "gateway answers" if record.gateway_ok else "gateway no answer",
        {True: "DNS ok", False: "DNS failing", None: "DNS not checked"}[record.dns_ok],
    ]
    if not (record.default_route and record.gateway_ok):  # NetworkManager is read only when the link is down
        parts.append(f"NetworkManager {record.nm_state or 'unavailable'}")
        parts.append({True: "hotspot active", False: "hotspot off", None: "hotspot unknown"}[record.hotspot])
    return ", ".join(parts)


def run_once(system: System, state_dir: Path, *, gateway: str | None = None, dry_run: bool = False) -> int:
    """Run one probe-decide-act cycle under the lock; return the exit status (0 unless a bug raises)."""
    state_path, lock_path = _state_paths(state_dir, dry_run)
    prefix = "dry-run: " if dry_run else ""

    def emit(priority: int, message: str) -> None:
        system.log(priority, prefix + message)

    with exclusive_lock(lock_path) as locked:
        if not locked:
            return 0  # an earlier run is still busy (a slow `device connect`): skip quietly
        state, was_corrupt = load_state(state_path)
        if was_corrupt:
            emit(syslog.LOG_WARNING, "state file unreadable or from another version: starting from a fresh state")
        now = system.now()
        probe = gather_probe(system, gateway)
        decision = decide(state, probe, now)
        for priority, message in decision.logs:
            emit(priority, message)
        state = decision.state
        save_failed: list[bool] = []

        def save(current: State) -> None:
            try:
                save_state(state_path, current)
            except OSError as exc:
                if not save_failed:
                    emit(syslog.LOG_WARNING, f"could not save the state file ({type(exc).__name__})")
                save_failed.append(True)

        save(state)  # the decision is on disk before anything is done about it
        for action in decision.actions:
            if dry_run:
                emit(syslog.LOG_NOTICE, f"would {action.describe()}")
                continue
            for priority, message in execute(action, state, system, save):
                emit(priority, message)
        if dry_run:
            planned = "; ".join(action.describe() for action in decision.actions)
            if not planned:
                planned = f"held back ({state.deferred_reason})" if state.deferred_reason else "no action due"
            system.out(f"{prefix}{_describe_probe(state.last_probe)}; status {state.status}; {planned}")
    return 0


def _format_time(epoch: float, now: float) -> str:
    """Format a saved wall-clock time with how long ago it was."""
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(epoch))} ({format_duration(now - epoch)} ago)"


def print_status(system: System, state_dir: Path, *, dry_run: bool = False) -> int:
    """Print the saved state and the last probe; probe and change nothing."""
    state_path, _lock_path = _state_paths(state_dir, dry_run)
    if not state_path.exists():
        system.out(f"no state yet: the watchdog has not run ({state_path} is missing)")
        return 0
    state, was_corrupt = load_state(state_path)
    if was_corrupt:
        system.out(f"state file is unreadable ({state_path}); the next run starts from a fresh state")
        return 0
    now = system.now()
    recent = [t for t in state.action_times if 0.0 <= now - t < ACTION_WINDOW_SECONDS]
    lines = [f"status: {state.status}", f"failed probes in a row: {state.consecutive_failures}"]
    if state.down_since is not None:
        lines.append(f"down since: {_format_time(state.down_since, now)}")
    lines += [
        f"recovery actions this outage: {state.outage_actions} "
        f"(radio cycles {state.radio_cycles}, reconnect {'done' if state.connect_done else 'not yet'})",
        f"recovery actions in the last hour: {len(recent)} of {MAX_ACTIONS_PER_WINDOW}",
        f"radio off pending: {'yes' if state.radio_off_pending else 'no'}",
    ]
    if state.deferred_reason is not None:
        lines.append(f"held back: {state.deferred_reason}")
    probe = state.last_probe
    when = _format_time(probe.at, now) if probe is not None else ""
    lines.append(f"last probe: {when + ': ' if when else ''}{_describe_probe(probe)}")
    for line in lines:
        system.out(line)
    return 0


def _ip_address(text: str) -> str:
    """Validate ``--gateway``."""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise argparse.ArgumentTypeError("expected an IPv4 or IPv6 address") from None


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="wifi_watchdog.py",
        description="Probe the robot's Wi-Fi once and recover it if it has been down for a while.",
    )
    parser.add_argument("--dry-run", action="store_true", help="decide and log, but run no nmcli action")
    parser.add_argument("--status", action="store_true", help="print the saved state and last probe, then exit")
    parser.add_argument("--gateway", type=_ip_address, help="ping this address instead of the routed gateway")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, system: System | None = None, state_dir: Path | None = None) -> int:
    """Entry point for cron and for interactive checks."""
    args = parse_args(argv)
    system = system if system is not None else System.real()
    state_dir = state_dir if state_dir is not None else default_state_dir()
    if args.status:
        return print_status(system, state_dir, dry_run=args.dry_run)
    return run_once(system, state_dir, gateway=args.gateway, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a bug: leave a trace in the journal, then fail loudly
        open_syslog()
        syslog.syslog(syslog.LOG_ERR, f"internal error: {type(exc).__name__}")
        raise
