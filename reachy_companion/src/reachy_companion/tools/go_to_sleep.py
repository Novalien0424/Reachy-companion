"""End the visit: silence the inputs, then hand the turn back for a goodbye.

Speak-then-act is not promptable. A sentence and the tool call that follows it
share one response, and on 2026-09-01 (00:17:48-58, nineteenth install) the
「進入睡眠模式」 turn produced a tool-call-only response with no audio delta at
all — so the quiesce correctly found `speaker quiet after 0.0s` and posed a
silent robot. The order is inverted instead: this tool does the irreversible
input half (mic mute, barge disarm) and returns facts; the session-ending branch
in `huggingface_realtime._deliver_tool_result` then issues ONE follow-up response
with `tool_choice: "none"` for the model to say goodbye into, waits for that
response's own `response.done`, and only then runs the drain and the pose through
`deps.go_to_sleep`.

Lifecycle sleeps (inactivity timeout, shutdown) never come through here:
`app_lifecycle.run_lifecycle_sleep` silences and poses directly, because there is
no live model turn there to speak a goodbye into.
"""

import logging
from typing import Any, ClassVar

from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class GoToSleep(Tool):
    """Silence the robot's inputs and let the model say goodbye before it rests."""

    name = "go_to_sleep"
    description = (
        "End the visit: Reachy stops, rests, and the conversation is over. Use when: you are sure the "
        "user wants you gone, off or asleep, in any wording. Do NOT use when: they want you to listen "
        "differently or record — that is set_conversation_mode; quiet for a moment — that is "
        "wait_for_user; or the request is unclear. Do not generate any other text when calling this "
        "tool: nothing before it, nothing alongside it. The result returns `status: sleeping_soon` and a "
        "`farewell_context`; then say one natural goodbye in character and stay quiet. The body lies "
        "down when it has finished playing."
    )
    needs_response = False
    ends_session: ClassVar[bool] = True
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Silence the inputs and report the facts the goodbye is composed from."""
        if deps.go_to_sleep is None:
            # Without a finalizer nothing will ever pose the robot, so promising
            # `sleeping_soon` would be exactly the overstatement this wave removes.
            return {"error": "go_to_sleep is unavailable in this runtime"}

        logger.info("Tool call: go_to_sleep")
        # Silence FIRST and unconditionally (Codex round 2, 2a-6): the goodbye
        # that follows takes seconds, and a live microphone through it means the
        # goodbye's own echo — or a repeated 「睡覺吧」 — opens a turn nobody will
        # answer. `begin_sleep` is idempotent; the finalizer repeats it.
        if deps.begin_sleep is not None:
            try:
                deps.begin_sleep()
            except Exception as e:  # noqa: BLE001 - a failed quiesce must not cost the goodbye
                logger.warning("go_to_sleep: could not silence the inputs: %s", e)

        # Facts and one render cue, no policy: the description above is the
        # higher-authority surface that says how `farewell_context` is used
        # (tool messages hold "No Authority" in the 2026 Model Spec).
        return {
            "status": "sleeping_soon",
            "farewell_context": {
                "reason": "user_asked_to_end_the_interaction",
                "listening_stopped": True,
                "person": deps.current_person,
            },
        }
