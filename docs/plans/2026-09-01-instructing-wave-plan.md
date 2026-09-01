# LLM-First Instructing Wave — Proposal-Stage Plan (2026-09-01, rev 2)

**Status:** revised after external review (Codex round 1: 41 findings, 41
accepted, 0 rejected — log at bottom). Awaiting operator approval; task
decomposition then follows the superpowers writing-plans skill.

**Goal:** fix the two field bugs from the 2026-09-01 live test (missing
goodbye before sleep; head not turning on 「看右邊」) and restructure the
instruction surface, under the house rule: the model decides which tools to
call and what to say; the app instructs it and holds the safety rails.

**Authority:** operator ruling (2026-09-01); evidence base — all four docs:
`docs/research-instructing-realtime-voice-2026-09.md`,
`docs/research-instructing-llms-2026-09.md`,
`docs/codex-research-instructing-2026-09.md`,
`docs/research-mini-tool-calling-2026-08.md`; contract
`.claude/skills/reachy-instructing-model/SKILL.md`.

**Constraints:** model stays `gpt-realtime-2.1-mini`; no new dependencies;
no daemon changes (app-only); Chinese-primary; operator prompt rules (no
numeric caps, no keyword-trigger lists; examples labelled 示範語氣，不是觸發條件).
Platform fact that bounds the whole wave: both 2.1 realtime models support
function calling but NOT structured outputs — argument-schema adherence is
not guaranteed, so every schema claim below is "JSON Schema + runtime
validation at the tool boundary", never SDK strict mode (the installed SDK's
realtime tool param has no `strict` field).

## Scope item 1 — goodbye-then-sleep (instructed generation turn)

Journal evidence (2026-09-01 00:17:48–58, nineteenth install): the
「進入睡眠模式」turn produced a tool-call-only response — no audio delta at
all — so the quiesce correctly found `speaker quiet after 0.0s` and posed a
silent robot. Speak-then-act ordering is not promptable; invert it:

- `go_to_sleep` description ends: do not generate any other text or
  response when calling this tool — and pre-declares how the return's
  farewell context is used (「收到 sleeping_soon 後，用一句自然的話道別，然後就
  安靜」-style rule; the higher-authority surface defines the cue, per the
  no-new-policy-in-returns rule).
- The tool executes the input quiesce (mic mute, barge disarm) but NOT the
  pose/stop; its `function_call_output` returns facts + cue:
  `{"status": "sleeping_soon", "farewell_context": …}` (fact/cue field, not
  an instruction-named field).
- **Sequencing is a named response-cycle helper, not loose calls**: the
  farewell response goes through the existing serialized
  `_safe_response_create()` queue (never a raw `connection.response.create`),
  with the SDK's nested payload shape `response={"tool_choice": "none"}`
  (verified against openai 2.28.0: `AsyncRealtimeResponseResource.create`
  takes a nested `response` object) so no late tool call can ride the
  goodbye. The helper resolves only when THAT specific response reaches
  `response.done` (correlate by response id — a bare
  `wait_for_reply_finished()` can return before the queued farewell even
  starts, which would recreate the original bug), then the existing bounded
  audio drain runs, then pose/stop.
- **Two sleep paths, split**: the voice path (model called the tool) runs
  prepare-sleep → farewell cycle → finalize. The lifecycle paths
  (inactivity timeout, shutdown) have no live model turn to speak — they
  keep the current direct pose/stop closure. `go_to_sleep.needs_response`
  stays false for the generic dispatcher; the session-ending branch owns
  the one follow-up response explicitly.
- A/B candidate (downgraded per review): alias tool named `finish_session`
  (same implementation, controlled exposure) — the in-distribution name
  list is documented for gpt-realtime-1.5 only, and a raw rename touches
  profile tool lists, toolboxes, record allowlist, tests, docs. Measure
  sleep-tool selection + false positives before any registered-name change.

## Scope item 2 — look_around / move_head head motion (execution-boundary code + honest returns)

Journal evidence (2026-09-01 00:15:52, 00:16:16, 00:17:24): three
`look_around` calls, each with correct direction args, each queuing
`move_head right` and capturing after settle — and the photo shows the
person straight ahead. The daemon face tracker (`main.py:475`, enabled at
boot, weight 1.0) overrides the queued goto while a face is in view.
Physical-state truth = legitimate rung-3 code, shaped per review:

- **Not** the `set_speaking` anchor pattern (it re-captures an anchor and
  falls back to it after the goto completes — the review's critical
  catch), and **not** `set_hold_still` (drops queued moves). Either a
  dedicated movement-manager command ("suspend daemon tracking without
  anchoring over completed manual moves") or `set_head_tracking(False)` +
  restore.
- **Single ownership of the window**: `look_around` owns
  suspend → move → settle → capture → restore; `move_head` (which it
  delegates to) must not independently restore mid-window. Factor one
  helper with an owner.
- **Restore to the PREVIOUS tracking state**, not unconditionally on — the
  operator may have turned tracking off; this needs a small seam that
  exposes the current desired state.
- **`move_head` gets its own semantics**: its contract says "leave it
  there", so restoring tracking right after would yank the head back.
  Either tracking stays suspended until a later command re-arms it, or the
  description/return changes to an honest temporary gesture. Decide at task
  decomposition; the two tools do NOT share one recipe.
- **Returns**: `direction_requested` STAYS until motion is verifiable
  (review's honesty-regression catch — `MoveHead` returns at queue time and
  the motion API attests nothing). `direction_moved` may be introduced
  only backed by a real check of the movement manager's commanded/current
  pose, with an error/partial state when unconfirmed.

## Scope item 3 — prompt restructure (subtractive + 2.x blocks)

- `# Message Channels` / `# Preambles` blocks — with the product decision
  made FIRST: the client currently suppresses `phase=="commentary"` items,
  and on 2.x preambles live in the commentary channel, so audible
  slow-tool preambles are impossible under current suppression. Ruling for
  this wave: keep suppression, DROP the spoken-preamble goal (prompt blocks
  still teach the model where tool talk belongs); a selective
  allow-commentary policy is a later, separately-tested wave.
- `# Reasoning` block (per-turn steering; session `reasoning.effort` stays
  where the operator set it — any level change needs the three-metric A/B).
- `## Tool Availability` block + **de-contradiction pass**: the base
  profile currently tells the model to use boxed tools directly (`tv`,
  `nas`, `calendar`, `tasks`, `drive`, `notion_add`, `email_send`) while
  the hardening block says open the toolbox first. One authoritative
  toolbox section; delete the duplicate direct-use rules.
- Cross-channel language clause + language de-contradiction: replace the
  base profile's broad 「如果对方用其他语言，就跟随对方的语言」 mirror rule
  (named anti-pattern) with the narrower Taiwan-Chinese
  default/switch-only-on-explicit-or-substantive rule that the hardening
  block already carries; normalize script/locale (Traditional Chinese,
  Taiwan terminology) across the profile body and greeting.
- Subtractive pass on enumerated banlists → few bans, each with reason and
  alternative action; `wait_for_user` (already in code) becomes the
  REQUIRED positive alternative for silence/background/unaddressed speech
  in the tool-policy block.
- Operator-rule compliance sweep of our own prompt: remove numeric length
  caps (「一句」「一句話答完」 etc. in profile + hardening) in favor of
  qualitative calibration; convert trigger-like phrase lists (who_is_this
  quoted phrasings, toolbox routing term lists) into semantic use
  conditions or tool-description examples.
- Memory injection restructure: `format_memory_for_prompt` currently
  prepends unlabeled "Things you remember" text ahead of the persona —
  restructure to labeled current-user-context placed with role/policy, with
  stated conflict priority (current user corrections beat remembered facts).

## Scope item 4 — tool-surface audit (rung 1)

- **Runtime validation everywhere** (schema alone proves nothing on this
  platform): `move_head` rejects unknown directions with a corrective error
  instead of silently moving front; `head_tracking` stops coercing
  `bool("false") == True`; `open_toolbox` validates `category` against the
  real toolbox set; `set_conversation_mode` validates `mode` against
  `MODE_VALUES` — all before the runtime seam, all errors model-readable
  with allowed values named.
- **Schema hygiene**: kill the meaningless required `dummy` boolean on
  `stop_dance`/`stop_emotion` (empty-object schema like `go_to_sleep`).
- **Returns audit across ALL physical-action tools** (not just
  look_around): every one returns named facts the model may cite; no status
  string overstates completion ("looking right" at queue time is exactly
  the overstatement).
- **Errors as model-directed advice** enabling self-correction.
- **Active-surface audit**: record `Tools in session` counts per
  mode/toolbox state; verify the prompt names no unexposed capability
  without `open_toolbox` routing.
- **Toolbox continuation check**: cold productivity/media request →
  `open_toolbox` → session-update ack → follow-up response calls the real
  tool WITHOUT re-asking the user (mini's documented setup-then-stall risk);
  if it stalls, fix the `open_toolbox` return/description before adding
  prompt text.
- Names sanity-check against pretraining conventions (report-only; renames
  are A/B-gated per Scope item 1's caveat).

## Explicitly out of scope

Model upgrade (cost-pinned to mini; full-2.1 allowed only as a one-shot
diagnostic run), new dependencies, daemon changes, reasoning-effort changes
without a three-metric A/B (tool hallucination, adherence, selection),
commentary-audio policy changes (deferred, see Scope item 3).

## Verification

Two layers (review finding: journal-only verification is fragile against
this many sequencing changes):

1. **SDK-simulated tests**: farewell `function_call_output` reaches the
   model; exactly one follow-up `response.create` with
   `response={"tool_choice": "none"}` (assert the outbound payload);
   pose/stop waits for the farewell's own `response.done` + drain;
   tracking suspension neither drops nor undoes the queued move and
   restores the prior state; invalid tool arguments are rejected with
   corrective errors; lifecycle sleep paths still pose directly.
2. **On-robot journal probes**: 「睡覺吧」→ goodbye audio then pose (journal
   order: farewell response.done → drain → pose); 「看右邊」→ head visibly
   turns, capture matches; cold 「幫我加個行程」 toolbox continuation; the
   operator's ear on the restructured prompt.

## Review log

- **Round 1 — Codex (`codex exec`, 2026-09-01, 290k tokens, code-grounded):
  41 findings, 41 accepted, 0 rejected.** Highest-value: the three critical
  feasibility catches (set_speaking anchor fallback would defeat the
  tracking pause; `_safe_response_create` enqueue-and-return would race the
  farewell wait; inactivity-sleep path breaks if the tool stops posing),
  the `direction_moved` honesty regression, the commentary-suppression vs
  audible-preamble contradiction (ruled: keep suppression, drop the spoken
  goal this wave), and four prompt self-contradictions our own operator
  rules already forbid (numeric caps, trigger lists, mirror-language rule,
  direct-use-vs-toolbox duplication). Accepted-with-qualification: the
  4–5-ban count is a heuristic, not a contract (finding 23);
  `finish_session` downgraded to alias A/B (findings 15/16). Full findings
  in the session transcript; skill updated in the same revision.
