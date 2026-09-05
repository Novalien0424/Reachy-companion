# Solo Interruption Fix — Plan (rev 3)

**Date:** 2026-09-05. **Source:** `docs/rca-solo-interrupt-2026-09-04.md`
(operator session 11:47–12:10 robot time, 19 of 22 interruptions rolled
back). **Operator decision (2026-09-05):** RCA candidate fix 1 approved —
in 一對一聊天模式 any real sentence stops the reply — with one added
requirement: *an interruption must preserve the transcript so the model
keeps the previous context, and the conversation must continue from the
latest human speech, never from where the old reply left off.*
**Execution model:** Claude plans, reviews and orchestrates; an Opus
subagent implements under review; Codex reviews the plan (2 rounds cap).

**Governing contract:** `.claude/skills/reachy-instructing-model/SKILL.md`.
This wave is rung 3 (boundary code — interruption timing) by definition:
which sound stops the speaker is an execution-boundary fact, not a
behaviour the model can be instructed into. Evidence — cite, do not
re-litigate: the RCA above, D-023 (pause-then-decide), D-028 (name gate),
D-029 (modes), `docs/research-realtime-api-2026-08.md` §2 (truncate).

## Global constraints (still binding from D-030/D-031)

1. Model stays `gpt-realtime-2.1-mini`; no new dependencies; app-only.
2. GROUP and RECORD behaviour must not move. No prompt or tool-description
   change is needed (the solo machine is boundary code).
3. Gates per task, from `reachy_companion/`: `ruff check .`,
   `mypy --strict src`, `python -m pytest` green (baseline 1873 / 30).
4. Instance `.env` on the robot is restored by the deploy ritual; it carries
   `REALTIME_DEFAULT_MODE=one_on_one` (2026-09-04) and no barge knob.

## Structural facts that shape the fix

- **The solo pause-then-decide machine runs only in `ONE_ON_ONE`.**
  `_party_mode` is True for GROUP and RECORD, and every solo barge site
  (`_solo_speech_started`, `_confirm_solo_barge`, `_rollback_timer`,
  `_maybe_commit_on_partial`, `_resolve_solo_barge`, the late-interrupt
  guard) returns early under it. So "mode-aware interruption gate" reduces
  to *the name gate's default*: flipping it touches solo only, by
  construction. No new mode plumbing.
- **The gate-off path is shipped and tested** (`REALTIME_SOLO_NAME_GATE=0`,
  the pre-D-028 rule): the confirm timer (`REALTIME_BARGE_CONFIRM_MS`,
  1600 ms) commits on sustained speech with no transcript at all; shorter
  speech is decided by its transcript — control phrase → commit,
  substantive → commit, backchannel/empty/failed → roll back; a 2 s
  rollback timer covers a transcript that never comes. This removes RCA
  Finding 2's cap problem directly: a long interjection commits at 1.6 s
  instead of resuming at 4 s, and the 4 s cap (`REALTIME_BARGE_MAX_PAUSE_MS`)
  is gate-on-only code.
- **The operator's context requirement is already the commit path's
  contract.** `_commit_solo_barge` → `_cancel_active_response` (server stops
  generating), `_clear_queue` (local player flushed — nothing of the old
  reply is heard after this instant), then `_truncate_heard_audio(item,
  heard_ms)` → `conversation.item.truncate` cuts the *server's* copy of the
  assistant item at the position that provably reached the ear
  (`_heard_audio_ms`: enqueued − outstanding − device buffer − slack,
  rounded down). Result (best-effort — round 1, finding 6: a truncate is
  skipped when nothing was heard and a server refusal is swallowed, so the
  unheard tail can survive in rare cases; that refusal now logs at INFO so
  it is visible): the model's context keeps the words the user heard,
  drops the unheard tail (which is what made an interrupted
  Reachy repeat itself before D-023), and the user's interrupting utterance
  is already a server-side conversation item. The turn then goes through
  the answer gate (open in solo) → hold-off → `response.create`, so the next
  reply answers the latest speech with the old reply's heard part still in
  context. Rollback paths never truncate (irreversible), which is why the
  rule change must land on the *decision*, not on the truncate.
- **The late path is the second half of the same decision.** Today it
  fires only on control phrase or (gate on) name. With the gate off, a
  substantive turn whose pause was rolled back by the 2 s rollback timer
  (transcript later than the timer) would still be answered *behind* the
  old reply — RCA Finding 3. The late guard must therefore use the same
  verdict as the pause.

---

## T1 — Default flip: `REALTIME_SOLO_NAME_GATE` off

`_solo_name_gate()` default `True` → `False`. Docstring records the
2026-09-05 operator ruling and keeps D-028's story-telling rationale as the
`=1` path. Adjust the docstrings that describe the gate as the default:
`_barge_confirm_s` (now the live commit backstop, no longer "gate-off
only"), `_barge_max_pause_s` (gate-on only), `_confirm_solo_barge`,
`warn_if_barge_confirm_races_vad` (its solo half is now normally live; the
`semantic_vad` stand-down stays). No behaviour change beyond the default.

## T2 — One verdict for pause and late path

Extract `_solo_interrupt_verdict(transcript) -> tuple[bool, str]` from the
two branches of `_resolve_solo_barge`: gate on → `_gate_text_accepts`
(`control phrase` / `name` / `unaddressed`); gate off → control phrase
first, then `is_substantive` (`substantive`), else `backchannel`. Use it in
`_resolve_solo_barge` (unchanged behaviour) and in the late-interrupt guard
in the `transcription.completed` handler, replacing
`accepted and (reason == "control phrase" or _solo_name_gate())` with the
verdict's `accepted`. Journal line unchanged in shape:
`late solo interrupt (<reason>) on committed turn`; `substantive` becomes a
possible reason. Backchannel never fires the late path under either gate.

### T2b — An interruption stops whatever is speaking, not only the paused reply (round 1, finding 2)

`_commit_solo_barge` and `_late_solo_interrupt` currently keep a response
whose id differs from the paused/resumed one ("answer already live"). Under
the operator's rule that response is precisely what the user is talking
over — a tool follow-up, an earlier turn's reply, a wake greeting — and the
flush has already dropped its audio, so keeping it generating produces a
gap and then the *rest* of that reply. Change both sites: cancel the active
response whatever its id; truncate the paused item at its stashed heard
position AND, when the live audio item differs, truncate the live item at
`_heard_audio_ms()` measured before the flush (the late path already does
the live measurement — reuse it). Journal: `solo barge: cancelling a newer
response (<id>) the user talked over` replaces the "keeping the new
response" line. The watchdog is armed in that branch too (there is now no
live answer to rely on).

Exception kept: a live response that the barge **watchdog** itself
requested for this same utterance (T2c) is the turn's answer — never cancel
it from the same turn's late path.

Stale-tool rule (round 2, finding 5): `_cancel_active_response` records the
cancelled response id, but only audio deltas consult that set. Extend it to
the tool-call event: a `response.function_call_arguments.done` (or the
output-item tool-call path) whose `response_id` is in the cancelled set is
dropped with `ignoring tool call from cancelled response <id>` — it must
not enter `_in_flight_tool_calls`, must not set
`_tool_batch_needs_response`, and must not start a music-hook tool phase.
Tools already in flight before the cancel finish normally and post their
outputs; their follow-up request rides the sender queue behind the user's
answer, which is today's behaviour for every cancel (party barge included)
and is not changed here.

### T2c — One answer per interrupting turn (round 1, finding 1)

Gate off, a sustained-speech commit precedes the transcript. The watchdog
fires 1.5 s after the commit when speech has stopped and nothing answered,
and requests a response from the committed audio; when the transcript then
lands, the accepted path requests a second one. `_stand_down_barge_watchdog`
only cancels a watchdog that has not fired.

Mechanism (round 2, findings 1–4): the marker is **per input item**, not a
session bool. At solo `speech_started` remember the utterance's `item_id`
as the pause's utterance (`_barge_utterance_item_id`, alongside the
existing paused-item stash). When the watchdog fires it records
`_barge_watchdog_answered_item = that id` right before
`_safe_response_create()`. In the accepted path the turn counts as
*already answered* iff `event_item_id == _barge_watchdog_answered_item`
AND `_barge_response_seen` is True (a `response.created` arrived since the
arm — the only proof the enqueue-only `_safe_response_create` offers).
Then `_request_accepted_turn_response(event_item_id,
already_answered=True)` runs ALL of its bookkeeping — `_take_speech_started_seq`,
`_take_speech_stopped_at`, older hold-off cancellation, `_holdoff_owed` —
and skips only the hold-off arm and the request itself; journal
`accepted turn already answered by the barge watchdog`. If the watchdog
requested but nothing was created yet, the accepted path requests
normally and the sender loop's one-active-response handling covers the
overlap (known narrow race, recorded). Clear sites for the marker: the
accepted path (consumed), the empty-transcript and failed-transcription
exits (pop by item), `_arm_barge_watchdog` (new utterance), and
`on_external_interrupt` (session reset).

### T2d — Late eligibility per input item (round 1, finding 4)

`_barge_late_eligible` is one session flag, but `transcription.completed`
can land after the NEXT utterance's `speech_started` (the reason
`_turn_modes` and the speech-seq stamps are per item). Stamp eligibility by
`item_id` at `speech_started` (bounded map, same eviction as
`_remember_bounded_time`), pop it by `event_item_id` in the completed
handler, and keep the single flag only as the fallback for an event with no
id. Clear sites (round 2, finding 6): `_resolve_solo_barge`, the deny branch,
the decided-turn clear and the failed-transcription exit pop the item's
entry (the failed exit already pops the other per-item maps —
`_take_turn_mode`, `_take_speech_started_seq`, `_take_speech_stopped_at` —
add this one beside them); `on_external_interrupt` has no item id and is
the session-reset path, so it clears the whole map.

## T3 — Partial name commits regardless of the gate

`_maybe_commit_on_partial`: drop `if reason == "name" and not
_solo_name_gate(): return`. A name in a partial proves address in any
mode; the restriction was documented as "the name path is gate-mode only",
a latency-lever scoping, not a safety property. No substantive-on-partial:
a partial cannot prove substantiveness (「嗯嗯」 grows into 「嗯嗯好」).
Known risk (round 1, finding 8): `_gate_text_accepts` is a substring match
on a provisional partial that the completed transcript may later correct;
the cost of a false positive is a cut reply with its heard part preserved,
never lost context. `test_solo_barge.py:1123` flips from "do not commit" to
"commit" with the intent rewritten.

## T4 — Instrument the declined late path (RCA Finding 3, open case)

When a committed turn began over a talking robot (item eligibility True,
`not pause_committed`, solo, client barge on) and the late path does NOT
fire, log one INFO line naming why:
`late solo interrupt declined (audible=<bool>, verdict=<reason>)`. This is
the missing evidence for the 11:51:23 case. Placement (round 1, finding 5):
the completed handler leaves through three `continue`s before the late
block — empty transcript, rolled-back pause, answer-gate denial — and in
one-on-one a backchannel exits at the denial. Emit the line from a small
helper called at the denial branch (verdict computed there) and at the
late block; the empty-transcript exit logs `verdict=empty`. The rolled-back
exit already logs its own reason. One line per such turn; no new state
beyond T2d's map.

## T5 — Tests (`tests/test_solo_barge.py`, plus any pin elsewhere)

- Default pin: `_solo_name_gate()` is False with the env unset; `=1`
  restores it.
- Every existing gate-on test that relied on the old default sets
  `REALTIME_SOLO_NAME_GATE=1` explicitly (grep the 32 references across
  `test_solo_barge.py`, `test_turn_holdoff.py`, `test_conversation_modes.py`
  and fix intent, not just assertions).
- `_solo_interrupt_verdict`: table test over both gates × {control, name,
  substantive, backchannel, empty}.
- Late path, gate off: substantive committed turn over an audible reply →
  `_late_solo_interrupt` called, journal reason `substantive`; backchannel
  → not called and the T4 declined line appears with `verdict=backchannel`;
  not audible → declined line with `audible=False`.
- Partial with the name, gate off → commits.
- Commit path contract (extend the existing pins at `test_solo_barge.py`
  ~1929 and ~1961, do not duplicate): after a substantive commit the
  response is cancelled, the player flushed, the watchdog armed, and THEN
  the paused item truncated at `heard_ms` (the watchdog arm precedes the
  truncate await on purpose — round 1, finding 7); the user transcript is
  still emitted; exactly one response is requested for the interrupting
  turn.
- T2b: a live response with a different id is cancelled and its item
  truncated at the live heard position; the paused item's truncate still
  happens.
- T2c: watchdog fired then transcript accepted → exactly one
  `response.create`; watchdog stood down before firing → exactly one.
- T2d: two utterances, completions out of order → each late verdict uses
  its own item's eligibility.
- T4: declined line from the denial branch (backchannel) and from the late
  block (not audible); `verdict=empty` on the empty exit.
- Truncate refusal now logs at INFO (round 1, finding 6) — pin the line.
- Invert, do not merely add beside, the four pins of the old
  "keep the newer response" behaviour (round 2, finding 7):
  `test_solo_barge.py` ~352 (commit: no cancel/no watchdog), ~1312 (late:
  no cancel/flush), ~1884 (truncate test expects no cancel), ~1945 (late
  truncate expects no truncate). Each becomes its T2b counterpart with the
  intent rewritten.
- T2b stale-tool rule: a tool call arriving with a cancelled `response_id`
  is dropped and starts no bookkeeping; one in flight before the cancel
  still posts its output.

## T6 — Records

- `DECISIONS.md` D-032: supersedes D-028 decision 1's *default* (the gate
  stays as a knob); states the context-preservation contract in the
  operator's words and maps it to cancel/flush/truncate; names the
  false-interruption risk and its knobs.
- `CHANGELOG.md` `[1.23.0]`; `pyproject.toml` version 1.22.0 → 1.23.0.
- `feature_list.json`: `VOICE-SOLO-BARGE` and `VOICE-LATE-INTERRUPT`
  verification rewritten for the gate-off default; new row
  `VOICE-INTERRUPT-CONTEXT` for the operator's requirement (verification:
  interrupt mid-reply, then ask 「你剛剛講到哪」 — the answer must reference
  only the heard part).
- `progress.md`, `session-handoff.md`.

## T7 — Deploy v1.23.0 and leave awake for the probe

`reachy-deploy` ritual (backup, two-step install, restore, persona sha
unchanged — no persona edit in this wave, step 6b). Boot gates: instance
persona, `Tools in session (one_on_one, boxes=none, startup, 22)`,
`Realtime session updated successfully`, `boot gate released`, zero
tracebacks. Operator probe: (a) interrupt a long reply with a plain sentence,
no name → `solo barge-in confirmed by transcript (substantive, N chars)` or
`confirmed by sustained speech`, `User intervention: flushing player queue`,
then an answer to the new sentence; (b) 「嗯」 mid-reply → `solo barge
rolled back (backchannel)` and the sentence finishes; (c) a sentence longer
than 2 s → `confirmed by sustained speech`, no `hit its cap`; (d) 「你剛剛講
到哪」 after (a) → refers to the heard part only.

## Risks and watchpoints

- **False interruptions from non-speech.** Under `semantic_vad` the server
  decides `speech_stopped` on its own schedule; a cough that keeps
  `_barge_speech_open` True past 1600 ms commits by sustained speech.
  Mitigations already in place: `REALTIME_VAD_THRESHOLD=0.7`; the cost of a
  false cut is a re-ask, never lost context (truncate keeps the heard part);
  knob `REALTIME_BARGE_CONFIRM_MS` to raise. Watch: `confirmed by sustained
  speech` lines with no user transcript following.
- **Stop latency is bounded by the transcript, not zero** (round 1,
  finding 3). Speech shorter than the 1600 ms confirm timer is decided by
  its transcript; if that arrives after the 2 s rollback timer
  (`REALTIME_BARGE_ROLLBACK_TIMEOUT_S`), the reply audibly resumes and is
  cut again by the late path (T2) when the transcript lands. The pause at
  onset still silences the robot immediately; what the operator hears in
  that case is a short resume-then-cut. Knob to lengthen the silent wait if
  the journal shows `rolled back (no transcript)` followed by
  `late solo interrupt (substantive)` often.
- **Barge response watchdog** (`_BARGE_RESPONSE_WATCHDOG_S`): a sustained-
  speech commit precedes the transcript; the watchdog exists for exactly
  that and is already exercised by the gate-off tests. Re-check its
  `_barge_speech_open` guard survives the flush (fix-round finding 1).
- **Finding 4 (first-audio latency growth)** is out of scope; operator
  `.env` A/B on `REALTIME_REASONING_EFFORT` separately.

## Out of scope (recorded so review does not re-open)

RCA candidate 4 (re-time the cap — moot, the cap is gate-on-only);
candidates 5–6 (effort / VAD eagerness A/B — instance `.env` probes, no
code); any prompt or tool-description change; GROUP/RECORD barge behaviour.

## Review log

**Round 1 (Codex, `codex --profile nova-auto exec`, gpt-5.5, ~9 min,
193k tokens; 8 findings, 8 accepted, 0 rejected):**

1. Important, watchdog fires before the transcript then the accepted path
   requests a second answer — **accepted** → T2c.
2. Important, "answer already live" guard keeps a newer response the user is
   talking over — **accepted** → T2b (operator rule: stop whatever speaks).
3. Important, gate-off gives eventual stopping for late transcripts, not a
   strict guarantee — **accepted as wording** → Risks (the late path is the
   guarantee; latency bounded by the transcript).
4. Important, `_barge_late_eligible` is one session flag while completions
   are out-of-order per item — **accepted** → T2d.
5. Important, T4's declined line sits after three `continue`s; backchannels
   exit at the answer-gate denial — **accepted** → T4 placement.
6. Minor, context-preservation claim too absolute (truncate best-effort,
   refusals at debug) — **accepted** → wording + refusal log at INFO.
7. Minor, T5's cancel→flush→truncate order contradicts the pinned
   arm-before-truncate order — **accepted** → T5 wording.
8. Minor, T3 changes a pinned gate-off behaviour on a provisional partial —
   **accepted as a risk note**; T3 stands.

**Round 2 (Codex, same profile, ~7 min, 129k tokens; 7 findings, 7
accepted, 0 rejected):**

1. Important, T2c's session bool mis-scopes the marker across out-of-order
   completions — **accepted** → per-item marker.
2. Important, skipping `_request_accepted_turn_response` would skip the
   hold-off bookkeeping it owns — **accepted** → `already_answered` flag,
   bookkeeping runs, only the request is skipped.
3. Important, marker never cleared on empty/failed exits or session reset —
   **accepted** → clear sites listed.
4. Important, "rejected watchdog request → repair paths stand" is
   unsupported by the enqueue-only API — **accepted** → answered iff
   `response.created` seen; otherwise request normally (narrow race
   recorded).
5. Important, cancelling a tool-batch follow-up leaves stale tool
   bookkeeping — **accepted** → stale-tool rule in T2b (drop tool calls of
   a cancelled response id).
6. Important, T2d clear sites underspecified (`on_external_interrupt` has
   no item; failed transcription) — **accepted** → T2d clear sites.
7. Minor, four old pins of "keep the newer response" must be inverted —
   **accepted** → T5.

**Review closed at the 2-round cap (CLAUDE.md). 15 findings over two rounds,
15 accepted, 0 rejected. Rev 3 is the execution spec.**
