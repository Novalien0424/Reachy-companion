---
name: reachy-instructing-model
description: Use when changing anything the realtime model reads or is judged by — persona.md and the system prompt, tool names, schemas, descriptions, return values, error strings, session config (reasoning, VAD, per-response instructions) — when adding, merging or removing a tool, when a live test shows Reachy picking the wrong tool, asking or not asking, or saying the wrong thing, or when tempted to fix a model misbehavior with deterministic code around the model.
---

# Reachy Instructing Model

**The model understands intent and decides which tools to call and what to
say; the app instructs it and holds the safety rails** (operator ruling,
memory `llm-first-instruction-design`). Reachy runs on `gpt-realtime-2.1-mini`
only, so every rule here is about what mini does. The evidence is in the
research docs: cite it, don't re-litigate it. They are
`docs/research-compact-tool-text-2026-10.md` (how mini sees tools, D-039),
`docs/research-instructing-realtime-voice-2026-09.md`,
`docs/research-instructing-llms-2026-09.md`, and
`docs/codex-research-instructing-2026-09.md` (structured outputs are
unsupported on both 2.1 realtime models).

## First: check what mini actually got

Before rewording anything, read the robot journal for the failing turn:

- `Tool call received` lines: which tool mini reached for **first**, with what
  arguments.
- The tool's result as delivered to the model, not as the tool returned it.
  A result can lose fields on the way; then the fix is transport, not wording.
- `Tools in session (<mode>, startup, N)`: the surface it had (20 / 20 / 4).
- The first `Response usage in=` of a fresh session: the fixed prefix.

## The escalation ladder

Fix in this order, and record which rung you used:

1. **The tool** — name, schema, description, *return shape*, error strings.
   The highest-yield surface by measured evidence (description rewrites: +60%
   query success; prompt rewording: "no consistent performance trends").
2. **The context** — what is in the prompt and *where*; per-response
   instructions for boundary moments (system-prompt compliance decays from
   ~85% at turn 1 to ~34% by turn 5 — placement beats volume).
3. **Code — only at the execution boundary**: safety, irreversibility,
   timing, interruption, physical-state truth (read the device back before
   reporting success: `hanova/ha_client.confirm_cast_started`). "Add code"
   appears on no vendor's ladder for *behavioral* fixes.

**Two text revisions that don't move a live rate end the rewording.** Change
the tool's shape (one composite action, new return fields, error advice), or
record the rate as mini's current ceiling with k/n in `feature_list.json`.
Never propose a full-tier run or a model switch: mini is fixed. Shape changes
have repeatedly fixed what text could not: one-call play worked once
`nas play` found the trip itself.

## Tool surface

- **Design for mini's first call.** Mini doesn't chain tools reliably. Watch
  which call it makes first for an intent and make that call finish the job
  (`nas play` finds and plays). A long description that teaches a recipe
  doesn't change the first call.
- **About 20 tools, all loaded, no toolboxes** (OpenAI: "fewer than 20
  functions at the start of a turn"). A box costs an extra call mini can
  miss, and changing tools mid-session lowers the cache rate. A phase the app
  knows gets its own smaller surface (record mode: 4).
- **Families of 2–3 related actions** (`dance`, `head`, `memory`, `face`, via
  `tool_family.dispatch_family` over the unchanged upstream classes). Stop
  there: an all-in-one tool raised no-call runs from 10% to 28%.
  A new capability joins a family or replaces a tool; going past 20 needs the
  operator's OK. Wiring a tool in: `docs/adding-a-skill.md` (the profile's
  `default_tools` and `CORE_TOOL_NAMES`).
- **Names: in-distribution, as an A/B.** Renames touch the profile's
  `default_tools`, `CORE_TOOL_NAMES` and tests; check the realtime doc §3.3
  first.

## Tool text

- **A description holds purpose, scope, the contrast with sibling tools, and
  grounding rules.** Contrast must be symmetric across siblings (the
  camera/move_head asymmetry was the original look bug).
- **No lists of example phrases and no ALL-CAPS or assertive cues:** both make
  small models over-trigger. Use at most one Chinese anchor, where a confusion
  was observed (「把電視關掉」 on `home_control`). Keep the Chinese→English
  glosses on arguments. The search tool keeps its one 示範語氣 preamble.
  Session-ending tools say "do not generate any other text when calling this".
- **Write full sentences, fewer of them.** Telegraphic compression hurts.
  Measure with the harmony render or the robot's `usage in=`, not with JSON
  characters, which overstate tools by about 14%.
- **Enums and required fields, validated at the tool boundary.** Function-
  calling JSON Schema is supported, structured outputs are not, so argument
  adherence isn't guaranteed. Reject with a corrective error naming the
  allowed values.
- **Returns carry ground truth in named fields.** If Reachy may say it, a
  tool returned it in a named field. Fabricated narration is fixed by making
  the true fact the easiest thing to say.
- **Refuse, never fall back silently.** Mini narrates a silent fallback as if
  it were what the user asked for.
- **Errors name their fix and who acts.** A fix the model can make says "call
  again yourself with …" (the archive is English: retry with the English
  words, without asking the user). A message for the person says to tell
  them. A shared "say what the error says" rule makes mini read retry advice
  aloud.
- **Exact read-backs come from the tool.** Before an irreversible act whose
  details the person must hear (email), the result carries the sentence in
  `response_text` under `require_repeat_verbatim`. Composing it alone, mini
  drops details.
- **Render flags, not policy.** Tool messages hold "No Authority"; a flag
  like `require_repeat_verbatim` works only because the system rules define
  it, once. Flow control belongs to `response.create` / `tool_choice`.

## Prompt rules

- **Each fact once, where the model needs it.** Before adding a rule, grep
  `persona.md`, `prompts.py` (`_HARDENING_BLOCK`), every tool description and
  the locked profile for the rules it repeats or collides with. Moving or
  deleting one beats adding one: a rule repeated in several places outranks
  the one you meant.
- **Mini reads every phrase as an instruction.** 「要放就直接放」 was read as
  "play without asking". When an equivalence matters, state it:
  naming 影片庫, NAS or YouTube counts as naming the source.
- **Lean beats thorough.** Every rule competes with the others. The reason to
  cut is quality, not cost: cached text is cheap.
- **Positive rules; every negative carries its reason** and an alternative
  action. The strongest alternative is a tool: `wait_for_user` is the no-op
  for silence and speech not meant for Reachy.
- **No numeric length caps, no keyword-trigger lists** (memory
  `prompt-style-judgment-over-caps`). Few-shot only where a principle
  demonstrably failed, labelled 示範語氣，不是觸發條件.
- **Structure blocks 2.x expects:** message channels and preambles (tool
  calls go in the commentary channel), per-turn reasoning steering, one
  `## Tool Availability` line (what is not in your tool list, say you can't
  do), and a cross-channel language clause.

## Boundary moments (greetings, goodbyes, mode switches, app-started turns)

Mid-conversation ceremony is an **instructed generation turn**: the app
inserts the facts as a conversation item, then triggers `response.create`,
and the model composes the words (`_send_startup_greeting_prompt`).

- **Speak-then-act is not promptable.** For speak-then-irreversibly-act
  (sleep): the tool description forbids extra speech and says how the
  return's farewell context is used; the app issues the follow-up
  `response.create` with `tool_choice: "none"`, waits for its `response.done`
  plus audio playout, then acts.
- **The override trap.** `response.create.instructions` *replaces* the
  session prompt for that response. Re-state what matters or ride the
  `function_call_output` instead; never ship a bare per-response instruction.

## Session config

- More `reasoning.effort` is not smarter: it raises tool hallucination and
  lowers adherence while helping multi-step selection. The robot runs
  `medium` (instance `.env`; the code default is `low`). Change it only with an
  on-robot A/B that measures all three.

## Red flags

| Thought | Do instead |
|---|---|
| "One more wording tweak" after two that didn't move the rate | Change the tool's shape, or record the ceiling |
| "Check it on the full model" | Mini is fixed; don't propose it |
| "Add a rule to the prompt" | Grep for the rule it collides with; can a return or error carry it? |
| "It passed" after one run | 3–4 fresh runs, k/n against the last build |
| "The error text must be wrong" | Check the model received it whole |
| "Add example phrases so it triggers" | They over-trigger; add the contrast with the sibling tool |

## Old patterns

Superseded by D-039 and the 2026-10 research: example-phrase lists in
descriptions (the search preamble stays), the full-tier diagnostic rung, and
`open_toolbox` gating. From `research-mini-tool-calling-2026-08.md`: its
capitals-and-redundancy advice and its unqualified "try
`reasoning.effort: medium`".

## Verification

Name the observed failure, the rung used and the journal signal that shows
the fix. Measure candidate wordings first with `qa_replay.py --override`
(text only, no deploy), then confirm on the shipped wheel and live: replay
overstates live rates, and one unrelated sentence can move a replay rate a
lot (D-045). Then run it live (`reachy-deploy` § Live verification): fresh
sessions, mic muted, 3–4 runs per case, k/n against the previous build, and
the new prefix size. Prompt-only changes still ride the persona sync ritual.
