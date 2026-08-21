"""Contract tests for the tri-state home-network probe (D-018, R4, finding 12, round 2 finding 3).

Two rules the whole module exists to keep:

1. Only **positive off-home routing evidence** may be reported as
   `away_from_home` -- the robot's own address sitting outside every declared
   home network. A failed connection to Home Assistant is not that: an HA that
   is down, a refused port, a DNS failure, a 401, a 5xx and a VPN tunnel are all
   `UNKNOWN`.
2. With `HANOVA_HOME_NETWORKS` unset there is no evidence that could justify
   `AWAY` at all, so the verdict is only ever `HOME` or `UNKNOWN`.
"""

import asyncio
import threading

import httpx
import pytest

from reachy_companion import home_net


HOME_LAN = "203.0.113.0/24"
AT_HOME_ADDRESS = "203.0.113.20"
ELSEWHERE_ADDRESS = "198.51.100.20"


class _MustNotBeCalled(BaseException):
    """Raised by the "this seam must never run" guards below.

    It derives from `BaseException`, not `Exception`, on purpose: `_probe` wraps
    both the LAN signal and the HTTP call in catch-alls so that a verdict is
    produced on every path (review finding 3 of the Task 2 review). A guard that
    raised `AssertionError` would be swallowed by those handlers and silently
    downgraded to `UNKNOWN` -- which is what several of these tests assert
    anyway, so the guard would stop guarding. A `BaseException` walks straight
    out through both handlers and fails the test loudly.
    """


class _FakeResponse:
    """Minimal stand-in for the httpx response Home Assistant returns."""

    def __init__(self, status_code: int = 200) -> None:
        """Record the status code the probe is going to read."""
        self.status_code = status_code


def _lan(reachable: bool = True, same_subnet: bool = True, local: str = AT_HOME_ADDRESS):
    """Build a `lan_signal` stand-in returning one fixed LanProbe."""

    async def probe(host, port, timeout_s):
        return home_net.LanProbe(
            reachable=reachable,
            local_address=local if reachable else "",
            same_subnet=same_subnet,
        )

    return probe


def _local(address: str):
    """Build a `local_address` stand-in returning one fixed source address."""

    async def resolve(host, timeout_s):
        return address

    return resolve


@pytest.fixture(autouse=True)
def clean_probe(monkeypatch):
    """Every test starts with an empty cache, a configured HA, and a good LAN."""
    home_net.reset_cache()
    monkeypatch.setenv("HA_URL", "http://ha.example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.delenv("HANOVA_HOME_CACHE_TTL_S", raising=False)
    monkeypatch.delenv("HANOVA_HOME_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("HANOVA_HOME_NETWORKS", raising=False)
    monkeypatch.setattr(home_net, "lan_signal", _lan())
    monkeypatch.setattr(home_net, "local_address", _local(AT_HOME_ADDRESS))
    yield
    home_net.reset_cache()


def test_away_payload_is_exactly_the_contract():
    """R4 fixes this shape; house-bound tools return it verbatim."""
    assert home_net.away_from_home() == {"status": "away_from_home"}


def test_unknown_payload_is_its_own_status_not_a_flavour_of_away():
    """Round 2, finding 3: "I cannot tell" gets its own name in the contract."""
    out = home_net.home_unknown()
    assert out["status"] == "home_status_unknown"
    assert out["status"] != "away_from_home"
    assert out["error"]


def test_unknown_payload_carries_no_caller_supplied_text():
    """Finding 6: a detail argument is how an HA error body would leak out."""
    import inspect

    signature = inspect.signature(home_net.home_unknown)
    assert list(signature.parameters) == [], "home_unknown() takes no arguments"


def test_the_is_home_shortcut_no_longer_exists():
    """Round 2, finding 3: a boolean cannot carry three verdicts."""
    assert not hasattr(home_net, "is_home")


@pytest.mark.asyncio
async def test_probe_hits_the_ha_api_root_with_the_token(monkeypatch):
    """The HTTP half is one authenticated GET on /api/, nothing heavier."""
    seen = {}

    async def fake_get(self, url, headers=None, **kw):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.HOME
    assert seen["url"] == "http://ha.example.invalid:8123/api/"
    assert seen["headers"]["Authorization"] == "Bearer tok"


# --- AWAY needs positive evidence (round 2, finding 3) ---------------------
@pytest.mark.asyncio
async def test_an_address_outside_every_declared_home_network_is_away(monkeypatch):
    """The one and only thing that justifies telling the user they are out."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", _lan(local=ELSEWHERE_ADDRESS, same_subnet=False))
    monkeypatch.setattr(home_net, "local_address", _local(ELSEWHERE_ADDRESS))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.AWAY


@pytest.mark.asyncio
async def test_off_home_is_away_even_when_home_assistant_is_unreachable(monkeypatch):
    """The evidence is our own routing, so it survives HA being down.

    This is what makes the verdict useful on a train: HA is unreachable *and*
    we are demonstrably on someone else's network.
    """

    async def fail_get(self, *args, **kwargs):
        raise _MustNotBeCalled("no HTTP call is needed once routing already decided")

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=False))
    monkeypatch.setattr(home_net, "local_address", _local(ELSEWHERE_ADDRESS))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.AWAY


@pytest.mark.asyncio
async def test_no_declared_home_network_can_never_produce_away(monkeypatch):
    """Round 2, finding 3: with nothing declared, absence is unprovable."""

    async def fail_get(self, *args, **kwargs):
        raise _MustNotBeCalled("no HTTP call once the LAN signal has already failed")

    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=False))
    monkeypatch.setattr(home_net, "local_address", _local(""))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_no_tcp_route_from_inside_the_home_network_is_unknown(monkeypatch):
    """Finding 3: a refused port at home is an HA outage, not absence."""

    async def fail_get(self, *args, **kwargs):
        raise _MustNotBeCalled("no HTTP call once the LAN signal has already failed")

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=False))
    monkeypatch.setattr(home_net, "local_address", _local(AT_HOME_ADDRESS))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_a_dns_failure_is_unknown_not_away(monkeypatch):
    """Round 2, finding 3: name resolution is infrastructure, not location."""

    async def dns_failure(host, port, timeout_s):
        return home_net.LanProbe(reachable=False, local_address="", same_subnet=False)

    async def fail_get(self, *args, **kwargs):
        raise _MustNotBeCalled("a DNS failure must not reach the HTTP layer")

    monkeypatch.setattr(home_net, "lan_signal", dns_failure)
    monkeypatch.setattr(home_net, "local_address", _local(""))
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_the_subnet_hint_can_withhold_home_but_never_assert_away(monkeypatch):
    """Round 2, finding 3: the /24 guess is demoted, not deleted.

    A /16 home LAN whose robot and HA sit in different /24s degrades to UNKNOWN
    -- honest and fixable with one config key -- rather than to a false AWAY.
    """

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setattr(home_net, "lan_signal", _lan(same_subnet=False))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_a_declared_home_network_overrides_the_subnet_hint(monkeypatch):
    """With a declaration, the /24 guess is not consulted at all."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", "203.0.113.0/16")
    monkeypatch.setattr(home_net, "lan_signal", _lan(same_subnet=False))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.HOME


@pytest.mark.asyncio
async def test_unauthorized_is_unknown_not_away(monkeypatch):
    """Finding 12: an expired HA token must not be reported as absence."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(401)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_server_error_is_unknown_not_away(monkeypatch):
    """An HA outage while the robot sits at home is not the robot being out."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(503)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_http_timeout_on_a_reachable_host_is_unknown(monkeypatch, caplog):
    """The socket connected, so the robot is on a network that reaches HA.

    This is also the only path that reaches `redact.error(exc)`, so the finding-6
    sentinel assertion belongs here: an httpx error message embeds the full
    request URL, which is the house's LAN address.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HA_URL", "http://SENTINEL_PRIVATE_x7.invalid:8123")

    async def slow_get(self, url, headers=None, **kw):
        raise httpx.ReadTimeout(f"timed out reading from {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", slow_get)
    assert await home_net.home_state() == home_net.UNKNOWN
    assert "SENTINEL_PRIVATE_x7" not in caplog.text


@pytest.mark.asyncio
async def test_a_non_httpx_failure_in_the_http_layer_is_still_a_verdict(monkeypatch, caplog):
    """Review finding 3: the catch-all is `Exception`, not just `httpx.HTTPError`.

    A verdict is required on every path. Anything the HTTP layer can raise that
    is not an `httpx.HTTPError` -- an SSL error, a bad-URL `ValueError`, a
    third-party wrapper -- used to escape `home_state()` and take every
    house-bound tool down with it.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HA_URL", "http://SENTINEL_PRIVATE_x7.invalid:8123")

    async def exploding_get(self, url, headers=None, **kw):
        raise RuntimeError(f"transport exploded talking to {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", exploding_get)
    assert await home_net.home_state() == home_net.UNKNOWN
    assert "SENTINEL_PRIVATE_x7" not in caplog.text


@pytest.mark.asyncio
async def test_reachable_over_a_vpn_is_unknown_not_home(monkeypatch):
    """Finding 12: remote access proves reachability, never presence."""

    async def fake_get(self, url, headers=None, **kw):
        return _FakeResponse(200)

    monkeypatch.setattr(home_net, "lan_signal", _lan(reachable=True, same_subnet=False))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_unconfigured_ha_is_unknown_without_any_request(monkeypatch):
    """No HA_URL means no probe at all -- not a 1.5 s wait on nothing."""

    async def fail_get(self, *args, **kwargs):
        raise _MustNotBeCalled("home_state must not probe when HA_URL is unset")

    async def fail_lan(*args, **kwargs):
        raise _MustNotBeCalled("home_state must not open a socket when HA_URL is unset")

    monkeypatch.delenv("HA_URL")
    monkeypatch.setattr(home_net, "lan_signal", fail_lan)
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_verdict_is_cached_for_the_ttl(monkeypatch):
    """A second call inside the TTL must not touch the network again."""
    calls = {"n": 0}

    async def counting_get(self, url, headers=None, **kw):
        calls["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", counting_get)
    assert await home_net.home_state() == home_net.HOME
    assert await home_net.home_state() == home_net.HOME
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_away_and_unknown_verdicts_are_cached_too(monkeypatch):
    """Neither of the negative verdicts may re-probe on every tool call."""
    calls = {"n": 0}

    async def counting_lan(host, port, timeout_s):
        calls["n"] += 1
        return home_net.LanProbe(reachable=False, local_address="", same_subnet=False)

    monkeypatch.setenv("HANOVA_HOME_NETWORKS", HOME_LAN)
    monkeypatch.setattr(home_net, "lan_signal", counting_lan)
    monkeypatch.setattr(home_net, "local_address", _local(ELSEWHERE_ADDRESS))
    assert await home_net.home_state() == home_net.AWAY
    assert await home_net.home_state() == home_net.AWAY
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cache_expires_and_reprobes(monkeypatch):
    """A zero TTL means every call re-probes, proving the TTL is honoured."""
    calls = {"n": 0}

    async def counting_get(self, url, headers=None, **kw):
        calls["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setenv("HANOVA_HOME_CACHE_TTL_S", "0")
    monkeypatch.setattr(httpx.AsyncClient, "get", counting_get)
    assert await home_net.home_state() == home_net.HOME
    assert await home_net.home_state() == home_net.HOME
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_concurrent_cold_probes_issue_one_request(monkeypatch):
    """Finding 12: a burst of tool calls on a cold cache is one probe, not five."""
    calls = {"n": 0}

    async def slow_get(self, url, headers=None, **kw):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return _FakeResponse(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", slow_get)
    results = await asyncio.gather(*(home_net.home_state() for _ in range(5)))
    assert results == [home_net.HOME] * 5
    assert calls["n"] == 1


def test_two_event_loops_on_two_threads_each_get_a_verdict(monkeypatch):
    """Review finding 1: the single-flight lock must not bind to one event loop.

    The settings web server runs on its own thread with its own loop, so
    `home_state()` is reachable from two loops in one process. A module-level
    `asyncio.Lock` binds itself to whichever loop first *contends* it and raises
    `RuntimeError: ... is bound to a different event loop` in the second one --
    which breaks the "a verdict on every path" guarantee that every house-bound
    tool depends on.

    Both threads contend their own loop (three concurrent calls each) and are
    held at a barrier so the two loops are demonstrably alive at the same time.
    A zero cache TTL keeps the first thread's verdict from short-circuiting the
    second thread before it ever reaches the lock.
    """
    monkeypatch.setenv("HANOVA_HOME_CACHE_TTL_S", "0")

    async def slow_get(self, url, headers=None, **kw):
        await asyncio.sleep(0.02)
        return _FakeResponse(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", slow_get)

    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def run_own_loop(name: str) -> None:
        async def contend():
            barrier.wait(timeout=10)
            return await asyncio.gather(*(home_net.home_state() for _ in range(3)))

        try:
            outcomes[name] = asyncio.run(contend())
        except BaseException as exc:  # noqa: BLE001 - the failure is the point
            outcomes[name] = exc

    # daemon=True on purpose: against a regressed implementation one of these
    # threads deadlocks on the shared lock rather than merely raising, and a
    # non-daemon thread would keep the whole pytest process alive after the
    # session ended. As daemons they let the assertions below fail loudly and
    # the process exit.
    threads = [threading.Thread(target=run_own_loop, args=(name,), daemon=True) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert outcomes["first"] == [home_net.HOME] * 3
    assert outcomes["second"] == [home_net.HOME] * 3


@pytest.mark.asyncio
async def test_probe_never_raises_whatever_the_socket_layer_does(monkeypatch):
    """A verdict is required on every path; an exception here breaks every tool."""

    async def exploding_lan(host, port, timeout_s):
        raise OSError("interface went away")

    monkeypatch.setattr(home_net, "lan_signal", exploding_lan)
    assert await home_net.home_state() == home_net.UNKNOWN


@pytest.mark.asyncio
async def test_the_probe_logs_no_address_and_no_url(monkeypatch, caplog):
    """Round 2, finding 6: home_net is a service seam and logs like one.

    The HA base URL is the house's LAN address and the source address is the
    robot's; neither belongs in a log line, at any level.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HA_URL", "http://SENTINEL_PRIVATE_x7.invalid:8123")
    monkeypatch.setattr(home_net, "lan_signal", _lan(same_subnet=False, local="SENTINEL_PRIVATE_x7"))
    monkeypatch.setattr(home_net, "local_address", _local("SENTINEL_PRIVATE_x7"))
    assert await home_net.home_state() == home_net.UNKNOWN
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
