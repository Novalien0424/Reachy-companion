# Session handoff — 2026-11-01 (v1.37.0 installed; session closed)

State: **v1.37.0 installed (wheel `11fca7fc`); Reachy asleep (go_to_sleep by
voice, app stopped), TV off.** Boots into 多人聊天模式 (`Tools in session
(group, startup, 19)`). Daemon v1.13.0, restarted twice today to clear app-stop
wedges (00:56:17Z, 01:03:11Z). The detail is in D-046.

## What this session did

- **Operator rulings on D-045:**
  - keep the 4096 reply rail;
  - a trip video plays directly when found;
  - leave the silent dance.
- **Video requests look in the 影片庫 first (1.37.0).** Live: our-trip 3/3
  (was 0/3), 「放波士頓的影片」 3/4, MV → YouTube 1/1. A miss now names its fix
  (tell the user, ask about YouTube).
- **Harness made concise:**
  - `deploy.sh` (+ `backup_instance.sh`, `verify_install.py`) is the whole
    install; this deploy used it.
  - `live_case.sh` redacts its own output.
  - `device_check.sh` uses one pause/resume/stop cycle.
  - `rpc.py` reports an asleep app in one line.
  - The deploy SKILL.md went from 431 to 216 lines.
- **Records cleaned:** progress.md (1206 → about 220 lines) and
  feature_list.json (100 rows) dropped 41 verified, retired and superseded
  items. The full text is at `0f67ce8`.

## Open, in order

1. **Operator decision:** `VIDEO-MISS-FOLLOWUP`. After a miss, Reachy asked
   about YouTube 1/3, played it unasked 1/3, and offered nothing 1/3. Either
   keep the ask, or let a miss play YouTube directly.
1b. **Operator decision:** `TIME-OF-DAY-ANSWER`. 「現在幾點？」 has no clock (no time
   tool in the 19, no time in the prompt); found by the harness check at 01:13.
2. **Operator, in normal use:** check W3/W4 with real speech
   (`VIDEO-QUIET-GATE`, `GROUP-BARGE-BY-NAME`).
3. **Next session:** read `~/HomeAssistant/state/nas-transcode.log` for the
   03:30 run (the first with `place_zh`; the robot index already matches
   「花蓮」 in Chinese), then run `watchdog_cron.sh status`.
4. **Known, not fixed:** `JOURNEY-2026-10-31-NOTES`, the 1.36.x list (see
   progress.md Known defects), and `DAEMON-STOP-WEDGE` (2 in 10 stops today).

## How to test cheaply

- **Deploy:** `bash .claude/skills/reachy-deploy/scripts/deploy.sh` (exit 3 =
  wedge).
- **Device truth, zero model tokens:** `device_check.sh nas|youtube|…`.
- **Model choice:** `qa_replay.py` + `qa_cases.json` on the robot, with
  `--override` to try a wording first. Confirm any gain live: replay said 4/4
  for the miss follow-up, and live was 1/3.
- **Live:** `live_case.sh 1|0 "<話>"`; its output is safe to quote except for
  titles from the NAS index.
