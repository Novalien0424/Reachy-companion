# Session handoff — 2026-10-31 (v1.36.3 installed; NAS route, latency, forgiving trip names)

State: **v1.36.3 installed 2026-10-31 (wheel `6f821c41`); Reachy asleep (app
stopped), TV off.** Boots into 多人聊天模式 (`Tools in session (group, startup,
19)`). The NAS index refreshes itself (D-041): nightly Mac pass at 03:30, and the
robot swaps in the NAS copy on wake. Daemon v1.13.0. `main` is pushed.

## What 1.36.2 and 1.36.3 did (D-044, "Play latency and two live misses")

- 1.36.2: the cast read-back polls every 0.5 s (it was 2 s), so Reachy's
  "it's on" comes right after the picture. Warm play: picture at 1.1 s, tool
  at 1.2 s; cold: about 6 s, mostly the cast app launching.
- 1.36.3: `nas play` resolves a re-spaced `top_folder` and searches one that
  names no folder. Both live misses play now.

## What 1.36.1 did (D-044, "W6 live and 1.36.1")

- The operator's QNAP virtual host serves the cast copies; the instance `.env`
  has `HANOVA_NAS_HTTP_BASE`. A clip kept playing 90 s after Reachy slept, and
  one paused before sleep resumed after the next wake.
- A video resumed after a wake is recorded again (`nas`, `youtube`, or a plain
  `tv` video), so the quiet rule and the stop bookmark hold for it.
- `device_check.sh sleep | paused-sleep`; `tv_state.py` shows the port the TV
  fetches from.

## What 1.36.0 did (D-044, plan `docs/plans/2026-10-31-pause-quiet-video-group-barge-plan.md`)

- `music play | pause | resume | stop`. Stop ends the speaker and the TV together
  and keeps a bookmark, so 「繼續」 continues from it. Verified on the real TV and
  speaker (device tier).
- While a video Reachy started is on the TV, only a named turn or a control
  phrase is answered (W3).
- In 多人, unaddressed speech no longer cuts Reachy off; a name or 「停」 does
  (W4). The knob is `REALTIME_PARTY_BARGE`.
- `camera` / `look_around` return `video_on_tv` during a video, and the `nas`
  results carry `place`.
- Smaller bugs (W5):
  - camelCase search tokens;
  - `place_zh` matching;
  - the cancel error kept off the transcript;
  - the drain cap is 10 s;
  - a pending sleep summary is kept and written at the next start;
  - the transcript copy is UTF-8.

## Open, in order

1. **Operator, in normal use:** W3/W4 with real speech (feature_list
   VIDEO-QUIET-GATE, GROUP-BARGE-BY-NAME). Unit-tested only; the synthetic-audio
   tier was not built, to save tokens.
2. **Tomorrow:** read `~/HomeAssistant/state/nas-transcode.log` (the 03:30 nightly
   run, the first with `place_zh`), and `watchdog_cron.sh status`.
3. **Known, not fixed:**
   - a mood query for speaker music ("relaxing jazz") finds no playable track;
   - simplified characters in some replies (a model limit);
   - 「暫停」 → `stop` about 1 time in 3 (mini's ceiling, absorbed by the
     bookmark).
   - The NAS web server is LAN-only and unauthenticated, like the robot stream
     it replaces. Its port must stay unforwarded (no router rule, no
     myQNAPcloud publishing).

## How to test cheaply (the operator asked for it, 2026-10-31)

- **Device truth, zero model tokens:**
  `bash .claude/skills/reachy-deploy/scripts/device_check.sh nas|youtube|speaker|both|all|sleep|paused-sleep`.
  One wake, with the mic muted.
- **Model choice:**
  - Copy `qa_replay.py` and `qa_cases.json` to the robot's `/tmp`, then run
    `/venvs/apps_venv/bin/python -I /tmp/qa_replay.py /tmp/qa_cases.json --runs 3`.
  - It uses a text-only session built from the installed wheel and the instance
    directory: no app, no greeting.
  - Seed the case history with the real result shapes.
- **Persona:** `persona_sync.sh status` (`~/.config/reachy_companion/persona.md`).
  The package directory's `persona.md` / `.env` are pre-v1.25.0 leftovers that
  nothing reads.
- **Live voice sessions** only for what needs real audio (W3/W4).
