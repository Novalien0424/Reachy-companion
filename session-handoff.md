# Session handoff — 2026-10-31 (v1.36.0 installed; pause, quiet video, group barge by name)

State: **v1.36.0 installed 2026-10-31 (wheel `c712306b`); Reachy asleep (app
stopped), TV idle.** Boots into 多人聊天模式 (`Tools in session (group, startup,
19)`). The NAS index refreshes itself (D-041): nightly Mac pass at 03:30, and the
robot swaps in the NAS copy on wake. Daemon v1.13.0. `main` is pushed.

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

1. **Operator: the QNAP virtual host (W6).**
   - QTS → Control Panel → Applications → Web Server → Virtual Host → enable.
   - Create a host: HTTP, a free port (8090 suggested), root at the cast-copy
     folder in its share.
   - If the picker offers only the `Web` share, move the cast-copy folder into
     `Web` (same volume) and repoint the Mac pipeline's output share.
   - Then the agent:
     - checks the root answers 403 (no listing) and a clip answers a range
       request with 206;
     - sets `HANOVA_NAS_HTTP_BASE=http://<nas>:<port>/` in
       `~/.config/reachy_companion/.env`;
     - runs `device_check.sh nas`, plus a sleep check: play, sleep, still playing
       after 60 s.
2. **Operator, in normal use:** W3/W4 with real speech (feature_list
   VIDEO-QUIET-GATE, GROUP-BARGE-BY-NAME). Unit-tested only; the synthetic-audio
   tier was not built, to save tokens.
3. **Tomorrow:** read `~/HomeAssistant/state/nas-transcode.log` (the 03:30 nightly
   run, the first with `place_zh`), and `watchdog_cron.sh status`.
4. **Known, not fixed:**
   - a mood query for speaker music ("relaxing jazz") finds no playable track;
   - simplified characters in some replies (a model limit);
   - 「暫停」 → `stop` about 1 time in 3 (mini's ceiling, absorbed by the
     bookmark).

## How to test cheaply (the operator asked for it, 2026-10-31)

- **Device truth, zero model tokens:**
  `bash .claude/skills/reachy-deploy/scripts/device_check.sh nas|youtube|speaker|both|all`.
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
