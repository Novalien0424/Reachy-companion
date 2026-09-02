# Field-Test Fixes Wave — Plan (rev 3)

**Status (2026-09-02): EXECUTED.** Seven commits `5715829..` on `main`; suite
1819 → 1859 / 30 skipped; design record D-031; shipped as v1.21.0, the
twenty-first install (deploy evidence in `progress.md`).

**Date:** 2026-09-01. **Source:** `session-handoff.md` consolidated RCA
(operator session 12:49–12:57 robot time). **Operator scope order:** fix
turn-detection over-commit first ("the main issue"), do RCA-3 (spoken
preambles for slow tools), fix RCA-6 (end-of-sleep failure).
**Execution model (operator directive, 2026-09-01):** Claude plans, reviews
and orchestrates; **Codex implements, tests, runs suites.**

**Governing contract:** `.claude/skills/reachy-instructing-model/SKILL.md`
(escalation ladder; every change names its rung). Evidence — cite, do not
re-litigate: `docs/codex-investigation-sleep-2026-09.md`,
`docs/codex-investigation-commentary-2026-09.md`,
`docs/codex-research-turn-detection-2026-09.md`,
`docs/research-realtime-api-2026-08.md`,
`docs/research-instructing-realtime-voice-2026-09.md`.

## Global constraints (carried over from D-030 wave, still binding)

1. Model stays `gpt-realtime-2.1-mini`; full-tier runs are diagnostics only.
2. No new dependencies; app-only (never the daemon); Chinese-primary;
   secrets externalized.
3. No numeric caps, no keyword-trigger lists in prompts (operator rule,
   memory `prompt-style-judgment-over-caps`).
4. Returns state facts and render cues, never new policy; errors are advice
   addressed to the model.
5. Gates per task, from `reachy_companion/`: `ruff check .` clean,
   `mypy --strict src` clean, `python -m pytest` green (baseline
   1819 passed / 30 skipped).
6. `reasoning.effort` and VAD *type* stay as the operator pinned them
   (`semantic_vad`/`low` eagerness, effort high in the instance `.env`)
   unless Item A's evidence names a specific, reviewed change.
7. Instance `persona.md` reaches the robot only via the operator scp+sha
   ritual; any persona edit makes the re-sync a hard pre-deploy gate.

---

## Item A — turn-detection over-commit (RCA-1 contributor; the operator's #1)

**Observed failure:** with `semantic_vad` + `eagerness=low` live-verified in
session config, turn detection still commits mid-sentence fragments —
「你。」「就是。」 answered in <400ms; longer sentences split across commits.
The model then answers (or hallucinates about) garbage partial turns; RCA-5's
phantom-NSFW refusal is downstream of this.

**Structural fact that shapes the fix:** `create_response=false` in every
mode since D-029 — **we already issue `response.create` ourselves after each
commit**. The commit→response seam is client-owned code, so a client-side
gate needs no new protocol machinery.

**Why client-side is the only lever (research-closed):** `semantic_vad`
exposes ONLY `eagerness`/`create_response`/`interrupt_response` — no
minimum-duration, threshold or commit-delay knob exists, and `eagerness`
raises only the *maximum* wait applied while the classifier is unsure; a
fragment with terminal prosody commits immediately at any eagerness.
Server-side tuning is exhausted. LiveKit Agents' production pattern
(pending end-of-utterance task cancelled by renewed speech, merging the
continuation into one turn) is the same semantics our seam allows one step
later. (`docs/codex-research-turn-detection-2026-09.md` Q1/Q2.)

**Design (rung 3 — execution-boundary timing, plus a rung-2 backstop):**

- **A1. Answer hold-off window (code, the core fix) — armed at the
  ACCEPTED-TURN seam, not at the commit event (review r1 finding 1).**
  The client today issues `response.create` only after
  `conversation.item.input_audio_transcription.completed` has passed the
  mode gate (`huggingface_realtime.py:3674-3811` region); there is no
  committed-event response path, and adding one would bypass the
  GROUP/RECORD/name gates. So the hold-off wraps the existing accepted-turn
  response issuance: when a turn is ACCEPTED, instead of queueing
  `_safe_response_create()` immediately, start a bounded window (default
  700ms, `REALTIME_COMMIT_HOLDOFF_MS`, 0 disables). **Skip the response**
  if (a) `input_audio_buffer.speech_started` has ALREADY fired since this
  turn's commit (the transcript arrives asynchronously — the continuation
  may already be in progress when we arm), or (b) it fires inside the
  window. The continuation then commits, transcribes, passes the gate,
  and its own (held) response answers fragment+continuation together —
  both are consecutive user items in history. If the window expires
  silent, respond as today. "No transcript gating" means no
  transcript-CONTENT heuristics; the transcription event itself is
  already the response trigger and stays so. Journal signal:
  `turn hold-off: awaiting continuation (…)` on skip.
  Composition rules, explicit (review r1 findings 2, 3):
  - The accepted-turn cleanup — late-interrupt eligibility reset and
    barge-watchdog stand-down (`:1816`, `:1847`, `:3765-3784`) — runs at
    acceptance, BEFORE the hold-off, and a skipped response must leave no
    repair watchdog armed that would `_safe_response_create()` the very
    answer we skipped (test-pinned; `test_solo_barge.py:2275,2330` pin
    today's order).
  - A hold-off skip is NOT the denied-turn path: `on_turn_without_response()`
    (music resume, `music_hooks.py:261-274`) must NOT run on skip — the
    turn is still pending its answer via the continuation.
  - A DENIED turn holds nothing; barge-in during a hold-off follows the
    existing barge machine, never a second path.
  - **Non-blocking (review r2 finding 1):** the window is a per-turn
    `asyncio.Task` (or loop timer handle) created at acceptance; the
    receive loop returns to `async for event in self.connection` (`:3481`)
    immediately. It must NEVER be an awaited sleep inside the
    `transcription.completed` branch — that would stall the single receive
    loop, so the `speech_started` branch (`:3490`) could not observe the
    continuation and skip condition (b) would be dead code. The
    `speech_started` branch cancels the pending hold-off (marks it skipped,
    journal line); on expiry the task enqueues through
    `_safe_response_create()` (`:2601`, a queue put that never blocks).
  - **Lifecycle (review r2 finding 2):** `_pending_responses` is created
    once in `__init__` (`:740`) and outlives sessions, while each session
    starts a fresh sender worker (`:3451`) cancelled in the session
    `finally` (`:4080-4084`) — so a hold-off that outlives its session
    would enqueue a `response.create` that the NEXT session's sender sends
    (a phantom answer on reconnect, or during shutdown). Rules: (i) the
    handle is bound at arm time to the connection it was armed under, and
    the fire path re-checks before enqueueing — not cancelled,
    `self.connection` is still that bound connection, `_receive_loop_active`,
    and no `speech_started` noted since arm — otherwise it drops with a
    journal line; (ii) the hold-off joins the timer set that
    `on_external_interrupt()` cancels (`:1617-1655`, same
    `_cancel_barge_task` pattern), which already covers the RPC
    `conversation.interrupt`/`say` flush, `_barge_reset_for_new_session`,
    the shutdown reset, and the sleep closure's existing interrupt call;
    (iii) `_barge_shutdown()` awaits it at session teardown beside the
    barge timers. Test pins: expiry in-session enqueues exactly once;
    `speech_started` inside the window enqueues nothing; teardown or
    reconnect with a hold-off pending enqueues nothing into the next
    session; `on_external_interrupt()` cancels a pending hold-off.
  - **Owed answer (implementation review, 2026-09-02):** a skip assumes the
    continuation will be ACCEPTED and answer both items. When the
    continuation instead ends with no response — empty transcript,
    `transcription.failed`, an answer-gate denial (GROUP/RECORD/name_only),
    or a solo-barge rollback — the earlier accepted turn must still be
    answered: a cough inside the window must not eat a real question. The
    handler keeps `_holdoff_owed`; each no-response exit that finds it set
    arms a fresh window for the held turn (journal `turn hold-off:
    continuation produced no turn (…); answering the held turn`) and, on
    the denied exit, does not run `on_turn_without_response()` for that
    item because a response is coming. Cleared on any real request, on
    `on_external_interrupt()`, and at new-session reset. Test-pinned.
  Cost: up to the window per turn — the operator has already chosen
  patience over speed twice (VAD silence 1000, eagerness low).
- **A2. Short-turn qualifier: DROPPED from this wave** (review r1 finding
  4: committed-audio duration is not currently tracked; the qualifier is a
  latency optimization that would need new timing state — YAGNI until the
  flat window measurably annoys).
- **A3. Rung-2 backstop:** strengthen placement of the existing
  unclear-audio→ask-again rule (RCA-5 showed it violated): move/restate it
  where turn-boundary damage lands (per research: placement beats volume —
  system-prompt compliance decays by turn). No keyword lists.
- **A4. Config surface:** new env knobs documented in `.env.example` +
  README; defaults conservative; `0` disables the hold-off entirely
  (revert-to-today safety).

**Explicitly NOT in this item:** changing VAD type back to `server_vad`,
changing eagerness, enabling `idle_timeout_ms`, transcript-based gating, or
any transcription-model change. The research confirmed no superior
server-side knob exists; the on-robot A/B (existing `VOICE-SEMANTIC-VAD-AB`
row) is the fallback only if the hold-off disappoints live.

## Item B — selective spoken preambles for slow tools (RCA-3, operator-requested)

**Observed failure:** 8–10s dead air on search turns. Cause is by-design:
ALL commentary-phase items are dropped at `huggingface_realtime.py:3522-3534`
(audio at `:3850-3868`, transcripts at `:3831-3847`) and the prompt's
訊息頻道/開場白 block (`prompts.py:27-37`) says tool pre-openers are dropped
so act silently. D-030 explicitly deferred "selective commentary" to a later
wave; the operator has now requested it.

**Survey facts (from `docs/codex-investigation-commentary-2026-09.md`):**
suppression timing is proven fine on this endpoint (the dead air proves
commentary is caught before it plays); the upcoming tool name is NOT
knowable at the suppression decision point (it arrives only at
`response.function_call_arguments.done`); preamble-vs-tool ordering is
concurrent, not sequential — which is acceptable here: speaking 「我查一下」
*while* the search runs is the desired behavior, and no slow tool in scope
is irreversible.

**Design (rungs 1–2 first, per the ladder; code seam change is the minimum
that makes speech physically possible):**

- **B1. Un-suppress commentary AUDIO, steer generation instead (rung 2 +
  minimal code).** Commentary audio flows to the speaker again, with full
  participation in drain/truncate accounting. But **commentary transcripts
  stay OUT of every persistence surface** (review r1 finding 5): the
  RECORD room log, the D-027 sleep-summary transcript tail, and the
  operator transcript emission must not treat 「我查一下」 as a final
  answer — the transcript-drop for commentary stays at the
  `_emit_transcript()`/`record_transcript()` boundary even as the audio
  drop is removed. The prompt's 訊息頻道/開場白 block flips from "act
  silently, pre-openers are dropped" to: preambles are spoken and belong
  before *slow* work (search, music resolution, MCP calls) as a brief,
  natural lead-in — enough to show the work has started, never a narration
  of the steps (a calibration principle, not a length cap: review r2
  finding 3, Global constraint 3); fast robot actions go straight to work.
  Semantic conditions, no trigger lists. Deliberate, named tradeoff (review r1 finding 6,
  accepted in part): selectivity is enforced by INSTRUCTION, not code —
  that is the escalation ladder's rung-2-first ruling, taken knowingly
  with B4's fallback pre-designed; code keeps no per-tool speech policy
  in this wave.
- **B2. PREAMBLE sample phrases in slow tools' descriptions (rung 1):**
  search (`tool_spaces.py:49-61`), `music` `action=play` only
  (`tools/music.py:21-33`), and the MCP wrap point
  (`mcp_servers.py:146-153`) gains one injected policy line for all remote
  tools. Fast tools stay marked proactive. Labeled 示範語氣，不是觸發條件.
  **Deploy trap (review r1 finding 7):** baked-in
  `PREINSTALLED_TOOL_SPACE_SPECS` are read only when no
  `installed_tool_spaces.json` manifest exists; a deployed instance with a
  manifest serves the CACHED description (`tool_spaces.py:215-220,
  336-342`). The task must make the edit reach a manifested robot —
  regenerate/refresh the manifest at deploy (mechanism chosen at
  implementation, test-pinned), and the deploy checklist gains a
  model-visible-description verification line.
- **B3. RCA-4 routing rider (rung 1, same lines B2 already edits):** the
  search description gains a do-NOT-use contrast ("user wants media
  *played* → `music`, not search"), and `music`'s description names
  YouTube-playback requests as its own. This addresses the routing half of
  RCA-4 for one description-edit's marginal cost; the fabricated-capability
  half stays out of scope (cross-cutting self-knowledge work).
- **B4. Known risk + fallback, stated up front:** un-suppressing may
  resurrect stale 「還在處理中」-style narration on fast tools (the reason
  suppression exists). Watchpoint on the first live session; if noise
  returns, the reviewed fallback is the survey's Option 1 (buffer
  commentary until `response.function_call_arguments.done` names the tool,
  then pass/drop by a slow-tool set) as its own follow-up task — not a
  reason to hard-code speech now.
- **B5. Test surface:** the five suppression pins
  (`test_huggingface_realtime.py:1372-1520`) are rewritten to pin the NEW
  contract, and (review r1 finding 8) the new contract needs MORE than a
  rewrite of the old five: pin that audible commentary participates in
  `on_response_audio()` drain and truncate accounting, that it does NOT
  enter RECORD/sleep persistence (B1), and that a commentary-first
  response does not release the boot gate early
  (`huggingface_realtime.py:3627-3645`). Prompt pins
  (`test_prompts_hardening.py:134-156`) rewritten to assert the flipped
  block. `persona.md` + profile body harmonized (no
  "open with the point" contradiction for slow-tool turns); persona
  re-sync becomes a deploy gate (Global constraint 7).

## Item C — end-of-sleep failure + lost sleep summary (RCA-6)

**Root cause (confirmed, `docs/codex-investigation-sleep-2026-09.md`):**
`request_stop_current_app` (`app_lifecycle.py:26-38`) catches only
`urllib.error.URLError`; the daemon accepts the stop POST and tears down
before completing the HTTP response, raising `http.client.RemoteDisconnected`
(→`ConnectionResetError`→`OSError`, NOT a `URLError`) out of
`response.read()`, past the guard, into main.py's C6 handler — which then
**unmutes the microphone** ~7s before process death. Second unguarded
suspect in the same block: `movement_manager.stop()`. Separately, the D-027
sleep summary is one `chat.completions.create` call under a single 8s
`asyncio.wait_for` with no retry (`sleep_summary.py:163-190`) — one timeout
loses the visit's memory.

**Design (rung 3 — pure execution-boundary correctness):**

- **C1. Broaden the stop-request guard:**
  `except (urllib.error.URLError, http.client.HTTPException, OSError)`;
  log the exact exception type; return `False`; caller continues to local
  stop exactly as today.
- **C2. Recovery correctness:** the C6 unmute-recovery must never run for
  failures at or after the stop request — by that point quiesce has muted
  the mic on purpose and the app is dying; reopening the mic contradicts
  sleep. Narrow the outer `try` (or add an inner boundary) in
  `main.py:347-408` so C6-unmute covers only genuinely pre-pose failures;
  also guard `movement_manager.stop()` (log-and-continue: a stop failure
  must not abort pose+shutdown) — in BOTH places (review r1 finding 11):
  the sleep closure (`main.py:358`) AND the final shutdown path
  (`main.py:522-539`), where the same unguarded call can still abort
  wobbling-disable/media-close/disconnect cleanup.
- **C3. Sleep-summary retry:** one bounded retry on `TimeoutError` with a
  FIXED short retry timeout (4s), so the total added shutdown delay is ≤4s
  regardless of `MEMORY_LAST_CHAT_TIMEOUT_S` (review r1 finding 10: "2×
  env" could hold `shutdown()` — and the still-open realtime connection,
  `huggingface_realtime.py:4160-4199` — for 60s at the env max); log
  attempt count. Persisting the transcript locally before summarization is
  recorded as the non-surgical follow-up if loss recurs.
- **C4. Tests — EXTEND the existing pins (review r1 finding 9 corrected an
  earlier "no coverage" claim; coverage exists at
  `test_app_lifecycle.py:14,36` (stop-request happy path),
  `test_main.py:210,233` (failed-sleep mic-unmute recovery),
  `test_sleep_summary.py:486,567` (failure swallowing, one-shot)):** pin `RemoteDisconnected`/`ConnectionResetError`/
  `BadStatusLine` → `False` without raising; pin that a stop-request
  failure does NOT unmute the mic; pin summary retry-once-then-give-up.

## Out of scope (recorded so review doesn't re-open them)

RCA-2 (GROUP boot default — operator config choice, voice-switchable
today); RCA-4's fabricated-capability half and RCA-5's refusal (downstream
of Item A + the cross-cutting self-knowledge family — next wave candidate);
`who_is_this` too_far capture defect; RPC-SAY-CROSS-LOOP; mode persistence.

## Verification

- Unit gates per Global constraint 5; new/updated tests per item.
- New `feature_list.json` rows: `VOICE-TURN-FRAGMENTS` (A: a deliberately
  fragmented Mandarin sentence gets ONE answer covering the whole thought;
  journal shows the hold-off merge line), `VOICE-SLOW-PREAMBLE` (B: search
  turn → audible brief preamble then the answer; a fast robot action →
  no narration), `SLEEP-CLEAN-STOP` (C: 「睡覺吧」 → goodbye → pose →
  journal shows either `Requested current app stop via` OR the new
  broadened-guard warning, NEVER C6 `microphone unmuted`; sleep summary
  line present or retry logged).
- Deploy = twenty-first install via `reachy-deploy`; persona re-sync gate
  active (Item B edits persona).

## Review log

**Round 1 (2026-09-01, `docs/plans/2026-09-01-field-test-fixes-review-r1.md`,
11 findings — 1 Critical / 7 Important / 3 Minor; 10 accepted, 1 accepted
in part, 0 rejected; plan rev 1 → rev 2):**

1. Critical, ACCEPTED — hold-off was specified at a commit-event seam that
   does not exist in this client; redesigned onto the accepted-turn seam
   with the already-speaking check (A1 rewritten).
2. Important, ACCEPTED — accepted-turn watchdog/late-interrupt cleanup must
   precede the hold-off; a skip must leave no armed repair watchdog (A1).
3. Important, ACCEPTED — skip ≠ denied-turn path; `on_turn_without_response`
   must not run on skip (A1).
4. Minor, ACCEPTED — A2 short-turn qualifier dropped (no duration state).
5. Important, ACCEPTED — commentary transcripts stay out of RECORD/sleep/
   operator persistence surfaces (B1).
6. Important, ACCEPTED IN PART — "selectivity is prompt-enforced" was
   already the plan's stated B4 tradeoff; wording strengthened to name it
   a knowing rung-2 ruling. No design change.
7. Important, ACCEPTED — description edits are inert under an existing
   `installed_tool_spaces.json`; manifest refresh is now a deploy gate (B2).
8. Minor, ACCEPTED — B5 test surface widened (drain/truncate accounting,
   persistence exclusion, boot gate).
9. Minor, ACCEPTED — C4's "no coverage" claim corrected; extend the named
   existing pins.
10. Important, ACCEPTED — retry budget fixed at +4s, decoupled from the env
    timeout (C3).
11. Important, ACCEPTED — `movement_manager.stop()` guarded in the final
    shutdown path too, not just the sleep closure (C2).

**Round 2 (2026-09-02, `docs/plans/2026-09-01-field-test-fixes-review-r2.md`,
3 findings — 0 Critical / 3 Important / 0 Minor; 3 accepted, 0 rejected;
plan rev 2 → rev 3; `codex --profile nova-auto exec`, gpt-5.5, ~4 min):**

1. Important, ACCEPTED — the hold-off must be non-blocking: an awaited sleep
   inside the `transcription.completed` branch would stall the single
   receive loop (`:3481`), so `speech_started` (`:3490`) could never cancel
   it. A1 now requires a per-turn task/timer handle (A1 "Non-blocking").
2. Important, ACCEPTED — the hold-off had no lifecycle: `_pending_responses`
   (`:740`) outlives sessions while the sender worker is per-session
   (`:3451`, `:4080`), so a stale hold-off could enqueue a `response.create`
   the NEXT session sends. A1 now binds the handle to its connection,
   re-checks at fire time, and joins the `on_external_interrupt()` /
   `_barge_shutdown()` cancellation set (A1 "Lifecycle").
3. Important, ACCEPTED — "one short sentence" in B1 was a numeric length cap
   inside a prompt instruction (Global constraint 3; skill line 95).
   Reworded as a calibration principle; the verification row's "one-line"
   wording followed suit.

Codex also verified sound against source: the A1 accepted-turn seam
(`:3674-3811`), the r1 findings 2–4 folds, the B1/B5 transcript-persistence
fold (`:3831-3847`), the B2 manifest deploy trap (`tool_spaces.py:215-220,
336-342`), every C1–C4 seam, and that no item reopens an out-of-scope item.

**Review closed at the round cap** (CLAUDE.md: up to 2 iterations, lowered
from 3 on 2026-09-02). Two rounds, 14 findings: 13 accepted, 1 accepted in
part, 0 rejected. **Rev 3 is cleared for execution.**
