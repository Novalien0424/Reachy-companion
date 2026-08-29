"""Sleep-time engagement memory: record the visit, write one 上次聊天 fact per person.

`record_transcript` is called by the realtime handler at its final-text push
sites; `write_sleep_summaries` (Task 4) runs once at handler shutdown.
"""

from __future__ import annotations
import os
import json
import time
import asyncio
import logging
from typing import Any, Final
from pathlib import Path

from reachy_companion.people import (
    MAX_FACTS_PER_PERSON,
    add_person_fact,
    facts_for_person,
    forget_person_fact,
)
from reachy_companion.audio.envparse import env_bool, env_float
from reachy_companion.tools.core_tools import ToolDependencies


logger = logging.getLogger(__name__)

# Bound on the visit tail held in ToolDependencies.session_transcript. The deque
# is built there with a literal maxlen — core_tools cannot import this module
# without a cycle (Tool classes import core_tools) — so keep the two in step.
TRANSCRIPT_MAX_ITEMS: Final[int] = 40
LAST_CHAT_PREFIX: Final[str] = "上次聊天"

_SYSTEM_PROMPT: Final[str] = (
    "你是機器人 Reachy 的記憶整理員。根據這次的對話記錄，為列出的每個人各寫一句"
    "「上次聊天」摘要（不超過 50 字，臺灣繁體中文）。優先寫：聊了什麼主題、"
    "還沒有結果的事（考試、計畫、承諾）。只根據記錄，不要編造。"
    "記錄裡看不出是誰說的：多人在場時寫「大家聊了什麼」的主題摘要，"
    "除非記錄裡明白寫出名字，否則不要把某句話歸給特定的人。"
    '只輸出 JSON 物件：{"人名": "摘要"}；名單外的人不要出現；沒有內容可寫的人給空字串。'
)


def record_transcript(deps: ToolDependencies, role: str, text: str) -> None:
    """Append one finalized utterance to the bounded session tail."""
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("[error]"):
        return
    deps.session_transcript.append((role, cleaned))


def _default_model() -> str:
    """Return the summarizer model — a small one; this runs after the visit ends."""
    return os.getenv("MEMORY_LAST_CHAT_MODEL", "").strip() or "gpt-5-mini"


def format_last_chat_fact(summary: str) -> str:
    """`上次聊天（M月D日）：<summary>` — the prefix is the supersession key."""
    stamp = time.strftime("%m月%d日").lstrip("0").replace("月0", "月")
    return f"{LAST_CHAT_PREFIX}（{stamp}）：{summary.strip()}"


async def write_sleep_summaries(deps: ToolDependencies, *, client: Any | None = None) -> int:
    """Summarize the visit for every recognized person. Never raises."""
    try:
        if not env_bool("MEMORY_LAST_CHAT_ENABLED", True):
            logger.info("Sleep summary disabled by MEMORY_LAST_CHAT_ENABLED.")
            return 0
        names = sorted(deps.recognized_people)
        transcript = list(deps.session_transcript)
        if not names or not transcript:
            return 0
        if client is None:
            from reachy_companion.hanova.images import build_client

            client = build_client()
        if client is None:
            logger.info("Sleep summary skipped: no OpenAI client available.")
            return 0
        lines = "\n".join(f"{'user' if role == 'user' else 'reachy'}: {text}" for role, text in transcript)
        user_prompt = f"在場的人：{'、'.join(names)}\n\n對話記錄：\n{lines}"
        timeout_s = env_float("MEMORY_LAST_CHAT_TIMEOUT_S", 8.0, lo=1.0, hi=30.0)
        async with client:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_default_model(),
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                ),
                timeout=timeout_s,
            )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            logger.warning("Sleep summary: model returned non-object JSON; skipping.")
            return 0
        written = 0
        for name in names:
            summary = parsed.get(name)
            if not isinstance(summary, str) or not summary.strip():
                continue
            written += await asyncio.to_thread(_replace_last_chat_fact, deps.instance_path, name, summary)
        return written
    except Exception as exc:  # noqa: BLE001 — memory must never break shutdown
        logger.warning("Sleep summary failed: %s", type(exc).__name__)
        return 0


def _replace_last_chat_fact(instance_path: str | Path | None, name: str, summary: str) -> int:
    """ADD first, then forget the old copies — a failure can never leave zero last-chat facts.

    forget_person_fact is substring-match, newest-candidate-first (people.py:381-405):
    passing an OLD fact's full text selects that fact and cannot match the new one
    (different date/summary). New text identical to an old one is returned unstored
    by add_person_fact's duplicate check — then there is nothing to forget.
    """
    facts = facts_for_person(instance_path, name)
    old_texts = [f.text for f in facts if f.text.startswith(LAST_CHAT_PREFIX)]
    new_text = format_last_chat_fact(summary)
    # forget_person_fact is SUBSTRING match, newest-candidate-first (people.py:405).
    # Two cases force forget-FIRST (tiny failure window; worst loss = last week's
    # callback, never a real fact):
    #  - at the 20-fact cap, add-first would evict a REAL fact;
    #  - an old text that is a substring of the new one (same-day re-sleep) would
    #    make the post-add forget delete the NEW fact instead of the old.
    forget_first = bool(old_texts) and (
        len(facts) >= MAX_FACTS_PER_PERSON or any(old in new_text for old in old_texts)
    )
    if forget_first:
        for old in old_texts:
            forget_person_fact(instance_path, name, query=old)
    stored = add_person_fact(instance_path, name, new_text)
    if stored is None:
        return 0
    if not forget_first:
        for old in old_texts:
            if old != stored.text and old not in stored.text:
                forget_person_fact(instance_path, name, query=old)
    return 1
