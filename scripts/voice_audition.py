#!/usr/bin/env python3
"""Voice audition harness (operator tool, dev-machine side).

Cycles the robot through candidate "cute robot voice" configurations so the
operator can pick one by ear. Each version = an OpenAI realtime base voice
(persona.md front-matter `voice`, D-016) + a VoiceFX chain (VOICEFX_* env,
D-017). Both are read at app start, so every version is: edit instance files
over SSH -> restart the app via the daemon's sanctioned start/stop API ->
inject one fixed test sentence via the app's /rpc `conversation.say`.

The robot-side edits are marker-scoped and fully reversible:
  - `.env`: one block between VOICE_AUDITION markers, replaced atomically.
  - `persona.md`: a `+++ voice = ... +++` front-matter block. The deployed
    persona has no front matter, so any front matter present was written by
    this tool and is safe to replace or strip.

Usage:
  python3 scripts/voice_audition.py list         # show the 10 versions
  python3 scripts/voice_audition.py 3            # audition version 3
  python3 scripts/voice_audition.py 3 --no-say   # apply + restart, stay silent
  python3 scripts/voice_audition.py restore      # back to shipped config
Credentials come from the repo-root .env (REACHY_HOST / REACHY_SSH_USER /
REACHY_SSH_PASSWORD); never printed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INST = "/venvs/apps_venv/lib/python3.12/site-packages/reachy_companion"
ENV_MARK_BEGIN = "# >>> VOICE_AUDITION >>>"
ENV_MARK_END = "# <<< VOICE_AUDITION <<<"
APP_NAME = "reachy_companion"
RPC_PORT = 7860

TEST_SENTENCE = "嗨，我是 Reachy！今天天氣真不錯，要不要聽首歌？"
SAY_TEXT = f"請完全照念下面這句話，一個字都不要多、不要少：「{TEST_SENTENCE}」"

# D-017 defaults (applied when a key is absent): PITCH 5.0, COMB 4.0/0.45/0.35,
# RINGMOD off, GAIN 5, ceiling -1 dBFS, knee 0.75. A version only lists overrides.
VERSIONS: dict[int, dict] = {
    1: {"label": "baseline — cedar, shipped D-017 tin-robot chain", "voice": "cedar", "fx": {}},
    2: {"label": "clean chipmunk — cedar, pitch only, no comb", "voice": "cedar",
        "fx": {"VOICEFX_COMB_MIX": "0.0"}},
    3: {"label": "soft tin — cedar, +4 st, gentler comb", "voice": "cedar",
        "fx": {"VOICEFX_PITCH_SEMITONES": "4.0", "VOICEFX_COMB_FEEDBACK": "0.40", "VOICEFX_COMB_MIX": "0.25"}},
    4: {"label": "extra cute — cedar, +6.5 st, light comb", "voice": "cedar",
        "fx": {"VOICEFX_PITCH_SEMITONES": "6.5", "VOICEFX_COMB_MIX": "0.30"}},
    5: {"label": "metal toy — cedar, tight comb + 300 Hz ring-mod", "voice": "cedar",
        "fx": {"VOICEFX_COMB_MS": "2.0", "VOICEFX_COMB_FEEDBACK": "0.55", "VOICEFX_COMB_MIX": "0.50",
               "VOICEFX_RINGMOD_HZ": "300.0", "VOICEFX_RINGMOD_MIX": "0.12"}},
    6: {"label": "marin tin — marin base, shipped chain", "voice": "marin", "fx": {}},
    7: {"label": "marin clean — marin, +4 st, no comb", "voice": "marin",
        "fx": {"VOICEFX_PITCH_SEMITONES": "4.0", "VOICEFX_COMB_MIX": "0.0"}},
    8: {"label": "shimmer lite — shimmer, +4 st, shipped comb", "voice": "shimmer",
        "fx": {"VOICEFX_PITCH_SEMITONES": "4.0"}},
    9: {"label": "coral tin — coral base, shipped chain", "voice": "coral", "fx": {}},
    10: {"label": "sage soft — sage, +4.5 st, gentle comb", "voice": "sage",
         "fx": {"VOICEFX_PITCH_SEMITONES": "4.5", "VOICEFX_COMB_FEEDBACK": "0.40", "VOICEFX_COMB_MIX": "0.25"}},
}

BUILTIN_VOICE = "cedar"  # locked profile voice; front matter is dropped for it


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (REPO_ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    for key in ("REACHY_HOST", "REACHY_SSH_USER", "REACHY_SSH_PASSWORD"):
        if not env.get(key):
            sys.exit(f"missing {key} in repo-root .env")
    return env


def rssh(env: dict[str, str], command: str, stdin_text: str | None = None) -> str:
    """Run a remote command over ssh, driving the password prompt with expect."""
    # The command travels in an env var: `expect -c SCRIPT ARG` would read ARG
    # as a script *file*, not as $argv.
    expect_script = (
        'set timeout 90\n'
        'set pw $env(REACHY_SSH_PASSWORD)\n'
        'spawn ssh -o StrictHostKeyChecking=accept-new '
        '$env(REACHY_SSH_USER)@$env(REACHY_HOST) $env(REACHY_REMOTE_CMD)\n'
        'expect {\n'
        '  -re "assword:" { send "$pw\\r"; exp_continue }\n'
        '  timeout { puts "EXPECT_TIMEOUT"; exit 97 }\n'
        '  eof\n'
        '}\n'
        'catch wait result\n'
        'exit [lindex $result 3]\n'
    )
    if stdin_text is not None:
        # Feed stdin through base64 to survive the tty layer expect introduces.
        import base64
        encoded = base64.b64encode(stdin_text.encode()).decode()
        command = f"echo {encoded} | base64 -d | {command}"
    import os
    proc = subprocess.run(
        ["expect", "-c", expect_script],
        capture_output=True, text=True,
        env={**os.environ, **env, "REACHY_REMOTE_CMD": command},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"remote command failed ({proc.returncode}):\n{proc.stdout[-2000:]}")
    return proc.stdout


def daemon(env: dict[str, str], method: str, route: str, timeout: float = 30.0) -> str:
    request = urllib.request.Request(f"http://{env['REACHY_HOST']}:8000{route}", method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode()


# Runs on the robot in the apps venv: applies one version's files.
APPLY_REMOTE = r'''
import sys, json, pathlib
spec = json.loads(sys.stdin.read())
inst = pathlib.Path("{inst}")

env_path = inst / ".env"
lines = env_path.read_text(encoding="utf-8").splitlines()
begin, end = "{mark_begin}", "{mark_end}"
if begin in lines and end in lines:
    b, e = lines.index(begin), lines.index(end)
    del lines[b:e + 1]
while lines and not lines[-1].strip():
    lines.pop()
if spec["fx"] or spec["enabled_line"]:
    lines += ["", begin] + spec["enabled_line"] + ["{{k}}={{v}}".format(k=k, v=v) for k, v in spec["fx"].items()] + [end]
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

persona_path = inst / "persona.md"
text = persona_path.read_text(encoding="utf-8")
if text.startswith("+++"):
    closer = text.index("+++", 3)
    text = text[closer + 3:].lstrip("\n")
if spec["voice"]:
    text = '+++\nvoice = "%s"\n+++\n\n' % spec["voice"] + text
persona_path.write_text(text, encoding="utf-8")
print("applied: voice=%s fx=%d keys" % (spec["voice"] or "(builtin)", len(spec["fx"])))
'''

# Runs on the robot: waits for the app session, then injects the say turn.
SAY_REMOTE = r'''
import asyncio, json, sys
import websockets

async def main():
    text = sys.stdin.read().strip()
    deadline = asyncio.get_event_loop().time() + 120
    attempt = 0
    while True:
        attempt += 1
        try:
            async with websockets.connect("ws://127.0.0.1:{port}/rpc", open_timeout=5) as ws:
                await ws.send(json.dumps({{"jsonrpc": "2.0", "id": 1, "method": "conversation.say",
                                           "params": {{"text": text}}}}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if reply.get("result", {{}}).get("ok"):
                    print("SPOKEN OK (attempt %d)" % attempt); return
                if reply.get("error", {{}}).get("data", {{}}).get("reason") == "not_running" or \
                   "no active session" in str(reply.get("error")):
                    raise RuntimeError("session not up yet")
                raise SystemExit("rpc error: %s" % reply.get("error"))
        except SystemExit:
            raise
        except Exception as exc:  # 403 until the route mounts, refused until uvicorn is up, not_running until the session connects
            if asyncio.get_event_loop().time() > deadline:
                raise SystemExit("timed out waiting for the app session (last: %r)" % exc)
            await asyncio.sleep(3)

asyncio.run(main())
'''


def apply_version_2step(env: dict[str, str], voice_override: str | None, fx: dict[str, str]) -> None:
    # VOICEFX_ENABLED deliberately stays out of the block: the instance .env
    # already carries it, and the block must never duplicate operator lines.
    spec_json = json.dumps({"voice": voice_override, "fx": fx, "enabled_line": []})
    script = APPLY_REMOTE.format(inst=INST, mark_begin=ENV_MARK_BEGIN, mark_end=ENV_MARK_END)
    rssh(env, f"cat > /tmp/voice_audition_apply.py", stdin_text=script)
    out = rssh(env, "/venvs/apps_venv/bin/python /tmp/voice_audition_apply.py", stdin_text=spec_json)
    print([l for l in out.splitlines() if l.startswith("applied")][-1])


def app_state(env: dict[str, str]) -> str:
    try:
        status = json.loads(daemon(env, "GET", "/api/apps/current-app-status", timeout=10))
    except Exception:
        return "unknown"
    if not status:
        return "stopped"
    return str(status.get("state") or "unknown")


def restart_app(env: dict[str, str]) -> None:
    state = app_state(env)
    if state in ("running", "starting"):
        try:
            daemon(env, "POST", "/api/apps/stop-current-app")
        except Exception:
            pass
    # Wait out "stopping" (and the stop above) before asking for a start:
    # the daemon answers 400 to start-app while a transition is in flight.
    deadline = time.time() + 60
    while time.time() < deadline:
        if app_state(env) in ("stopped", "unknown"):
            break
        time.sleep(2)
    for attempt in range(5):
        try:
            daemon(env, "POST", f"/api/apps/start-app/{APP_NAME}", timeout=60)
            print("app restart requested")
            return
        except Exception as exc:
            if attempt == 4:
                raise
            time.sleep(4)


def say_test_line(env: dict[str, str]) -> None:
    script = SAY_REMOTE.format(port=RPC_PORT)
    rssh(env, "cat > /tmp/voice_audition_say.py", stdin_text=script)
    out = rssh(env, "/venvs/apps_venv/bin/python /tmp/voice_audition_say.py", stdin_text=SAY_TEXT)
    print([l for l in out.splitlines() if "SPOKEN" in l or "timed out" in l][-1])


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "list"):
        for number, version in VERSIONS.items():
            print(f"V{number:>2}  {version['label']}")
        return

    env = load_env()
    target = sys.argv[1]
    no_say = "--no-say" in sys.argv

    if target == "restore":
        apply_version_2step(env, None, {})
        restart_app(env)
        print("restored shipped config (cedar + D-017 defaults); app restarting")
        return

    number = int(target)
    version = VERSIONS[number]
    voice_override = None if version["voice"] == BUILTIN_VOICE else version["voice"]
    print(f"== V{number}: {version['label']}")
    apply_version_2step(env, voice_override, version["fx"])
    restart_app(env)
    if no_say:
        print("applied; skipping the spoken line (--no-say)")
        return
    print("waiting for the session, then speaking the test line …")
    say_test_line(env)
    print(f"V{number} live. Robot stays on this config until the next version or 'restore'.")


if __name__ == "__main__":
    main()
