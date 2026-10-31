# Session handoff — 2026-10-31 evening (v1.36.7 installed; session closed)

State: **v1.36.7 installed (wheel `3dc93b1b`); Reachy asleep (go_to_sleep by
voice, app stopped), TV off.** Boots into 多人聊天模式
(`Tools in session (group, startup, 19)`). Daemon v1.13.0, no wedge.
The detail is in D-045.

## What this session did

- A journey check on 1.36.3: every demo path worked (chat, dance, camera, web
  search, TV on, NAS video, pause/stop, text barge, music).
- **The reply rail is 4096** (about 160 s). 900 cut the news and a requested
  story mid-word; on 1.36.7 both finished live.
- **smbprotocol is quiet:** the wake no longer logs the NAS address and the
  SMB username.
- **「我們」 names the video source** in the hardening block and the nas/youtube
  descriptions. Replay improved (0/4 → 12/24), but live stayed 0/3, so it is
  recorded at mini's ceiling.
- **Harness:**
  - `live_case.sh` shows `status=incomplete` and the app's WARNING/ERROR lines;
  - `qa_replay.py --override cand.json` measures a candidate wording before it
    ships (it prints `chars=` too).

## Open, in order

1. **Operator decisions, with data in feature_list:**
   - `REPLY-RAIL-4096`: keep 4096, or `inf` (upstream). A "long story" runs
     3–4 min on mini and can still reach 4096. `inf` is one instance `.env`
     line, no redeploy.
   - `VIDEO-OUR-TRIP-SOURCE`: send a trip request to the 影片庫 first (overturns
     D-038's ask-first), or accept the one extra turn.
   - `DANCE-SPEAKS-ON-GOOD-NEWS`: a spoken follow-up for `dance play` only, or
     accept the silent dance (3/4).
2. **Operator, in normal use:** check W3/W4 with real speech
   (VIDEO-QUIET-GATE, GROUP-BARGE-BY-NAME); they are unit-tested only.
3. **Next session:** read `~/HomeAssistant/state/nas-transcode.log` for the
   2026-11-01 03:30 run, the first with `place_zh` (the host index has 0
   `place_zh` entries now), then run `watchdog_cron.sh status`.
4. **Known, not fixed:** `JOURNEY-2026-10-31-NOTES` (vague news answer,
   mid-session TV-on without preamble, greeting slips, the benign sleep
   stop-timeout warning), plus the 1.36.x list (mood query for speaker music,
   occasional simplified characters, 「暫停」 → `stop` about 1 in 3).

## How to test cheaply

- **Device truth, zero model tokens:**
  `bash .claude/skills/reachy-deploy/scripts/device_check.sh nas|youtube|speaker|both|all|sleep|paused-sleep`.
- **Model choice:** `qa_replay.py` + `qa_cases.json` on the robot, text only.
  Add `--override` to try a wording first.
  - **Confirm any gain live:** text replay overstated our-trip (12/24 replay,
    0/3 live).
- **Live sessions** (`live_case.sh`) only for what needs the model or real
  audio. A `status=incomplete` or a WARNING line now shows in its output.
- Records keep trip, clip and folder names out: they come from the NAS index.
