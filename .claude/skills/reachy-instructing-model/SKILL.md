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
every rule lives in the three research docs; do not re-litigate them, cite
them:

- `docs/research-instructing-realtime-voice-2026-09.md` (realtime/voice, Sept 2026)
- `docs/research-instructing-llms-2026-09.md` (general instructing SOTA, Sept 2026)
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

- **Names in-distribution.** Prefer names the model saw in pretraining
  (`finish_session` over `go_to_sleep`-style internal vocabulary). Check the
  realtime doc §3.3 before inventing a name.
- **Descriptions: use-when / do-NOT-use-when pairs**, symmetric across
  sibling tools (the camera/move_head asymmetry was the original look-bug).
  Slow tools carry PREAMBLE sample phrases *in the description*; fast tools
  are marked proactive. Session-ending tools instead say: do not generate
  any other text when calling this.
- **Enums and required fields, not prose.** The mini tier's characteristic
  failure is confident guessing; strict schema is the only layer that makes
  guessing structurally impossible. Format rules buried in parameter
  descriptions are poorly followed — use enums.
- **Returns carry ground truth in named fields.** If the robot may say it, a
  tool must have returned it in a named field (`direction_moved`, not vibes
  — free-text extraction: 22–26% inconsistency; named field: ~1%).
  Fabricated action narration is fixed by making the true fact the easiest
  thing to say, not by prohibition.
- **Returns state facts, never orders.** Tool messages hold "No Authority"
  in the 2026 Model Spec; a `next_step: "call camera"` field is officially
  non-authoritative. To make the model speak after a tool, the
  `function_call_output` may carry *what to convey* (the LiveKit farewell
  pattern), but flow control belongs to `response.create` / `tool_choice`.
- **Errors are advice addressed to the model**, written to enable
  self-correction: "head is already at the right limit; call camera to see
  what is there" — never a bare traceback or status code.

## Prompt rules

- **Lean beats thorough.** 2026 reversal of 2024 advice: leaner prompts
  scored 10–15% better; repetition and ALL-CAPS emphasis now cause
  over-triggering. Start under-specified, add only against observed failure.
- **Positive rules, and every negative carries its reason.** Bare negation
  costs 23–32% accuracy. Enumerated banlists become "a menu of likely
  outputs" — keep at most 4–5 bans, each with its why and an alternative
  action.
- **No numeric length caps, no keyword-trigger lists** (operator rule,
  memory `prompt-style-judgment-over-caps`, now evidence-backed). Prefer a
  well-stated calibration principle; add few-shot examples only where a
  principle demonstrably failed, and label them 示範語氣，不是觸發條件.
- **Structure blocks the 2.x models expect**: `# Message Channels` /
  `# Preambles` ("tool calls happen in the commentary channel" — say what
  should be spoken before/during/after tool use in relation to it),
  `## Tool Availability` (a prompt describing tools absent from the current
  tool list invites the model to *simulate* them — mandatory with
  `open_toolbox` gating), and a cross-channel language clause (preambles,
  bridges, tool messages, answers all in the same language).

## Boundary moments (greetings, goodbyes, mode switches)

Mid-conversation ceremony is an **instructed generation turn**, not a static
string and not a hope that the model volunteers it. The field's line: who
*composes the words* decides whether it is instructing (model composes) or
hard-coding (app composes). A system-triggered `response.create` where the
model writes the sentence is instructing.

- **Speak-then-act is not promptable.** When a message and a tool call share
  one response, execution timing belongs to the platform; preamble ordering
  fails 15–33% even on full-tier models. For speak-then-irreversibly-act
  (sleep): the tool description forbids extra speech, the
  `function_call_output` carries 「跟使用者道別」-style guidance, the app
  issues the follow-up `response.create` (the documented OpenAI protocol),
  waits for playout, then acts.
- **The override trap.** `response.create.instructions` *replaces* the
  session prompt for that response — persona, Chinese pinning and
  anti-fabrication rules all vanish. Any per-response instruction must
  either re-state what matters or ride the `function_call_output` /
  a conversation item instead. Never ship a bare per-response instruction.

## Small-model (mini-tier) specifics

- Expect: literal interpretation, no reliable tool chaining (compose
  sequences into one tool), fastest degradation on non-Latin scripts,
  worse results from *longer* reasoning chains.
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
