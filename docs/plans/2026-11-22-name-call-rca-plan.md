# Calling 「瑞奇」: RCA of the 2026-11-21 session, and the fix plan (2026-11-22)

Operator ask (2026-11-22): "when called out Reachy/Richie it will stop its
talking and listen … this weekend's conversation is not good. Do RCA then plan
for accurate fix according to your survey."

**Status: plan only. Nothing here is built.** The model switch of the same day
(D-049, 1.39.0) changes none of this: every mechanism below is client-side.

## Evidence

- **The robot's journal** holds one session since 2026-11-10: Saturday
  2026-11-21, 07:13–07:18 Taipei (23:13–23:18 UTC on the 20th), v1.38.0,
  多人聊天模式, mini. 5.5 minutes, 4 answered turns.
- **A timing bench on the robot** (`name_latency_bench.py` in the deploy
  skill's scripts): synthetic 「瑞奇…」 speech streamed in real time into a
  fresh session with the app's own audio-input config. It measures when the
  name reaches the app as text.
- **Code read:** `huggingface_realtime.py` (answer gate, hold-off, name barge)
  and `openai_realtime.py` (`_transcription`, `_turn_detection`).
- **OpenAI docs** (realtime-transcription, realtime-vad, model pages), read
  2026-11-22.

## What happened in the session

Times are UTC. A reply's end is worked out from its audio tokens (about 31 ms
each, calibrated on the one reply whose drain was logged).

| When | Heard | What Reachy did |
|---|---|---|
| 23:14:02 | 「Richie，你剛剛前面有看到人嗎？」 | camera, answered in 2.6 s. Reply ended about 23:14:24 |
| 23:15:00 | 「請你再看一次前方。」 | **denied**: no name, follow-up window long over |
| 23:15:18 | 「Ritchie.」 | accepted, **answer held**: speech had started again |
| 23:15:21 | 「你再看一次前面。」 | accepted, **held again** (onset 161 ms into the 700 ms hold-off) |
| 23:15:28 | 「去打一下。」 | accepted, **held again** (this utterance took 7 s to close) |
| 23:15:34 | 「我們來看 Richie 怎麼了，為什麼他忽然間掛了。」 | answered at 23:15:36.5. **18 s after the first call**, and it answered this sentence, not the request |
| 23:15:57 | 「早。」 | denied (2 chars) |
| 23:15:59 | 「瑞奇，瑞奇。」 | accepted, **held** |
| 23:16:02 | 「Richie！」 | accepted, **held** |
| 23:16:05 | 「瑞，我要你看前面。」 | accepted (follow-up window), **held** |
| 23:16:07 | 「你有看到什么?」 | answered at 23:16:09.6, **about 10 s after the first call** |
| 23:16:33–57 | six one-word turns | denied |

The name was called five times. Every call was heard and accepted, and each
time the answer was deferred.

**No call in this session landed while Reachy was talking.** The three replies
ended at about 23:14:24, 23:15:51 and 23:16:28; every user line came later.
So this log neither confirms nor clears the stop-while-talking path. It has
never been checked with real speech (`GROUP-BARGE-BY-NAME` is unit-tested
only).

## Root causes

### RC1 — a called name waits for the room to go quiet (seen 6 times)

An accepted turn's answer waits 700 ms for a continuation
(`_request_accepted_turn_response`). Any new `speech_started` cancels the wait
and the answer becomes "owed" until that next utterance has its transcript.
Nothing bounds the chain.

- The hold-off was calibrated for one speaker finishing a sentence
  (`docs/research-holdoff-calibration-2026-09.md`). 多人 became the start mode
  on 2026-10-31, and in a room someone is nearly always starting to speak.
- One link cost 7 s on its own: the semantic VAD (eagerness `low`) took that
  long to close 「去打一下」.
- The video case already has this fix: while a video plays the hold-off runs
  its 700 ms and answers (`_video_keeps_holdoff`), because "the TV would defer
  「瑞奇，暫停」 for as long as it keeps talking". People do the same thing.

### RC2 — the name is only read after the caller stops speaking (measured)

Reachy stops for its name by reading transcript text
(`_name_barge_on_partial`, then the committed path). The shipped transcriber,
`gpt-transcribe`, starts only after the turn commits (OpenAI: "use
`gpt-transcribe` … only when you specifically need transcription to begin
after a committed audio turn"). D-028 says the same: the partial fast path was
"staged for `gpt-live-transcribe`", and that model was never switched on.

Bench, time from the end of the spoken name to the name arriving as text:

| Utterance | `gpt-transcribe` (shipped) | `gpt-live-transcribe` | live, `delay=low` | live, `delay=minimal` |
|---|---|---|---|---|
| 「瑞奇」 then silence | +0.79 s | +0.47 s | +0.11 s | −0.06 s, heard as 「瑞曲」 |
| 「瑞奇，你先停一下，我想問你…」 (8 s sentence) | **+8.51 s** | **+0.46 s** | +0.13 s | −0.07 s, heard as 「瑞曲」 |

So today 「瑞奇」 followed by a pause stops Reachy in about a second, on clean
audio. 「瑞奇，你先停一下…」 said in one breath does not stop it until the
whole sentence is over: Reachy talks over the caller for as long as they keep
talking. `gpt-live-transcribe` works inside the conversation session with the
semantic VAD on, which the docs do not state; the bench shows it.

Two things the bench cannot show, both about the microphone while Reachy's own
voice is playing: whether the server hears the caller at all through the echo
cancellation, and whether Reachy's own 「Reachy」 leaks into the transcript.

### RC3 — the follow-up window is spent while Reachy is still talking

`_party_last_accept_at` is stamped when a turn is accepted, and the window is
20 s. The replies ran 14–16 s after 2–9 s of thinking and tools, so the window
was over, or nearly, when Reachy stopped speaking. Reachy's replies end with a
question (「你想要我再仔細看其他方向嗎？」); the answer to it needs the name
again.

### RC4 — the journal cannot answer the question that matters

`speech_started` and `speech_stopped` are logged at DEBUG. From an INFO
journal there is no telling whether a call over Reachy's voice was heard and
ignored, heard late, or never heard. This RCA had to infer playback ends from
token counts.

### Smaller

- 「瑞」 alone is not an address name; 「瑞，我要你看前面」 was accepted only by
  the follow-up window.
- Nothing visible or audible tells the caller their call was heard while the
  answer is pending. `set_listening` runs for any speech in the room.

## Work items (WIP = 1, in this order)

### N1 — one INFO line per utterance (the missing evidence)

At `transcription.completed` (and `.failed`), log: onset → stop, stop →
transcript, whether Reachy was audible at the onset and is now, the first
partial's delay, and the gate's verdict. Add one line when the robot's own VAD
(`on_local_speech`, already running) hears speech during a reply and the
server reports no `speech_started` within 1 s.

- **Files:** `huggingface_realtime.py` (the three per-item stamp maps already
  hold the times), its tests.
- **Verify:** unit tests for the line's fields; one `live_case.sh` turn shows
  it in the journal.
- **Why first:** N2–N4 are judged with it.

### N2 — hear the name while it is being spoken: `gpt-live-transcribe`

Make `gpt-live-transcribe` the input transcriber, at the server's default
delay (the name came out right; `minimal` heard 「瑞曲」).

- **Change:** `_transcription()` sends `languages: [lang]` for this model
  instead of `language` (the docs: "don't send both"); keywords and prompt
  stay. The fallback to `gpt-4o-transcribe` on a rejected session stays.
  `_name_barge_on_partial` already does the stopping, in 多人 and during a
  video; one-on-one already commits on a name or control phrase in a partial.
- **Files:** `openai_realtime.py`, `.env.example`, tests.
- **Verify:** the bench against the installed wheel (V0 must show the name
  under 1 s into the long sentence); unit tests for the session shape; five
  fresh wakes with no `name barge-in` line caused by Reachy's own greeting
  (it says 「Reachy」 in it).
- **Cost, for the operator's ruling:** $0.017 per minute against $0.0045,
  billed as "realtime audio duration". If that means every awake minute, it is
  about $1.02 an hour awake against $0.27. One measured hour on the usage page
  settles which.
- **Risk:** a partial is provisional. A false name costs one cut reply, with
  the heard part kept by the truncate (the existing rule since D-032).

### N3 — a called name is answered even when the room keeps talking

In 多人, bound the deferral:

1. A held answer may be deferred by **one** continuation. The next accepted
   turn's hold-off is not cancelled by a new onset; it answers 700 ms after
   its transcript.
2. An absolute cap: if the continuation has produced no transcript
   `REALTIME_HOLDOFF_MAX_DEFER_MS` (start at 4000) after the held turn's
   transcript, answer anyway.

One-on-one keeps today's behavior. The cost is the one W3 accepted: now and
then Reachy answers the first half of a sentence, and the rest arrives as a
follow-up.

- **Files:** `huggingface_realtime.py` (`_request_accepted_turn_response`, the
  `speech_started` branch), tests.
- **Verify:** a unit test replays the 23:15:18 → 23:15:34 event sequence and
  must request the response within 4.7 s of the first transcript (it took
  16.6 s); the same for 23:15:59 → 23:16:08. The existing hold-off tests stay
  green for one-on-one.

### N4 — the follow-up window starts when Reachy stops talking

Re-stamp `_party_last_accept_at` when the accepted turn's reply has drained
(`audio_drain`), so the 20 s are the caller's.

- **Files:** `huggingface_realtime.py`, tests.
- **Verify:** unit test with a 15 s reply: an unnamed turn 10 s after the
  drain is accepted, one 25 s after is denied.
- **Open to the operator:** this widens when room talk gets an answer. Seven
  of the eight denials in the session were one-word turns.

### N5 — acceptance with a real voice (operator, about five minutes)

The bench is clean audio. The stop-while-talking path depends on the
microphone during playback, which only a person in the room can exercise.
After N1–N4 are installed, in 多人, asking for a long story each time:

| Case | Say, over Reachy's voice | Pass |
|---|---|---|
| a | 「瑞奇」, then wait | stops within about 1 s, 5/5 |
| b | 「瑞奇，停一下，我想問你…」 in one breath | stops within about 1 s of the name, 5/5 |
| c | a sentence to someone else, no name | keeps talking, 5/5 |
| d | 「瑞奇」 while it is quiet, others talking | answers within 5 s, 5/5 |

The N1 lines give the measured delays. If (a) or (b) fail with the N1 line
showing the server never heard the onset, the cause is the echo canceller
during double-talk, and the next step is an on-device keyword spotter, which
is a new dependency and a separate decision.

## Not in this plan

- **An on-device wake word.** `docs/research-realtime-api-2026-08.md` §4
  rules it out of POC scope, and N2 reaches about half a second without it.
  It comes back only on the N5 failure above.
- **`gpt-live-1.1`** ("can listen while it speaks"). It runs on a different
  endpoint (`v1/live`), takes no image input, and would replace the whole
  realtime integration.
- **A cue that the call was heard** (a head or antenna gesture on the name).
  Worth doing after N2, which is what makes the name available early; it
  would reuse an SDK move.

## Order and gates

N1 → N2 → N3 → N4, one install each or together as 1.40.0, then N5. The
operator rules on N2's cost and N4's wider window before they ship.

## Review log (self-review against PRD, research, `reference/`)

| # | Finding | Resolution |
|---|---|---|
| 1 | Reuse-first: does upstream have a streaming transcriber or wake word? | No. `reference/reachy_mini_conversation_app` hard-codes `gpt-4o-transcribe`; neither repo has a wake word. Turn behavior is ours to build (CLAUDE.md). |
| 2 | Claim "gpt-live-transcribe works in a conversation session with VAD on" has no doc behind it. | Backed by the bench on the robot (V4–V6), not by the docs, which only describe it in transcription sessions with turn detection off. N2's verification repeats the bench on the installed wheel. |
| 3 | Claim "no call overlapped a reply" rests on a 31 ms/token estimate from one drain line. | Stated as an estimate. The margins are 6 s or more in all three cases. N1 removes the need to estimate. |
| 4 | PRD § Converse: 多人 answers "a turn that names it, or one inside a short follow-up window". | N3 and N4 keep that rule; N4 moves where the window starts. PRD wording needs no change. |
| 5 | N3 could make one-on-one answer half sentences. | Scoped to 多人; one-on-one path untouched and covered by the existing tests. |
| 6 | N2's cost is unknown until measured. | Marked as the operator's ruling, with the one-hour measurement that settles it. |
| 7 | The bench used the full model; the session ran on mini. | Transcription and VAD are separate from the speech model; V1 reproduces the shipped transcriber and VAD exactly. |
