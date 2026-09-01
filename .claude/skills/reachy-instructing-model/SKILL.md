---
name: reachy-instructing-model
description: Use when changing ANYTHING the realtime model reads or is judged by — system prompt / persona, tool names, schemas, descriptions, return values, error strings, session config (reasoning, VAD, per-response instructions) — or when tempted to fix a model misbehavior with deterministic code around the model.
---

# Reachy Instructing Model

## Overview

Operator ruling (2026-09-01, memory `llm-first-instruction-design`): **the
model understands intent and decides which tools to call and what to say; the
app instructs it and holds the safety rails.** This skill is the operating
contract for every change to the model's instruction surface. Evidence for
every rule lives in the four research docs; do not re-litigate them, cite
them:

- `docs/research-instructing-realtime-voice-2026-09.md` (realtime/voice, Sept 2026)
- `docs/research-instructing-llms-2026-09.md` (general instructing SOTA, Sept 2026)
- `docs/codex-research-instructing-2026-09.md` (independent Codex survey —
  required for the 2.1 feature-matrix caveats, e.g. structured outputs
  UNSUPPORTED on both 2.1 realtime models, and the Chinese voice-eval
  ecosystem: VCB-Bench, VocalBench-zh)
- `docs/research-mini-tool-calling-2026-08.md` (mini-tier tool calling — two
  findings superseded: see "Supersessions" below)

## The escalation ladder (house rule)

When the robot misbehaves, fix in this order, and record which rung you used:

1. **The tool** — name, schema, description, *return shape*, error strings.
   This is the highest-yield surface by measured evidence (renaming to
   in-distribution conventions: +17% accuracy; description rewrites: +60%
   query success; prompt rewording: "no consistent performance trends").
2. **The context** — what is in the prompt and *where*; per-response
   instructions for boundary moments (system-prompt compliance decays from
   ~85% at turn 1 to ~34% by turn 5 — placement beats volume).
3. **Code — only at the execution boundary**: safety, irreversibility,
   timing, interruption, physical-state truth (tracking weights, quiesce
   ordering, motor limits). "Add code" appears on no vendor's ladder for
   *behavioral* fixes.

## Tool design rules

- **Names in-distribution — as an A/B, not a rule.** The common-tool-name
  list (`finish_session` etc.) is documented for `gpt-realtime-1.5` only;
  transfer to 2.x is untested. Treat renames as measured A/B candidates
  (and mind the registration cost: profile tool lists, toolboxes, tests all
  hard-code names — use an alias tool, not a raw rename). Check the
  realtime doc §3.3 before inventing a new name.
- **Descriptions: use-when / do-NOT-use-when pairs**, symmetric across
  sibling tools (the camera/move_head asymmetry was the original look-bug).
  Slow tools carry PREAMBLE sample phrases *in the description*; fast tools
  are marked proactive. Session-ending tools instead say: do not generate
  any other text when calling this.
- **Enums and required fields, not prose — enforced at runtime too.** The
  mini tier's characteristic failure is confident guessing. But know the
  platform limit: both 2.1 realtime models support function-calling JSON
  Schema and do NOT support structured outputs — schema adherence on
  arguments is not guaranteed, and reply text is never schema-constrained.
  So every robot-action tool validates its arguments at the tool boundary
  (reject with a corrective, model-readable error naming the allowed
  values — never silently coerce or fall back). Format rules buried in
  parameter descriptions are poorly followed — use enums.
- **Returns carry ground truth in named fields.** If the robot may say it, a
  tool must have returned it in a named field (`direction_moved`, not vibes
  — free-text extraction: 22–26% inconsistency; named field: ~1%).
  Fabricated action narration is fixed by making the true fact the easiest
  thing to say, not by prohibition.
- **Returns state facts and render cues, never new policy.** Tool messages
  hold "No Authority" in the 2026 Model Spec; a `next_step: "call camera"`
  field is officially non-authoritative. Render flags
  (`require_repeat_verbatim`, farewell context) are legitimate ONLY when a
  higher-authority surface — the session prompt or the tool description —
  already defines how to interpret them; the return itself creates no
  policy. Flow control belongs to `response.create` / `tool_choice` (e.g.
  `tool_choice: "none"` on a farewell response so no late tool call can
  ride it).
- **Errors are advice addressed to the model**, written to enable
  self-correction: "head is already at the right limit; call camera to see
  what is there" — never a bare traceback or status code.

## Prompt rules

- **Lean beats thorough.** 2026 reversal of 2024 advice: leaner prompts
  scored 10–15% better; repetition and ALL-CAPS emphasis now cause
  over-triggering. Start under-specified, add only against observed failure.
- **Positive rules, and every negative carries its reason.** Bare negation
  costs 23–32% accuracy. Enumerated banlists become "a menu of likely
  outputs" — keep negatives to a handful (a review heuristic, not a hard
  count — the no-numeric-caps rule applies to our own contracts too), each
  with its why and an alternative action. The strongest alternative to a
  prohibition is a *tool*: `wait_for_user` is the official no-op action for
  silence, background audio, and speech not addressed to the robot — a
  valid thing to *do* instead of a rule about not speaking.
- **No numeric length caps, no keyword-trigger lists** (operator rule,
  memory `prompt-style-judgment-over-caps`, now evidence-backed). Prefer a
  well-stated calibration principle; add few-shot examples only where a
  principle demonstrably failed, and label them 示範語氣，不是觸發條件.
- **Structure blocks the 2.x models expect**: `# Message Channels` /
  `# Preambles` ("tool calls happen in the commentary channel" — say what
  should be spoken before/during/after tool use in relation to it; check
  what the client does with commentary-phase items BEFORE promising audible
  preambles), `# Reasoning` (per-turn effort steering — cheaper than
  raising the session level, which is A/B-gated), `## Tool Availability`
  (a prompt describing tools absent from the current tool list invites the
  model to *simulate* them — mandatory with `open_toolbox` gating; the
  prompt must never say "use X" for a boxed tool without naming
  `open_toolbox` first), and a cross-channel language clause (preambles,
  bridges, tool messages, answers all in the same language).

## Boundary moments (greetings, goodbyes, mode switches)

Mid-conversation ceremony is an **instructed generation turn**, not a static
string and not a hope that the model volunteers it. Project house rule
(supported by LiveKit/Vapi/Retell practice, not a sourced field consensus):
who *composes the words* decides whether it is instructing (model composes)
or hard-coding (app composes). A system-triggered `response.create` where
the model writes the sentence is instructing.

- **Speak-then-act is not promptable.** When a message and a tool call share
  one response, execution timing belongs to the platform; preamble ordering
  fails 15–33% even on full-tier models. For speak-then-irreversibly-act
  (sleep): the tool description forbids extra speech AND pre-declares how
  the return's farewell context is to be used; the `function_call_output`
  carries the context as facts; the app issues the follow-up
  `response.create` with `tool_choice: "none"` (the documented OpenAI
  protocol — and no late tool call can ride the goodbye), waits for THAT
  response's `response.done` plus audio playout, then acts.
- **The override trap.** `response.create.instructions` *replaces* the
  session prompt for that response — persona, Chinese pinning and
  anti-fabrication rules all vanish. Any per-response instruction must
  either re-state what matters or ride the `function_call_output` /
  a conversation item instead. Never ship a bare per-response instruction.

## Small-model (mini-tier) specifics

- Expect: literal interpretation, no reliable tool chaining (compose
  sequences into one tool), fastest degradation on non-Latin scripts,
  worse results from *longer* reasoning chains. (The capability loss is
  practitioner-reported inference — the 2.1-mini model page itself claims
  only "distilled, faster, lower-cost".)
- Every instruction-surface change re-checks the ACTIVE tool surface: the
  `Tools in session` count per mode/toolbox state, and that the prompt
  names no capability that is currently unexposed without routing through
  `open_toolbox`.
- More `reasoning.effort` is not "smarter": 2026 results show it increases
  tool hallucination and decreases instruction adherence while helping
  multi-step selection. Change effort only with an on-robot A/B that
  measures all three.
- If a failure survives rungs 1–2, run the same prompt once on full
  `gpt-realtime-2.1` as a diagnostic: if full-tier fixes it, it is a cost
  decision, not a prompting problem — stop spending prompt revisions on it.

## Supersessions

Two items in `docs/research-mini-tool-calling-2026-08.md` are superseded by
the Sept 2026 docs: its capitals-and-redundancy advice (now over-triggering
risk) and its unqualified "try `reasoning.effort: medium`" (must measure
tool hallucination + adherence + selection together).

## Verification

Instruction changes get evidence like code changes: name the observed
failure, the rung used, and the on-robot journal signal that would show the
fix (tool-call lines, `Tools in session`, transcript). Prompt-only changes
still ride the persona sync ritual (`reachy-deploy`) and an antenna-wake
check.
