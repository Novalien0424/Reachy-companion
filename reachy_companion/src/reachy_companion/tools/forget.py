import logging
from typing import Any

from reachy_companion.memory import forget_memory_fact
from reachy_companion.people import forget_person_fact
from reachy_companion.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)


class Forget(Tool):
    """Remove one long-term memory fact."""

    name = "forget"
    description = (
        "Remove a previously saved fact from long-term memory. Call this when the user asks you to forget something, "
        "or when saved information becomes obsolete. Match by a specific free-text phrase present in the fact. "
        "Searches the recognized person's facts first, then general memory."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A short search phrase that should be present in the fact to remove. Matching is case-insensitive."
                ),
            },
        },
        "required": ["query"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Forget one memory fact by query."""
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            logger.warning("forget: empty query")
            return {"error": "query must be a non-empty string"}

        # The recognized person's own facts are searched first: "forget that"
        # while talking to someone means their fact, not a shared household one.
        # No match there falls through to global memory rather than stopping.
        person = deps.current_person
        if person:
            person_result = forget_person_fact(deps.instance_path, person, query=query)
            if person_result.removed is not None:
                person_response: dict[str, Any] = {
                    "removed": person_result.removed.text,
                    "memory_id": person_result.removed.id,
                    "scope": f"person:{person}",
                }
                if len(person_result.candidates) > 1:
                    person_response["other_matches"] = [fact.text for fact in person_result.candidates[1:]]

                logger.info(
                    "Tool call: forget person=%s query=%s removed=%s",
                    person[:40],
                    query[:120],
                    person_result.removed.text[:120],
                )
                return person_response

        result = forget_memory_fact(deps.instance_path, query=query)
        if result.removed is None:
            logger.info("Tool call: forget query=%s no_match", query[:120])
            return {"error": f'no memory matched "{query}"; nothing was removed'}

        response: dict[str, Any] = {
            "removed": result.removed.text,
            "memory_id": result.removed.id,
            "scope": "global",
        }
        if len(result.candidates) > 1:
            response["other_matches"] = [fact.text for fact in result.candidates[1:]]

        logger.info("Tool call: forget query=%s removed=%s", query[:120], result.removed.text[:120])
        return response
