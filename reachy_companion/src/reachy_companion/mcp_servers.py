"""Generic remote-MCP discovery and registration (D-004).

Upstream can only install MCP tool servers that are Gradio Hugging Face Spaces
(`tool_spaces.py`). This module bypasses that restriction: any HTTP(S) MCP
endpoint declared in the environment is discovered once at startup and its tools
are registered through the persistent extra-tools seam in
`tools.core_tools` (`EXTRA_TOOLS` / `register_extra_tool`). Both
`RemoteMcpToolClient` and `RemoteMcpTool` are reused unchanged.
"""

from __future__ import annotations
import os
import asyncio
import logging
from dataclasses import replace

from reachy_companion.mcp_client import RemoteToolSpec, RemoteMcpToolClient, RemoteMcpServerConfig


logger = logging.getLogger(__name__)

# A dead or unauthorized MCP server must never block startup, so discovery is
# bounded: two attempts with one backoff, then the server is skipped.
_DISCOVERY_ATTEMPTS = 2
_DISCOVERY_BACKOFF_S = 2.0

# Discovery runs on its own config. RemoteMcpToolClient._session sets the httpx
# timeout to max(request_timeout_s, tool_timeout_s) (mcp_client.py:357), so the
# live 30 s tool budget would silently become the discovery budget too. Pinning
# both to _DISCOVERY_TIMEOUT_S keeps a dead server to 2 x 8 s + 2 s backoff = 18 s,
# inside the startup budget main.py allows, while live tool calls keep their own
# config below.
_DISCOVERY_TIMEOUT_S = 8.0
_REQUEST_TIMEOUT_S = 10.0
_TOOL_TIMEOUT_S = 30.0

# (server alias, URL env var, bearer-token env var). The alias namespaces the
# remote tool names (`notion__search_pages`), so it must be a bare identifier.
_SERVER_ENV: tuple[tuple[str, str, str], ...] = (("notion", "NOTION_MCP_URL", "NOTION_MCP_TOKEN"),)


def _load_server(alias: str, url_var: str, token_var: str) -> RemoteMcpServerConfig | None:
    """Build one server's config from the environment, or None when it is unconfigured.

    An unset URL means "server not configured". A URL that *is* set but is
    unusable (non-HTTPS off localhost, missing token) raises ValueError so the
    misconfiguration is loud; the caller decides how far that failure travels.
    """
    url = (os.getenv(url_var) or "").strip()
    if not url:
        return None

    token = (os.getenv(token_var) or "").strip()
    if not token:
        raise ValueError(f"{url_var} is set but {token_var} is empty; cannot authenticate to MCP server '{alias}'.")

    # RemoteMcpServerConfig.__post_init__ runs validate_http_mcp_url, which
    # rejects plain http outside localhost.
    return RemoteMcpServerConfig(
        alias=alias,
        url=url,
        headers={"Authorization": f"Bearer {token}"},
        request_timeout_s=_REQUEST_TIMEOUT_S,
        tool_timeout_s=_TOOL_TIMEOUT_S,
    )


def load_mcp_servers() -> list[RemoteMcpServerConfig]:
    """Build remote MCP server configs from the environment.

    Strict by design: the first unusable entry raises ValueError rather than
    being silently ignored. `register_mcp_tools` does not use this — it loads
    each server separately so one bad entry cannot disable the rest.
    """
    servers: list[RemoteMcpServerConfig] = []
    for alias, url_var, token_var in _SERVER_ENV:
        server = _load_server(alias, url_var, token_var)
        if server is not None:
            servers.append(server)
    return servers


async def _backoff(seconds: float) -> None:
    """Wait between discovery attempts.

    A module-level seam so tests can shorten the backoff without patching the
    global `asyncio.sleep`, which the event loop and every other coroutine share.
    """
    await asyncio.sleep(seconds)


async def _discover_with_retry(client: RemoteMcpToolClient, alias: str) -> list[RemoteToolSpec] | None:
    """List the server's tools, retrying once; return None once the budget is spent."""
    for attempt in range(1, _DISCOVERY_ATTEMPTS + 1):
        try:
            return await client.list_tool_specs()
        except Exception as exc:  # auth/transport/dependency — degrade, don't die
            logger.warning(
                "MCP server '%s' discovery attempt %d/%d failed: %s", alias, attempt, _DISCOVERY_ATTEMPTS, exc
            )
            if attempt < _DISCOVERY_ATTEMPTS:
                await _backoff(_DISCOVERY_BACKOFF_S)
    return None


async def register_mcp_tools() -> list[str]:
    """Discover every configured MCP server's tools and register them.

    Never raises. The conversation app must start with its local tools even when
    an MCP server is misconfigured, unreachable or rejects our credentials, so
    every failure here is logged and the server is skipped. Returns the
    namespaced names of the tools that were registered.
    """
    # Imported lazily: core_tools pulls in the robot SDK, and this module is
    # imported from the startup path before the registry exists.
    from reachy_companion.tools.core_tools import RemoteMcpTool, register_extra_tool

    registered_names: list[str] = []
    for alias, url_var, token_var in _SERVER_ENV:
        # Config is loaded per server, not in one batch: a single malformed entry
        # must cost only its own tools, never every other server's.
        try:
            server = _load_server(alias, url_var, token_var)
        except Exception as exc:
            logger.error("MCP server '%s' is misconfigured and is skipped: %s", alias, exc)
            continue
        if server is None:
            continue

        # Discovery gets a short-timeout copy of the config; the tools then get a
        # client on the real config, seeded with what we just discovered so the
        # first live call does not have to re-discover (same pattern as
        # tool_spaces.build_remote_client's cached_tools).
        discovery_server = replace(server, request_timeout_s=_DISCOVERY_TIMEOUT_S, tool_timeout_s=_DISCOVERY_TIMEOUT_S)
        specs = await _discover_with_retry(RemoteMcpToolClient(discovery_server), server.alias)
        if specs is None:
            logger.error("MCP server '%s' is disabled for this session; its tools are unavailable.", server.alias)
            continue

        client = RemoteMcpToolClient(server, known_tools=specs)
        registered_here = 0
        for spec in specs:
            # Same construction as _resolve_remote_tools (core_tools.py) and the
            # installed-Space path (tool_spaces.py): call_tool resolves by the
            # namespaced name, so that is what client_tool_name must carry.
            tool = RemoteMcpTool(
                slug=server.alias,
                name=spec.namespaced_name,
                description=spec.description,
                parameters_schema=dict(spec.parameters_schema),
                client_tool_name=spec.namespaced_name,
                client=client,
            )
            try:
                register_extra_tool(tool)
            except ValueError as exc:
                logger.warning("Skipping remote MCP tool '%s': %s", spec.namespaced_name, exc)
                continue
            registered_names.append(spec.namespaced_name)
            registered_here += 1

        logger.info(
            "MCP server '%s' contributed %d of %d discovered tool(s).", server.alias, registered_here, len(specs)
        )

    return registered_names
