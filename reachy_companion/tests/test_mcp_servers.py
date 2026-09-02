from __future__ import annotations
import asyncio
import logging
import threading
from types import ModuleType
from typing import Any
from importlib import import_module
from collections.abc import Iterator

import pytest

import reachy_companion.main as main_mod
import reachy_companion.mcp_servers as mcp_servers
from reachy_companion.mcp_client import RemoteMcpToolClient, RemoteMcpServerConfig
from reachy_companion.mcp_servers import load_mcp_servers, register_mcp_tools


MCP_ENV_VARS = ("NOTION_MCP_URL", "NOTION_MCP_TOKEN")


def core_tools() -> ModuleType:
    """Resolve core_tools at call time.

    Other test modules pop it from sys.modules (test_external_loading.py:23,
    test_tool_space_runtime.py:34), so a module-level import here could end up
    pointing at a stale object while mcp_servers imports the live one.
    """
    return import_module("reachy_companion.tools.core_tools")


@pytest.fixture(autouse=True)
def clean_mcp_env_and_seam(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate the module-level extra-tools seam and the MCP env surface per test."""
    for name in MCP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    core_tools().EXTRA_TOOLS.clear()
    yield
    module = core_tools()
    module.EXTRA_TOOLS.clear()
    # Drop any registry that still carries this test's seam tools.
    module.initialize_tools(force=True)


class FakeSpec:
    """Mirror the RemoteToolSpec fields RemoteMcpTool is built from (mcp_client.py:183-211)."""

    server_alias = "notion"
    remote_name = "search_pages"
    namespaced_name = "notion__search_pages"
    description = "search notion"
    parameters_schema = {"type": "object", "properties": {}}


class FakeClient:
    """Stand-in for RemoteMcpToolClient that discovers one tool without a network call."""

    instances: list[Any] = []

    def __init__(self, server: Any, known_tools: Any = ()) -> None:
        """Record the config and seeded tool cache the caller built for this server."""
        self.server = server
        self.known_tools = list(known_tools)
        FakeClient.instances.append(self)

    async def list_tool_specs(self) -> list[FakeSpec]:
        """Return a single fake remote tool spec."""
        return [FakeSpec()]


def test_empty_env_yields_no_servers() -> None:
    """An unconfigured environment yields no MCP servers at all."""
    assert load_mcp_servers() == []


def test_notion_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured URL/token pair produces one authenticated server config."""
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")
    (srv,) = load_mcp_servers()
    assert srv.alias == "notion"
    assert srv.url == "https://mcp.notion.com/mcp"
    assert srv.headers["Authorization"] == "Bearer secret"
    # RemoteMcpServerConfig requires positive timeouts; we pass its defaults explicitly.
    assert srv.request_timeout_s == 10.0
    assert srv.tool_timeout_s == 30.0


def test_invalid_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plain http outside localhost is refused."""
    monkeypatch.setenv("NOTION_MCP_URL", "http://not-https.example.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "x")
    with pytest.raises(ValueError):
        load_mcp_servers()


def test_localhost_http_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_http_mcp_url permits plain http on localhost only (mcp_client.py:83-85)."""
    monkeypatch.setenv("NOTION_MCP_URL", "http://localhost:3333/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "x")
    (srv,) = load_mcp_servers()
    assert srv.url == "http://localhost:3333/mcp"


def test_missing_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL without its token is a configuration error, not a silent skip."""
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    with pytest.raises(ValueError):
        load_mcp_servers()


@pytest.mark.asyncio
async def test_register_discovers_and_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovered tools are registered through the seam and survive a registry rebuild."""
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")
    monkeypatch.setattr(FakeClient, "instances", [])
    monkeypatch.setattr(mcp_servers, "RemoteMcpToolClient", FakeClient)

    names = await register_mcp_tools()
    assert names == ["notion__search_pages"]

    # Discovery and live calls get separate clients: _session sets the httpx
    # timeout to max(request, tool) (mcp_client.py:357), so a shared config would
    # let the 30s tool budget become the discovery budget.
    discovery_client, live_client = FakeClient.instances
    assert discovery_client.server.request_timeout_s == 8.0
    assert discovery_client.server.tool_timeout_s == 8.0
    assert live_client.server.request_timeout_s == 10.0
    assert live_client.server.tool_timeout_s == 30.0
    assert [client.server.alias for client in FakeClient.instances] == ["notion", "notion"]
    # The live client is seeded, so the first real tool call needs no re-discovery.
    assert [spec.namespaced_name for spec in live_client.known_tools] == ["notion__search_pages"]

    module = core_tools()
    registered = module.EXTRA_TOOLS["notion__search_pages"]
    assert isinstance(registered, module.RemoteMcpTool)
    # plan rev 3 B2: the wrap point appends one lead-in policy sentence to every remote tool.
    assert registered.description.startswith("search notion ")
    assert registered.description.endswith("then call this tool.")
    assert registered.parameters_schema == {"type": "object", "properties": {}}
    assert registered._client is live_client
    # call_tool resolves by the namespaced name (mcp_client.py:332-341), exactly
    # like the installed-Space path passes client_tool_name=spec.namespaced_name
    # (tool_spaces.py:475).
    assert registered._client_tool_name == "notion__search_pages"

    # The seam must SURVIVE a registry rebuild (initialize_tools reconstructs ALL_TOOLS):
    module.initialize_tools(force=True)
    assert any(tool.name == "notion__search_pages" for tool in module.get_tools().values())
    assert any(spec["name"] == "notion__search_pages" for spec in module.get_tool_specs())


@pytest.mark.asyncio
async def test_registered_tool_is_visible_without_an_explicit_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registration after the registry was already built still reaches get_tool_specs()."""
    module = core_tools()
    module.initialize_tools(force=True)
    assert not any(spec["name"] == "notion__search_pages" for spec in module.get_tool_specs())

    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")
    monkeypatch.setattr(FakeClient, "instances", [])
    monkeypatch.setattr(mcp_servers, "RemoteMcpToolClient", FakeClient)

    assert await register_mcp_tools() == ["notion__search_pages"]
    assert any(spec["name"] == "notion__search_pages" for spec in module.get_tool_specs())


@pytest.mark.asyncio
async def test_discovery_failure_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead/unauthorized MCP server must not prevent app startup (Codex R3-4)."""
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "bad")

    attempts: list[int] = []
    slept: list[float] = []

    class FailingClient:
        def __init__(self, server: Any, known_tools: Any = ()) -> None:
            pass

        async def list_tool_specs(self) -> list[FakeSpec]:
            attempts.append(1)
            raise RuntimeError("401 unauthorized")

    async def no_backoff(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(mcp_servers, "RemoteMcpToolClient", FailingClient)
    monkeypatch.setattr(mcp_servers, "_backoff", no_backoff)

    assert await register_mcp_tools() == []  # skipped, not raised
    assert len(attempts) == 2  # bounded retry
    assert slept == [2.0]  # one backoff between the two attempts
    assert core_tools().EXTRA_TOOLS == {}


@pytest.mark.asyncio
async def test_transient_failure_recovers_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second discovery attempt is what actually registers a flaky server's tools."""
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")

    class FlakyClient:
        def __init__(self, server: Any, known_tools: Any = ()) -> None:
            self.calls = 0

        async def list_tool_specs(self) -> list[FakeSpec]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient transport error")
            return [FakeSpec()]

    async def no_backoff(seconds: float) -> None:
        return None

    monkeypatch.setattr(mcp_servers, "RemoteMcpToolClient", FlakyClient)
    monkeypatch.setattr(mcp_servers, "_backoff", no_backoff)

    assert await register_mcp_tools() == ["notion__search_pages"]


@pytest.mark.asyncio
async def test_bad_config_does_not_break_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_mcp_servers() raises on malformed config; register_mcp_tools() must not."""
    monkeypatch.setenv("NOTION_MCP_URL", "http://not-https.example.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "x")
    assert await register_mcp_tools() == []


@pytest.mark.asyncio
async def test_one_malformed_server_does_not_disable_the_others(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Config errors are isolated per server: a bad entry costs only its own tools.

    The module's contract is "never raises, degrade and skip" (:100-106). Loading
    every server's config in one shot broke that at the granularity that matters:
    one malformed URL aborted discovery for every other configured server too.
    """
    monkeypatch.setattr(
        mcp_servers,
        "_SERVER_ENV",
        (("broken", "BROKEN_MCP_URL", "BROKEN_MCP_TOKEN"), ("notion", "NOTION_MCP_URL", "NOTION_MCP_TOKEN")),
    )
    # Plain http off localhost: RemoteMcpServerConfig.__post_init__ rejects it.
    monkeypatch.setenv("BROKEN_MCP_URL", "http://not-https.example.com/mcp")
    monkeypatch.setenv("BROKEN_MCP_TOKEN", "x")
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")
    monkeypatch.setattr(FakeClient, "instances", [])
    monkeypatch.setattr(mcp_servers, "RemoteMcpToolClient", FakeClient)

    with caplog.at_level(logging.WARNING):
        names = await register_mcp_tools()

    assert names == ["notion__search_pages"]
    assert "notion__search_pages" in core_tools().EXTRA_TOOLS
    assert "broken" in caplog.text
    # Only the healthy server was ever contacted (discovery + live client).
    assert [client.server.alias for client in FakeClient.instances] == ["notion", "notion"]


def test_seam_tool_colliding_with_a_builtin_is_dropped_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    """A colliding seam name must degrade to a warning, never brick startup.

    initialize_tools() failing would exit the app at main.py's `sys.exit(1)`, so an
    MCP alias that shadows a local tool must lose quietly instead.
    """
    module = core_tools()
    module.initialize_tools(force=True)
    builtin_type = type(module.get_tools()["camera"])

    client = RemoteMcpToolClient(RemoteMcpServerConfig(alias="notion", url="https://mcp.notion.com/mcp"))
    module.register_extra_tool(
        module.RemoteMcpTool(
            slug="notion",
            name="camera",
            description="impostor",
            parameters_schema={"type": "object", "properties": {}},
            client_tool_name="notion__camera",
            client=client,
        )
    )

    with caplog.at_level(logging.WARNING):
        module.initialize_tools(force=True)

    # initialize_tools succeeded (no RuntimeError, no exit) and the built-in won.
    assert "collides with an already-registered tool" in caplog.text
    surviving = module.get_tools()["camera"]
    assert isinstance(surviving, builtin_type)
    assert not isinstance(surviving, module.RemoteMcpTool)
    assert surviving.description != "impostor"


def test_wrapper_registers_and_logs_discovered_tools(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The startup wrapper reports what it registered, and the tool reaches the session specs."""
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")
    monkeypatch.setattr(FakeClient, "instances", [])
    monkeypatch.setattr(mcp_servers, "RemoteMcpToolClient", FakeClient)

    with caplog.at_level(logging.INFO):
        names = main_mod._discover_remote_mcp_tools(logging.getLogger("test_mcp_servers"))

    assert names == ["notion__search_pages"]
    assert "Registered 1 remote MCP tool(s)" in caplog.text
    assert any(spec["name"] == "notion__search_pages" for spec in core_tools().get_tool_specs())


def test_wrapper_abandons_discovery_that_overruns_its_budget(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A hung MCP server must not block startup nor keep the interpreter alive."""
    release = threading.Event()

    async def hanging_register() -> list[str]:
        await asyncio.to_thread(release.wait, 30.0)
        return ["notion__search_pages"]

    monkeypatch.setattr(mcp_servers, "register_mcp_tools", hanging_register)

    with caplog.at_level(logging.WARNING):
        names = main_mod._discover_remote_mcp_tools(logging.getLogger("test_mcp_servers"), budget_s=0.2)

    assert names == []  # startup proceeds without MCP tools
    assert "exceeded its 0.2s startup budget" in caplog.text

    worker = next(thread for thread in threading.enumerate() if thread.name == "mcp-discovery")
    assert worker.is_alive()  # the join really did time out
    assert worker.daemon is True  # ...and it cannot hold the interpreter open at exit

    release.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()


def test_register_extra_tool_rejects_duplicates() -> None:
    """The seam refuses to shadow a name it already holds."""
    module = core_tools()
    client = RemoteMcpToolClient(RemoteMcpServerConfig(alias="notion", url="https://mcp.notion.com/mcp"))
    tool = module.RemoteMcpTool(
        slug="notion",
        name="notion__search_pages",
        description="search notion",
        parameters_schema={"type": "object", "properties": {}},
        client_tool_name="notion__search_pages",
        client=client,
    )
    module.register_extra_tool(tool)
    with pytest.raises(ValueError):
        module.register_extra_tool(tool)
