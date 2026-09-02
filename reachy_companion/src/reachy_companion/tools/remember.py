import logging
from typing import Any

from reachy_companion.memory import add_memory_fact
from reachy_companion.people import add_person_fact
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class Remember(Tool):
    """Save one short long-term memory fact about the user."""

    name = "remember"
    description = (
        "Save a single short user fact to long-term memory for future sessions. "
        "Use when: the person explicitly shares stable information: name, preferences, hobbies, recurring "
        "projects, important people, plans, or ongoing threads; prefer ongoing threads over static traits, and "
        "the fact may mention other named people. "
        "Do NOT use when: the detail is transient for this turn, was not shared by the person speaking, or is "
        "sensitive (passwords, addresses, payment info, health diagnoses), because those do not belong in "
        "long-term memory. "
        "Do NOT use when: the user asks to remove a saved fact; that is `forget`. "
        "If you recognized the speaker, save it about that person; use this silently in the background and "
        'acknowledge naturally without saying "I will remember that".'
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": (
                    "A short, third-person statement about the user, such as "
                    '"Has a dog named Mochi" or "Prefers replies in French". One fact per call.'
                ),
            },
        },
        "required": ["fact"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Save one memory fact."""
        fact = kwargs.get("fact")
        if not isinstance(fact, str) or not fact.strip():
            logger.warning("remember: empty fact")
            return {"error": "fact must be a non-empty string"}

        # A recognized person scopes the fact to them. When the person store
        # refuses it (an empty name after normalization), the fact is still worth
        # keeping, so the global store is the fallback rather than an error.
        person = deps.current_person
        if person:
            stored_person_fact = add_person_fact(deps.instance_path, person, fact)
            if stored_person_fact is not None:
                logger.info(
                    "Tool call: remember person=%s fact=%s",
                    person[:40],
                    stored_person_fact.text[:120],
                )
                return {
                    "saved": stored_person_fact.text,
                    "memory_id": stored_person_fact.id,
                    "scope": f"person:{person}",
                }

        stored = add_memory_fact(deps.instance_path, fact)
        if stored is None:
            return {"error": "fact was empty or invalid; nothing was saved"}

        logger.info("Tool call: remember fact=%s", stored.text[:120])
        return {"saved": stored.text, "memory_id": stored.id, "scope": "global"}
