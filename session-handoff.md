# Session handoff — 2026-11-22 (v1.40.0 installed; N5 needs a person in the room)

State: **v1.40.0 installed (wheel `3624c233`); Reachy asleep (app stopped), TV
off.** Boots into 多人聊天模式 on `gpt-realtime-2.1`, transcribing with
`gpt-live-transcribe`. Daemon v1.13.0. No wedge in 5 app stops today.

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
  - N3: 多人 and 紀錄模式 hold an answer for one continuation, 4 s at most.
  - N4: the follow-up window starts when Reachy stops talking.
- **New tool:** `name_latency_bench.py` in the deploy skill's scripts.

## Open, in order

1. **N5, the operator, about five minutes** (progress.md Next action has the
   four cases). Until then, stopping when called over a reply is unobserved:
   the bench is clean audio and `live_case.sh` mutes the mic.
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
