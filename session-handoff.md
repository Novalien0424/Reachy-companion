# Session handoff — 2026-11-22 (v1.39.0 installed; name-call plan awaits rulings)

State: **v1.39.0 installed (wheel `4cd12177`); Reachy asleep (app stopped), TV
off.** Boots into 多人聊天模式 on `gpt-realtime-2.1`. Daemon v1.13.0. No wedge
in 2 app stops.

## What this session did

- **Full model (D-049, 1.39.0).** `_DEFAULT_MODEL` is `gpt-realtime-2.1`.
  There is no "latest" alias: bare `gpt-realtime` is the deprecated 2025
  model. Verified live (time, camera, chat, sleep) and by replay (11/12).
- **RCA of the 2026-11-21 session, and a plan**
  (`docs/plans/2026-11-22-name-call-rca-plan.md`). Nothing in it is built.
  - RC1: a named turn's answer waits for the room to go quiet (18 s, 10 s).
  - RC2: the name is read only after the caller stops speaking; measured
    +8.51 s in a long sentence, +0.46 s with `gpt-live-transcribe`.
  - RC3: the 20 s follow-up window runs out while Reachy is still talking.
  - RC4: speech onsets are DEBUG-only, so the journal can't show a call over
    a reply.
- **New tool:** `name_latency_bench.py` in the deploy skill's scripts (its
  docstring has the usage and how to make the PCM files).

## Open, in order

1. **Operator rulings:** N2's cost and N4's wider window (the plan's "Order
   and gates"). Then N1 → N4 as 1.40.0, then N5 with a real voice.
2. **Operator, in normal use:** the full model over a real conversation.
3. **Owed from 2026-11-01:** the NAS transcode log's first `place_zh` run, and
   `watchdog_cron.sh status`.
4. **Known, not fixed:** see progress.md Known defects.

## How to test cheaply

- **Deploy:** `bash .claude/skills/reachy-deploy/scripts/deploy.sh` (exit 3 =
  wedge).
- **Device truth, zero model tokens:** `device_check.sh nas|youtube|…`.
- **Model choice:** `qa_replay.py` + `qa_cases.json` on the robot, with
  `--override` to try a wording first. Confirm any gain live.
- **Name timing:** `name_latency_bench.py` on the robot (clean audio only).
- **Live:** `live_case.sh 1|0 "<話>"`. It mutes the mic, so it can't test a
  call over Reachy's voice: that needs a person in the room.
