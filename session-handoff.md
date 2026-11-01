# Session handoff — 2026-11-01 (v1.38.0 installed; session closed)

State: **v1.38.0 installed (wheel `60232618`); Reachy asleep (app stopped), TV
off.** Boots into 多人聊天模式 (`Tools in session (group, startup, 20)`). Daemon
v1.13.0. Today: two wedges on 1.37.0 (00:56:17Z, 01:03:11Z), none in the 9
stops on 1.38.0. The detail is in D-046 and D-047.

## What this session did

- **1.37.0 (D-046):** a trip or place video plays from the 影片庫 first; the
  reply rail stays 4096; the silent dance is left as is. The harness became
  `deploy.sh` plus a concise skill, and the records dropped 41 fixed rows (full
  text at `0f67ce8`).
- **1.38.0 (D-047), operator rulings:**
  - **A miss asks permission before YouTube.** The miss result carries the
    question (`ask_user`), and `youtube` refuses until the user speaks.
    Live: asked 3/4, YouTube unasked 0/4, no offer 1/4; after 「好啊」, YouTube
    played.
  - **Time and date:** Pollen's bundled MCP time tool is the 20th tool. Live:
    time 3/3, date 2/2, news still goes to search.

## Open, in order

1. **Operator, in normal use:** check W3/W4 with real speech
   (`VIDEO-QUIET-GATE`, `GROUP-BARGE-BY-NAME`).
2. **Next session:** read `~/HomeAssistant/state/nas-transcode.log` for the
   03:30 run (the first with `place_zh`), then run `watchdog_cron.sh status`.
3. **Known, not fixed:** `JOURNEY-2026-10-31-NOTES`, the 1.36.x list (see
   progress.md Known defects), `DAEMON-STOP-WEDGE`, and the miss's no-offer
   slip (1/4).

## How to test cheaply

- **Deploy:** `bash .claude/skills/reachy-deploy/scripts/deploy.sh` (exit 3 =
  wedge).
- **Device truth, zero model tokens:** `device_check.sh nas|youtube|…`.
- **Model choice:** `qa_replay.py` + `qa_cases.json` on the robot (now with
  `time-now`, `date-today`, `news-stays-search`), with `--override` to try a
  wording first. Confirm any gain live: replay overstates.
- **Live:** `live_case.sh 1|0 "<話>"`; its output is safe to quote except for
  titles from the NAS index. The YouTube hold logs `youtube: waiting for the
  user's yes`.
