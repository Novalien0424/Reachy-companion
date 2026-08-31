"""Shared dispatch for action-enum tool families (2026-08-31 tool diet).

Six CRUD/action families went from 18 separately registered tools to 6, because
41 tools at the start of a turn is well past OpenAI's own "aim for fewer than 20
functions available at the start of a turn" and inside the measured degradation
zone (docs/research-mini-tool-calling-2026-08.md §A1).

The consolidation is a SCHEMA refactor, not a behavior change. Each family
validates its action *and nothing else*, then calls the ORIGINAL `Tool` instance
unchanged -- so every confirmation gate (`hanova.confirm`), every
`settings.tool_status(self.name)` prerequisite check, every retry and every
error string still comes from the module that always produced it, and every
existing test of those modules still tests shipped code.

The sub-tool modules stay on disk and simply leave the profile's
`default_tools`: the registry loader imports one module per listed name and
picks up only the `Tool` subclasses *defined* there (`core_tools.py:256-274`),
so a family module importing its delegates registers nothing extra.
"""

from __future__ import annotations
import logging
from typing import Any, Mapping

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


async def dispatch_family(
    *,
    family: str,
    action: Any,
    actions: Mapping[str, Tool],
    deps: ToolDependencies,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Route one family call to the tool that has always handled it.

    The ONLY validation here is the action name, because that is the only thing
    the delegates cannot check -- they never see it. Argument validation stays
    where it has always lived: inside each delegate, *after* its
    `settings.tool_status(self.name)` prerequisite check, with its own error
    string. Checking arguments here would reorder those two answers and reword
    one of them, which is a behavior change, and this refactor is not allowed to
    be one (Codex round 1, P2-5).

    Everything but `action` is forwarded untouched, including unknown extra
    keys: every delegate reads its arguments with `kwargs.get`, so a stray key
    is inert and a dropped one would not be.
    """
    if not isinstance(action, str) or action not in actions:
        return {"error": f"{family}: action must be one of {sorted(actions)}"}
    forwarded = {key: value for key, value in kwargs.items() if key != "action" and value is not None}
    logger.info("Tool call: %s action=%s", family, action)
    return await actions[action](deps, **forwarded)
