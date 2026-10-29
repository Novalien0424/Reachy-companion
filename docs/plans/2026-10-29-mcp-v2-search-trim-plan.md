# MCP v2 client and trimmed MCP tool results — plan (2026-10-29)

Operator request (2026-10-29): "trim the search result, and upgrade the MCP
v2 client". Both touch the remote-MCP path (`mcp_client.py`,
`tools/core_tools.py:RemoteMcpTool`), so they ship together as v1.26.0.

## M1 — MCP v2 client (port of upstream `7632260` + `cd07771` + tests `f7f863f`)

Our `mcp_client.py` is byte-identical to upstream's pre-v2 file (plus
`dd08179`, already ported), so the upstream diff applies as is:
`ClientSession` + `initialize()` → `Client` (mode `auto`: probes
`server/discover`, falls back to `initialize`); snake_case reads
(`input_schema`, `is_error`, `structured_content`, `next_cursor`);
`read_timeout_seconds` as float; transport on `httpx2`; the SDK's
`MCPError(-32001)` counts as a timeout. Pins as upstream HEAD:
`mcp>=2.0,<3`, `mcp-types>=2.0,<3`, `httpx2>=2.5.0` (lock: mcp 2.3.0,
mcp-types 2.3.0, httpx2 2.16.0). Plain `httpx` stays (HF hub, our code).

Why now: the shared apps venv holds Pollen's conversation app too, and its
current release (1.1.x) requires `mcp>=2,<3`; our `<2` bound (D-008) would
conflict with any update of it. The robot already carries `httpx2` 2.10.0
and `mcp-types` 2.0.0 from the first install (2026-08-16), unused.

Verify: upstream's migrated tests (snake_case construction, the two rename
regression tests, the integration suite rebuilt on `mcp.server.MCPServer` —
it was silently skipping on v2), our dd08179 tests and
`test_background_tool_manager` moved to `mcp_types` and snake_case. Gate:
suite, ruff, mypy. On robot: `pip check` clean apart from the pre-existing
mediapipe/protobuf line; startup log lists the search tool with its schema;
a live search returns results (demo 4).

## M2 — model-facing MCP results without duplicates (ours; rung 1, tool return)

Captured live from Pollen's search Space (2026-10-29): the payload the model
receives carries the result string twice (`content_blocks[0].text` and
`text`) plus `server_alias`, `remote_tool_name`, `namespaced_tool_name` and
`tool_space_slug`, all of which restate the tool the model just called.

`RemoteMcpTool.__call__` (the model boundary; the client's envelope stays
complete for its own callers and tests) returns:
- `status`; `error` on failure.
- `text` when present — unless `structured_content` is present and `text`
  is exactly its JSON serialization (the MCP spec's back-compat text block),
  then `structured_content` alone; on failure `text` is dropped when it
  equals `error`.
- `structured_content` when present.
- `content_blocks` only for non-text blocks (text blocks are all in `text`);
  the key is omitted when none remain.

No information the model could use is removed, so this ships on by default
(same reasoning as L2 `ensure_ascii=False`, latency plan self-review 2).

Verify: unit tests for each rule (text-only, structured duplicate,
structured distinct, error, non-text block kept); the background manager
still classifies `error` as FAILED. On robot: demo 4 answer still carries
today's facts; the L1 `Response usage:` input tokens of a search turn drop.

## Self-review log
1. *Reuse first?* M1 is a straight upstream port; M2 is "ours to build"
   (MCP wiring) at the adapter we own, keeping upstream's `mcp_client.py`
   identical to HEAD so future ports stay clean. Accepted.
2. *New/major dependency approval?* Operator asked for the v2 client
   2026-10-29; upstream (pre-approved) ships the same pins. Accepted.
3. *Old conversation app 1.0.0 in the shared venv* declares `mcp>=1.27.1`
   with v1 reads — under mcp 2 its remote tools degrade (upstream fixed this
   in 1.0.1+). It is not the running app; recorded, operator can update it
   from the dashboard. Accepted.
4. *httpx2 brings `truststore`*: does it verify against the OS store or
   certifi, given the emSign CA removal that made us pin the OpenAI
   websocket to certifi (v1.24.0)? Read `httpcore2/_ssl.py`: on Linux it
   loads the system store (`/etc/ssl/certs`), and the robot's store
   (`ca-certificates 20250419`) still holds four emSign roots where certifi
   2026.10.23 keeps one. Resolved: `_connected_client` passes
   `verify=ssl.create_default_context(cafile=certifi.where())` — the one
   deviation from upstream's file, commented as ours.
5. *Does anything read the dropped metadata?* `grep` over `src/` and
   `static/`: only `core_tools.py` writes `namespaced_tool_name` /
   `tool_space_slug`; nothing reads them. Tests that pinned them are updated.
6. *Could the model lose provenance it cites?* The tool name already names
   the source; URLs live inside the result text. Accepted.
