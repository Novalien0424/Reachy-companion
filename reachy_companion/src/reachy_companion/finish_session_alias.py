"""`go_to_sleep` under an in-distribution second name, for a measured A/B.

The common-tool-name list (`finish_session` among them) is documented for
`gpt-realtime-1.5` only; transfer to 2.x is untested, and a raw rename would
touch the profile tool lists, the toolboxes, the record allowlist, the tests and
the docs. So this is an ALIAS with controlled exposure: the same implementation
under a second name, registered only when `INSTRUCTING_FINISH_SESSION_ALIAS` is
set. `EXTRA_TOOLS` members are never hidden by `session_tool_exclusions` in any
mode and bypass the profile allowlist, so exposure costs one flag and no list
edits.

Deliberately NOT in `tools/`: that directory's modules are loaded by name
(filename == `Tool.name`) and importing `GoToSleep` into one of them would offer
the duplicate-name guard a second class called `go_to_sleep`.

What to measure before any registered-name change (spec section 1): sleep-tool
SELECTION rate on genuine end-of-visit requests, and false positives on sleepy
small talk and idle turns.
"""

from __future__ import annotations
import logging
from typing import ClassVar

from reachy_companion.audio.envparse import env_bool
from reachy_companion.tools.core_tools import register_extra_tool
from reachy_companion.tools.go_to_sleep import GoToSleep


logger = logging.getLogger(__name__)

ALIAS_ENV = "INSTRUCTING_FINISH_SESSION_ALIAS"


class FinishSession(GoToSleep):
    """The sleep tool under an in-distribution name; behaviour is identical."""

    # Never picked up by a module scan: exposure is the flag's decision alone.
    _auto_register: ClassVar[bool] = False

    name = "finish_session"


def register_finish_session_alias() -> bool:
    """Register the alias when the A/B flag is on. Returns whether it was added."""
    if not env_bool(ALIAS_ENV, False):
        return False
    try:
        register_extra_tool(FinishSession())
    except ValueError:
        # Already registered earlier in this process; nothing to do.
        return False
    logger.info("A/B: the finish_session alias is exposed alongside go_to_sleep")
    return True
