"""Tidy the Mac people store's facts through the model — an operator command.

`backend.consolidate` computes the rewrite; this is the thing an operator runs.
It exists to make two decisions safe to take at a terminal:

**Read the diff before it happens.** The default is a dry run: every person's
before/after is printed as a unified diff and nothing is written. `--apply`
writes, and only then. What is printed is the module's `after` verbatim —
already normalized and deduped exactly as `store.replace_facts` will hold it —
so the preview is the write, not an approximation of it.

**Never share the store with a running backend.** `store`'s lock is a
`threading.RLock` in one process (`backend/store.py:93`), so a CLI write while
the server is serving is a lost update: both sides read `people.json`, both
write it back, and whichever finishes last silently erases the other. Python
locks do not cross processes and file locks were not what the backend was built
on, so the guard here is a *probe* — and it fails CLOSED. Only a connection
refused on every plausible bind lets the run continue; an answer stops it, and
so does a timeout, a TLS error, or anything else that leaves the question open.
An operator who sees a false refusal stops the backend and runs again, which
costs a minute. The other mistake costs somebody's memory.

"Every plausible bind" is the part worth stating: the documented production bind
is the *tailnet IP*, not loopback (`run.sh:8`, README "Access over Tailscale"),
so probing `127.0.0.1` alone would clear a run against the exact deployment the
README recommends. The candidates are loopback, `COMPANION_BACKEND_HOST` when
set, and whatever `tailscale ip -4` reports.

`--import-first` and `--push-after` exist because the UI's import and push need
the server this guard forbids. Together they make the whole round trip runnable
with the backend stopped, in the one order that is safe: import (the robot's
own writes come back first, so the model organizes them too), consolidate, push.
Both call `backend.robot` directly; there is no second copy of the sync rules
here.

    ../reachy_companion/.venv/bin/python scripts/consolidate.py            # review
    ../reachy_companion/.venv/bin/python scripts/consolidate.py --apply    # keep it

Exit codes: 0 done, 1 refused or failed (bad flags, an unknown `--person`, a
robot error, a blocked push), 2 nothing consolidated because there is no OpenAI
client, 3 the backend is running or could not be proven stopped.

`backend.consolidate` is imported; `reachy_companion.sleep_summary` deliberately
is not — importing it reaches `reachy_companion.config`, which runs
`load_dotenv(override=True)` at import time and would rewrite this process's
environment, `OPENAI_API_KEY` included, as a side effect of a lookup.
"""

from __future__ import annotations
import os
import sys
import shutil
import difflib
import logging
import argparse
import subprocess
from typing import Any, Final
from pathlib import Path

import httpx


# Run as a plain script out of `companion_backend/`, exactly as `run.sh` runs
# the server: the package root goes on `sys.path` so `backend.*` imports the
# same way it does there.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from backend import robot, consolidate  # noqa: E402  (needs the path insert above)
from backend.config import Settings, load_settings  # noqa: E402


EXIT_OK: Final[int] = 0
EXIT_REFUSED: Final[int] = 1
EXIT_NO_CLIENT: Final[int] = 2
EXIT_BACKEND_UP: Final[int] = 3

# The backend's own port and bind knob, restated from `run.sh` rather than
# imported: importing `backend.app` to learn a port number would start pulling
# fastapi and the recognizer warmup into a script that needs neither.
BACKEND_PORT: Final[int] = 8710
BACKEND_HOST_ENV: Final[str] = "COMPANION_BACKEND_HOST"
LOOPBACK: Final[str] = "127.0.0.1"

# `/api/config` is the cheapest route the backend serves and it touches no
# person data — the probe asks "is anything there", never for content.
PROBE_PATH: Final[str] = "/api/config"
PROBE_TIMEOUT_SECONDS: Final[float] = 2.0
TAILSCALE_TIMEOUT_SECONDS: Final[float] = 5.0


def _say(message: str = "") -> None:
    """Print one line of the report to stdout, flushed so it interleaves with logs."""
    print(message, flush=True)


# --------------------------------------------------------------------------
# the guard: prove the backend is stopped, or refuse
# --------------------------------------------------------------------------


def _probe(url: str) -> None:
    """GET `url` and discard the answer. The one network seam; tests stand in for it.

    Whatever httpx raises is raised on through, because the caller's verdict is
    the exception *type*: only a refused connection means "nothing is listening".
    """
    with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS) as client:
        client.get(url)


def _tailscale_hosts() -> list[str]:
    """Return this Mac's tailnet addresses, or nothing at all — best effort by design.

    A missing binary, a non-zero exit or a hung command all mean the same thing
    here: no tailnet address was learned. That is a *narrower candidate list*,
    never a cleared run — the loopback probe still has to refuse.
    """
    if shutil.which("tailscale") is None:
        return []
    try:
        completed = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=TAILSCALE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def candidate_hosts() -> list[str]:
    """Return every address the backend might be bound to, loopback first, deduped."""
    configured = (os.getenv(BACKEND_HOST_ENV) or "").strip()
    found = [LOOPBACK, *([configured] if configured else []), *_tailscale_hosts()]

    hosts: list[str] = []
    for host in found:
        if host not in hosts:
            hosts.append(host)
    return hosts


def backend_objection() -> str | None:
    """Return why this run must not touch the store, or None when it is safe to.

    Fails closed twice over: an answer from any candidate host is a refusal, and
    so is any outcome that is not an outright connection refused. The loop stops
    at the first host that does not refuse — the others cannot make an answered
    probe safe.
    """
    for host in candidate_hosts():
        url = f"http://{host}:{BACKEND_PORT}{PROBE_PATH}"
        try:
            _probe(url)
        except httpx.ConnectError:
            # Nothing is listening there. The only outcome that clears a host.
            continue
        except Exception as exc:  # noqa: BLE001 - an unclear answer is still a refusal
            return (
                f"cannot prove the backend is stopped — probing {url} raised "
                f"{type(exc).__name__}. Stop the backend (and anything else on that "
                "port), then run this again."
            )
        return (
            f"the backend answered on {url} — stop it first. The store lock is "
            "per-process, so a write from here while the server serves is a lost update."
        )
    return None


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _render(result: consolidate.PersonConsolidation) -> None:
    """Print one person's row: a skip, a no-op, or the diff of what would be stored."""
    if result.error is not None:
        _say(f"{result.name}: skipped ({result.error})")
        return
    if not result.changed:
        _say(f"{result.name}: unchanged ({len(result.before)} fact(s))")
        return

    _say(f"{result.name}: {len(result.before)} -> {len(result.after)} fact(s)")
    lines = difflib.unified_diff(
        list(result.before),
        list(result.after),
        fromfile=f"{result.name} (before)",
        tofile=f"{result.name} (after)",
        lineterm="",
    )
    for line in lines:
        _say(f"  {line}")
    _say()


# --------------------------------------------------------------------------
# the robot half, reusing `backend.robot` as-is
# --------------------------------------------------------------------------


def _import_first(settings: Settings) -> str | None:
    """Bring the robot's own writes back before the model sees the facts.

    Returns None on success, or the message to report. The robot's new facts are
    part of what the pass should organize, and its forgets should be applied
    before a consolidation rewrites the list they refer to — which is why this
    runs first rather than after.
    """
    try:
        diff = robot.import_from_robot(settings)
        result = robot.apply_import(settings, diff)
    except robot.RobotError as exc:
        return f"the import failed: {exc}"

    _say(f"import     applied {result.applied} item(s), {len(result.conflicts)} conflict(s)")
    for conflict in result.conflicts:
        _say(f"  conflict {conflict}")
    _say()
    return None


def _push_after(settings: Settings) -> str | None:
    """Push the consolidated store to the robot. Returns None on success, else the message."""
    try:
        result = robot.push(settings)
    except robot.RobotError as exc:
        return f"the push failed: {exc}"
    if not result.pushed:
        return (
            "the push was refused: the robot holds content this store does not know. "
            "Run again with --import-first, or push from the UI once the diff is resolved."
        )
    _say(f"push       {result.faces_count} face(s), {result.people_count} person(s)")
    for name in result.skipped:
        _say(f"  skipped  {name} (nothing projectable)")
    return None


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="consolidate.py",
        description="Merge, de-contradict and rank each person's remembered facts. Dry run by default.",
    )
    parser.add_argument("--apply", action="store_true", help="write the rewrites (default: print them and stop)")
    parser.add_argument("--person", default=None, metavar="NAME", help="only this person (name or alias)")
    parser.add_argument(
        "--import-first",
        action="store_true",
        help="import the robot's own writes before consolidating (needs the backend stopped, which is the point)",
    )
    parser.add_argument("--push-after", action="store_true", help="push to the robot afterwards; needs --apply")
    return parser


def main(argv: list[str] | None = None, *, client: Any | None = None) -> int:
    """Run one consolidation pass and return the process exit code."""
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    if args.push_after and not args.apply:
        # Checked before the probe: a flag combination that can never be right
        # should not cost the operator one timeout per candidate host.
        _say("REFUSED  --push-after needs --apply; a dry run has consolidated nothing to push.")
        return EXIT_REFUSED

    # First, and before anything reads the store.
    objection = backend_objection()
    if objection is not None:
        _say(f"REFUSED  {objection}")
        return EXIT_BACKEND_UP

    settings = load_settings()
    _say(f"data dir   {settings.data_dir}")
    _say(f"mode       {'APPLY — this writes' if args.apply else 'dry run — nothing is written'}")
    _say()

    if args.import_first:
        failure = _import_first(settings)
        if failure is not None:
            _say(f"FAILED   {failure}")
            return EXIT_REFUSED

    results = consolidate.run(settings, apply=args.apply, only=args.person, client=client)
    if not results:
        if args.person is not None:
            _say(f"Nobody here answers to {args.person!r}. Check the name on the person page.")
            return EXIT_REFUSED
        # Returning here also skips any `--push-after`, deliberately: a push
        # projects this store onto the robot, so pushing an empty one would
        # clear the robot's faces on the strength of a run that did nothing.
        _say("The store holds nobody to consolidate.")
        return EXIT_OK

    for result in results:
        _render(result)

    changed = sum(1 for result in results if result.changed)
    _say(f"{len(results)} person(s), {changed} changed, applied: {'yes' if args.apply else 'no'}")

    if all(result.error == consolidate.NO_CLIENT for result in results):
        _say(f"Nothing was consolidated ({consolidate.NO_CLIENT}): set OPENAI_API_KEY and run again.")
        if args.push_after:
            _say("Skipping the push: this run consolidated nothing.")
        return EXIT_NO_CLIENT

    if not args.apply and changed:
        _say("Dry run — nothing was written. Re-run with --apply to keep this.")

    if args.push_after:
        failure = _push_after(settings)
        if failure is not None:
            _say(f"FAILED   {failure}")
            return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
