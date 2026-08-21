"""The self-destruct joke, on the standard confirmation gate (D-018, R2/R3).

Upstream used a bespoke `arm` / `confirm` / `abort` stage enum with its own
module-global timer (`server.py:2140-2165`). The *mechanism* here is the shared
`ConfirmationGate` -- one contract, one TTL, one place to audit -- but the
**wording stays upstream's in-character ritual** (review round 1, finding 17,
controller ruling).

Why the summary is not the generic one: the generic gate summary exists so a
user can hear exactly what irreversible thing is about to happen. Nothing
irreversible happens here -- it plays a sound -- and spelling the punchline out
destroys the only thing the tool is for. So the arm returns the countdown
ritual, the confirmation phrase is thematic, and `abort` is a real,
code-enforced path rather than a punchline.

The TTL is still `HANOVA_CONFIRM_TTL_S` (90 s) and it is still enforced by the
gate, not by the prompt.
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from reachy_companion.hanova import sfx, settings
from reachy_companion.hanova.confirm import GATE, confirmation_expired
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# In-character, and deliberately not an explanation (finding 17).
_ARM_SUMMARY = (
    "SELF-DESTRUCT SEQUENCE ARMED. Ninety seconds on the clock. "
    "Say 'authorise self-destruct' to commit, or 'abort self-destruct' to stand down."
)
_CLIP_TITLE = "self-destruct sequence"


class SelfDestruct(Tool):
    """Run the two-step self-destruct ritual on the robot's speaker."""

    name = "self_destruct"
    description = "Run the self-destruct sequence. 需要先確認或取消才會執行。"
    parameters_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "confirm": {
                "type": "boolean",
                "description": "Set true when the user authorises the sequence.",
            },
            "abort": {
                "type": "boolean",
                "description": "Set true when the user stands the sequence down.",
            },
        },
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Arm the ritual, stand it down, or run it once authorised."""
        available, reason = settings.tool_status(self.name)
        if not available:
            return settings.unavailable(reason)

        # Finding 17: an explicit abort word, enforced here rather than left to
        # the model to interpret. Aborting something never armed is still fine.
        # Round 2, finding 2: this is the *bare* abort -- it may drop an armed
        # sequence but never yank one that is already playing, which answers
        # `action_in_flight` instead.
        if bool(kwargs.get("abort")):
            logger.info("Tool call: self_destruct aborted")
            return GATE.abort(self.name)

        if bool(kwargs.get("confirm")):
            return await self._execute_confirmed(deps)

        return GATE.arm(self.name, _ARM_SUMMARY, {"video_id": settings.self_destruct_yt_id()})

    async def _execute_confirmed(self, deps: ToolDependencies) -> Dict[str, Any]:
        """Play the clip the user authorised, settling the claim on every path."""
        pending = GATE.claim(self.name)
        if pending is None:
            # The 90 s window closed, or nothing was armed. In character.
            return confirmation_expired()
        logger.info("Tool call: self_destruct authorised")
        settled = False
        try:
            # The gate's contract is that the *parked* payload runs, not whatever
            # the confirming call carried -- so the clip id comes from the action
            # that was read back, even if the environment changed underneath it.
            result = await sfx.play_clip(deps, str(pending.payload["video_id"]), _CLIP_TITLE, deps.instance_path)
            if result.get("ok"):
                GATE.complete(self.name, pending.claim_id)
            else:
                # A clip that would not download or play is transient: the
                # authorisation still describes exactly what the user asked for.
                GATE.release(self.name, pending.claim_id)
            settled = True
            return result
        finally:
            if not settled:
                # Task 2 review ruling: a holder that dies with an unreleased
                # claim strands the slot until the session resets. Spend it --
                # an unexpected fault is not a known-transient one (finding 9).
                logger.warning("self_destruct ended unexpectedly; spending the confirmation")
                GATE.complete(self.name, pending.claim_id)
