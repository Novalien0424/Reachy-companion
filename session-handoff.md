# Session handoff — 2026-10-31 (v1.36.3 installed; session closed)

State: **v1.36.3 installed (wheel `6f821c41`); Reachy asleep (app stopped), TV
off.** Boots into 多人聊天模式 (`Tools in session (group, startup, 19)`).
Daemon v1.13.0. `main` is pushed and clean. The detail is in D-044, and the
plan is `docs/plans/2026-10-31-pause-quiet-video-group-barge-plan.md`.

## What the 1.36.x line did

- `music play | pause | resume | stop`: stop ends both the speaker and the TV,
  and keeps a bookmark for 「繼續」.
- While a video is on the TV, only a named turn or 「停」 is answered (W3).
  In 多人, only a name or 「停」 cuts Reachy off (W4).
- The NAS serves the clips (`HANOVA_NAS_HTTP_BASE`, a QNAP virtual host), so
  they outlive Reachy's sleep.
  - A play shows in about 1.1 s with the cast app open and about 6 s from a
    TV that is off.
  - Reachy confirms within 0.3 s of the picture.
- `nas play` forgives a re-spaced or invented `top_folder`.

## Open, in order

1. **Operator, in normal use:** check W3/W4 with real speech
   (VIDEO-QUIET-GATE, GROUP-BARGE-BY-NAME); they are unit-tested only.
2. **Next session:** read `~/HomeAssistant/state/nas-transcode.log` (the 03:30
   run, the first with `place_zh`), then run `watchdog_cron.sh status`.
3. **Known, not fixed:**
   - a mood query for speaker music finds no track;
   - simplified characters now and then (a model limit);
   - 「暫停」 → `stop` about 1 time in 3 (the bookmark absorbs it);
   - after 「停」 Reachy once said the video was 「暫停」.
4. **The NAS web server is LAN-only and unauthenticated:** its port stays
   unforwarded.

## How to test cheaply

- **Device truth, zero model tokens:**
  `bash .claude/skills/reachy-deploy/scripts/device_check.sh nas|youtube|speaker|both|all|sleep|paused-sleep`.
- **Model choice:** run `qa_replay.py` + `qa_cases.json` on the robot (text
  only, from the installed wheel; its docstring has the usage).
- **Persona:** `persona_sync.sh status` reads `~/.config/reachy_companion/`.
  The package directory holds pre-v1.25.0 leftovers.
- **Live sessions** only for what needs the model or real audio. Line them up
  with an HA poll of the TV for latency.
- Records keep trip, clip and folder names out: they come from the NAS index.
