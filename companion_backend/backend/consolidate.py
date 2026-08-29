"""The LLM consolidation pass over the Mac people store.

A person's facts accumulate the way a conversation produces them: one line at a
time, in the order they were said, with duplicates, near-duplicates and things
that were true last year. The robot answers from the newest twenty, so a memory
nobody tidies degrades into a list of trivia. This module is the tidying: for
each person, hand the model their whole fact list and take back a merged,
contradiction-resolved, usefulness-ranked one.

Three properties are the whole design:

* **Dry-run first.** `run(settings, apply=False)` computes every rewrite and
  writes nothing at all — not one byte of `people.json` — so the operator reads
  the diff before it happens. Only `apply=True` writes, and only for people
  whose list actually changed and whose model answer validated.
* **The store's standing is not news.** Writes go through
  `store.replace_facts(..., preserve_updated_at=True)`: a background pass over
  everybody must not reshuffle the projection's top-twelve recency ranking
  (`projection.py:104`), and the position matters as much as the timestamp
  because that sort is stable.
* **`上次聊天` facts are not the model's business.** They are the sleep-time
  callback (`reachy_companion.sleep_summary`), and their exact prefix is a
  supersession key rather than ordinary text. So: every one of them is popped
  *before* the prompt is built, any string the model hands back carrying that
  prefix is dropped, and only the newest popped one is put back, at position 0.

That last rule is also a repair. The robot supersedes its own callback fact by
adding the new one and forgetting the old; an import cannot see the forget while
the robot's record sits at the twenty-fact cap (`backend/robot.py:548`, where an
eviction and a deletion are indistinguishable), so the Mac can end up holding
both and would push the stale one straight back. Deduping keep-newest on every
run heals that pair before the next push, and
`test_full_sync_cycle_heals_stale_last_chat` pins the whole cycle.

A model answer is validated whole or refused whole. Nothing is salvaged item by
item: a payload the model got wrong is not evidence about the items inside it,
and half a rewrite applied over a person's memory deletes the other half. A
refusal is reported per person and costs that person nothing — they keep exactly
the facts they had.

Failures are logged by *type*, never by message: an API error body can echo the
prompt back, and the prompt is somebody's memory (the `redact` posture of
`hanova/images.py`).
"""

from __future__ import annotations
import os
import json
import logging
from typing import Any, Final
from dataclasses import dataclass
from collections.abc import Sequence

from reachy_companion import faces, memory, people
from backend import store
from backend.config import Settings


logger = logging.getLogger(__name__)

# The supersession key `reachy_companion.sleep_summary.LAST_CHAT_PREFIX` owns,
# restated rather than imported: importing that module pulls in
# `reachy_companion.tools`, which runs `load_dotenv(override=True)` at import
# time (`reachy_companion/config.py:305`) and would rewrite this process's
# environment — including the `OPENAI_API_KEY` this module reads — as a side
# effect of looking up a constant. `test_the_last_chat_prefix_matches_the_robot_s_own`
# pins the two together so the restatement cannot drift in silence.
LAST_CHAT_PREFIX: Final[str] = "上次聊天"

# What a person's row can say instead of a rewrite. Named here because the CLI
# (Task 8) renders them and neither side should be spelling them by hand.
NO_CLIENT: Final[str] = "no_client"
INVALID_RESPONSE: Final[str] = "invalid_response"

# Both caps are the robot's own, re-exported rather than restated — the same rule
# `projection` follows. They are also written into the prompt below in words the
# model reads (「最多 20 條，每條不超過 280 字」); if either constant ever moves
# upstream, that sentence has to move with it.
MAX_FACTS: Final[int] = people.MAX_FACTS_PER_PERSON
MAX_FACT_CHARS: Final[int] = memory.MAX_FACT_CHARS

_SYSTEM_PROMPT = (
    "你是家用機器人 Reachy 的記憶整理員。整理一個人的記憶清單：\n"
    "1. 合併重複或近似的事實。\n"
    "2. 互相矛盾的事實合併成一條「以前…，現在…」。\n"
    "3. 刪除一次性的瑣事；保留穩定特質、進行中的事、人際關係。\n"
    "4. 按「聊天時最有用」排序，最有用的放最前面。\n"
    "5. 只能重組既有內容，絕對不可以新增資訊。\n"
    "6. 最多 20 條，每條不超過 280 字，臺灣繁體中文。\n"
    '只輸出 JSON：{"facts": ["...", "..."]}'
)


@dataclass(frozen=True)
class PersonConsolidation:
    """What the pass would do, or did, to one person's facts.

    `before` and `after` are the **full** lists, last-chat facts included, so
    `changed` answers the only question the write depends on: would the store
    hold something different afterwards. `error` is None on success and a short,
    stable reason on a skip; a skipped person always has `after == before`.
    """

    person_id: str
    name: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    changed: bool
    error: str | None


def build_llm_client() -> Any | None:
    """Return a synchronous OpenAI client, or None when there is no way to build one.

    Mirrors `hanova.images.build_client`'s tolerance — no key or no SDK is a
    *reason to skip*, never an exception — with the sync class, because this pass
    runs from a CLI (Task 8) rather than inside the robot's event loop. The
    caller reports `NO_CLIENT` for every person and writes nothing.
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI

        return OpenAI(api_key=api_key)
    except Exception:  # noqa: BLE001 - a missing SDK must not raise here
        logger.warning("Could not build an OpenAI client for the consolidation pass.")
        return None


def default_model() -> str:
    """Return the consolidation model — small by default; this is a batch job, not a turn."""
    return os.getenv("COMPANION_CONSOLIDATE_MODEL", "").strip() or "gpt-5-mini"


def _validated(parsed: object) -> list[str] | None:
    """Return the model's fact list, or None when the payload is not one.

    Blank items are dropped rather than refused — they are nothing to store, and
    the store's own normalizer would drop them anyway. A list left empty by that
    is refused: applying it would erase every fact the model was asked to
    *organize*, on the strength of the one answer that says least.
    """
    if not isinstance(parsed, dict):
        return None
    values = parsed.get("facts")
    if not isinstance(values, list) or len(values) > MAX_FACTS:
        return None
    texts: list[str] = []
    for item in values:
        if not isinstance(item, str) or len(item) > MAX_FACT_CHARS:
            return None
        cleaned = item.strip()
        if cleaned:
            texts.append(cleaned)
    return texts or None


def consolidate_person(client: Any, model: str, name: str, facts: Sequence[str]) -> list[str] | None:
    """Ask the model to rewrite one person's facts. Returns None on any unusable answer.

    The facts are numbered in the prompt because the model is asked to *rank*
    them: a bare list invites it to preserve the order it was given.
    """
    listing = "\n".join(f"{index}. {text}" for index, text in enumerate(facts, start=1))
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"{name} 的記憶：\n{listing}"},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content or "")
    except Exception as exc:  # noqa: BLE001 - one person's failure is not the run's
        # The type only: an API error body can quote the prompt, and the prompt
        # is this person's memory.
        logger.warning("Consolidating one person's facts failed: %s", type(exc).__name__)
        return None
    return _validated(parsed)


def _selected(settings: Settings, only: str | None) -> list[store.BackendPerson]:
    """Return the people to pass over — everybody, or the one `only` names.

    Resolved through the store's own index rule: the name is normalized exactly
    as `store` normalizes it and matched case-insensitively against everything
    the person answers to, aliases included, so a name merged away still reaches
    its survivor.
    """
    everybody = store.list_people(settings)
    if only is None:
        return everybody
    # Annotated because `reachy_companion` ships no `py.typed`, as `store` does.
    normalized: str = faces.normalize_face_name(only)
    if not normalized:
        return []
    key = normalized.casefold()
    return [person for person in everybody if key in person.name_keys()]


def run(
    settings: Settings,
    *,
    apply: bool,
    only: str | None = None,
    client: Any | None = None,
    model: str | None = None,
) -> list[PersonConsolidation]:
    """Consolidate every selected person's facts, writing only when `apply` is set.

    One row is returned per selected person, in the store's own order, whether
    the pass rewrote them, left them alone, or skipped them.
    """
    selected = _selected(settings, only)
    if client is None:
        client = build_llm_client()
    chosen_model = (model or "").strip() or default_model()

    results: list[PersonConsolidation] = []
    for person in selected:
        before = tuple(fact.text for fact in person.facts)
        if client is None:
            results.append(
                PersonConsolidation(person.id, person.name, before, before, False, NO_CLIENT)
            )
            continue

        # Newest first, so the head of this list is the callback fact to keep.
        last_chat = [text for text in before if text.startswith(LAST_CHAT_PREFIX)]
        sendable = [text for text in before if not text.startswith(LAST_CHAT_PREFIX)]

        error: str | None = None
        if not sendable:
            # Nothing for the model to organize; the dedupe below still has work
            # to do when the sync hole left two callback facts behind.
            after = tuple(last_chat[:1])
        else:
            rewritten = consolidate_person(client, chosen_model, person.name, sendable)
            # The model never authors a callback fact: it would be a callback to
            # a conversation that never happened.
            body = [] if rewritten is None else [t for t in rewritten if not t.startswith(LAST_CHAT_PREFIX)]
            if not body:
                # Either the answer was unusable, or every line of it was an
                # invented callback — and there was real memory to organize here,
                # so an empty result is a refusal, never an instruction to erase.
                after, error = before, INVALID_RESPONSE
            else:
                after = (*last_chat[:1], *body)

        changed = after != before
        if apply and changed and error is None:
            store.replace_facts(settings, person.id, after, preserve_updated_at=True)
        results.append(PersonConsolidation(person.id, person.name, before, after, changed, error))

    changed_count = sum(1 for result in results if result.changed)
    skipped = sum(1 for result in results if result.error is not None)
    logger.info(
        "Consolidation pass over %d people: %d changed, %d skipped (%s).",
        len(results),
        changed_count,
        skipped,
        "applied" if apply else "dry run",
    )
    return results
