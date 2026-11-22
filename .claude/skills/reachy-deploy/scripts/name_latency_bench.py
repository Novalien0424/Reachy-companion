"""Time how soon the robot's name reaches the app as transcript text. Runs ON the robot.

    /venvs/apps_venv/bin/python -I /tmp/name_bench/name_latency_bench.py /tmp/name_bench \
        [--only V1,V4] [--utts bare-name,name-then-sentence] [--model ID]

It streams synthetic speech (24 kHz mono s16 PCM) in real time into a fresh realtime
session whose audio-input block is the app's own, with the transcription model and the
turn detection swapped per variant. No response is created and the app is not involved;
the API key stays in the robot's instance `.env`. Each line is a server event, timed
against the END OF THE NAME in the audio. `NAME-IN-PARTIAL` is the moment the name
barge (`_name_barge_on_partial`) could act.

The PCM files are made on the Mac and copied next to this script:

    say -v Meijia -o name.aiff "瑞奇"; say -v Meijia -o name2.aiff "瑞奇，瑞奇"
    say -v Meijia -o stop.aiff "瑞奇，停"
    say -v Meijia -o rest.aiff "你先停一下，我想問你另外一件事情，就是明天早上我們要去哪裡吃早餐"
    for f in name name2 rest stop; do ffmpeg -y -i $f.aiff -ar 24000 -ac 1 -f s16le $f.pcm; done

It measures the API's timing on clean audio. It cannot show what the microphone hears
while Reachy itself is talking: that needs a person in the room (2026-11-22 plan, N5).
"""

from __future__ import annotations
import json
import time
import base64
import asyncio
import pathlib
import argparse
from typing import Any
from unittest.mock import MagicMock


NAMES = ("reachy", "richie", "ritchie", "瑞奇", "里奇", "小瑞", "瑞曲")
RATE = 24000
FRAME = RATE // 50 * 2  # 20 ms of s16


def _base_cfg() -> tuple[dict[str, Any], str, str]:
    from dotenv import load_dotenv

    import reachy_companion
    from reachy_companion.config import set_instance_path, resolve_instance_path, refresh_runtime_config_from_env

    inst = resolve_instance_path(pathlib.Path(reachy_companion.__file__).parent)
    set_instance_path(str(inst))
    load_dotenv(dotenv_path=str(inst / ".env"), override=True)
    refresh_runtime_config_from_env()
    import os

    from reachy_companion.openai_realtime import OpenAIRealtimeHandler, realtime_model
    from reachy_companion.tools.core_tools import ToolDependencies

    handler = OpenAIRealtimeHandler(
        ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock()), instance_path=str(inst)
    )
    cfg = dict(handler._get_session_config([]))
    cfg["instructions"] = "Bench session. Never speak."
    cfg["tools"] = []
    cfg["output_modalities"] = ["text"]
    audio = dict(cfg["audio"])
    audio.pop("output", None)
    cfg["audio"] = audio
    return cfg, realtime_model(), os.environ["OPENAI_API_KEY"]


def _semantic(eagerness: str) -> dict[str, Any]:
    """Semantic VAD as the app sends it: the client answers and interrupts."""
    return {"type": "semantic_vad", "eagerness": eagerness, "create_response": False, "interrupt_response": False}


def _server(silence_ms: int) -> dict[str, Any]:
    """Server VAD with the given end-of-speech silence."""
    return {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": silence_ms,
        "create_response": False,
        "interrupt_response": False,
    }


KW = list(NAMES)
PROMPT = "與家用陪伴機器人的台灣中文對話"
GT = {"model": "gpt-transcribe", "language": "zh", "keywords": KW, "prompt": PROMPT}
LIVE = {"model": "gpt-live-transcribe", "languages": ["zh"], "keywords": KW, "prompt": PROMPT}
VARIANTS: dict[str, tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = {
    "V0": ("app config as installed", None, None),
    "V1": ("gpt-transcribe + semantic low", GT, _semantic("low")),
    "V2": ("gpt-transcribe + semantic high", GT, _semantic("high")),
    "V3": ("gpt-transcribe + server_vad 500", GT, _server(500)),
    "V4": ("gpt-live-transcribe + semantic low", LIVE, _semantic("low")),
    "V5": ("gpt-live-transcribe delay=minimal + semantic low", {**LIVE, "delay": "minimal"}, _semantic("low")),
    "V6": ("gpt-live-transcribe delay=low + server_vad 500", {**LIVE, "delay": "low"}, _server(500)),
    "V7": (
        "gpt-realtime-whisper + semantic low",
        {"model": "gpt-realtime-whisper", "language": "zh"},
        _semantic("low"),
    ),
}


def _utterances(d: pathlib.Path) -> dict[str, tuple[bytes, float]]:
    """Name -> (pcm, seconds from audio start to the end of the name)."""

    def sil(s):
        return b"\0" * (int(RATE * s) * 2)

    name, name2, rest, stop = ((d / f"{n}.pcm").read_bytes() for n in ("name", "name2", "rest", "stop"))

    def secs(b):
        return len(b) / 2 / RATE

    lead = 1.0
    return {
        "bare-name": (sil(lead) + name + sil(12), lead + secs(name)),
        "name-twice": (sil(lead) + name2 + sil(12), lead + secs(name2)),
        "name-then-sentence": (sil(lead) + name + sil(0.12) + rest + sil(12), lead + secs(name)),
        "name-stop": (sil(lead) + stop + sil(12), lead + secs(stop)),
    }


async def _one(
    client: Any, model: str, cfg: dict[str, Any], pcm: bytes, name_end: float, wait_all: bool = False
) -> list[str]:
    out: list[str] = []
    async with client.realtime.connect(model=model) as conn:
        await conn.session.update(session=cfg)
        while True:
            ev = await asyncio.wait_for(conn.recv(), 15)
            if ev.type == "session.updated":
                break
            if ev.type == "error":
                return [f"REJECTED: {getattr(ev.error, 'message', ev.error)!s:.300}"]
        t0 = time.monotonic()
        done = asyncio.Event()

        async def send() -> None:
            for n, i in enumerate(range(0, len(pcm), FRAME)):
                if done.is_set():
                    return
                await conn.input_audio_buffer.append(audio=base64.b64encode(pcm[i : i + FRAME]).decode())
                delay = t0 + (n + 1) * 0.02 - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)

        sender = asyncio.create_task(send())
        partial = ""
        saw_name = False
        first_delta = False
        try:
            while True:
                ev = await asyncio.wait_for(
                    conn.recv(), 6 if (wait_all and any(line.startswith("completed") for line in out)) else 14
                )
                rel = time.monotonic() - t0 - name_end
                k = ev.type
                if k in ("input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"):
                    out.append(
                        f"{k.split('.')[-1]} {rel:+.2f}s (audio@{getattr(ev, 'audio_start_ms', getattr(ev, 'audio_end_ms', '?'))}ms)"
                    )
                elif k == "input_audio_buffer.committed":
                    out.append(f"committed {rel:+.2f}s")
                elif k == "conversation.item.input_audio_transcription.delta":
                    partial += ev.delta or ""
                    if not first_delta:
                        first_delta = True
                        out.append(f"first-delta {rel:+.2f}s {partial!r}")
                    if not saw_name and any(n in partial.casefold() for n in NAMES):
                        saw_name = True
                        out.append(f"NAME-IN-PARTIAL {rel:+.2f}s {partial!r}")
                elif k == "conversation.item.input_audio_transcription.completed":
                    out.append(f"completed {rel:+.2f}s {ev.transcript!r}")
                    if not wait_all:
                        break
                elif k == "conversation.item.input_audio_transcription.failed":
                    out.append(f"transcription FAILED {rel:+.2f}s {getattr(ev.error, 'message', '')!s:.200}")
                    break
                elif k == "error":
                    out.append(f"error {rel:+.2f}s {getattr(ev.error, 'message', ev.error)!s:.300}")
        except asyncio.TimeoutError:
            out.append("timeout: no further events for 14 s")
        done.set()
        sender.cancel()
    return out


async def main() -> None:
    """Run the chosen variants over the chosen utterances and print the timings."""
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--only", default="")
    ap.add_argument("--utts", default="")
    ap.add_argument("--model", default="")
    a = ap.parse_args()
    from openai import AsyncOpenAI

    base, model, key = _base_cfg()
    model = a.model or model
    client = AsyncOpenAI(api_key=key)
    utts = _utterances(pathlib.Path(a.dir))
    print(f"model={model}")
    print("installed audio.input:", json.dumps(dict(base["audio"]["input"].items()), ensure_ascii=False)[:600])
    for vid, (label, tr, td) in VARIANTS.items():
        if a.only and vid not in a.only.split(","):
            continue
        cfg = json.loads(json.dumps(base))
        if tr is not None:
            cfg["audio"]["input"]["transcription"] = tr
        if td is not None:
            cfg["audio"]["input"]["turn_detection"] = td
        print(f"\n== {vid} {label}")
        for uname, (pcm, name_end) in utts.items():
            if a.utts and uname not in a.utts.split(","):
                continue
            try:
                lines = await _one(client, model, cfg, pcm, name_end, wait_all="sentence" in uname)
            except Exception as exc:  # noqa: BLE001
                lines = [f"EXC {type(exc).__name__}: {str(exc)[:300]}"]
            print(f"  [{uname}] name ends at {name_end:.2f}s; times are relative to that")
            for line in lines:
                print("     ", line)


asyncio.run(main())
