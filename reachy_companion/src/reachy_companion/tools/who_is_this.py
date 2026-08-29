import asyncio
import logging
from typing import Any

from reachy_companion.people import PERSON_FACTS_DEFAULT, facts_for_person
from reachy_companion.audio.envparse import env_int
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.tools.face_support import identify_with_retries, recognizer_or_unavailable


logger = logging.getLogger(__name__)


class WhoIsThis(Tool):
    """Look once and report who is in front of the robot, by name if known."""

    name = "who_is_this"
    description = (
        "Look at the person in front of the camera and check whether you recognize them from face memory. "
        "Always use this tool — instead of the camera tool — whenever the question is about a person's "
        'IDENTITY: who someone is, "do you know me", "do you remember me", "what is my name", or who just '
        "arrived. Returns a status only: recognized (with the remembered name), unknown, ambiguous, no_face, "
        "too_far or unavailable. It never returns a picture. If the status is not recognized, "
        "say plainly that you do not recognize them — never guess a name. "
        "When recognized, the result includes the remembered name and short facts about that person: say the "
        "name exactly as returned and state the facts as returned — do not add, alter, or guess details the "
        "result does not contain."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Identify the face in front of the camera, over a few short looks."""
        recognizer, refusal = recognizer_or_unavailable(deps)
        if refusal is not None:
            return refusal

        result = await identify_with_retries(deps, recognizer)
        name = result.get("name")
        if result.get("status") == "recognized" and isinstance(name, str) and name:
            # A recognition labels the session, exactly as the boot greeting
            # does: `remember` and `forget` scope to this person from here on.
            # A miss deliberately leaves the label alone — a badly lit look is
            # not evidence that the person you were talking to left the room.
            deps.current_person = name
            # The visit's guest list (sleep_summary.py) is the other half: the
            # label above is one slot the next recognition overwrites, this set
            # keeps everyone met this run so the sleep summary can write each of
            # them a 上次聊天 fact.
            deps.recognized_people.add(name)
            await self._attach_known_facts(deps, result, name)
        logger.info(
            "Tool call: who_is_this status=%s name=%s score=%s facts=%d",
            result.get("status"),
            result.get("name"),
            result.get("score"),
            len(result.get("known_facts", ())),
        )
        return result

    async def _attach_known_facts(self, deps: ToolDependencies, result: dict[str, Any], name: str) -> None:
        """Add `known_facts` to a recognized result, or leave the result untouched.

        The field lives on the result dict only: `Identification` and its closed
        Literals stay a pure camera answer. A recognized person with nothing on
        file still gets the key, empty — "I know you, I remember nothing yet" is
        an answer. Reading the store is I/O, so it goes off the event loop, and
        any failure — an unreadable file, the recall switched off — costs the
        facts and never the recognition the caller is waiting on.
        """
        # How many facts one recognition may hand back: deliberately the same
        # knob AND the same default object as the boot greeting's recall, which
        # is why the number lives in `people` rather than being written out here
        # too. Both are the same act — the robot recognizing someone and drawing
        # on what it remembers — so an operator who turns the greeting's recall
        # down or off must not still get facts through the tool.
        limit = env_int("FACE_GREETING_FACTS", PERSON_FACTS_DEFAULT, lo=0, hi=20)
        if limit <= 0:
            return
        try:
            facts = await asyncio.to_thread(facts_for_person, deps.instance_path, name, limit=limit)
        except Exception as e:
            logger.warning("who_is_this: could not read person facts: %s: %s", type(e).__name__, e)
            return
        result["known_facts"] = [fact.text for fact in facts]
