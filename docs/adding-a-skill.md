# Adding a Skill

A "Skill" is a tool the model can call mid-conversation. Adding one is **one new
file plus one line in a profile** — the conversational core (`huggingface_realtime.py`,
`conversation_handler.py`, the dispatcher) is never touched. `home_control` was added
exactly this way; read `reachy_companion/src/reachy_companion/tools/home_control.py`
alongside this guide.

All paths below are relative to `reachy_companion/`.

## Step 1 — create `src/reachy_companion/tools/<name>.py`

**The filename is the contract.** The loader imports `reachy_companion.tools.<tool_name>`
where `<tool_name>` is the string listed in the profile
(`tools/core_tools.py:398`), then picks up every non-abstract `Tool` subclass
*defined in that module* (`core_tools.py:207-225`). So the file must be named after the
tool, and `Tool.name` must match the filename — otherwise the profile enables a name
the registry never produces.

Minimal complete skeleton (trimmed from `home_control.py`):

```python
"""One-line summary of what this Skill does. Filename == Tool.name."""

import os
import logging
from typing import Any, Dict

from reachy_companion.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)


class MySkill(Tool):
    """Docstring: ruff lints this package under pydocstyle."""

    name = "my_skill"                              # == the filename, == the profile entry
    description = "What it does and when the model should call it."
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"target": {"type": "string", "description": "..."}},
        "required": ["target"],
    }
    # needs_response = False   # opt out of the spoken follow-up (see below)

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Run the Skill. Must not raise."""
        target = str(kwargs.get("target", ""))
        if not target:
            return {"ok": False, "error": "target is required"}
        api_key = (os.getenv("MY_SKILL_KEY") or "").strip()
        if not api_key:
            return {"ok": False, "error": "MY_SKILL_KEY is not set"}
        logger.info("Tool call: my_skill target=%s", target)
        return {"ok": True, "target": target}
```

The `Tool` ABC (`core_tools.py:57-88`) requires exactly `name`, `description`,
`parameters_schema` and `async __call__`. That is the whole surface.

## Step 2 — enable it in the locked profile

Add the tool name to `default_tools` in
`profiles/_reachy_companion_locked_profile/profile.md` (the app ships as a single
persona; `config.LOCKED_PROFILE` overrides `REACHY_MINI_CUSTOM_PROFILE`):

```toml
default_tools = [
  "camera",
  "play_emotion",
  "home_control",   # <- one line
]
```

If the model needs a routing hint ("when the user asks X, use Y"), add one sentence to
the instruction body below the `+++` block. `home_control` has one.

## Step 3 — restart the app

The registry is built once, cached behind a signature of profile + directories
(`core_tools.py:421-470`), and only rebuilt on `initialize_tools(force=True)`.
A new file is not hot-loaded.

## Rules the loader and the runtime enforce

- **Never raise — return an error.** Convention is `{"ok": False, "error": "..."}`, plus
  any field that helps the model self-correct (`home_control.py:91` echoes the known
  device list). A raised exception is caught by the dispatcher and turned into
  `{"error": "TypeError: ..."}` (`core_tools.py:510-513`) — a differently shaped payload
  with no `ok` key. Degrade, don't die.
- **`__init__` must never raise either.** Tools are instantiated at
  `core_tools.py:286`, *outside* the `try/except` that guards module loading
  (`:412-416`); an exception there propagates out of `initialize_tools()` and
  `main.py:336-338` exits the process. `home_control._entities()` is the worked example:
  a typo in the `HA_ENTITIES` JSON logs a warning and yields an empty allowlist instead
  of bricking startup with a `json.decoder` traceback.
- **Read env in `__init__` if you want it in the schema.** `main.py:156-166` loads the
  instance `.env` *before* `initialize_tools()` (`main.py:335`), so construction-time
  `os.getenv` is safe. `home_control` builds its `target` enum and description this way.
  This works because `Tool.spec()` reads `self.` (`core_tools.py:76-83`) — instance
  attributes shadow the class defaults, and nothing anywhere reads `type(tool).x`.
  A later env change needs an `initialize_tools(force=True)` rebuild.
- **`async` means async — do not block.** Tools run as `asyncio.create_task` on the
  realtime session's own loop (`background_tool_manager.py:157`, started at
  `huggingface_realtime.py:881`). A blocking `requests.get` or `time.sleep` stalls audio
  and conversation. Use `httpx.AsyncClient` (already a direct dependency), `await`
  everything, and wrap unavoidably synchronous work in `asyncio.to_thread`
  (`tools/go_to_sleep.py:33`).
- **`needs_response`** (class var, defaults `True`) — leave it `True` when the model
  should speak a confirmation; set `False` for tools whose effect is physical and
  self-evident (`play_emotion`, `move_head`). Consumed at
  `huggingface_realtime.py:685`; errors always trigger a response regardless.
- **Names must be unique.** A duplicate `Tool.name` raises at registry build
  (`core_tools.py:295-300`).

## Verify it worked

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q   # green baseline; 30 skips are expected
```

Use the project-root venv — a bare `python` picks up Anaconda and fails collection.

Then prove the tool actually reaches the model's session config, which is what
`get_tool_specs()` returns (`core_tools.py:476-481`):

```powershell
..\.venv\Scripts\python.exe -c "from reachy_companion.tools.core_tools import get_tool_specs; print(sorted(s['name'] for s in get_tool_specs()))"
```

Your tool name must appear in that list. Pin it with a test — see
`tests/test_home_control.py::test_locked_profile_registers_the_skill_by_filename`,
which rebuilds the real registry and asserts the name is in `get_tool_specs()`.
Also check the startup log line `tool registered: <name> - <description>`
(`core_tools.py:472-473`).

## The other route: remote MCP tools

A Skill that already exists as a remote MCP server needs no Python file at all.
`mcp_servers.py` discovers any HTTP(S) MCP endpoint declared in the environment at
startup and registers each of its tools through the persistent `EXTRA_TOOLS` seam
(`core_tools.py:106-119`), which survives every registry rebuild. Server aliases and
their env vars live in `mcp_servers.py:39` — currently
`NOTION_MCP_URL` / `NOTION_MCP_TOKEN`, documented in `.env.example`. Tool names are
namespaced by alias (`notion__search_pages`). Discovery is bounded and never raises:
an unreachable or unauthorized server is logged and skipped, and the app starts with
its local tools. Adding another server is a new tuple in `_SERVER_ENV` plus two env vars
— still no change to the conversational core.
