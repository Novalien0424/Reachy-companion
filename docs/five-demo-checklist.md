# Five-demo in-room checklist (PRD §8)

For a person in the room with the robot. Each demo lists what to do and the
journal line that proves it. Pull the journal afterwards from the Mac:

```sh
ssh "$REACHY_SSH_USER@$REACHY_HOST" \
  "journalctl -u reachy-mini-daemon --since '<start time>' --no-pager -o short-iso" > session.log
```

App lines are the `reachy_mini.apps.manager.runner - WARNING - … reachy_companion…`
payloads. Wake the robot first (antenna touch); wait for
`boot gate released (greeting played)`.

| # | Demo | Do | Pass when the journal shows |
|---|---|---|---|
| 1 | Conversation + interruption | Three or more Chinese turns on one topic; then talk over a long answer with a real sentence (「等一下，我想問別的」) | user transcripts and replies on each turn; `solo barge-in confirmed by transcript (…)` or `… by sustained speech`, then `User intervention: flushing player queue`; the next reply answers the new sentence. A 「嗯」 or cough must NOT interrupt (`solo barge rolled back (backchannel)`) |
| 2 | Expression | Tell it something happy, then something sad | `tool_name='play_emotion'` (or a dance/move) whose emotion fits; antennas go to the listening pose when you start talking and back when you stop |
| 3 | Vision | Hold up an object with a clear colour: 「你看這是什麼？什麼顏色？」 | `tool_name='camera'`; the spoken description names the object and the right colour (JPEG colour fixed by the SDK v1.13 upgrade) |
| 4 | Web | Ask about today: 「今天台北天氣怎樣？」 or today's news | `tool_name='…search_web'` (or weather) before the answer; a spoken lead-in while it searches; the answer carries today's facts |
| 5 | Skill (home device) | Ask it to switch one real device: 「幫我把客廳燈打開」 | `tool_name='home_control'`; the confirmation step if the device requires it (`Confirmation gate: …`); the device actually changes state |

Also check, new in v1.24/v1.25:
- Head tracking follows your face and aims about 15° lower than before
  (daemon v1.11+ trim) — looking at your face, not your hairline.
- After each reply, a `usage` line per response (v1.25 latency telemetry):
  note `input`, `cached`, `audio`/`image` tokens as the session gets long.
- End with 「睡覺吧」: goodbye, sleep pose, `App stopped successfully`.

Record each row's pass/fail with the timestamp in `feature_list.json`
(`evidence`), or the blocker if a demo could not run.
