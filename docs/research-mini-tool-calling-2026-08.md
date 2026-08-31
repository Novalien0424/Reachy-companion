# Research: Tool-calling & instruction-following on `gpt-realtime-2.1-mini`

Date: 2026-08-31. Scope: web research only, no code changes. Target: our Reachy Mini
voice app on the OpenAI Realtime API over WebSocket, model `gpt-realtime-2.1-mini`,
41 registered function tools, Chinese-primary.

**Evidence labels used throughout:**
- `[OFFICIAL]` — stated in OpenAI (or Microsoft Foundry mirror) documentation / cookbook.
- `[COMMUNITY]` — developer forum report, single-source or anecdotal.
- `[INFERENCE]` — my reasoning applied to our specific failure modes; not sourced.

Observed failure modes this report targets:
1. **Wrong tool selection** — "turn right and look who's there" → calls `camera` only,
   never `move_head`→`camera`, then falsely narrates "I looked to the right".
2. **Ignoring instruction constraints** — banned preambles and trailing filler questions
   appear every turn; length rules ignored.
3. **Fabrication against tool results** — told to repeat a returned name (雲霓) exactly,
   the model said a different name.

---

## A. Official guidance for function-calling reliability on realtime / mini models

### A1. Tool count: 41 is over OpenAI's own soft ceiling

`[OFFICIAL]` The function-calling guide states: **"Aim for fewer than 20 functions
available at the start of a turn"** (with the caveat "though this is just a soft
suggestion"), and **"Keep the number of initially available functions small for higher
accuracy."**
<https://developers.openai.com/api/docs/guides/function-calling>

`[OFFICIAL]` The realtime prompting docs make the same point behaviourally: keeping tool
lists focused per conversation phase "prevents the model from misselecting tools," and
dynamic tool lists via `session.update` "reduce confusion compared to providing all tools
simultaneously."
<https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[OFFICIAL]` The realtime cookbook's **Dynamic Conversation Flow** pattern is the concrete
implementation: "Instead of exposing the model to all possible rules and tools at once,
you only provide what's relevant to the active phase… you use `session.update` to
transition, replacing the prompt and tools with those needed for the next phase. This
approach reduces the model's cognitive load."
<https://github.com/openai/openai-cookbook/blob/main/examples/Realtime_prompting_guide.ipynb>

`[COMMUNITY]` / third-party engineering write-ups converge on 15–20 tools as the practical
knee: "Most production teams see accuracy drop noticeably once they cross 15 to 20 tools
in active rotation," with named failure modes *attention dilution*, *tool collision*
(semantic boundaries between similar tools blur), and *prompt budget starvation*
(~4–6k tokens for 40 schemas, re-sent every turn).
<https://tianpan.co/blog/2026-04-19-over-tooled-agent-problem>

`[RESEARCH]` Chance-corrected academic result (arXiv 2605.24660, May–Jun 2026): shorter
adaptive tool shortlists measurably improve selection even when the right tool is present
in both lists — 93.1% vs 87.1% correct selection with adaptive-short vs fixed-5, widening
to **76.8% vs 60.9% on medium-difficulty queries where the correct tool is present but not
ranked first**. Our `move_head` case is exactly "present but not ranked first."
<https://arxiv.org/abs/2605.24660>

> **Recommendation A1** — Cut the per-turn tool surface from 41 to ≲15. Two mechanisms,
> both supported: (a) `allowed_tools` to narrow the surface — `[OFFICIAL]` "Keep the tool
> surface narrow with `allowed_tools`"
> (<https://developers.openai.com/api/docs/guides/realtime-mcp>); (b) swap tool sets with
> `session.update` when the app enters a mode (idle / vision / home-control / memory).
> Keep a small always-on core (conversation, camera, move_head, search) and gate the long
> tail. `[INFERENCE]` This alone is likely the single highest-leverage change for failure
> mode 1, because 41 schemas on a mini model is squarely in the degradation zone.

### A2. Tool description style — the asymmetry is the bug

`[OFFICIAL]` Cookbook §"Tool Call Performance": *"As use cases grow more complex and the
number of available tools increases, it becomes critical to explicitly guide the model on
when to use each tool and just as importantly, when not to."* The prescribed shape is a
per-tool block:

```
## check_outage(address)
Use when: user reports connectivity issues or slow speeds.
Do NOT use when: question is billing-only.

## refund_credit(account_id, minutes)
Use when: confirmed outage > 240 minutes in the past 7 days.
Do NOT use when: outage is unconfirmed; route to Diagnose → check_outage first.
```

Note the last line: it encodes an **ordering constraint** inside a "Do NOT use when".

`[OFFICIAL]` The realtime-models docs list what a good description contains: clear trigger
conditions; **when NOT to use** (explicit exclusions preventing misapplication); preamble
sample phrases; parameter guidance; failure recovery.

`[OFFICIAL]` §"Tool Selection": *"if you have conflicting instructions in your prompt to
what the model is expecting… it can lead to bad responses"* — tool names/descriptions in
the system prompt must match the registered schemas exactly, and *"the descriptions do not
contradict each other."*

> **Recommendation A2** — Our `move_head` description ("Move your head in a given
> direction: left, right, up, down or front.") is a bare capability statement with **no
> trigger conditions**, competing against a `camera` description that carries an explicit,
> emphatic trigger ("If the user asks you to look without saying at what, do not ask for
> clarification, call this tool"). `[INFERENCE]` The mini model is doing exactly what the
> descriptions tell it. Rewrite **both** symmetrically, and make `camera`'s trigger
> conditional on direction:
>
> ```
> ## move_head(direction)
> Use when: the user names a direction or a person/thing to the side
>   (右 / 左 / 後面 / 轉過去 / 看看誰在那邊).
> Use FIRST, before camera, whenever the user's request contains a direction.
> Do NOT use when: the user only asks what you see, with no direction.
>
> ## camera()
> Use when: the user asks what you see, or asks about something in front of you.
> Do NOT use when: the user named a direction and you have not yet called move_head —
>   call move_head first, then camera.
> NEVER say you turned, looked around, or moved unless move_head returned successfully.
> ```

### A3. Instructions vs. description precedence

`[OFFICIAL]` "Use the system prompt to describe when (and when not) to use each function.
Generally, tell the model *exactly* what to do." Descriptions carry the narrative
guidance; the system prompt carries cross-tool policy.
<https://developers.openai.com/api/docs/guides/function-calling>

`[INFERENCE]` No documented hard precedence rule exists. The operative rule from
`[OFFICIAL]` §Tool Selection is that **contradiction between the two degrades
performance**, so the safe pattern is: put the same rule in both, worded identically, and
never state a rule in only one place if it must survive. For a mini model, redundancy is
cheaper than a precedence gamble.

### A4. `tool_choice` and per-response restriction

`[OFFICIAL]` `tool_choice` accepts `auto` (default), `none`, `required`, a **forced named
function** (`{"type":"function","name":"move_head"}`), and `allowed_tools` for a subset.
Available in both `session.update` (`session.tools` / `session.tool_choice`) and
per-response in `response.create` (`response.tools`, `response.tool_choice`) — the latter
"can be used if you only need the tool for one turn."
<https://developers.openai.com/api/docs/guides/function-calling>,
<https://developers.openai.com/api/docs/guides/realtime-conversations>,
<https://developers.openai.com/api/reference/ruby/resources/realtime>

`[OFFICIAL]` Caveat: `tool_choice: "required"` fails if no tool is currently eligible.
`[COMMUNITY]` There is an old open bug report of `tool_choice: "required"` not being
honoured in Realtime (<https://community.openai.com/t/realtime-api-tool-choice-required-not-working/980380>) —
verify empirically before depending on it.

> **Recommendation A4** — Use per-response forcing as the *deterministic* fallback for
> chaining (see B2), not as the default. Leave the session at `auto`.

---

## B. Multi-step / chained tool use in one user turn

### B1. What the API supports vs. what the mini model does

`[OFFICIAL]` The Realtime API supports parallel tool calling and multiple output items per
turn; after you post a `function_call_output` you send `response.create` and the model
continues. Nothing in the protocol blocks A-then-B.
<https://developers.openai.com/api/docs/guides/realtime-conversations>,
<https://openai.com/index/introducing-gpt-realtime/>

`[OFFICIAL]` The cookbook explicitly endorses prompting for sequences: *"You can also add
instructions on sequences of tool calls (after Tool call A, you can call Tool call B or
C)."*

`[COMMUNITY]` But sequential chaining is where mini tiers demonstrably break. Reported:
`gpt-realtime-2.1-mini` completing collect→confirm and then **silently skipping the
function call entirely** with identical prompt/tools that worked on `gpt-realtime-mini`;
unresolved, OpenAI staff could not reproduce.
<https://community.openai.com/t/model-gpt-realtime-2-1-mini-not-calling-function-tools-in-sip-realtime-while-gpt-realtime-mini-works-with-the-same-prompt-tools/1386141>
`[COMMUNITY]` And on the earlier mini: "counting/retry logic failures — prompts like
'allow max 2 tries then call off topic' work in the full realtime model but fail in mini,"
with an estimate that mini delivers "60% of what the realtime does."
<https://community.openai.com/t/giving-up-on-realtime-mini/1379423>

### B2. The recommended pattern is a composite tool

`[OFFICIAL]` **This is explicit in the function-calling guide:** *"Combine functions that
are always called in sequence. For example, if you always call `mark_location()` after
`query_location()`, just move the marking logic into the query function call."*
<https://developers.openai.com/api/docs/guides/function-calling>

> **Recommendation B2 (highest confidence in this report)** — Add a composite tool that
> performs both steps client-side, and demote the primitives:
>
> ```
> look_around(direction: "left"|"right"|"up"|"down"|"behind"|"front")
>   → moves the head to `direction`, waits for motion to settle, takes a picture,
>     returns {"direction_moved": "...", "image": ...}
> ```
>
> This removes the chaining decision from the model entirely — the failure mode
> disappears rather than being prompted around. It also fixes the *false narration*
> half of failure mode 1: the tool result now carries `direction_moved`, so the model has
> ground truth for "I looked to the right" instead of inventing it.
> `[INFERENCE]` Keep bare `move_head` for pure-motion requests ("抬頭"), and bare `camera`
> for directionless looking; route the compound utterance to `look_around`. Net tool count
> is unchanged (+1) but the *decision* becomes single-hop, which is what mini tiers are
> reliable at.

### B3. If you must keep the chain

`[INFERENCE]` Deterministic client-side chaining, in order of decreasing model trust:
1. On `move_head` completing, the **app** (not the model) immediately issues
   `response.create` with `tool_choice: {"type":"function","name":"camera"}` — forces
   step B without asking the model to decide.
2. Return a structured `move_head` result that instructs the next step, e.g.
   `{"direction_moved":"right","next_step":"call camera now to see what is there"}`.
   `[OFFICIAL]` This exploits the documented fact that JSON-shaped tool outputs are more
   in-distribution than raw strings (see C3).
3. Prompt-level `Do NOT use when … call move_head first` blocks (A2). Weakest; use as
   defence-in-depth, not as the mechanism.

---

## C. Techniques that improve instruction-following on the mini tier

### C1. Prompt structure: labeled sections, bullets, capitals

`[OFFICIAL]` Cookbook "General Tips" — verbatim:
- "**Prefer bullets over paragraphs**: Clear, short bullets outperform long paragraphs."
- "**Guide with examples**: The model strongly closely follows sample phrases."
- "**Use capitalized text for emphasis**: Capitalizing key rules makes them stand out and
  easier for the model to follow."
- "**Be precise**: Ambiguity or conflicting instructions = degraded performance."
- "**Control language**: Pin output to a target language if you see unwanted language
  switching."
- "**Reduce repetition**: Add a Variety rule to reduce robotic phrasing."
- "**Convert non-text rules to text**: instead of writing 'IF x > 3 THEN ESCALATE', write
  'IF MORE THAN THREE FAILURES THEN ESCALATE'."
- "**Iterate relentlessly**: Small wording changes can make or break behavior." (Their own
  example: swapping "inaudible" → "unintelligible" measurably improved noisy-input handling.)

`[OFFICIAL]` Recommended section skeleton (cookbook): `# Role & Objective`,
`# Personality & Tone`, `# Context`, `# Reference Pronunciations`, `# Tools`,
`# Instructions / Rules`, `# Conversation Flow`, `# Safety & Escalation`. The 2.x docs add
`# Language`, `# Reasoning`, `# Message Channels`, `# Preambles`, `# Verbosity`,
`# Unclear Audio`, `# Long Context Behavior`.

### C2. Length and trailing-question control — replace vague constraints with a table

`[OFFICIAL]` The realtime-models guide names our exact anti-pattern: **"Vague constraint
language ('Be concise')"** is listed under *avoid*, alongside overlapping
always/never/only/must rules. The replacement is *"clear trigger, action, and exception
rules: when to act, what to do, and when not to do it."*

`[OFFICIAL]` Its `## Verbosity` block is a per-situation table, not a global adjective:

```
## Verbosity
- Direct answers: Use 1-2 short sentences.
- Clarifying questions: Ask one question at a time.
- Tool results: Summarize first, then next useful action.
- Troubleshooting: Give one step at a time unless user asks for procedure.
- Escalations: Briefly explain why escalation needed, what happens next.
```

`[OFFICIAL]` The cookbook uses a blunt `## Length` line — "2–3 sentences per turn." — and
pairs it with a `## Variety` block ("Do not repeat the same sentence twice. Vary your
responses so it doesn't sound robotic.").

> **Recommendation C2** — Our current instruction ("length calibrated to content") is
> precisely the *vague constraint language* the docs warn against, and there is a
> documented user memory in this project against numeric caps. `[INFERENCE]` The
> reconcilable form is a **situation→length table** rather than a global number: it is
> calibration expressed as triggers, which is what the model can follow. E.g.
> `直接回答問題：一到兩句。/ 描述看到的畫面：先講重點，再補一個細節。/ 工具失敗：一句說明 + 一個下一步。`
>
> For the trailing question specifically: `[OFFICIAL]` note that OpenAI's own recommended
> "Rephrase Supervisor" template **ends with a confirmation question**
> ("opener + one-sentence gist + up to 3 key details + a quick confirmation or choice").
> If any similar idiom is in our prompt, it is actively teaching the behavior we're
> banning. `[INFERENCE]` Replace the ban with a positive rule plus counter-examples, since
> "the model strongly closely follows sample phrases" — a list of 4–6 sample *endings*
> that are flat statements will outperform a prohibition.

### C3. Fabrication against tool results — use a JSON envelope, not "say it exactly"

`[OFFICIAL]` Cookbook §"Tool Output Formatting" is written for our failure mode 3 almost
word for word: *"Some tool outputs, especially long strings that must be repeated
verbatim, can be out-of-distribution… If your tool returns a raw string and separately asks
the model to 'repeat exactly', the model may be more prone to paraphrasing, truncation, or
blending in its own preamble."* Documented symptoms include paraphrase, truncation, and
"Adds extra commentary ('Can I help with anything else?')" — i.e. our failure modes 2 and 3
have a **shared root cause**.

The prescribed fix, verbatim example:

```json
{
  "response_text": "I just sent you an email with the verification link. Please open it and click “Confirm”.",
  "require_repeat_verbatim": true
}
```

with matching instructions in both the Tools section and the tool definition: *"If
`require_repeat_verbatim` is true, output exactly `response_text` and nothing else"* /
*"Render `response_text` as-is; do not add, omit, or reorder fields from the tool output."*

> **Recommendation C3** — Any tool returning a name, ID, or quoted string (the 雲霓 case)
> must return a JSON object with the authoritative field named and a
> `require_repeat_verbatim: true` flag, plus the matching rendering rule in the Tools
> section. Do not rely on a prose "say the name exactly as returned."
> `[INFERENCE]` For Chinese proper nouns, also add a `# Reference Pronunciations` entry —
> the cookbook has a dedicated section for exactly this, and mini's non-English weakness
> (D2) makes it more necessary here than in an English deployment.

### C4. Reasoning effort — an actual knob on 2.1-mini

`[OFFICIAL]` `gpt-realtime-2.1-mini` supports reasoning. Session config:

```json
{ "model": "gpt-realtime-2.1-mini", "reasoning": { "effort": "low" } }
```

Values: `minimal`, `low`, `medium`, `high` (the full 2.1 also documents `xhigh`).
**`low` is the default.** OpenAI's guidance: "start low for most production voice agents,"
then raise for complexity.
<https://developers.openai.com/api/docs/guides/realtime-models-prompting>,
<https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/realtime-2>,
<https://www.marktechpost.com/2026/07/06/openai-gpt-realtime-2-1-mini-reasoning-realtime-api/>

`[OFFICIAL]` The docs say to pair the API setting with a prompt block:

```
## Reasoning
- For direct answers, simple lookups, short confirmations: respond quickly, do not reason.
- For multi-step tasks, tool decisions, troubleshooting: reason before acting.
- Do not perform extended reasoning when audio unclear; ask for clarification.
```

and place `medium` at "Assistant must reason through multi-step tasks" — which is
literally our move_head→camera case.

> **Recommendation C4** — Test `reasoning.effort: "medium"`. `[INFERENCE]` This is the
> cheapest experiment available (one config field) and it targets the exact capability
> that's failing. Measure added latency on the robot; if medium is too slow for
> conversational feel, keep `low` and lean harder on B2 (composite tool), which needs no
> reasoning at all.

### C5. Routing-rules block: supported, but as *tool-level* rules

`[OFFICIAL]` The cookbook's **"Tool Level Behavior"** pattern is the closest documented
thing to a routing table, and it uses ALL-CAPS tags per tool:

```
# TOOLS
- For the tools marked PROACTIVE: do not ask for confirmation and do not output a preamble.
- For the tools marked as CONFIRMATION FIRST: always ask for confirmation.
- For the tools marked as PREAMBLES: Before any tool call, say one short line…

## lookup_account(email_or_phone) — PROACTIVE
Use when: …
Do NOT use when: …
```

`[INFERENCE]` An utterance-pattern → tool-name mapping block (e.g. `"轉/看右邊/後面" →
look_around`) is not a documented OpenAI pattern, but it is a direct application of two
documented ones ("guide with examples: the model strongly closely follows sample phrases"
+ "Use when / Do NOT use when"). Worth trying, but subordinate to B2 and A1 — it is
prompt-level mitigation of a structural problem.

`[OFFICIAL]` Also relevant to our false-narration bug: *"Only say an action completed after
the tool call succeeds. If the tool fails, explain briefly, avoid raw errors, give a clear
next step."* Add this verbatim (in Chinese) to `# Instructions/Rules`.

### C6. Preambles are ON by default on 2.x — this is half of failure mode 2

`[OFFICIAL]` `gpt-realtime-2.x` **generates preambles by default**, and each output item
carries a `phase` field:

| Phase | Description |
| --- | --- |
| `commentary` | A promptable preamble, often used before longer reasoning. |
| `final_answer` | The final answer after the model completes reasoning. |

<https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/realtime-2>

`[OFFICIAL]` There is no documented boolean to disable preambles. The docs instead give a
*negative trigger list*: "Do not use a preamble when: the answer is direct and can be given
immediately; the user is only confirming, correcting, or declining something; the audio is
unclear… [or] the latest audio is silence, background noise, hold music, TV audio, side
conversation, or speech not addressed to the assistant."

> **Recommendation C6** — Two-part fix.
> (a) `[INFERENCE]` Client-side: inspect the `phase` on each output item. If the app is
> counting a `commentary` item's text toward "the response", our brevity rule is being
> judged against concatenated preamble + answer. Suppressing or separately handling
> `commentary` items may make failure mode 2 partly disappear without any prompt change —
> **verify what our WebSocket handler currently does with multi-item responses before
> rewriting the prompt.**
> (b) Prompt-side: replace the blanket "no preambles" ban with the docs' explicit
> negative-trigger list, phrased as when-NOT-to conditions. `[OFFICIAL]` A blanket ban is
> the "overlapping always/never/only/must" anti-pattern; a trigger list is the prescribed
> form.

### C7. Literal-interpretation trap (relevant to Chinese phrasing)

`[OFFICIAL]` "The model may prioritize the exact wording of an instruction over the broader
behavior you intended." Their example: a rule about "confirmation code" not generalizing to
"order ID." Foundry's mirror adds that 2.x instruction following is *stricter* than earlier
realtime models, so narrow wording is now more likely to under-trigger.

`[INFERENCE]` Our `move_head` rule must enumerate real Chinese phrasings (右邊 / 右手邊 /
轉過去 / 轉頭 / 看看後面 / 那邊), not a single canonical form, or it will simply not match
what users say.

---

## D. Known regressions and quirks of `gpt-realtime-2.1-mini`

`[OFFICIAL]` Positioning, from the model docs: 2.1-mini is "a distilled reasoning model for
faster, lower-cost realtime voice interactions"; use the full 2.1 "when you want the
strongest realtime reasoning, tool use, instruction following, and voice-agent behavior."
Both share a 2024-09-30 knowledge cutoff and 128k context. Mini is ~6x cheaper on text and
~3x on audio. OpenAI's own framing is that mini **trades capability** in exactly the three
axes we're failing on.
<https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini>,
<https://developers.openai.com/api/docs/models/gpt-realtime-2.1>

### D1. Tool calls silently skipped on 2.1-mini
`[COMMUNITY]` A production SIP voice agent (Portuguese) reported 2.1-mini completing the
conversational steps then **never emitting the function call**, with identical
prompt/tools/handlers that worked on `gpt-realtime-mini`. OpenAI staff (VeitB) could not
reproduce; another dev suspected a guardrail refusal. **Unresolved.**
<https://community.openai.com/t/model-gpt-realtime-2-1-mini-not-calling-function-tools-in-sip-realtime-while-gpt-realtime-mini-works-with-the-same-prompt-tools/1386141>

### D2. Non-English quality regression — directly relevant to our Chinese-primary POC
`[COMMUNITY]` "GPT Realtime 2.1 exhibits language drift": with system prompts entirely in
Spanish/German and explicit language instructions, the agent drifts to English or speaks
the target language with an English accent. On mini specifically: **"2.1 mini is so nice
fastest reasoning model, but on other languages except english is very very bad."**
Reporter calls it a "production showstopper"; multiple prompt fixes attempted "to no
effect." Realtime 1.5 reportedly handled non-English better. **No staff reply.**
<https://community.openai.com/t/gpt-realtime-2-1-exhibits-language-drift/1386953>

`[COMMUNITY]` Separately, a Romanian production deployment reported the newer mini
snapshot **"hallucinated non-existing departments, services, and operational details"** not
present in the provided data — i.e. fabrication against supplied context, our failure mode
3 — plus "noticeably worse language quality," after a forced snapshot migration.
<https://community.openai.com/t/realtime-regression-in-non-english-production-voice-agents-gpt-realtime-mini-vs-gpt-realtime-mini-2025-10-06/1380643>

> `[INFERENCE]` This is the most concerning finding for us. Two independent non-English
> production reports describe *our* failure mode 3 as a model-level regression, not a
> prompting error. Mitigations (C3 JSON envelope, C1 language pinning, pronunciations) are
> worth doing, but we should set expectations that verbatim-name fidelity may not reach
> 100% on this tier, and design around it — e.g. prefer tool results the robot *acts* on
> over ones it must *recite*.

### D3. Instruction leakage / commentary narration on 2.1-mini
`[COMMUNITY]` A production voice-agent team migrating realtime-mini → 2.1-mini documented:
"instruction leakage" causing unwanted narration ("Let me continue with the call…");
commentary-channel generation **despite instructions against it**; "literal transition
matching" where example phrases in the prompt triggered false matches; and markedly
increased sensitivity to prompt wording. Their verdict: "realtime-mini … still behaves
better than 2.1 for deterministic, structured voice workflows."
<https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896>

> This is failure mode 2, reported by others, on our exact model. Note "literal transition
> matching": `[INFERENCE]` few-shot sample phrases (C1/C5) are double-edged on this tier —
> they steer style well but can be matched too literally as triggers. Label example blocks
> clearly as style examples, not as conditions.

### D4. Older mini tier baseline
`[COMMUNITY]` "Giving up on Realtime - Mini": inconsistent constraint enforcement ("random
tool calling failure"), scope-boundary violations (off-topic engagement despite system
instructions), and counting/retry logic that works on the full model and fails on mini.
Estimated at "60% of what the realtime does." OpenAI support acknowledged with no timeline.
<https://community.openai.com/t/giving-up-on-realtime-mini/1379423>

---

## Prioritized action list

| # | Action | Fixes | Confidence | Cost |
|---|--------|-------|-----------|------|
| 1 | Add composite `look_around(direction)` (move_head + settle + camera, returns `direction_moved`) | #1 both halves | `[OFFICIAL]`-backed pattern | Low |
| 2 | Cut per-turn tool surface 41 → ≲15 via `allowed_tools` / mode-scoped `session.update` | #1, #2 | `[OFFICIAL]` (<20 rule) | Medium |
| 3 | Wrap verbatim-critical tool results in `{response_text, require_repeat_verbatim:true}` + matching render rule | #3 | `[OFFICIAL]`, exact-match section | Low |
| 4 | Inspect output-item `phase`; handle/suppress `commentary` items client-side | #2 | `[OFFICIAL]` field exists; `[INFERENCE]` on our handler | Low |
| 5 | Symmetric `Use when: / Do NOT use when:` blocks for every tool, with Chinese trigger phrasings enumerated | #1 | `[OFFICIAL]` | Low |
| 6 | Replace "calibrate length" + preamble ban with situation→length table and negative-trigger preamble list | #2 | `[OFFICIAL]` | Low |
| 7 | A/B `reasoning.effort: "medium"` vs `low`, measure latency on-robot | #1, #2 | `[OFFICIAL]` knob, `[INFERENCE]` effect | Trivial |
| 8 | Add `# Language` pinning block + `# Reference Pronunciations` for Chinese names | #3, D2 | `[OFFICIAL]` | Low |
| 9 | Fallback only: force step B with `response.create` + `tool_choice:{type:"function",name:"camera"}` | #1 | `[OFFICIAL]` API; `[COMMUNITY]` bug risk | Medium |

## Sources

- <https://developers.openai.com/api/docs/guides/function-calling>
- <https://developers.openai.com/api/docs/guides/realtime-models-prompting>
- <https://developers.openai.com/api/docs/guides/realtime-conversations>
- <https://developers.openai.com/api/docs/guides/realtime-mcp>
- <https://developers.openai.com/api/docs/guides/realtime>
- <https://github.com/openai/openai-cookbook/blob/main/examples/Realtime_prompting_guide.ipynb>
- <https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini>
- <https://developers.openai.com/api/docs/models/gpt-realtime-2.1>
- <https://developers.openai.com/api/reference/ruby/resources/realtime>
- <https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/realtime-2>
- <https://openai.com/index/introducing-gpt-realtime/>
- <https://www.marktechpost.com/2026/07/06/openai-gpt-realtime-2-1-mini-reasoning-realtime-api/>
- <https://community.openai.com/t/model-gpt-realtime-2-1-mini-not-calling-function-tools-in-sip-realtime-while-gpt-realtime-mini-works-with-the-same-prompt-tools/1386141>
- <https://community.openai.com/t/gpt-realtime-2-1-exhibits-language-drift/1386953>
- <https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896>
- <https://community.openai.com/t/realtime-regression-in-non-english-production-voice-agents-gpt-realtime-mini-vs-gpt-realtime-mini-2025-10-06/1380643>
- <https://community.openai.com/t/giving-up-on-realtime-mini/1379423>
- <https://community.openai.com/t/realtime-api-tool-choice-required-not-working/980380>
- <https://arxiv.org/abs/2605.24660>
- <https://tianpan.co/blog/2026-04-19-over-tooled-agent-problem>
