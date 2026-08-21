"""Home-network awareness for house-bound tools (D-018, R4).

House-bound capabilities (TV casting, the NAS home-video library) only work when
the robot is on the same LAN as Home Assistant. Rather than let each one fail
with a socket error tens of seconds later, they ask `home_state()` first.

**The verdict is tri-state** (review round 1, finding 12), and **`AWAY` requires
positive evidence** (review round 2, finding 3):

* `AWAY` -- the robot's **own** address is outside every network the operator
  declared in `HANOVA_HOME_NETWORKS`. That is a fact about where this machine is
  attached, it does not depend on Home Assistant answering, and it is the *only*
  thing that justifies telling the user they are not at home. With no declared
  network this verdict is **unreachable**, by design.
* `UNKNOWN` -- everything else that is not a clean `HOME`: the TCP connect
  failed (no route, DNS failure, connection refused -- all of which are what an
  HA outage looks like), the answer was 401/403 or 5xx, the HTTP read timed out,
  the connection came from a plainly different subnet (a VPN or a remote proxy),
  or there is simply no declaration to judge locality with. The robot cannot
  tell where it is, and it says exactly that.
* `HOME` -- inside a declared home network (or, with none declared, on the same
  subnet as Home Assistant) **and** `/api/` answered 200.

**The `/24` subnet comparison is a demoted hint** (round 2, finding 3). It ran as
a hard rule in round 1, which misclassifies any home LAN wider than a `/24`. It
may now only *withhold* `HOME`; it can never produce `AWAY`, and a declared
`HANOVA_HOME_NETWORKS` bypasses it entirely. A `/16` home LAN therefore degrades
to `UNKNOWN` -- honest, and fixable with one config key -- instead of to a lie.

The probe is a TCP connect plus one `GET {HA_URL}/api/` with the long-lived
token, both capped at `HANOVA_HOME_PROBE_TIMEOUT_S` (1.5 s) and cached -- all
three verdicts -- for `HANOVA_HOME_CACHE_TTL_S` (30 s). A single-flight
`asyncio.Lock` collapses a burst of cold tool calls into one probe; a
`threading.Lock` guards the cache itself because the settings web server runs on
its own thread.

Cloud tools (calendar, tasks, Notion, Drive, email) and music never call this:
they work from anywhere, and a needless probe would be pure added latency.

There is deliberately **no boolean `is_home` shortcut** (round 2, finding 3): a
boolean cannot carry three verdicts, and every call site that used one turned
`UNKNOWN` into "yes, go ahead".
"""

from __future__ import annotations
import time
import socket
import asyncio
import logging
import ipaddress
import threading
import urllib.parse
from typing import Any, Dict, List
from dataclasses import dataclass

import httpx

from reachy_companion.hanova import redact, settings


logger = logging.getLogger(__name__)

HOME = "home"
AWAY = "away"
UNKNOWN = "unknown"

_LOCK = threading.Lock()
_PROBE_LOCK = asyncio.Lock()
_CACHED_VERDICT: str | None = None
_CACHED_AT: float = 0.0


@dataclass(frozen=True)
class LanProbe:
    """What one TCP connect to Home Assistant told us about where we are."""

    reachable: bool
    local_address: str
    same_subnet: bool


def reset_cache() -> None:
    """Drop the cached verdict so the next probe runs. Used by tests."""
    global _CACHED_VERDICT, _CACHED_AT
    with _LOCK:
        _CACHED_VERDICT = None
        _CACHED_AT = 0.0


def _read_cache(ttl_s: float) -> str | None:
    with _LOCK:
        if _CACHED_VERDICT is None:
            return None
        if (time.monotonic() - _CACHED_AT) > ttl_s:
            return None
        return _CACHED_VERDICT


def _write_cache(verdict: str) -> None:
    global _CACHED_VERDICT, _CACHED_AT
    with _LOCK:
        _CACHED_VERDICT = verdict
        _CACHED_AT = time.monotonic()


def _same_subnet(local: str, peer: str) -> bool:
    """Return whether two addresses *look* like they share one local network.

    Round 2, finding 3: this is a **hint**, not a rule. A /24 for IPv4 and a /64
    for IPv6 is true for a typical home LAN and false for a VPN tunnel address,
    but it is also false for a perfectly ordinary /22 or /16 home network. Its
    only permitted effect is to withhold `HOME`; it can never produce `AWAY`, and
    a configured `HANOVA_HOME_NETWORKS` bypasses it entirely.
    """
    try:
        left = ipaddress.ip_address(local)
        right = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if left.version != right.version:
        return False
    prefix = 24 if left.version == 4 else 64
    network = ipaddress.ip_network(f"{right}/{prefix}", strict=False)
    return left in network


def _inside_declared_home(address: str, networks: List[Any]) -> bool | None:
    """Return True/False when the declaration can decide, None when it cannot."""
    if not networks or not address:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    for network in networks:
        if parsed.version == network.version and parsed in network:
            return True
    return False


async def local_address(host: str, timeout_s: float) -> str:
    """Return this machine's source address on the route towards *host*.

    Round 2, finding 3: `AWAY` has to be decidable even when Home Assistant is
    down, so locality cannot be a by-product of a successful connect. A UDP
    socket that is `connect()`ed sends **no packets** -- it only asks the kernel
    which interface and address would be used -- so this works with HA offline
    and costs nothing. Returns "" when there is no route at all, which is itself
    not evidence of anything and yields `UNKNOWN`.

    A seam tests monkeypatch. Never raises.
    """

    def _probe() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout_s)
            sock.connect((host, 9))  # discard port; nothing is transmitted
            return str(sock.getsockname()[0])
        finally:
            sock.close()

    try:
        return await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout_s)
    except (OSError, asyncio.TimeoutError, socket.gaierror):
        return ""


async def lan_signal(host: str, port: int, timeout_s: float) -> LanProbe:
    """Open one TCP connection and report reachability plus locality.

    The single seam tests monkeypatch. Never raises.
    """
    writer = None
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_s)
        local = writer.get_extra_info("sockname")
        peer = writer.get_extra_info("peername")
        if not local or not peer:
            # Connected, but we cannot tell which network we are on.
            return LanProbe(reachable=True, local_address="", same_subnet=False)
        return LanProbe(
            reachable=True,
            local_address=str(local[0]),
            same_subnet=_same_subnet(str(local[0]), str(peer[0])),
        )
    except (OSError, asyncio.TimeoutError, socket.gaierror):
        # Round 2, finding 3: a failed connect is an HA fact, not a location
        # fact. It never produces AWAY on its own.
        return LanProbe(reachable=False, local_address="", same_subnet=False)
    finally:
        if writer is not None:
            writer.close()


def _host_and_port(base_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname or "", int(port)


async def _probe() -> str:
    base_url = settings.ha_url()
    token = settings.ha_token()
    if not base_url or not token:
        # Unconfigured is not absence. The tools that need HA are already
        # `unavailable` by their prerequisites (settings.tool_status).
        return UNKNOWN

    timeout_s = settings.home_probe_timeout_s()
    host, port = _host_and_port(base_url)
    if not host:
        return UNKNOWN

    networks = settings.home_networks()

    try:
        signal = await lan_signal(host, port, timeout_s)
    except Exception:  # noqa: BLE001 - a verdict is required on every path
        logger.info("hanova home probe: LAN signal failed; verdict unknown.")
        signal = LanProbe(reachable=False, local_address="", same_subnet=False)

    # --- step 1: positive off-home evidence, decided before anything else ---
    # This runs whether or not Home Assistant answered, which is the whole point
    # (round 2, finding 3): being on a foreign network is a fact about us.
    own_address = signal.local_address
    if not own_address:
        try:
            own_address = await local_address(host, timeout_s)
        except Exception:  # noqa: BLE001 - a verdict is required on every path
            own_address = ""

    verdict_from_declaration = _inside_declared_home(own_address, networks)
    if verdict_from_declaration is False:
        logger.info("hanova home probe: this machine is outside every declared home network; verdict away.")
        return AWAY

    # --- step 2: everything else needs Home Assistant to actually answer ----
    if not signal.reachable:
        # No route, DNS failure, refused port, connect timeout. All of these are
        # what a Home Assistant outage looks like from here, and none of them
        # says where we are (round 2, finding 3).
        logger.info("hanova home probe: Home Assistant did not accept a connection; verdict unknown.")
        return UNKNOWN

    if verdict_from_declaration is None and not signal.same_subnet:
        # No declaration to judge with, and the demoted hint says "probably not
        # local". Withhold HOME; never assert AWAY.
        logger.info("hanova home probe: no declared home network and an off-subnet route; verdict unknown.")
        return UNKNOWN

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(
                f"{base_url}/api/",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        # Finding 6: an httpx error string embeds the full URL.
        logger.info(
            "hanova home probe: HTTP layer failed on a reachable host (%s); verdict unknown.",
            redact.error(exc),
        )
        return UNKNOWN

    if response.status_code == 200:
        return HOME
    logger.info("hanova home probe: Home Assistant answered %d; verdict unknown.", response.status_code)
    return UNKNOWN


async def home_state() -> str:
    """Return HOME, AWAY or UNKNOWN for where the robot is right now."""
    ttl_s = settings.home_cache_ttl_s()
    cached = _read_cache(ttl_s)
    if cached is not None:
        return cached
    async with _PROBE_LOCK:
        cached = _read_cache(ttl_s)
        if cached is not None:
            return cached
        verdict = await _probe()
        _write_cache(verdict)
        return verdict


def away_from_home() -> Dict[str, Any]:
    """Return the exact payload a house-bound tool emits off the home network (R4)."""
    return {"status": "away_from_home"}


def home_unknown() -> Dict[str, Any]:
    """Say "I cannot tell where I am." Neither presence nor absence.

    Round 2, finding 3: this is a distinct status, `home_status_unknown`, so the
    persona can never confuse it with `away_from_home`. It takes **no argument**
    (finding 6): a caller-supplied detail is exactly how a Home Assistant error
    body -- which quotes the house's LAN address back -- would reach the model.
    """
    return {
        "status": "home_status_unknown",
        "error": (
            "Cannot tell whether the robot is on the home network right now. "
            "Say you are not sure you are at home and that the home system is "
            "not answering; do not tell the user they are out of the house."
        ),
    }
