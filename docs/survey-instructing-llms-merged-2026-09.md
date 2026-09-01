# How to instruct an LLM voice agent — merged survey (September 2026)

**Date:** 2026-09-01. **Audience:** the project operator, reading later, to understand how to instruct LLMs — especially `gpt-realtime-2.1-mini` speech-to-speech agents — and why this project's rules are what they are.

**What this merges.** Three surveys run independently on 2026-09-01: **A** = `docs/research-instructing-realtime-voice-2026-09.md` (Claude; OpenAI Realtime + voice-vendor practice) · **B** = `docs/research-instructing-llms-2026-09.md` (Claude; cross-vendor and academic state of the art) · **C** = `docs/codex-research-instructing-2026-09.md` (Codex, run without sight of A and B, plus its deltas section). Background: `docs/research-mini-tool-calling-2026-08.md`. Decisions already taken from this evidence: `docs/plans/2026-09-01-instructing-wave-plan.md` (rev 2) and `.claude/skills/reachy-instructing-model/SKILL.md`.

**Tiers**, preserved from the sources: `[OFFICIAL]` vendor / protocol spec / benchmark maintainer · `[PRACTITIONER]` framework docs, engineering blogs, community reports · `[RESEARCH]` peer-reviewed or arXiv · `[INFERENCE]` reasoning, not sourced.

**On convergence.** Survey C ran blind and was then compared against A and B. Where all three land on the same finding it is marked **independently confirmed by Codex** — that agreement is itself evidence quality. Where they diverge, the divergence is stated rather than smoothed over.

---

## Executive summary — the ten things worth remembering

1. **Tool design outranks prompt wording, and it is not close.** Renaming tools to conventions the model already knows bought **+17% accuracy / −80% schema-misalignment errors**; rewriting descriptions bought **+60.89%** query-level success; instruction rephrasing gives *"no consistent performance trends."*
2. **2026 reversed 2024: write less.** OpenAI measured leaner system prompts at **+10–15% eval scores with 41–66% fewer tokens**; both vendors now warn that ALL-CAPS emphasis and repeated rules cause *over*-triggering.
3. **Speak-then-act is not promptable.** When a message and a tool call share one response the platform decides which executes first; model-generated preambles fail **15–33%** of the time even on full-tier models.
4. **The fix is the protocol, not a hack:** tool call → `function_call_output` → a second, app-issued `response.create`. OpenAI's documented flow and LiveKit's shipped `end_call` default — independently confirmed by Codex.
5. **System-prompt compliance decays hard across a conversation** — 84.8% at round 1 to **33.7% by round 5** — and non-Latin scripts including Chinese show higher error rates each turn. Placement beats volume.
6. **Bare prohibitions are expensive.** Negation costs **23–32%** accuracy across 14 models, and long "never say X, Y, Z" lists read to the model as a menu of likely outputs. The best alternative to a prohibition is a *tool* to call instead.
7. **Make the ground truth a field of the tool return.** Free-text extraction of a decision is **22–26%** inconsistent; a machine-checkable field is **~1%**.
8. **Tool returns carry facts, never new policy** — the 2026 Model Spec puts tool messages at *"No Authority."* Codex's qualification: *render flags* like `require_repeat_verbatim` are fine when a higher-authority surface already defines how to read them; imperative `next_step` fields are not.
9. **More reasoning is not "smarter."** Higher effort increases tool hallucination and decreases instruction adherence while helping multi-step selection — and doubles time-to-first-audio (**1.12s → 2.33s**). Nobody publishes tool-call accuracy by effort level.
10. **Both 2.1 realtime models support function calling but NOT structured outputs** — Codex's most operationally important catch, missed by both Claude surveys. Argument-schema adherence is never guaranteed here, so every robot-action tool must validate at the tool boundary.

---

## The core principle: instruct, don't hard-code

**The escalation ladder.** No single source states it whole; every major one implies it. The project's written form:
**(1) fix the tool** — name, schema, description, return shape, error strings; **(2) fix the context** — what is in
the prompt and *where*; **(3) code only at the execution boundary** — safety, irreversibility, timing,
interruption, physical-state truth.

`[OFFICIAL]` OpenAI states rungs 1–2 almost literally: *"Use multiple agents if improving tool clarity by
providing descriptive names, clear parameters, and detailed descriptions doesn't improve performance"*
(*A practical guide to building agents*, p.16). Note the absence — *"add deterministic code"* is not a rung on
OpenAI's ladder at all. `[OFFICIAL]` Anthropic gives the same instinct as strategy, *"instilling good heuristics
rather than rigid rules"*, and warns against the opposite failure: engineers *"hardcod[ing] complex, brittle logic
in their prompts."* `[PRACTITIONER]` LangChain states it most crisply: *"Use deterministic code for steps with
clear requirements, and give the LLM control where the application must interpret unstructured input or choose
the next action."* <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>

**Where code genuinely belongs — independently confirmed by Codex.** Survey C reached the same boundary from
OpenAI's product surface rather than its essays: `tool_choice` can force `none`/`auto`/`required`/a named
function/an allowed subset, and guardrails plus human approval pause sensitive tool calls *even when the model
decided the action is needed*. Its formulation: semantic policy in the prompt (when to ask, when to act, what to
say, how to recover); hard-code the invariants — auth, approvals, idempotency, retries, rate limits,
**session-closure timing**, validation of every tool input and output. `[OFFICIAL]` One code rung is
protocol-level rather than taste — MCP: *"there SHOULD always be a human in the loop with the ability to deny
tool invocations."* <https://developers.openai.com/api/docs/guides/agents/guardrails-approvals>

**The "who composes the words" test.** `[INFERENCE]` **This is a project house rule, supported by practice but not
a sourced field consensus** — no vendor states it. It answers the operator's question (*is a system-triggered "now
say goodbye" turn instructing or hard-coding?*) by asking who writes the sentence:

| | Who decides *when* | Who composes *the words* | Verdict |
|---|---|---|---|
| `say("你好")` / static `firstMessage` | code | code | hard-coding |
| `generate_reply(instructions="Greet warmly")` | code | model | **instructing** |
| Model emits `end_call` | model | model | instructing |
| Prompt rule: "when the user leaves, …" | model | model | instructing |

`[PRACTITIONER]` The practice it rests on: LiveKit's quickstart and every handoff example use
`generate_reply(instructions=…)` rather than static text; and for *silent handoffs* Vapi flips its own
recommendation to `assistant-speaks-first-with-model-generated-message` with the static `firstMessage` emptied.
The rule of thumb: **static text is acceptable for a cold open; every mid-conversation boundary should be an
instructed generation turn.** <https://docs.vapi.ai/squads/silent-handoffs>

---

## Topic 1 — Tool design as the primary instruction surface

`[OFFICIAL]` **The headline.** Berkeley's BFCL v4 prompt-variation ablation is the cleanest evidence that the
prompt is the *wrong* place to spend effort: Markdown conversion and instruction rephrasing produce *"no
consistent performance trends"*, while **serialization format does** matter (JSON > XML > Python), and format
friction bites small models hardest — `<TOOLCALL>` wrapper tags cost capable models a *"slight performance drop"*
but caused *"significant performance drops"* for 8B models and *"near-zero performance"* for CoALM-70B.
<https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html>

`[RESEARCH]` **Names.** PA-Tool names the mechanism, *schema misalignment*: *"models hallucinate plausible tool
names that are absent from the provided tool schema, due to different naming conventions internalized during
pretraining."* Renaming to match those conventions gave *"improvements of up to 17%, with schema misalignment
errors reduced by 80%"* — no training. Rule: name a tool what the model already expects it to be called.
<https://arxiv.org/abs/2510.07248> `[OFFICIAL]` Relatedly OpenAI lists tools `gpt-realtime-1.5` was *trained*
on — `answer()`, `escalate_to_human()`, `finish_session()` — advising you *"keep the names, signatures, and
descriptions close to these."* **Caveat:** documented for 1.5 only, no 2.x equivalent, so the transfer is
untested — which is why this project treats renames as A/B candidates, not rules.

`[OFFICIAL]` **Descriptions.** Anthropic is categorical: *"Provide extremely detailed descriptions. This is by far
the most important factor in tool performance"* — 3–4 sentences minimum, covering what the tool does, when it
should be used **and when it shouldn't**, and what it does *not* return. `[RESEARCH]` Measured: rewriting
descriptions alone cut *"accuracy degradation by 29.23%"* as catalogs scale and improved *"average query-level
success by 60.89%"* on StableToolBench (arXiv 2602.20426). The failure it addresses dominates — LiveMCPBench
(527 tools, 70 servers): *"[r]etrieval errors account for nearly half of all failures."* The hard part is picking
the right tool, not calling it correctly. `[OFFICIAL]` **A real vendor tension:** OpenAI pulls the other way —
*"keep their descriptions concise and precise"*, listing *"simplifying tool descriptions"* among its lean-prompt
gains. `[INFERENCE]` Reconcilable: both attack *low-information* text — Anthropic fights under-specified
one-liners, OpenAI fights boilerplate. Shared rule: **high information density per token.**
<https://www.anthropic.com/engineering/writing-tools-for-agents>

`[OFFICIAL]` The realtime-specific shape — source of this project's `Use when: … / Do NOT use when: …` convention
(August mini doc §A2), independently confirmed by Codex as official realtime guidance — pairs each tool with its
trigger *and* its exclusion, plus sequencing notes. `[PRACTITIONER]` The most concrete wording tip found: **action
verbs, not returns-language** — Deepgram reports that *"a description like 'respond naturally to the query field'
can cause the model to skip the function and generate a text answer"*; write *"call this to…", "retrieve…", "look
up…"*. `[PRACTITIONER]` **A disagreement worth knowing:** Vapi says the fault is *"almost always in the tool
description — not the prompt"*; Retell says LLMs *"often struggle to determine when to call tools based solely on
tool descriptions"* and to state conditions in the prompt. `[INFERENCE]` Synthesis both Claude surveys reach: **do
both, worded identically, referencing the tool by its exact registered name.**

`[OFFICIAL]` **Count.** *"Aim for fewer than 20 functions available at the start of a turn"* — explicitly *"just a
soft suggestion."* **No vendor states a hard limit;** circulating 10–15 figures are third-party blogs citing
unnamed benchmarks. `[RESEARCH]` The verified result is chance-corrected: adaptive shortlists reached **93.1% vs
87.1%** selection accuracy with **90.3% coverage using ~7 tools instead of 50** (arXiv 2605.24660; authors/venue
unverified — suggestive). `[PRACTITIONER]` Retell adds an *architectural* threshold: leave single-prompt agents
behind at *"5+ different functions/tools"* or *"more than 3-4 conditional paths."*

> ⚠️ **Do not cite** *"BFCL: 43% → 2% accuracy when tools go from 4 to 51"* or *"740 tools: 0–20%."* Survey B
> searched for a primary source and found none.

`[OFFICIAL]` **Combine or split.** Both vendors say *combine*, and both give selection ambiguity — not token
cost — as the reason. The decisive test: ***"If a human engineer can't definitively say which tool should be used
in a given situation, an AI agent can't be expected to do better."*** Counterweight from the same source: each
tool still needs a clear, distinct purpose.

`[OFFICIAL]` **Errors.** MCP draws the line by *who can act on the error*: **tool execution errors** *"contain
actionable feedback that language models can use to self-correct and retry with adjusted parameters"*, returned
in-band with `isError: true`, and read as advice — *"Invalid departure date: must be in the future. Current date
is 08/08/2025."* Anthropic concurs: error responses should communicate *"specific and actionable improvements,
rather than opaque error codes or tracebacks."*

`[RESEARCH]` **The limit of "put it in the schema."** IFEval-FC (arXiv 2509.18420; 750 cases per Codex) tests
format instructions *embedded in JSON schema parameter descriptions* and finds *"even state-of-the-art
proprietary models, including GPT-5 and Claude 4.1 Opus, frequently fail to follow basic formatting rules"*
there. `[INFERENCE]` The split: **description** for *when to use this tool*, **schema constraints** (enums,
`required`, strict types) for *argument shape*, and never prose format rules buried in a parameter description.

`[OFFICIAL]` **Codex's platform caveat, and why schemas alone are not enough here:** both `gpt-realtime-2.1` and
`gpt-realtime-2.1-mini` mark **structured outputs as UNSUPPORTED** on their model pages. Function calling is
supported; guaranteed argument conformance is not. Anthropic ships grammar-constrained *strict tool use* (Codex's
other addition), but that is an Anthropic feature and does not exist on this platform. The consequence is runtime
validation at every tool boundary. <https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini>

---

## Topic 2 — Prompt structure for 2.x realtime models

`[OFFICIAL]` **A generational hazard first.** OpenAI's realtime prompting page contains *two* generations of
advice: the `gpt-realtime-1.5` sections recommend same-response preambles; the `gpt-realtime-2.x` sections
replace much of that with channel-aware behavior and confirm-then-act. **We are on 2.1-mini, so the 2.x guidance
governs** — many widely-quoted "OpenAI says" snippets in blogs are from the 1.5 era.

`[OFFICIAL]` **The current section skeleton** (bolded five are new in 2.x) — independently confirmed by Codex as
"explicit and sectioned": `# Role and Objective` · `# Personality and Tone` · **`# Language`** ·
**`# Reasoning`** · **`# Message Channels`** · **`# Preambles`** · **`# Verbosity`** · `# Tools` ·
**`# Unclear Audio`** · **`# Entity Capture`** · **`# Long Context Behavior`** · `# Escalation`. Same page: *"Not
every use case needs every section"*, and *"Use short, labeled sections. The model should be able to find the
relevant instructions quickly."* <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[OFFICIAL]` **Length is a discipline, not a budget.** No token cap is published. The rule is *"Start simple. Do
not over-prompt upfront… add instructions only for behaviors that fail in testing"*, plus *"Be careful with
constraint words such as `must`, `only`, `never`, and `always`."* Named anti-patterns: vague guidance in place of
trigger/action/exception rules; overlapping `always`/`never` rules with no priority; broad language instructions
like "mirror the user"; and **mentioning tools in the prompt that aren't in the actual tool list**.
`[PRACTITIONER]` Directionally consistent: ElevenLabs — *"Prompts over 2000 tokens increase latency and cost"*;
Vapi supplies the mechanism — the prompt *"loads into the model's context on every turn… increases time to first
token, which the caller experiences as dead air."*

`[OFFICIAL]` **The lean-prompt turn, quantified.** OpenAI's 2026 model guidance: *"leaner system prompts improved
evaluation scores by roughly 10–15% while reducing total tokens by 41–66% and cost by 33–67%"* (*"treat these
ranges as directional"*). Keep domain context, hard constraints, approval boundaries, success criteria; the
operative rule is ***"state each instruction once."*** And a warning matching this project's bug class:
*"Repeating instructions such as 'ask first,' 'do not mutate,' or 'wait for approval' can cause unnecessary
approval requests for safe, expected actions."* Anthropic lands in the same place — *"minimal does not necessarily
mean short"* — and advises dialing back emphasis: where you said *"CRITICAL: You MUST use this tool when…"*, now
say *"Use this tool when…"*. <https://developers.openai.com/api/docs/guides/prompt-guidance>

> ⚠️ **Conflict, unresolved.** The August mini doc quotes the OpenAI realtime *cookbook* endorsing *"Use
> capitalized text for emphasis"* and concludes that for a mini model *"redundancy is cheaper than a precedence
> gamble."* The 2026 guidance from **both** vendors says the opposite. Both are `[OFFICIAL]`; the realtime/mini
> guidance is more specific to this deployment, the lean guidance newer and quantified. Treat "dial it back" as a
> direction to A/B, not a mandate — the skill records this as a supersession.

`[RESEARCH]` **Where an instruction sits beats how it is phrased.** Positional Failures in Long-Context LLMs
(arXiv 2605.23170): moving the task from end-of-context to the middle cost **−12 to −86pp at 8K, −20 to −84pp at
32K, up to −94pp at 64K** (Qwen 2.5-7B: 94% → 0%). Two operational details — *"76% of middle-position errors match
surrounding filler text versus 22% at end position"* (the model answers with nearby text instead of the
instruction), and **duplicating the task at end-of-context restored near-end-level accuracy (within ±4pp)**,
proving the loss is positional, not a capability ceiling. DeepSeek-V3.2 showed ~0pp, so it is model-dependent.

`[RESEARCH]` **Compliance decays across a conversation — the number to remember.** SysBench (arXiv 2408.10943):
GPT-4o leads at CSR 87.1% / ISR 76.4% / **SSR 54.4%** — the best model fully honors the system prompt across a
whole conversation barely half the time — with per-round compliance falling from **84.8% at round 1 to 33.7% by
round 5**. Corroborated four ways: Multi-IF (o1-preview 0.877 → 0.707 by turn three), LLMs Get Lost (**39% average
drop** single- → multi-turn; *"when LLMs take a wrong turn… they get lost and do not recover"*), MultiChallenge
(every frontier model under 50%), IFBench (frontier *"score below 50%"* on held-out constraints vs ~90% on
IFEval — do not read IFEval scores as instruction-following reliability).

> **Resolution of a thin claim.** The circulating cadence *"re-inject a condensed reminder every 3–5 turns"*
> traces only to SEO content — **do not cite the cadence.** The *technique* is well-supported by the positional
> result. Reconciling with *"state each instruction once"*: state each rule once **per generation**, refreshed by
> position, not stacked in one prompt.

`[PRACTITIONER]` **Repetition, honestly split.** ElevenLabs and LiveKit recommend repeating the one or two most
important instructions (*"the model almost always needs more redundancy than you expect"*); Deepgram says define
tone *"once at the start"* because repetition *"dilutes signal"*; OpenAI says state each instruction once.
`[INFERENCE]` The reconcilable version both Claude surveys reach: **repeat one or two critical *behavioral* rules;
never repeat *stylistic* guidance.** `[PRACTITIONER]` **Placement folklore:** the claim that models weight a
prompt's edges over its middle rests on **a single uncited blog**. What *is* documented is that authority comes
from labeling and sectioning, not position.

`[OFFICIAL]` **Literal interpretation got stricter on 2.x:** *"Instruction following is stricter than in earlier
realtime models. If your system prompt contains narrow wording… you might need to broaden or rephrase
instructions."* The fix is to enumerate, not generalize. **Structured context injection** — for anything injected
into the prompt (a memory block, retrieved facts): *"Do not rely on the model to infer source priority from a raw
transcript or large context dump. **Use structure.**"* The template ranks by authority: `### Current State` /
`### Authoritative Sources` / `### Historical or Background Sources` (*"Do not use for current decisions if it
conflicts with a current source"*) / `### Relevant Policy or Rules` / `### Other Context`.

**Examples vs rules — this shifted in 2026.** `[OFFICIAL]` Anthropic still rates examples highly but is stingier —
*"Start with one example (one-shot)"* — and names the anti-pattern plainly: teams *"stuff a laundry list of edge
cases into a prompt… **We do not recommend this.**"* `[RESEARCH]` The optimization literature tilts further: GEPA
(arXiv 2507.19457, **ICLR 2026 Oral**) optimizes **instructions only**, beats the prior SOTA prompt optimizer by
over 10%, and produces prompts *"up to 9.2× shorter"* than the approach that jointly optimized instructions plus
bootstrapped demos. `[INFERENCE]` The arc is the finding: instruction-following got good enough that
demonstrations stopped paying for their tokens. Prefer a well-stated principle; add examples only where a
principle demonstrably failed.

---

## Topic 3 — Speaking around tool calls, and ending a session

Where the three surveys converge most tightly, and where the project's biggest design inversion came from.

`[OFFICIAL]` **The protocol permits it; the platform decides the timing.** A single response may contain both a
message and a function call — *"A Response will include at least one Item, and may have two, in which case the
second will be a function call."* `[PRACTITIONER]` But which executes first is not yours to prompt. A practitioner
debugging exactly this failure captured timestamped logs (tool fired at `13:56:59:962`, farewell audio after) and
diagnosed it plainly: *"When the LLM generates both a response and a function call in the same turn, the execution
timing is determined by the platform, not the prompt instructions."* Their fix was to abandon prompt-ordering
entirely.

`[PRACTITIONER]` **Preamble reliability: 15–33% failure.** Two community threads pointing in *opposite* directions
land in the same place. Wanting preambles: *"probably 1/3 of the time, the agent will not speak a preamble."*
Wanting to suppress them, after trying system prompts, in-context examples and modified function descriptions:
*"a ~15% chance that it generates speech before calling a function"*; *"there is no real control to instruct the
system whether it should create speech before calling a function."*

`[OFFICIAL]` **On 2.x the control surface is the commentary channel** — the single most important official
sentence here: *"tool calls happen in the commentary channel. If you want the assistant to say something before,
during, or after tool use, specify that behavior in relation to the commentary channel."* `gpt-realtime-2` emits
multiple response phases per turn, exposed as a `phase` value on `response.done` — `commentary` (*"Preambles and
tool calls"*) versus `final_answer`. Foundry adds a caveat: *"If the model is interrupted during thinking, it
discards the current chain of thought and starts a new turn."* **Preamble use/don't-use rules:** use before a call
that may take noticeable time or when *"silence would make the assistant feel unresponsive"*; do **not** when the
answer is immediate, the user is confirming or declining, audio is unclear, or *"the tool call is lightweight."*

`[OFFICIAL]` **The highest-yield promptable lever is the tool description, not the system prompt** — independently
confirmed by Codex: *"you can add sample phrases in the tool spec description"*, paired with per-tool labels in the
prompt (`PROACTIVE` / `CONFIRMATION FIRST` / `PREAMBLES`). Note the 1.5-era framing is **concurrent, not
sequential** — *"at the same time as the tool call"* — never speak-then-wait-then-act. Codex adds the mirror-image
failure from the same page: a model instructed to be proactive *"may directly call the tool with no response
audio"*, which is precisely the observed field bug.

`[OFFICIAL]` **2.x reframes "speak then act" as *confirm* then act.** For write tools and external actions:
summarize the intended action, state the consequence, ask for confirmation, and *"Do not call the tool until the
user clearly confirms"* — plus *"Only say an action was completed after the tool call succeeds."* `[INFERENCE]`
That is two model turns with a *user turn between them*, not a same-response preamble.

`[OFFICIAL]` **The idiomatic pattern for genuine speak-then-act: the app drives a second response.** The protocol
already requires one — *"Once we have added the conversation item containing our function call results, we again
emit the `response.create` event from the client."* So tool call → `function_call_output` → second
model-generated response **is the protocol, not a workaround**; framing it as a hack is the actual error.

`[PRACTITIONER]` **LiveKit ships exactly this as its default `end_call` tool, and it is the mirror image of the
naive design** — independently confirmed by Codex, which reached the same page from a different search. The
description sent to the LLM ends: *"This is the final action the agent can take… **Don't generate any other text
or response when the tool is called.**"* The goodbye is produced by the **tool output** — `end_instructions`,
defaulting to *"say goodbye to the user"* — and the lifecycle is: agent generates a final response based on
`end_instructions` → session shuts down after that response completes.
<https://docs.livekit.io/agents/prebuilt/tools/end-call-tool/>

> **The inversion in one line.** The naive design tells the model *"say goodbye, then call the tool."* LiveKit
> tells it *"call the tool, and say nothing"* — and generates the goodbye as the **second** response. Ordering
> becomes a property of the code, not a hope about the model.

**One divergence to port carefully.** `[PRACTITIONER]` LiveKit's `generate_reply(instructions=…)` is *additive* —
session instructions remain active — whereas `[OFFICIAL]` raw `response.create.instructions` **replaces** session
config for that response; the load-bearing spec wording is that server defaults *"will be used if this field is
not set"*, and no merge semantic is documented anywhere. `[INFERENCE]` The hazard is concrete: a bare
`response.create` with `instructions: "說再見"` generates that response with **no persona, no language pinning, no
anti-fabrication rule** — and since English is the documented default response language, it is a plausible route
to an English goodbye from a Chinese-speaking robot. Carry the instruction in the `function_call_output` or a
conversation item instead.

`[PRACTITIONER]` **The other vendor idioms all route around the model:** farewell as a *tool argument* (ElevenLabs
`end_call(message)`, Retell's equivalent); phrase-triggered app-side hangup (Vapi `endCallPhrases`); and app-side
deterministic speech recommended explicitly *over* prompt instructions (Deepgram's server-injected
`InjectAgentMessage`, Pipecat's `TTSSpeakFrame`, LiveKit's `with_filler`, which *"plays audio directly… bypassing
the LLM"*). Codex adds Vapi's default-tools page as the cleanest citation that `endCall` is a callable capability
rather than a prompt wish.

`[OFFICIAL]` **Give the model a tool instead of a prohibition** — independently confirmed by Codex as the right
model-native pattern: *"If the latest audio is silence, background noise, hold music, TV audio, side conversation,
or speech not addressed to you, call `wait_for_user`"*, plus *"do not respond conversationally after calling this
tool."* `[INFERENCE]` A generalizable technique: when you want the model not to do something, give it a valid
alternative action.

> **Sourcing honesty.** Session end is consistently *implemented* as an `end_call`-style tool across
> Vapi/Retell/LiveKit, but **no first-party vendor essay recommends it as best practice** — searches returned
> mostly low-quality SEO content. Genuinely under-documented; choices here should be justified by our own
> transcripts, not by appeal to consensus.

---

## Topic 4 — Small and distilled models (the mini tier)

`[OFFICIAL]` **What OpenAI actually says** (changelog 2026-07-06): 2.1 is *"an updated realtime reasoning model
with improved alphanumeric recognition, silence and noise handling, and interruption behavior"*; 2.1-mini is *"a
faster, lower-cost distilled reasoning model."* Both: 128k context, 32k max output, cutoff 2024-09-30, function
calling, reasoning tokens, prompt caching; mini is roughly 6–7× cheaper on text and ~3× on audio.

> **A qualification the three surveys settle between them.** Survey B wrote that OpenAI's positioning of 2.1-mini
> *"names the same three axes we are failing on — reasoning, tool use, instruction following."* **Codex disputes
> this, and Codex is right:** the 2.1-mini model page says only *distilled, faster, lower-cost* and publishes the
> same modality/function-calling matrix as full 2.1. Survey A reached the same conclusion independently — the
> capability framing lives in the prompting guide's model-selection table, which still reads `gpt-realtime-2` vs
> `gpt-realtime-1.5` and **has not been refreshed for the 2.1 generation.** So: **mini capability loss is a
> reasonable inference with practitioner support, not a model-page fact.** The skill states it that way.

`[PRACTITIONER]` **What practitioners report failing** — the richest taxonomy, from a team that abandoned the
tier: *"The Mini can detect a pattern… but it doesnt action on it"*; *"The mini is unable to count. So a prompt
that says 'allow max 2 tries and then call off topic'… the mini cannot do"*; *"Ignores scope boundaries"*; *"the
mini does 60% of what the realtime does."* Two practitioners, no benchmark — but specific and testable.
`[INFERENCE]` The generalizable shape: **mini fails at rules requiring state accumulation across turns, and at
detect-then-act rules where detection succeeds but the action doesn't fire.** Prompts are also not portable across
mini generations — one developer reported 2.1-mini refusing a tool that `gpt-realtime-mini` called correctly with
identical prompt and tools. <https://community.openai.com/t/giving-up-on-realtime-mini/1379423>

`[RESEARCH]` **What degrades first, ordered by evidence strength:** (1) **negation and multi-constraint
tracking** — *"a dominant failure mode, causing accuracy reductions of 23-32% across models"*, below 35% at the
hardest constraint level; (2) **format/serialization robustness**, per BFCL v4's small-model collapse under
wrapper tags; (3) **long reasoning chains actively hurt small students** — models ≤3B get *worse* from long
chain-of-thought traces from strong teachers (arXiv 2502.12143); (4) **non-English, per additional turn**
(Multi-IF); (5) **multi-step chaining via compounding error** — *"erroneous tool calls cascade across subsequent
reasoning steps."*

`[OFFICIAL]` **What works**, from OpenAI's *Seven tips for prompting voice agents*: precision over volume
(changing *"inaudible"* → *"unintelligible"* *"significantly improved how the model handled noisy inputs"*);
bullets over paragraphs (*"Realtime models have shown to follow short bullet points better than long
paragraphs"*); short varied sample phrases; and *"convert non-text rules (such as numerical conditions) into
text"* — write "IF MORE THAN THREE FAILURES THEN ESCALATE", not "IF x > 3". `[INFERENCE]` That last tip lands
harder given mini cannot count: spelling thresholds out in words targets a documented weakness cheaply.

`[PRACTITIONER]` **The banlist finding** — the most consequential item for this codebase: *"Long enumerated 'never
say X, Y, Z' lists are an anti-pattern. Every banned phrase is a token in the model's active context — and under
output uncertainty, recently-activated tokens can be over-sampled, so the verbose ban effectively becomes a menu
of likely outputs."* Independently corroborated by a second practitioner source recommending *"four or five, plus
one line on what to do instead"*, and pointing the same way as OpenAI's constraint-word warning. `[INFERENCE]` The
mechanism (token over-sampling) is a **hypothesis, not a measured result** — it ranks high because it *predicted
this project's observed bug*: the exact banned phrases recurring every turn.
<https://github.com/VapiAI/docs/blob/main/fern/prompting-guide.mdx> `[PRACTITIONER]` One more documented mini
failure, already defended against in the shipped prompt: **example phrases mistaken for triggers** ("literal
transition matching" on 2.1-mini). Mitigation: label examples as tone demonstrations, not conditions —
示範語氣，不是觸發條件.

**Mitigations, ranked by measured return** `[RESEARCH]/[OFFICIAL]`: fix the schema/names (+17% / −80%); remove the
decision entirely via composite tools; rewrite descriptions (+60.89%); shrink the per-turn tool surface; constrain
via enums and `required` rather than prose — converting Anthropic's *"[Haiku models] may infer missing
parameters"* from a silent guess into a validation error the model can self-correct from. `[INFERENCE]` That
phrase is a precise, under-appreciated failure description: **the small tier does not stall on ambiguity, it
guesses** — exactly how confident false narration is produced.

---

## Topic 5 — Reasoning effort

`[OFFICIAL]` **Levels and default:** `minimal` (*"Lowest latency matters most and the task is simple"*) · `low`
(*"responsiveness plus basic reasoning"*) · `medium` (*"must reason through multi-step tasks"*) · `high` ·
`xhigh`. Guidance: *"Start with `low` for most production voice agents"*, and OpenAI staff confirm *"the default
reasoning level is `low` when no value is specified."* **Source discrepancy flagged:** Azure Foundry documents
only `minimal|low|medium|high` and a 256,000-token context; OpenAI's pages document `xhigh` and 128,000. Trust
OpenAI's pages for the OpenAI endpoint.

`[PRACTITIONER]` **Measured cost** — the only concrete numbers found (The Batch, 2026-05-15, reporting OpenAI
benchmark disclosures): **1.12s to first audio at minimal reasoning, 2.33s at high**, against a standard where
conversational voice *"benefit[s] from latency lower than 500 milliseconds"*. The spread by benchmark is the
sharpest trade-off in the whole survey set: Conversational Dynamics — **minimal** led at 96.1%; Big Bench Audio —
**high** tied Gemini 3.1 Flash Live Preview at 96.6%; Scale AI Audio MultiChallenge (instruction retention,
memory, coherence) — **xhigh** placed first at 48.45%.
<https://www.deeplearning.ai/the-batch/openai-challenges-speech-to-speech-leaders>

> `[INFERENCE]` **The effort level that wins on conversational feel is the one that loses on instruction
> retention, and vice versa.** A companion robot needs both — which argues for keeping the conversational path
> fast and moving hard sequencing off it (composite tools, app-side ordering) rather than buying reliability with
> latency everywhere.

`[RESEARCH]` **More reasoning increases fabrication.** The Reasoning Trap (arXiv 2510.22977): *"progressively
enhancing reasoning through RL increases tool hallucination proportionally with task performance gains… appearing
when reasoning is instilled via supervised fine-tuning **and when it is merely elicited at inference by switching
from direct answers to step-by-step thinking**"* — with *"a fundamental reliability-capability trade-off: reducing
hallucination consistently degrades utility."* Corroborated from the instruction side by MathIF: *"models that
reason more effectively often struggle to comply with user directives."*

> ⚠️ **This directly qualifies the August mini doc's action #7** (*"try `reasoning.effort: medium`"* to fix
> multi-step tool selection). Raising effort should be expected to *increase* fabrication and *decrease* adherence
> while improving chaining. Still worth running — it is one config field — but measured against **all three**
> failure modes, never shipped on a chaining improvement alone. The skill records this as a supersession.

`[OFFICIAL]` **The cheaper knob:** a `## Reasoning` prompt block makes effort conditional *within* a turn — *"For
direct answers, simple lookups, and short confirmations, respond quickly and do not reason. For multi-step tasks,
tool decisions, troubleshooting, or escalation, reason before acting."* Codex independently recommends the same
shape. **The gap, confirmed three ways:** `medium` is officially placed at multi-step reasoning and higher effort
officially costs latency and output tokens — but **no source publishes tool-call accuracy as a function of
reasoning effort.** Survey A found none; Codex searched independently and states the same absence. Measure
on-robot rather than argue.

---

## Topic 6 — Chinese-primary agents

`[OFFICIAL]` **English is the default and must be explicitly overridden.** *"A user's accent is not the same as
their intended language. A user may speak English with a Hindi, Spanish, French, or Mandarin accent and still
expect English responses."* The page names as anti-patterns the exact instructions many prompts contain — *"Mirror
the user. / Respond naturally in the user's language. / Switch languages when appropriate."* — because *"the model
may interpret accent, filler words, backchannels, or isolated foreign words as a reason to switch languages."* The
sanctioned block (inverted for a Chinese-primary agent) defaults to one language and switches only on an explicit
request or a **substantive utterance** — *"a complete request, question, or correction in another language, not
just a greeting, name, address, filler word, or borrowed phrase"* — never on accent, fillers, backchannels, names,
or isolated foreign words. **The clause most prompts are missing**, newly relevant now that commentary is a
separate channel: *"Keep preambles, spoken bridges, tool-related messages, and final answers in the same
language."*

`[RESEARCH]` **Non-Latin scripts degrade fastest across turns.** Multi-IF: every model degrades with each
additional turn, and *"languages with non-Latin scripts (Hindi, Russian, and Chinese) generally exhibit higher
error rates."* <https://arxiv.org/abs/2410.15553>

**Should the prompt itself be in Chinese or English? Genuinely unsettled.** `[OFFICIAL]` There is **no official
OpenAI guidance on the language of the `instructions` field**; a targeted search found none. What is observable is
that every OpenAI example prompt, including the multilingual ones, is written in English with the target language
named inside it. `[RESEARCH]` Pulling the other way, *Cross-Lingual Prompt Steerability* (arXiv 2512.02841; five
languages, three LLMs, three benchmarks): *"English-prompt setting exhibits slightly worse overall performance
compared to Same-language setting, yielding lower average accuracy and consistency, as well as significantly
higher accuracy variance."* `[INFERENCE]` The study is on text LLMs, calls the gap marginal, and says nothing
about distilled models — so an all-Chinese prompt is defensible but not clearly optimal, and a wholesale rewrite
into English is **not** supported. The one narrow experiment worth running: **English for tool-selection rules
only**, since tool names, enums and schemas are already English and a Chinese rule referencing an English tool
name is the exact cross-lingual seam most likely to under-trigger — sharpened by 2.x's stricter literal matching.

`[PRACTITIONER]` **Mini-tier non-English regression**, carried forward and still unresolved: language drift on 2.1
despite explicit instructions; *"2.1 mini is so nice fastest reasoning model, but on other languages except
english is very very bad"*; a production deployment reporting fabrication against supplied context after a forced
snapshot migration. No staff resolution on any thread. `[INFERENCE]` **Some of what prompting is trying to fix
here may be a model-tier limitation.** The decisive experiment is not another prompt revision — it is running the
same prompt once on full `gpt-realtime-2.1` and measuring the delta.

`[RESEARCH]` **Codex's addition: the Chinese voice-eval ecosystem.** Neither Claude survey found these, and they
are the most useful new material Codex brought back. **VCB-Bench** (Tencent) — Chinese benchmark built from *real
human speech*, evaluating instruction following, multi-turn dialogue, speaker/environment/content variation,
accents (Tianjin, Beijing, Dongbei, Sichuan), speech speed, background chat and code-switching. **VocalBench-zh**
(SJTU) — Chinese voice-agent leaderboard across Qwen3-Omni, Qwen2.5-Omni, Kimi-Audio, GLM-4-Voice, Baichuan-Omni,
MiniCPM-o, MiMo-Audio. **VoiceAssistant-Eval** — listening/speaking/viewing, 13 task types. `[OFFICIAL]` Codex also
notes the honest limit: the `gpt-realtime-2.1` model page makes **no Chinese-specific benchmark claim**, so
Mandarin, Traditional Chinese, code-switching and local acoustics must be validated in-house. `[INFERENCE]` Its
practical advice: author prompts in the target spoken variety rather than translating from English; for
Taiwan-facing agents prefer Traditional Chinese, Taiwan terminology and local formats; and treat exact-identifier
handling as *more* important in Chinese flows, because users mix Mandarin numerals, Latin letters, aliases and
spoken punctuation. <https://github.com/Tencent/VCB-Bench>

---

## Topic 7 — Grounding claims in evidence (not narrating fiction)

The project's characteristic bug: the robot says "I looked to the right" when no motion tool ran.

`[RESEARCH]` **It has a name and a taxonomy.** MIRAGE-Bench (arXiv 2507.21017, Berkeley) is *"the first unified
benchmark for eliciting and evaluating hallucinations in interactive LLM-agent scenarios."* Agentic hallucinations
are actions unfaithful to *"(i) task instructions, (ii) execution history, or (iii) environment observations."*
Its canonical example is an agent that **hallucinates a successful navigation step despite observing that it
failed, then builds on the false success** — this bug almost exactly.

`[OFFICIAL]` **Why it happens, per OpenAI** — and the diagnosis matters more than the block: *"Realtime models are
eager to help. **If the prompt mentions a tool that is not actually available, or if the tool list does not match
the prompt, the model may invent a tool name or pretend it completed the action.**"* The remedy is a
`## Tool Availability` block: use only tools in the current list; do not invent, assume or simulate; treat a tool
mentioned in the instructions but absent from the list as unavailable; *"Only say an action was completed after
the relevant tool call succeeds."* `[INFERENCE]` Any design that *gates* tools while the prompt still describes
them in prose — a toolbox pattern, for instance — creates precisely the named condition.

**Techniques, ranked by strength of evidence.** (1) `[RESEARCH]` **Force a machine-checkable decision field
instead of free text** — the strongest number available: agents execute their *stated* decisions almost perfectly
(**0.7% inconsistency for Claude Haiku 4.5, 1.4% for DeepSeek-Reasoner**) but **22–26%** when the conclusion must
be extracted from free text (arXiv 2606.00476; single-author preprint, one task family, large effect).
(2) `[RESEARCH]` **Instruct mechanical rule application** — an explicit "apply the rule mechanically" instruction
cut rule misapplication from **13.9% to 6.8%** in the same work. (3) `[OFFICIAL]` **Semantic returns instead of
opaque identifiers** — the one quantified vendor intervention: resolving *"arbitrary alphanumeric UUIDs to more
semantically meaningful and interpretable language… significantly improves Claude's precision in retrieval tasks
by reducing hallucinations."* Changing only the return representation changed the hallucination rate.
(4) `[OFFICIAL]` **Structured returns with named fields** — MCP `outputSchema` + `structuredContent`.
(5) `[OFFICIAL]` **JSON envelopes with render flags** — independently confirmed by Codex as official realtime
guidance: *"If your tool returns a raw string and separately asks the model to 'repeat exactly,' the model may be
more prone to paraphrasing, truncation, or blending in its own preamble"*; the shape is
`{response_text, require_repeat_verbatim: true}` with the matching render rule in *both* the Tools section and the
tool definition (August mini doc §C3). (6) `[OFFICIAL]` **Suppress process narration explicitly** — *"Do not
include process/tooling narration."* Narration never produced cannot be false.

`[OFFICIAL]` **The authority rule, and Codex's qualification of it.** OpenAI's Model Spec (2026-08-18) places tool
output at the bottom of the authority ladder: Root → System → Developer → User → Guideline, then ***"No Authority:
assistant and tool messages."*** `[INFERENCE]` Survey B concluded that a `next_step: "call camera now"` field
smuggled into a tool return is officially non-authoritative and should underperform a real instruction or a forced
`tool_choice`. **Codex qualifies this, and the qualification is what the project adopted:** *render flags* such as
`require_repeat_verbatim` — or a farewell-context cue — are legitimate **when the higher-authority prompt or tool
description already defines how to interpret them**. Tool returns may carry facts and render metadata; they must
not create new policy. <https://model-spec.openai.com/2026-08-18.html>

> `[INFERENCE]` **The structural fix outranks all of the above.** If the action and the observation happen in the
> *same* tool call returning `{"direction_moved": "right"}`, there is no window in which the model can invent a
> movement. Grounding is best achieved by making the ground truth a **required field of the return**, not by
> adding a rule.

`[RESEARCH]` **One finding that names this project's exact operating condition.** τ²-bench (arXiv 2506.07982,
Sierra/Princeton) measures agents that must follow policy *while conversing*: pass^1 falls **74% (retail) → 56%
(airline) → 34% (telecom)**, and *"shifting from no user operation to a collaborative setup where the agent must
guide the user results in a substantial drop in pass^1 (18% drop for gpt-4.1 and 25% drop for o4-mini)."*
`[INFERENCE]` **Policy compliance collapses precisely when the agent must talk to someone while acting.** Some of
the rule-following gap here is a structural property of conversational agency, not a defect in the prompt — an
argument for structural fixes over more rules, and for a realistic acceptance bar in PRD §8.

---

## What we decided for Reachy

The rev-2 plan turns the above into four scope items, under the operator ruling that *the model decides which
tools to call and what to say; the app instructs it and holds the safety rails*. One platform fact bounds the
whole wave — **function calling yes, structured outputs no on both 2.1 models** (Topic 1, Codex) — so every schema
claim is "JSON Schema **plus runtime validation at the tool boundary**", never SDK strict mode.

**Scope 1 — goodbye-then-sleep becomes an instructed generation turn.** The field bug (2026-09-01 journal: the
sleep turn produced a tool-call-only response with no audio delta at all, so the quiesce correctly found a silent
speaker and posed a mute robot) is the textbook case from Topic 3. The fix is LiveKit's inversion:
`go_to_sleep`'s description ends *"do not generate any other text or response when calling this tool"* and
pre-declares how the return's farewell cue is used; the tool returns
`{"status": "sleeping_soon", "farewell_context": …}` as **facts and a cue, not an instruction** (Topic 7, with
Codex's render-flag qualification); the app then issues one follow-up response through the serialized
`_safe_response_create()` queue with `tool_choice: "none"` so no late tool call can ride the goodbye, waits for
*that specific* `response.done` plus the audio drain, and only then poses. The `finish_session` rename was
**downgraded to an A/B alias**, precisely because the in-distribution name list is documented for 1.5 only.

**Scope 2 — head motion is rung-3 code, with honest returns.** Three `look_around` calls with correct direction
arguments each queued a move and captured a photo of a person straight ahead: the daemon face tracker was
overriding the queued goto. That is physical-state truth, which the ladder assigns to code at the execution
boundary — not a prompt problem. The honesty catch comes straight from Topic 7: `direction_requested` **stays**
until motion is actually verifiable; `direction_moved` may be introduced only when backed by a real check of the
movement manager's pose, with an explicit partial/error state when unconfirmed. A field that *looks* like ground
truth but isn't would be worse than no field.

**Scope 3 — prompt restructure, subtractive first.** Adds the 2.x blocks the shipped prompt silently lacks
(`# Message Channels`, `# Preambles`, `# Reasoning`, `## Tool Availability`, the cross-channel language clause) and
removes more than it adds: enumerated banlists become a handful of negatives each carrying its reason and an
alternative action, with `wait_for_user` as the required positive alternative for silence and unaddressed speech
(Topics 2–4); the base profile's broad 「跟随对方的语言」 mirror rule — a *named* OpenAI anti-pattern — is replaced
by the narrow Taiwan-Chinese default/substantive-switch rule (Topic 6); numeric length caps and trigger-like
phrase lists come out per the operator's own rule, now evidence-backed; and memory injection is restructured into
labeled current-user context with stated conflict priority, per the structured-context template. One product
ruling made *first*: since the client suppresses `phase=="commentary"` items and 2.x preambles live in that
channel, audible preambles are impossible today — so the blocks teach the model where tool talk belongs, and the
spoken-preamble goal is dropped for this wave.

**Scope 4 — tool-surface audit (rung 1).** The highest-yield rung by measured evidence, so it gets its own scope
item: runtime validation on every robot-action tool with corrective, model-readable errors naming allowed values
(no silent coercion — `bool("false") == True` is exactly the confident-guess failure mode); schema hygiene; a
returns audit across *all* physical-action tools so no status string overstates completion; errors rewritten as
advice the model can self-correct from; an active-surface audit recording `Tools in session` per mode; a toolbox
continuation check against mini's documented setup-then-stall risk; and a report-only names sanity-check, with
renames A/B-gated.

**And in the skill.** The escalation ladder, the who-composes-the-words test, use-when/do-NOT-use-when
descriptions, returns-carry-facts-not-policy, errors-as-advice, lean-beats-thorough, negatives-carry-their-reason,
the 2.x block list, the override trap, and the mini-tier expectations — each traceable to a section above. Two
items from the August mini doc are marked **superseded**: its capitals-and-redundancy advice (now an
over-triggering risk) and its unqualified `reasoning.effort: medium` suggestion (now requires a three-metric A/B).

---

## Honest gaps

**What no source measures:**

- **Tool-call accuracy as a function of reasoning effort.** Searched independently by Survey A and by Codex;
  neither found a primary source. A one-field on-robot experiment, not an argument to be won.
- **Whether a mini-tier model can reliably narrate-then-call in one response.** No source measures it. Every proxy
  points the same way (15–33% failure on full-tier models; *"the mini can detect a pattern… but it doesnt action
  on it"*), but the inference is from adjacent failures only. Do not bet an irreversible action on it.
- **Goodbye and session-end best practice.** Consistently *implemented* as an `end_call`-style tool across three
  vendors, but **no first-party essay recommends it**; searches returned mostly SEO content.
- **Which instruction-following capability degrades first under distillation**, with numbers, across tiers of one
  model family. Topic 4's ordering is assembled from adjacent results plus vendor positioning — a working
  hypothesis, not a measured ranking.
- **Enums vs free-form arguments for voice agents** (no vendor doc addresses it; a third-party "enum collapse"
  claim could not be corroborated); **tool-count hard limits** (no vendor states one); **a clean ablation of tool
  return shape against what the model then says** (the UUID→semantic-name result is real and quantified, but
  nobody ran the experiment the operator's question implies); **prompt language, Chinese vs English** (one
  text-LLM study, marginal effect, contradicted by OpenAI's demonstrated convention).

**Do-not-cite list, carried forward from Survey B** — these circulate widely with no primary source: *"BFCL: 43% →
2% accuracy when tools go from 4 to 51"* and *"740 tools: 0–20%"* (use the three verified tool-count results in
Topic 1); *"re-inject the system prompt every 3–5 turns"* (technique supported, **cadence invented**); *"put the
heaviest rules at the top and bottom"* (one uncited blog — folklore); the claim that the 2026 Model Spec makes
honesty explicitly outrank confidentiality (the spec was fetched and does not support it); and several 2026
single-author preprints seen but unverified — IF-RewardBench, AgentHallu, "Operational Hallucination and Safety
Drift," "Calibrated Enough to Know, Not Calibrated to Act," "Analyzing the Narration Gap in LLM-Solver Loops,"
"Compaction as Epistemic Failure," "Invocation-Level Reliability" — named only so a future reader knows they were
seen and not relied upon.

**Mechanism claims flagged as hypotheses, not results:** the banlist over-sampling explanation (two independent
practitioner sources assert it, OpenAI's constraint-word warning points the same way, and it predicted this
project's observed bug — but it is not measured); and the mini failure taxonomy (two practitioners, no benchmark).

**Access notes:** `platform.openai.com/docs/*` and `openai.com/index/*` return HTTP 403 to automated fetch, so
some content is cited via mirrors (the openai-python spec, Microsoft Foundry, core42) that matched on every
cross-checkable field. Two Foundry/OpenAI discrepancies stand unresolved (context window, `xhigh`); trust OpenAI's
pages for the OpenAI endpoint.

---

## Source index

### `[OFFICIAL]` — OpenAI

Guides: [realtime-models-prompting](https://developers.openai.com/api/docs/guides/realtime-models-prompting) · [realtime-conversations](https://developers.openai.com/api/docs/guides/realtime-conversations) · [prompt-guidance](https://developers.openai.com/api/docs/guides/prompt-guidance) · [prompt-engineering](https://developers.openai.com/api/docs/guides/prompt-engineering) · [function-calling](https://developers.openai.com/api/docs/guides/function-calling) · [latency-optimization](https://developers.openai.com/api/docs/guides/latency-optimization) · [guardrails-approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals) · [define-agents](https://developers.openai.com/api/docs/guides/agents/define-agents) · [changelog](https://developers.openai.com/api/docs/changelog)

Models and reference: [gpt-realtime-2.1](https://developers.openai.com/api/docs/models/gpt-realtime-2.1) · [gpt-realtime-2.1-mini](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini) · [realtime reference](https://developers.openai.com/api/reference/realtime) · [server-events](https://developers.openai.com/api/reference/resources/realtime/server-events) · [client-events](https://developers.openai.com/api/reference/resources/realtime/client-events) · [ruby/realtime](https://developers.openai.com/api/reference/ruby/resources/realtime) · [response_create_params spec](https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/realtime/realtime_response_create_params.py)

Cookbook and PDFs: [realtime_prompting_guide](https://developers.openai.com/cookbook/examples/realtime_prompting_guide) · [out-of-band transcription](https://developers.openai.com/cookbook/examples/realtime_out_of_band_transcription) · [gpt-5.1 prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-1_prompting_guide) · [Seven tips (PDF)](https://cdn.openai.com/API/docs/realtime-prompting-guide.pdf) · [practical guide to building agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) · [Model Spec 2026-08-18](https://model-spec.openai.com/2026-08-18.html) · [Agents SDK](https://openai.github.io/openai-agents-python/agents/)

Mirrors (openai.com returns 403 to fetch): [Foundry realtime-2](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/realtime-2) · [Foundry realtime-audio](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio) · [core42 API reference](https://www.core42.ai/compass/documentation/realtime-api-reference)

### `[OFFICIAL]` — Anthropic, protocol and benchmark maintainers

[writing-tools-for-agents](https://www.anthropic.com/engineering/writing-tools-for-agents) · [effective-context-engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) · [building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents) · [multi-agent-research-system](https://www.anthropic.com/engineering/multi-agent-research-system) · [best-practices-for-prompt-engineering](https://claude.com/blog/best-practices-for-prompt-engineering) · [define-tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) · [strict-tool-use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use) · [claude-prompting-best-practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) · [MCP server/tools spec](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) · [BFCL v4 prompt variation](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html) · [BFCL leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html)

### `[PRACTITIONER]` — frameworks, vendors, engineering writing

LiveKit: [end-call-tool](https://docs.livekit.io/agents/prebuilt/tools/end-call-tool/) · [end_call.py](https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/beta/tools/end_call.py) · [tools/definition](https://docs.livekit.io/agents/logic/tools/definition/) · [agents-handoffs](https://docs.livekit.io/agents/logic/agents-handoffs/) · [build/audio](https://docs.livekit.io/agents/build/audio/) · [start/voice-ai](https://docs.livekit.io/agents/start/voice-ai/) · [async-tools](https://livekit.com/blog/async-tools-voice-agents) · [prompting-voice-agents](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic)

Vapi: [prompting-guide.mdx](https://github.com/VapiAI/docs/blob/main/fern/prompting-guide.mdx) · [assistants/create](https://docs.vapi.ai/api-reference/assistants/create) · [default-tools](https://docs.vapi.ai/tools/default-tools) · [silent-handoffs](https://docs.vapi.ai/squads/silent-handoffs)

Deepgram / Pipecat / ElevenLabs / Retell: [prompting-voice-agents](https://developers.deepgram.com/docs/prompting-voice-agents) · [inject-agent-message](https://developers.deepgram.com/docs/voice-agent-inject-agent-message) · [pipecat function-calling](https://docs.pipecat.ai/pipecat/learn/function-calling) · [pipecat text-to-speech](https://docs.pipecat.ai/pipecat/learn/text-to-speech) · [elevenlabs prompting-guide](https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide) · [elevenlabs end-call](https://elevenlabs.io/docs/eleven-agents/customization/tools/system-tools/end-call) · [retell prompt-engineering](https://docs.retellai.com/build/prompt-engineering-guide) · [retell end-call](https://docs.retellai.com/build/single-multi-prompt/end-call) · [retell basic-settings](https://docs.retellai.com/build/single-multi-prompt/configure-basic-settings)

Essays and reporting: [12-factor-agents](https://github.com/humanlayer/12-factor-agents) · [LangChain what-is-an-agent](https://www.langchain.com/blog/what-is-an-agent) · [Cognition don't-build-multi-agents](https://cognition.com/blog/dont-build-multi-agents) · [bitter lesson (Lance Martin)](https://rlancemartin.github.io/2025/07/30/bitter_lesson/) · [Chroma context rot](https://www.trychroma.com/research/context-rot) · [The Batch on speech-to-speech](https://www.deeplearning.ai/the-batch/openai-challenges-speech-to-speech-leaders) · [Vapi call-ending post-mortem](https://legacy.patrickmichael.co.za/how-do-you-end-calls-smoothly-vapi-complete-guide-professional-voice-agent-call-endings) · [relinns voice-AI prompting](https://relinns.com/blogs/guide-to-voice-ai-prompting) · [MLM tool selection](https://machinelearningmastery.com/the-complete-guide-to-tool-selection-in-ai-agents/) · [kn8 webmcp tool design](https://www.kn8.ai/blog/webmcp-tool-design-best-practices) · [over-tooled agent problem](https://tianpan.co/blog/2026-04-19-over-tooled-agent-problem) · [Agora on 2.x preambles](https://prod.agora.io/en/blog/gpt-realtime-2-is-here-and-preambles-change-how-voice-agents-feel) · [Codex lead interview](https://linearb.io/dev-interrupted/podcast/openai-codex-thibault-sottiaux-agentic-autonomy)

OpenAI community: [giving up on realtime mini](https://community.openai.com/t/giving-up-on-realtime-mini/1379423) · [2.1 release thread](https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896) · [2.1-mini not calling tools](https://community.openai.com/t/model-gpt-realtime-2-1-mini-not-calling-function-tools-in-sip-realtime-while-gpt-realtime-mini-works-with-the-same-prompt-tools/1386141) · [default reasoning level](https://community.openai.com/t/gpt-realtime-model-default-reasoning/1387803) · [preamble inconsistent](https://community.openai.com/t/realtime-api-preamble-inconsistent/1361953) · [speech before tool call](https://community.openai.com/t/realtime-api-sometimes-creates-speech-before-a-tool-call-sometimes-doesnt/1153507)

### `[RESEARCH]` — instruction following and decay

[2408.10943](https://arxiv.org/abs/2408.10943) SysBench · [2410.15553](https://arxiv.org/abs/2410.15553) Multi-IF · [2501.17399](https://arxiv.org/abs/2501.17399) MultiChallenge · [2505.06120](https://arxiv.org/abs/2505.06120) LLMs Get Lost · [2507.02833](https://arxiv.org/abs/2507.02833) IFBench · [2311.07911](https://arxiv.org/abs/2311.07911) IFEval · [2402.10962](https://arxiv.org/abs/2402.10962) Instruction (In)Stability · [2512.14754](https://arxiv.org/abs/2512.14754) Reliability in Instruction-Following · [2605.23170](https://arxiv.org/abs/2605.23170) Positional Failures · [2505.14810](https://arxiv.org/abs/2505.14810) MathIF · [2603.05344](https://arxiv.org/abs/2603.05344) instruction fade-out (thin) · [ACL 2026 SRW 119](https://aclanthology.org/2026.acl-srw.119/) negation · [2512.02841](https://arxiv.org/html/2512.02841) Cross-Lingual Prompt Steerability

### `[RESEARCH]` — tool use, grounding, small models, prompt optimization

[2510.07248](https://arxiv.org/abs/2510.07248) PA-Tool · [2602.20426](https://arxiv.org/abs/2602.20426) rewriting tool descriptions · [2508.01780](https://arxiv.org/abs/2508.01780) LiveMCPBench · [2605.24660](https://arxiv.org/abs/2605.24660) how many tools · [2509.18420](https://arxiv.org/abs/2509.18420) IFEval-FC ([HF](https://huggingface.co/papers/2509.18420)) · [2506.07982](https://arxiv.org/abs/2506.07982) τ²-bench · [2608.06370](https://arxiv.org/abs/2608.06370) Bitter Lesson of Tool Calling · [2507.21017](https://arxiv.org/abs/2507.21017) MIRAGE-Bench · [2510.22977](https://arxiv.org/abs/2510.22977) The Reasoning Trap · [2606.00476](https://arxiv.org/abs/2606.00476) faithfulness gap · [2502.12143](https://arxiv.org/abs/2502.12143) small models & strong reasoners · [2509.14257](https://arxiv.org/abs/2509.14257) student-centred distillation · [2605.07725](https://arxiv.org/abs/2605.07725) tool-call error cascade · [2507.19457](https://arxiv.org/abs/2507.19457) GEPA · [2406.11695](https://arxiv.org/abs/2406.11695) MIPRO

### `[RESEARCH]` — Chinese voice evaluation (Codex additions)

[VCB-Bench](https://github.com/Tencent/VCB-Bench) · [VocalBench-zh](https://github.com/SJTU-OmniAgent/VocalBench-zh) · [VoiceAssistant-Eval](https://mathllm.github.io/VoiceAssistantEval/) · [2503.20215](https://arxiv.org/abs/2503.20215) Qwen2.5-Omni · [2504.18425](https://arxiv.org/abs/2504.18425) Kimi-Audio

### Repo documents

`docs/research-instructing-realtime-voice-2026-09.md` (Survey A) · `docs/research-instructing-llms-2026-09.md` (Survey B) · `docs/codex-research-instructing-2026-09.md` (Survey C) · `docs/research-mini-tool-calling-2026-08.md` (August background) · `docs/plans/2026-09-01-instructing-wave-plan.md` (rev-2 plan) · `.claude/skills/reachy-instructing-model/SKILL.md` (operating contract)
