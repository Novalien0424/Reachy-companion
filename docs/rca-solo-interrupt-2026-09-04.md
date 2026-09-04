# RCA — solo-mode interruption: hard to stop, and the old reply plays out first (2026-09-04)

Operator report (2026-09-04): (1) while Reachy is talking it has become
harder to interrupt; (2) after an interruption it usually finishes the
previous reply before answering the latest question. Investigation only —
no behaviour change shipped with this note. The evidence is the robot's
journal for the 2026-09-04 11:47–12:10 session (v1.22.0, one operator,
switched to 一對一聊天模式 at 11:49:09), read against
`huggingface_realtime.py` and D-028/D-029/D-031.

## Session facts

Instance `.env` at the time: `REALTIME_VAD_TYPE=semantic_vad`,
`REALTIME_VAD_EAGERNESS=low`, `REALTIME_REASONING_EFFORT=medium`,
`REALTIME_VAD_THRESHOLD=0.7`; no barge knob set, so
`REALTIME_SOLO_NAME_GATE=1`, `REALTIME_BARGE_MAX_PAUSE_MS=4000`,
`REALTIME_COMMIT_HOLDOFF_MS=700` defaults apply.

Barge outcomes in the 21 minutes of solo conversation:

| outcome | count | journal line |
| --- | --- | --- |
| interruption committed | 3 | `solo barge-in confirmed by partial transcript (name)` ×2, `late solo interrupt (control phrase)` ×1 |
| pause hit the 4 s cap, reply resumed | 4 | `solo barge pause hit its cap with no address; resuming reply` |
| rolled back: substantive but unaddressed | 10 | `solo barge rolled back (unaddressed)` |
| rolled back: backchannel | 3 | `solo barge rolled back (backchannel)` |
| rolled back: empty transcript | 2 | `solo barge rolled back (empty)` |

So 19 of 22 speech onsets over a talking robot ended with the robot carrying
on. Rolled-back utterances included 「等一下」 (12:01:14), 「你就播吧」
(11:53:46), 「嗯嗯嗯。这句话很怪怪的」 (12:09:23) — all plainly aimed at the
robot in a one-person room. At 11:49:22 the operator asked the robot
whether one-on-one mode means the name is not needed, and it answered yes.

## Finding 1 — the name gate governs interruption in solo mode (symptom 1, cause A)

D-029 decision 1 keeps `REALTIME_SOLO_NAME_GATE` on in every mode: a paused
solo reply commits only when `_gate_text_accepts(transcript)` finds an
address name (`reachy,richie,ritchie,瑞奇,里奇,小瑞,瑞曲`) or a control
phrase (`停|閉嘴|安靜|睡覺|別唱|stop|quiet|shut up`). Everything else is
"unaddressed" and the reply resumes. The gate was designed for the room
posture and deliberately not tied to the answer gate
(`REALTIME_ONE_ON_ONE_ANSWER_GATE=open`), so in 一對一聊天模式 the robot
answers anything but can only be *stopped* by name or 停. That is a design
choice, recorded, and it is exactly what the operator now experiences as
"more difficult to interrupt": the previous generation of the barge logic
(D-023, `REALTIME_SOLO_NAME_GATE=0` path, still shipped) committed on any
substantive transcript.

## Finding 2 — the pause cap fires before a transcript can exist (symptom 1, cause B)

The pause-then-decide machine (D-023) pauses at `speech_started` and waits
for the *transcript* to decide. But the server transcribes a turn only after
it commits it, i.e. after the user stops talking plus the VAD tail; the
partial-transcript fast path (`_maybe_commit_on_partial`) does not help with
the shipped `gpt-transcribe`, whose partials arrive post-commit (D-029
decision 1 says so; today both partial commits landed within 0.2 s of the
completed transcript). The 4 s cap (`REALTIME_BARGE_MAX_PAUSE_MS`) is
measured from speech onset. Therefore any interjection longer than roughly
4 s minus the VAD tail minus transcription time (about 1.5–2 s of speech)
hits the cap before its own transcript exists, the reply resumes over the
user, and only the late path (`_late_solo_interrupt`) can stop it once the
transcript lands.

Evidence: 12:02:00 `pause hit its cap` → 12:02:02 `late solo interrupt
(control phrase)` for 「Ritchie, Ritchie. Ritchie，停，Ritchie。Ritchie.」 —
the operator said the name five times over ~6 s before the robot stopped.
11:51:16 cap → transcript at 11:51:23 (7 s after the cap) containing
「Richie」. `semantic_vad` with `eagerness=low` (set 2026-09-01) lengthens
the commit delay and so widens this window; progress.md flagged on 09-01
that the barge timing was tuned against `server_vad`. No `server_vad`
session exists in the journal since then, so the size of that contribution
is unmeasured; the structural gap (cap from onset, transcript after commit)
exists under either VAD.

## Finding 3 — a rolled-back pause leaves the old reply queued in front of the new answer (symptom 2)

Server-side audio generation runs far ahead of playback: the 11:51 reply
finished generating at 11:51:05 (`status=incomplete`, max output tokens),
three seconds after its first audio delta, while roughly 25 s of it was
still to play. `output_queue` is unbounded, and only a *confirmed* barge
flushes it (`User intervention: flushing player queue`). On every rollback
path (Findings 1 and 2) the held audio goes straight back into playback.
The user's utterance is nevertheless a committed turn on the server; the
open answer gate accepts it, the hold-off requests a response, and that
response's audio is appended behind the remaining old audio. The operator
hears the old reply to its end and then the new answer — symptom 2 exactly.

The one thing that could have prevented this on the 11:51:23 turn is the
late interrupt (the name was in the transcript), and it did not fire. The
guard needs `_barge_late_eligible` (captured at onset), `not
pause_committed`, and `_robot_audible()` (`audio_drain.is_audible()`:
paused, queue non-empty, or outstanding audio). The journal cannot show
which condition failed; there is no log line on the declined branch. That
is the open question below.

## Finding 4 — time-to-first-audio grew from 2 s to 10 s over the session (aggravating)

`Turn latency: response.created` stayed at ~990 ms all session (the 700 ms
hold-off plus transport). `first audio delta` climbed monotonically:

| time | first audio delta after user transcript |
| --- | --- |
| 11:48 | 2.3 s |
| 11:51 | 4.9 s |
| 11:54 | 5.8 s |
| 12:01 | 7.9 s |
| 12:05 | 8.4 s |
| 12:09 | 10.6 s |
| 12:10 (goodbye) | 20.8 s |

That interval is model generation time before the first audio token; it is
measured at receipt, so local playback backlog cannot inflate it. The
growth tracks conversation length under `reasoning.effort=medium` on
`gpt-realtime-2.1-mini`; no usage/reasoning-token telemetry is logged, so
the split between reasoning and prefill is unknown. Effect on the reported
symptoms: after a *successful* interruption (12:09:25) the robot stood
silent for 10.6 s before the new answer, and the goodbye response missed the
10 s farewell wait (`the farewell response did not finish within 10s;
sleeping anyway`). The handoff already lists a `reasoning.effort`
three-metric A/B as next-wave work; this is the first live data for it.

## What was NOT the cause

- Not the 700 ms hold-off: it adds ~1 s to `response.created`, constant
  across the session, and never withheld an answer today except by design
  (`turn hold-off: awaiting continuation` lines all had continuations).
- Not GROUP mode: the session was in 一對一聊天模式 from 11:49:09; the
  two `party barge-in confirmed` lines before that behaved as designed.
- Not a crash or reconnect: zero tracebacks, one session, clean sleep.

## Root-cause statement

In 一對一聊天模式 an interruption succeeds only if it is short enough for
its transcript to beat the 4 s pause cap *and* contains the robot's name or
a control phrase. Everything else is rolled back, and a rollback resumes a
reply whose audio the server has already delivered in full to the local
queue, so the new answer plays after it. Growing model latency then adds
up to ten seconds of silence before the new answer.

## Candidate fixes (for the plan, in ladder order — none applied)

1. **Mode-aware interruption gate (rung 3, boundary code):** in
   `ONE_ON_ONE`, decide a pause on the D-023 substantive rule rather than
   the name gate — i.e. resolve `_solo_name_gate()` per mode, default off
   in solo and on in GROUP/RECORD. Keeps D-029's room protection where it
   was built for, restores the one-person promise the robot itself makes.
   Backchannel/empty/failed rollbacks stay.
2. **Flush on any accepted post-rollback turn:** when a turn that began
   over a talking robot is accepted for an answer, cancel and flush the
   old reply before requesting the new one (the late-interrupt path
   already does this; extend its trigger from "addressed" to "accepted for
   answer" in solo). This alone removes symptom 2 even with the gate on.
3. **Instrument the declined late path:** one INFO line naming
   eligible/audible/outstanding when a committed turn over a talking robot
   is *not* honoured, so the 11:51:23 case can be explained next time.
4. **Re-time the cap against transcript arrival, not onset**, or arm it at
   `speech_stopped`: the cap's job is to bound side conversations, and it
   currently also bounds the user's own sentence length.
5. **Reasoning effort A/B** (`none`/`low` vs `medium`), measuring first
   audio delta, tool hallucination and adherence — Finding 4's data is the
   baseline.
6. **`semantic_vad` eagerness** back to `auto`/`medium` for one session to
   size Finding 2's VAD contribution; a `server_vad` control session too.

Operator decision needed on (1): it changes what stops the robot in solo
mode, the exact question D-029 decision 1 answered the other way.
