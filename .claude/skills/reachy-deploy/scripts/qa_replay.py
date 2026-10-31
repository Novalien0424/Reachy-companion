"""Replay model-choice cases against the installed wheel, text only. Runs ON the robot.

    /venvs/apps_venv/bin/python -I /tmp/qa_replay.py /tmp/qa_cases.json [--runs N] [--only id,id]

Tier 3 of the 2026-10-31 QA plan. Each run opens a fresh realtime session built
exactly as the app builds one: the installed handler's `_get_session_config`
(instance persona, `_HARDENING_BLOCK`, the boot mode's rules block, memory, all
tools), switched to text output with turn detection off. The app is not
involved: no app restart (the daemon wedge), no greeting, no audio either way.
The API key never leaves the robot's instance `.env`.

A case seeds `history` as conversation items, sends `say` as a user turn, and
records the response: tool calls (name and arguments) and text. No tool runs.
When the case gives `outputs` (tool name -> canned result), the first matching
call is answered with it and the follow-up reply is recorded too.

Case file: a JSON list of
  {"id": "...", "say": "...", "expect": {"tool": "music", "args": {"action": "pause"}}
   | {"no_tool": ["camera"]} | {"text_has": ["..."]} | {"text_any": ["...", "..."]},
   "history": [ {"user": "..."} | {"assistant": "..."}
                | {"call": "nas", "args": {...}, "result": {...}} ],
   "outputs": {"nas": {...}}}
Prints one line per run and a k/n summary per case.
"""

from __future__ import annotations

import sys
import json
import time
import asyncio
import pathlib
import argparse
from typing import Any
from unittest.mock import MagicMock


def _session() -> tuple[dict[str, Any], str]:
    """Build the app's own session config from the installed wheel and instance dir."""
    import reachy_companion
    from dotenv import load_dotenv

    from reachy_companion.config import resolve_instance_path, set_instance_path, refresh_runtime_config_from_env

    # The app's own resolution: ~/.config/reachy_companion since v1.25.0. The package
    # directory still holds pre-migration copies that nothing reads.
    inst = resolve_instance_path(pathlib.Path(reachy_companion.__file__).parent)

    set_instance_path(str(inst))
    load_dotenv(dotenv_path=str(inst / ".env"), override=True)
    refresh_runtime_config_from_env()
    from reachy_companion.startup_settings import load_startup_settings_into_runtime

    load_startup_settings_into_runtime(str(inst))
    from reachy_companion.persona import log_persona_source
    from reachy_companion.tools.core_tools import ToolDependencies, get_tool_specs, initialize_tools
    from reachy_companion.openai_realtime import OpenAIRealtimeHandler, realtime_model

    log_persona_source(str(inst))
    initialize_tools(str(inst))
    handler = OpenAIRealtimeHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()), instance_path=str(inst)
    )
    # The session's own list: the app excludes per mode (`Tools in session (group, startup, 19)`).
    cfg = dict(handler._get_session_config(get_tool_specs(exclusion_list=handler._mode_tool_exclusions())))
    cfg["output_modalities"] = ["text"]
    audio = dict(cfg.get("audio") or {})
    if "input" in audio:
        audio["input"] = {**dict(audio["input"]), "turn_detection": None}
    audio.pop("output", None)
    cfg["audio"] = audio
    cfg["_mode"] = str(handler._current_mode().value)
    return cfg, realtime_model()


def _items(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a case's history into realtime conversation items."""
    items: list[dict[str, Any]] = []
    for n, turn in enumerate(history):
        if "user" in turn:
            items.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": turn["user"]}]})
        elif "assistant" in turn:
            items.append(
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": turn["assistant"]}]}
            )
        elif "call" in turn:
            call_id = f"call_qa_{n}"
            items.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": turn["call"],
                    "arguments": json.dumps(turn.get("args", {}), ensure_ascii=False),
                }
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(turn.get("result", {"ok": True}), ensure_ascii=False),
                }
            )
    return items


async def _response(conn: Any) -> dict[str, Any]:
    """Collect one response: its tool calls, its text and its usage."""
    calls: list[dict[str, Any]] = []
    text: list[str] = []
    while True:
        event = await asyncio.wait_for(conn.recv(), 45)
        kind = getattr(event, "type", "")
        if kind == "response.output_item.done" and getattr(event.item, "type", "") == "function_call":
            calls.append({"name": event.item.name, "args": event.item.arguments, "call_id": event.item.call_id})
        elif kind in ("response.output_text.delta", "response.text.delta"):
            text.append(event.delta)
        elif kind == "error":
            raise RuntimeError(f"realtime error: {getattr(event, 'error', event)}")
        elif kind == "response.done":
            usage = getattr(event.response, "usage", None)
            return {
                "calls": calls,
                "text": "".join(text),
                "in": getattr(usage, "input_tokens", None),
                "cached": getattr(getattr(usage, "input_token_details", None), "cached_tokens", None),
            }


async def _run_case(client: Any, model: str, cfg: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    async with client.realtime.connect(model=model) as conn:
        await conn.session.update(session=cfg)
        for item in _items(case.get("history", [])):
            await conn.conversation.item.create(item=item)
        await conn.conversation.item.create(
            item={"type": "message", "role": "user", "content": [{"type": "input_text", "text": case["say"]}]}
        )
        await conn.response.create()
        first = await _response(conn)
        follow = None
        outputs = case.get("outputs") or {}
        call = next((c for c in first["calls"] if c["name"] in outputs), None)
        if call is not None:
            await conn.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(outputs[call["name"]], ensure_ascii=False),
                }
            )
            await conn.response.create()
            follow = await _response(conn)
        return {"first": first, "follow": follow}


def _verdict(case: dict[str, Any], run: dict[str, Any]) -> bool:
    expect = case.get("expect") or {}
    calls = run["first"]["calls"]
    said = run["first"]["text"] + ((run["follow"] or {}).get("text") or "")
    if "tool" in expect:
        if not calls or calls[0]["name"] != expect["tool"]:
            return False
        args = json.loads(calls[0]["args"] or "{}")
        if any(args.get(k) != v for k, v in (expect.get("args") or {}).items()):
            return False
    if any(c["name"] in expect.get("no_tool", []) for c in calls):
        return False
    if expect.get("no_call") and calls:
        return False
    if expect.get("text_any") and not any(t in said for t in expect["text_any"]):
        return False
    return all(t in said for t in expect.get("text_has", []))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", default="")
    opts = parser.parse_args()
    cases = json.loads(pathlib.Path(opts.cases).read_text(encoding="utf-8"))
    if opts.only:
        keep = set(opts.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    cfg, model = _session()
    mode = cfg.pop("_mode")
    from openai import AsyncOpenAI
    import os

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print(f"model {model}; mode {mode}; tools {len(cfg.get('tools') or [])}; instructions {len(cfg.get('instructions') or '')} chars")
    for case in cases:
        passed = 0
        runs = 0
        for n in range(opts.runs):
            started = time.monotonic()
            try:
                run = await _run_case(client, model, cfg, case)
            except Exception as exc:  # noqa: BLE001 - report and go on
                print(f"{case['id']} #{n + 1} ERROR {type(exc).__name__}: {str(exc)[:200]}")
                continue
            runs += 1
            ok = _verdict(case, run)
            passed += ok
            first = run["first"]
            calls = " ".join(f"{c['name']}{c['args']}" for c in first["calls"]) or "-"
            said = (first["text"] + " | " + ((run["follow"] or {}).get("text") or "")).strip(" |")
            print(
                f"{case['id']} #{n + 1} {'PASS' if ok else 'FAIL'} {time.monotonic() - started:.1f}s "
                f"in={first['in']} cached={first['cached']} calls={calls[:160]} said={said[:160]!r}"
            )
            if n == 1 and passed == 2 and not case.get("targeted"):
                break  # stop rule: 2/2 on a case the change was not aimed at
        print(f"== {case['id']}: {passed}/{runs}")


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
