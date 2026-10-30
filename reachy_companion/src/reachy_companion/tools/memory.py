"""Long-term facts about people, as one action-enum tool. Filename == Tool.name.

Façade over upstream's `remember` (`save`) and `forget` (`forget`), which keep
their modules and their own argument checks (mini context diet, 2026-10-30).
See `tool_family.py` for why. Neither delegate opts out of the spoken
follow-up, so neither does the family.

Not to be confused with `reachy_companion.memory`, the fact store both
delegates write to.
"""

from __future__ import annotations
from typing import Any, Dict, Mapping, ClassVar

from reachy_companion.tools.forget import Forget
from reachy_companion.tools.remember import Remember
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.tool_family import dispatch_family


class Memory(Tool):
    """Save or forget one long-term fact through one tool."""

    name = "memory"
    ACTIONS: ClassVar[Mapping[str, Tool]] = {
        "save": Remember(),
        "forget": Forget(),
    }
    description = (
        "Long-term facts about the people you talk with, kept across visits. `save` stores one short "
        "fact the speaker shared: plans and ongoing things first (an exam, a trip, a song they are "
        "writing), then preferences, hobbies and people they mention; if you recognized the speaker, it "
        "is saved about them. Save quietly and reply naturally, without announcing that you will "
        "remember. `forget` removes a fact they ask you to forget or that is no longer true; to correct "
        "one, `save` the right fact. Do NOT use when: the detail only matters now, someone else said it "
        "about them, or it is sensitive (passwords, addresses, payment details, health diagnoses), "
        "because those don't belong in long-term memory; or it is about a face — that is face."
    )
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "forget"],
            },
            "fact": {
                "type": "string",
                "description": 'For save: one short third-person fact, such as "Has a dog named Mochi". One fact per call.',
            },
            "query": {
                "type": "string",
                "description": "For forget: a short phrase found in the fact to remove.",
            },
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Forward one memory action to the tool that has always handled it."""
        return await dispatch_family(
            family=self.name,
            action=kwargs.get("action"),
            actions=self.ACTIONS,
            deps=deps,
            kwargs=kwargs,
        )
