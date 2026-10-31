"""Drive the app's console /rpc WebSocket. Runs ON the robot, under the apps venv with -I.

    rpc.py mute | unmute      set the mic (a muted mic keeps room speech out of a test)
    rpc.py say                mute the mic, then send the text read from stdin as a user turn
    rpc.py tool NAME ARGS     call one tool as the model would (ARGS is a JSON object); needs
                              the QA flag /tmp/reachy_qa present when the app started
"""

import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:7860/rpc"


async def call(ws, rid: int, method: str, params: dict, timeout: float = 15) -> None:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    while True:
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if reply.get("id") == rid:
            print(method, json.dumps(reply.get("result", reply.get("error")), ensure_ascii=False))
            return


async def main(mode: str, rest: list) -> None:
    async with websockets.connect(URL, open_timeout=5) as ws:
        if mode in ("mute", "say"):
            await call(ws, 1, "conversation.mic", {"muted": True})
        if mode == "unmute":
            await call(ws, 1, "conversation.mic", {"muted": False})
        if mode == "say":
            await call(ws, 2, "conversation.say", {"text": sys.stdin.read().strip()})
        if mode == "tool":
            await call(ws, 3, "qa.tool", {"name": rest[0], "args": json.loads(rest[1] if len(rest) > 1 else "{}")}, 95)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("mute", "unmute", "say", "tool") or (
        sys.argv[1] == "tool" and len(sys.argv) < 3
    ):
        sys.exit("usage: rpc.py mute|unmute|say|tool NAME [ARGS]  (say reads the text from stdin)")
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
