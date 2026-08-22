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
(`tools/core_tools.py:403`), then picks up every non-abstract `Tool` subclass
*defined in that module* (`core_tools.py:212-230`). So the file must be named after the
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

The `Tool` ABC (`core_tools.py:62-93`) requires exactly `name`, `description`,
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
(`core_tools.py:426-478`), and only rebuilt on `initialize_tools(force=True)`.
A new file is not hot-loaded.

## Rules the loader and the runtime enforce

- **Never raise — return an error.** Convention is `{"ok": False, "error": "..."}`, plus
  any field that helps the model self-correct (`home_control.py:91` echoes the known
  device list). A raised exception is caught by the dispatcher and turned into
  `{"error": "TypeError: ..."}` (`core_tools.py:518-521`) — a differently shaped payload
  with no `ok` key. Degrade, don't die.
- **`__init__` must never raise either.** Tools are instantiated at
  `core_tools.py:291`, *outside* the `try/except` that guards module loading
  (`:404-421`); an exception there propagates out of `initialize_tools()` and
  `main.py:360-364` exits the process. `home_control._entities()` is the worked example:
  a typo in the `HA_ENTITIES` JSON logs a warning and yields an empty allowlist instead
  of bricking startup with a `json.decoder` traceback.
- **Read env in `__init__` if you want it in the schema.** `main.py:179-189` loads the
  instance `.env` *before* `initialize_tools()` (`main.py:361`), so construction-time
  `os.getenv` is safe. `home_control` builds its `target` enum and description this way.
  This works because `Tool.spec()` reads `self.` (`core_tools.py:81-88`) — instance
  attributes shadow the class defaults, and nothing anywhere reads `type(tool).x`.
  A later env change needs an `initialize_tools(force=True)` rebuild.
- **`async` means async — do not block.** Tools run as `asyncio.create_task` on the
  realtime session's own loop (`background_tool_manager.py:157`, started at
  `huggingface_realtime.py:830`). A blocking `requests.get` or `time.sleep` stalls audio
  and conversation. Use `httpx.AsyncClient` (already a direct dependency), `await`
  everything, and wrap unavoidably synchronous work in `asyncio.to_thread`
  (`tools/go_to_sleep.py:33`).
- **`needs_response`** (class var, defaults `True`) — leave it `True` when the model
  should speak a confirmation; set `False` for tools whose effect is physical and
  self-evident (`play_emotion`, `move_head`). Consumed at
  `huggingface_realtime.py:779-780`; errors always trigger a response regardless.
- **Names must be unique.** A duplicate `Tool.name` raises at registry build
  (`core_tools.py:300-305`).

## Verify it worked

```powershell
cd C:\Project\Reachy-mini\reachy_companion
..\.venv\Scripts\python.exe -m pytest -q   # green baseline; 30 skips are expected
```

Use the project-root venv — a bare `python` picks up Anaconda and fails collection.

Then prove the tool actually reaches the model's session config, which is what
`get_tool_specs()` returns (`core_tools.py:481-486`):

```powershell
..\.venv\Scripts\python.exe -c "from reachy_companion.tools.core_tools import get_tool_specs; print(sorted(s['name'] for s in get_tool_specs()))"
```

Your tool name must appear in that list. Pin it with a test — see
`tests/test_home_control.py::test_locked_profile_registers_the_skill_by_filename`,
which rebuilds the real registry and asserts the name is in `get_tool_specs()`.
Also check the startup log line `tool registered: <name> - <description>`
(`core_tools.py:477-478`).

## The other route: remote MCP tools

A Skill that already exists as a remote MCP server needs no Python file at all.
`mcp_servers.py` discovers any HTTP(S) MCP endpoint declared in the environment at
startup and registers each of its tools through the persistent `EXTRA_TOOLS` seam
(`core_tools.py:111-124`), which survives every registry rebuild. Server aliases and
their env vars live in `mcp_servers.py:39` — currently
`NOTION_MCP_URL` / `NOTION_MCP_TOKEN`, documented in `.env.example`. Tool names are
namespaced by alias (`notion__search_pages`). Discovery is bounded and never raises:
an unreachable or unauthorized server is logged and skipped, and the app starts with
its local tools. Adding another server is a new tuple in `_SERVER_ENV` plus two env vars
— still no change to the conversational core.

## The ported HomeAssistant-Nova families (D-018)

Twenty-two of the app's Skills are ports of the operator's `ha-actions` MCP
server, and they follow three extra conventions on top of everything above.
No single file demonstrates all three, and none can: the destructive tools are
all cloud tools, and a cloud tool is forbidden to touch the home-network probe
(convention 2 below, enforced in both directions by
`tests/test_hanova_integration.py`). Read two worked examples instead —
`src/reachy_companion/tools/calendar_delete.py` for conventions 1 and 3
(per-tool gating, then the full arm/claim/complete lifecycle), and
`src/reachy_companion/tools/play_video.py` for convention 2 (per-tool gating,
then the three-way home verdict).

**1. Per-tool gating, per-family reporting.** Every ported tool declares its own
prerequisites in `settings.TOOL_PREREQS` and starts with
`settings.tool_status(self.name)`; a tool that is not configured returns
`settings.unavailable(reason)`, where *reason* is the name of the first missing
**config key** — never its value. Families aggregate their tools into one
tri-state startup line: `hanova family notion: disabled
(HANOVA_NOTION_TOKEN)` / `partial (2/4 tools ready; ...)` / `enabled`. The app
boots green with none of this configured. `stop_music` is the one documented
exemption: it has no prerequisites, because a robot that cannot be silenced by
voice is a safety defect.

**2. House-bound tools check the network, and the verdict is tri-state.**
Anything that needs Home Assistant or the NAS calls `await home_state()` from
`home_net.py` first and branches **all three** verdicts:

```python
verdict = await home_state()
if verdict == AWAY:
    return away_from_home()
if verdict != HOME:
    return home_unknown()      # status: home_status_unknown -- and do NO work
```

`AWAY` requires **positive off-home routing evidence**: the robot's own address
outside every network in `HANOVA_HOME_NETWORKS`. A failed connection to Home
Assistant is not that — it is what an outage looks like from inside the house —
and neither is a 401, a 5xx, an HTTP timeout or a VPN-shaped route. All of those
are `UNKNOWN`, which returns `home_status_unknown` and performs nothing: telling
the user they are out of their own house on that evidence is a lie, and doing the
house action anyway is worse. Cloud tools and music must **not** call it; a
needless probe is pure latency. `tests/test_hanova_integration.py` enforces both
directions and the exact three-way shape.

**3. Destructive tools are gated in code, and the authorisation is spent on
success.** A gated tool called without `confirm: true` resolves the action,
parks it in `hanova/confirm.py`'s `GATE` with a 90 s TTL **stamped with the
current session epoch**, and returns
`{"status": "needs_confirmation", "summary": "<the exact action, in full>"}`.
The confirming call carries only `confirm: true` — the action fields are
optional in the schema so the model cannot mis-hear them a second time — and it
executes the **parked** payload. `GATE.claim()` takes it in flight and returns a
`PendingAction` carrying an opaque **`claim_id`**; every mutator requires that
id, so a call can only spend, retry or cancel *the authorisation it actually
holds*. Then: `GATE.complete(name, claim_id)` on success **and on a terminal
failure** (an auth error, a refused recipient, a validation error — the approved
action cannot succeed as approved, so it must be read back afresh),
`GATE.release(name, claim_id)` only on a **transient** failure so the user can
say "try again", and `GATE.abort()` when they say no. Re-arming a slot whose
action is mid-execution is refused with `action_in_flight`. A new realtime
session mints a new epoch, so nothing armed in one conversation can be confirmed
in another. The gate is never a prompt instruction alone.

`email_send` is the strictest case. Its `summary` carries every recipient (To
and CC), the subject and the **entire normalized body, verbatim** — not a
preview, not a digest of one. The digest that follows the body is only an
integrity token appended after it, never a substitute for reading it: two
messages sharing an opening line produced indistinguishable confirmations while
the sent mail differed. A body longer than `gmail_smtp.MAX_BODY_CHARS` (500) is
refused with `body_too_long` rather than summarised, and the persona is
instructed to read the body out in full rather than condense it.

**4. Nothing personal reaches a log line or an error string.** Ported tools log
through `hanova/redact.py`: counts, lengths, salted digests, HTTP statuses,
durations. Never a query, title, subject, recipient, id, path or URL, and never
a raw API error body — Google, Notion, Drive and SMTP all echo the request back
inside theirs. A tool's user-facing `error` is a fixed, speakable sentence, not
the upstream message. A module that logs nothing but fixed strings and counts
may skip the helper, but only as a named entry with its reason in
`_REDACT_EXEMPT` in `tests/test_hanova_integration.py` — the exemption list is a
deliberate act someone has to write down, and a second test re-reads every
exempt module's log lines to keep the claim honest. (Scope note: this covers the
ported tool surface. The framework's own model-visible tool-result logging is
unchanged by D-018.)

Adding a tool to one of these families is still one file plus one profile line;
it just also needs an entry in `TOOL_PREREQS`, possibly a home check, redacted
logging, and — if it destroys anything — a `confirm` parameter and the
claim/complete two-branch body.

### One deployment assumption the code cannot check

NAS path containment (`nas.validate_cast_path`, `HANOVA_NAS_SUBPATH` /
`HANOVA_NAS_CAST_SUBPATH`) is a **client-side check on the normalized path
string**: it refuses `..`, absolute paths and anything that does not resolve
inside the configured subtree. It cannot see the server side, and `smbprotocol`
follows symlinks and reparse points wherever the SMB server resolves them. So
the two configured subpaths must contain **no symlink or reparse point that
leads out of the subtree** — otherwise a path this check accepts can still open
a file elsewhere on the share. Treat that as a property of the operator's NAS
layout, verified when the share is set up, not as something the robot enforces.
