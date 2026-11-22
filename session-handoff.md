# Session handoff — 2026-11-22 (v1.40.1 installed; N5 half run by the operator)

State: **v1.40.1 installed (wheel `0be6d492`); Reachy asleep (app stopped), TV
off.** Boots into 多人聊天模式 on `gpt-realtime-2.1`, transcribing with
`gpt-live-transcribe`. Daemon v1.13.0. No wedge in 7 app stops today.

## What this session did

- **1.39.0 (D-049): the full model.** `gpt-realtime-2.1` is the default.
  OpenAI has no "latest" alias: bare `gpt-realtime` is the deprecated 2025
  model.
- **RCA of the 2026-11-21 session and a plan**
  (`docs/plans/2026-11-22-name-call-rca-plan.md`), then, on the operator's go,
  **1.40.0 (D-050): N1–N4.**
  - N1: one `utterance:` journal line per transcript, and a line when the
    robot's own detector hears speech over a reply that the server missed.
  - N2: `gpt-live-transcribe`. The name arrives 0.52 s after it is spoken,
    mid-sentence (8.51 s before).
  - N3: 多人 and 紀錄模式 hold an answer for one continuation.
  - N4: the follow-up window starts when Reachy stops talking.
- **The operator's first check (10:43–10:51 UTC), then 1.40.1.** "Responds
  quick, but sometimes talks before I finish."
  - Worked: a name over the greeting stopped it 1.0 s after the onset, and a
    second name barge stopped a reply mid-sentence.
  - Broke: N3's 4 s cap answered that bare name four seconds into the
    caller's own 14 s request. 1.40.1 makes the cap a 15 s safety net.
  - Seen once, not changed: a sentence that paused about 2 s mid-way was
    answered at the pause (`late continuation 1031 ms after the window`).
    It followed Reachy talking over the caller, so it may not recur.
- **New tool:** `name_latency_bench.py` in the deploy skill's scripts.

## Open, in order

1. **N5 again on 1.40.1, the operator** (progress.md Next action has the four
   cases). Case d, a call while others talk, has not been run. If a sentence
   is still answered at a mid-way pause, count the `late continuation` lines
   before touching `REALTIME_COMMIT_HOLDOFF_MS` (700).
2. **After N5:** read the journal's `utterance:`, `turn hold-off:` and
   `name barge-in` lines. If a call over a reply shows no `utterance:` line
   and a `Local VAD heard speech while Reachy was talking` line instead, the
   microphone lost the caller during playback (D-050's last bullet).
3. **Cost check:** what an hour awake bills for `gpt-live-transcribe`.
4. **Owed from 2026-11-01:** the NAS transcode log's first `place_zh` run, and
   `watchdog_cron.sh status`.

## How to test cheaply

- **Deploy:** `bash .claude/skills/reachy-deploy/scripts/deploy.sh` (exit 3 =
  wedge).
- **Device truth, zero model tokens:** `device_check.sh nas|youtube|…`.
- **Model choice:** `qa_replay.py` + `qa_cases.json` on the robot.
- **Name timing:** `name_latency_bench.py --only V0` on the robot measures the
  installed wheel (clean audio).
- **Live:** `live_case.sh 1|0 "<話>"`. It mutes the mic. For a mic-open reply:
  `rpc.py say` then `rpc.py unmute` at once, and read the journal.
