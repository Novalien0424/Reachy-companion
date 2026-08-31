# Research: Instructing OpenAI realtime speech-to-speech models (Sept 2026)

Date: 2026-09-01. Scope: web research only, no code changes.
Target: our Reachy Mini companion on the OpenAI Realtime API over WebSocket,
`gpt-realtime-2.1-mini` (with `gpt-realtime-2.1` as the upgrade path),
Chinese-primary, ~27 default tools plus toolbox-gated extras.

**Design question behind this research:** we want the *model* to understand
intent, decide which tools to call, and decide what to say — the app should
**instruct**, not hard-code. This doc collects what OpenAI and practitioners
actually say about how to do that, and, more usefully, where that ambition hits
a hard limit and the app must take over.

**Evidence labels:**
- `[OFFICIAL]` — OpenAI docs / cookbook / model cards / changelog, or the
  Microsoft Foundry mirror.
- `[PRACTITIONER]` — agent-framework docs and source (LiveKit, Vapi, Pipecat,
  Deepgram, ElevenLabs, Retell), engineering blogs, OpenAI community reports.
- `[INFERENCE]` — my reasoning applied to Reachy specifically; not sourced.

**Builds on** `docs/research-mini-tool-calling-2026-08.md` (tool-count ceilings,
`Use when / Do NOT use when` blocks, JSON verbatim envelopes, mini-tier
regressions). Those are treated as settled and not re-litigated.

**A generational caveat that matters throughout.** OpenAI's realtime prompting
guidance changed between model generations, and the current page contains
*both*. The `gpt-realtime-1.5` sections advise same-response preambles;
the `gpt-realtime-2.x` sections replace much of that with channel-aware
behavior and confirm-then-act. **We are on 2.1-mini, so the 2.x guidance
governs** — and several widely-quoted "OpenAI says" snippets circulating in
blogs are from the 1.5 era.

---

## Q1. Prompt structure, ordering, length — and what each instruction surface is for

### 1.1 The current official section skeleton `[OFFICIAL]`

Two skeletons are published. The 2.x one is the one to follow:

| Cookbook skeleton (gpt-realtime, 2025) | Realtime-models guide (gpt-realtime-2.x, current) |
|---|---|
| Role & Objective | `# Role and Objective` |
| Personality & Tone | `# Personality and Tone` |
| Context | **`# Language`** |
| Reference Pronunciations | **`# Reasoning`** |
| Tools | **`# Message Channels`** |
| Instructions / Rules | **`# Preambles`** |
| Conversation Flow | **`# Verbosity`** |
| Safety & Escalation | `# Tools` |
| | **`# Unclear Audio`** |
| | **`# Entity Capture`** |
| | **`# Long Context Behavior`** |
| | `# Escalation` |

> "Not every use case needs every section. Add the sections that are relevant
> for your product."
> "Use short, labeled sections. The model should be able to find the relevant
> instructions quickly."
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[INFERENCE]` The five bolded sections are new in 2.x and map one-to-one onto
capabilities 2.x added. Our prompt is built on the 2025 skeleton and is
therefore silently missing the steering surfaces for the newest behaviors —
most consequentially `# Message Channels`, which is where the speak-around-tools
control now lives (§2.3).

### 1.2 Length: the official advice is "start under-specified" `[OFFICIAL]`

No token cap is published for `instructions`. The guidance is a discipline, not
a budget:

> "Start simple. Do not over-prompt upfront. Begin with a minimal prompt, run
> evaluations, then add instructions only for behaviors that fail in testing."
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>
> (verified by direct fetch of that page)

> "Be careful with constraint words such as `must`, `only`, `never`, and
> `always`. Use them when the behavior is truly required, not as general
> emphasis."
> — same page

Named anti-patterns on that page: vague guidance ("be helpful") in place of
trigger/action/exception rules; overlapping `always`/`never`/`only`/`must` rules
with no defined priority; broad language instructions like "mirror the user";
**mentioning tools in the prompt that aren't in the actual tool list**.

`[PRACTITIONER]` Independent numeric guidance, weaker but directionally
consistent: ElevenLabs — "Prompts over 2000 tokens increase latency and cost.
Focus on conciseness: every line should serve a clear purpose"
(<https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide>).
Vapi supplies the mechanism: "The system prompt loads into the model's context
on every turn. A bloated prompt increases time to first token, which the caller
experiences as dead air"
(<https://github.com/VapiAI/docs/blob/main/fern/prompting-guide.mdx>).

`[INFERENCE]` This is the most uncomfortable finding in the doc for us. Our
shipped prompt is a ~65-line profile plus a ~60-line hardening block, composed
overwhelmingly of negative constraints (「不要…」「絕對不要…」「只有…才…」). That
is the exact shape the docs name as an anti-pattern, and it grew by accretion
rather than by "add instructions only for behaviors that fail in testing." See
§4.4 for why long banlists are actively counterproductive rather than merely
wasteful — that is the finding that turns this from a style note into a bug.

### 1.3 Literal interpretation is stricter on 2.x `[OFFICIAL]`

> "Instruction following is stricter than in earlier realtime models. If your
> system prompt contains narrow wording (for example, distinguishing 'order ID'
> from 'confirmation code'), you might need to broaden or rephrase instructions
> to match real user phrasing."
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>,
> mirrored verbatim at
> <https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/realtime-2>

The prescribed fix is to **enumerate** rather than generalize.

### 1.4 The instruction surfaces — and the override trap `[OFFICIAL]`

This decides whether "per-response instructions for a specific moment" is
idiomatic or a foot-gun. Answer: it is idiomatic, *and* it has a sharp edge.

**(a) Session `instructions`** (`session.update`) — the persistent system prompt.

**(b) Per-response `instructions`** (`response.create`). The decisive wording:

> "The `response.create` event includes inference configuration like
> `instructions` and `tools`. If these are set, they will **override the
> Session's configuration for this Response only**."
> — <https://developers.openai.com/api/reference/ruby/resources/realtime>,
> also at <https://developers.openai.com/api/reference/resources/realtime/client-events>

and, from the spec-generated field description:

> "The default system instructions (i.e. system message) prepended to model
> calls… Note that the server sets default instructions **which will be used if
> this field is not set** and are visible in the `session.created` event at the
> start of the session."
> — <https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/realtime/realtime_response_create_params.py>

> **This is a replace, not an append.** "…used if this field is not set" is the
> load-bearing clause: session instructions apply only in the *absence* of
> per-response instructions. There is no documented merge semantic anywhere.
> `[INFERENCE]` For us this is a live hazard, not a theoretical one: a bare
> `response.create` with `instructions: "說再見"` generates that response with
> **no persona, no language pinning, no anti-fabrication rule, no verbosity
> table**. Given that the docs elsewhere state "English is the default response
> language," a bare per-response instruction is a plausible route to an English
> goodbye from a Chinese-speaking robot.

**(c) Conversation items** (`conversation.item.create`) — durable turns. Our
greeting already uses this (a synthetic user turn), which is a sound idiom for
"make the model do something as if asked," and notably it does *not* discard
session instructions.

**(d) Out-of-band responses** — `response.create` with `conversation: "none"`:

> "you may want to generate model responses outside the context of the session's
> default conversation, or have multiple responses generated concurrently."
> "Set this to `none` to create an out-of-band response which will not add items
> to default conversation."
> — <https://developers.openai.com/api/docs/guides/realtime-conversations>

Canonical shape, from the same guide:

```javascript
const event = {
  type: "response.create",
  response: {
    conversation: "none",
    metadata: { topic: "classification" },
    output_modalities: ["text"],
    instructions: prompt,
  },
};
```

There is an official cookbook for the pattern:
<https://developers.openai.com/cookbook/examples/realtime_out_of_band_transcription>.
`response.input` also exists — "Using this field creates a new context for this
Response instead of using the default conversation."

> `[INFERENCE]` Out-of-band is the *right* home for side-tasks — engagement
> classification, intent tagging, silent summarization — and it is precisely the
> case where the instructions-replace semantic is a feature rather than a
> hazard, because a side-task genuinely should not inherit the persona.

**Also relevant:** `parallel_tool_calls` is "Only supported by reasoning Realtime
models such as `gpt-realtime-2`" — so it is available to us on 2.1-mini and not
on the older tier.

### 1.5 Structured context injection `[OFFICIAL]`

For anything injected into the prompt (our memory block):

> "Do not rely on the model to infer source priority from a raw transcript or
> large context dump. **Use structure.**"

The template ranks by authority: `### Current State` / `### Authoritative
Sources` (Status: current) / `### Historical or Background Sources` (Status:
stale or background — "Do not use for current decisions if it conflicts with a
current source") / `### Relevant Policy or Rules` / `### Other Context`.
— <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[INFERENCE]` Directly applicable to `format_memory_for_prompt`, which currently
prepends memory as an unlabeled block ahead of the persona.

---

## Q2. Getting the model to SPEAK before / while calling a tool

The concrete case: our profile instructs 「當用戶明確說想讓你睡覺…先簡短道別再調用」
— say goodbye, *then* call `go_to_sleep`. This section is the core of the
research and the answer is unambiguous.

### 2.1 The protocol permits it; the platform decides the timing

`[OFFICIAL]` A single response may contain both a message and a function call:

> "The server is generating a Response, which if successful will produce either
> one or two Items, which will be of type `message` (role assistant) or type
> `function_call`. A Response will include at least one Item, and may have two,
> in which case the second will be a function call."
> — <https://developers.openai.com/api/reference/resources/realtime/server-events>

`[PRACTITIONER]` But — and this is the finding that settles the question —
**which one actually executes first is not yours to prompt.** A practitioner
debugging exactly our failure captured it with timestamped logs:

```
13:56:59:962: Model called tool: endCall()
13:56:59:966: Voice input: No problem. Thank you for contacting ... Have a great day.
13:56:59:975: Voice cached: Goodbye
```

with the diagnosis stated plainly:

> "When the LLM generates both a response and a function call in the same turn,
> the execution timing is determined by the platform, not the prompt
> instructions."

Their fix was to abandon prompt-ordering entirely and switch to a
platform-level mechanism.
— <https://legacy.patrickmichael.co.za/how-do-you-end-calls-smoothly-vapi-complete-guide-professional-voice-agent-call-endings>
(single practitioner, but with logs and a clean diagnosis)

### 2.2 Reliability of model-generated preambles: 15–33% failure `[PRACTITIONER]`

Two independent community threads, pointing in *opposite* directions, both land
on "stochastic, not promptable to 100%":

- Wanting preambles, on gpt-5-realtime: "preambles defined with tool calls will
  inconsistently present themselves alongside the tool call… probably 1/3 of the
  time, the agent will not speak a preamble."
  <https://community.openai.com/t/realtime-api-preamble-inconsistent/1361953>
- Wanting to *suppress* them, on the earlier Realtime API, after trying system
  prompt instructions, in-context examples, and modified function descriptions:
  "a ~15% chance that it generates speech before calling a function"; "there is
  no real control to instruct the system whether it should create speech before
  calling a function."
  <https://community.openai.com/t/realtime-api-sometimes-creates-speech-before-a-tool-call-sometimes-doesnt/1153507>

### 2.3 On 2.x the control surface is the *commentary channel* `[OFFICIAL]`

This is the single most important official sentence for our question, and it is
in the `# Message Channels` section we don't have:

> "For example, **tool calls happen in the commentary channel. If you want the
> assistant to say something before, during, or after tool use, specify that
> behavior in relation to the commentary channel.**"
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

The channels:

| Channel / phase | Meaning |
|---|---|
| `commentary` | "Preambles and tool calls." / "A promptable preamble, often used before longer reasoning." |
| `final` / `final_answer` | "Final user-facing message." / "The final answer after the model completes reasoning." |

> "`gpt-realtime-2` can emit multiple response phases in a single turn. In API
> output, this distinction is represented by the `response.done` event, which
> includes a `phase` value."
> "commentary can be played or displayed as a short intermediate update, while
> `final_answer` can be reserved for the assistant's completed response."

— <https://developers.openai.com/api/docs/guides/realtime-models-prompting>;
`phase` shipped to the Responses API on 2026-02-24 per
<https://developers.openai.com/api/docs/changelog>; mirrored at
<https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/realtime-2>

Foundry adds an operationally important caveat:

> "If the model is interrupted during thinking, it discards the current chain of
> thought and starts a new turn."

**Preamble use/don't-use rules** `[OFFICIAL]` — use before a tool call that may
take noticeable time, during multi-step reasoning, when checking records, when
"silence would make the assistant feel unresponsive." Do NOT use when the answer
is immediate, the user is confirming/correcting/declining, the audio is unclear,
or **"the tool call is lightweight and the user would not benefit from an
update."** Style: "Use one short sentence"; "describe the action, not the
internal reasoning"; avoid "Let me think…", "Hmm…".

> `[INFERENCE]` **Preambles are the wrong mechanism for our farewell.** A
> preamble exists to mask *latency*. A goodbye before sleeping is not a
> latency-filler — it is a substantive final utterance that must complete before
> an irreversible action. The docs' own "do NOT use when the tool call is
> lightweight" arguably excludes our case outright. Preambles are right for
> `camera`, `look_around`, `search_web`, `nas` query. They are not right for
> `go_to_sleep`.

### 2.4 The highest-yield promptable lever: preamble phrases in the *tool description* `[OFFICIAL]`

If you do want model-spoken narration around a tool, OpenAI's own recipe puts it
in the tool spec, not the system prompt:

> "If you want to control more closely what type of phrases the model outputs at
> the same time it calls a tool, you can add sample phrases in the tool spec
> description."

```python
"description": """Retrieve a customer account using either an email or phone number to enable verification and account-specific actions.

Preamble sample phrases:
- For security, I'll pull up your account using the email on file.
- Let me look up your account by {email} now.
- I'm fetching the account linked to {phone} to verify access.
- One moment—I'm opening your account details.""",
```

with per-tool behavior tags in the system prompt:

```
# TOOLS
- For the tools marked PROACTIVE: do not ask for confirmation from the user and do not output a preamble.
- For the tools marked as CONFIRMATION FIRST: always ask for confirmation to the user.
- For the tools marked as PREAMBLES: Before any tool call, say one short line like "I'm checking that now." Then call the tool immediately.
```

— <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[OFFICIAL]` Note the framing in the 1.5-era text is **concurrent, not
sequential**: "the model outputs an audio response 'I'm checking that right now'
**at the same time as** the tool call." It was never advertised as
speak-then-wait-then-act.

`[PRACTITIONER]` A community suggestion independently converges on the same
placement: "give the encouragement or prohibition for writing to the user (in
the final channel) on a per-tool basis" rather than in the system prompt
(<https://community.openai.com/t/realtime-api-sometimes-creates-speech-before-a-tool-call-sometimes-doesnt/1153507>).

### 2.5 The 2.x guidance reframes "speak then act" as *confirm* then act `[OFFICIAL]`

> "For write tools or external actions: Summarize the intended action before
> calling the tool. Include the key consequence, such as what will be changed,
> sent, canceled, ordered, or charged. Ask for confirmation. **Do not call the
> tool until the user clearly confirms.**"
> "After tool calls: Only say an action was completed after the tool call
> succeeds."
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[INFERENCE]` This is two model turns with a *user turn between them* — not a
same-response preamble. For irreversible actions, OpenAI's 2.x answer to "speak
before acting" is a confirmation turn, which we already implement for email via
`needs_confirmation`. `go_to_sleep` is the one irreversible-ish action where we
do *not* want a confirmation question (it would be annoying), which is exactly
why it needs the mechanism in §2.6 instead.

### 2.6 The idiomatic pattern for "speak, THEN act": the app drives a second response

This converges from every tier of evidence, and it inverts our current design.

`[OFFICIAL]` The Realtime protocol already *requires* a second model response
after a tool:

> "Once we have added the conversation item containing our function call
> results, we again emit the `response.create` event from the client. This will
> trigger a model response using the data from the function call."
> — <https://developers.openai.com/api/docs/guides/realtime-conversations>

So "tool call → `function_call_output` → second model-generated response" is
**not a workaround. It is the protocol.** Any framing of it as a hack is wrong.

`[PRACTITIONER]` **LiveKit ships exactly this as its default `end_call` tool** —
and the design is the mirror image of ours. Verified by direct fetch of both the
source and the docs.

The description LiveKit sends to the LLM ends with:

> "This is the final action the agent can take. Once called, no further
> interaction is possible with the user. **Don't generate any other text or
> response when the tool is called.**"
> — <https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/beta/tools/end_call.py>

The goodbye is instead produced by the **tool output**: `end_instructions`,
described as "Tool output to the LLM for generating the tool response. This is
the message the LLM receives after the tool is called," defaulting to
**`"say goodbye to the user"`**. The documented lifecycle:

> 1. "The agent generates a final response (based on `end_instructions`)."
> 2. "The session shuts down after the response is complete."
> 3. "If `delete_room` is `True`, the room is deleted…"

— <https://docs.livekit.io/agents/prebuilt/tools/end-call-tool/>

> **Read that inversion carefully.** We tell the model *"say goodbye, then call
> the tool."* LiveKit tells the model *"call the tool, and say nothing."* The
> goodbye is then generated as the **second** response, instructed by the tool's
> own return value, and the app waits for that speech to finish before tearing
> down. Ordering becomes a property of the code, not a hope about the model.

`[PRACTITIONER]` LiveKit also exposes the general primitive — speak from inside
the tool handler and await playout before doing the real work:

> "Use `ctx.wait_for_playout()` to wait for any pre-tool speech to finish."

```python
@function_tool()
async def process_order(self, context: RunContext, order_id: str):
    """Process an order and notify the user."""
    await self.session.generate_reply(
        instructions=f"Processing order {order_id}. This may take a moment."
    )
    result = await process_order_internal(order_id)
    return result
```
— <https://docs.livekit.io/agents/logic/tools/definition/>

And `SpeechHandle` for coordination: both `say()` and `generate_reply()` return
one, which "lets you track the state of the agent's speech, which can be useful
for coordinating follow-up actions, **for example, notifying the user before
ending the call**" (<https://docs.livekit.io/agents/build/audio/>).

> **One divergence to port carefully.** LiveKit's `generate_reply(instructions=…)`
> is *additive* — "session-level instructions remain active — `generate_reply`
> instructions don't replace them" — whereas raw
> `response.create.instructions` *replaces* (§1.4). `[INFERENCE]` Copying the
> LiveKit pattern onto the raw API means reconstructing additivity ourselves:
> either re-send the session instructions alongside the moment-specific line, or
> put the instruction in a conversation item / the `function_call_output` (as
> LiveKit does) rather than in the `instructions` field.

### 2.7 The other three vendor idioms — all of which route around the model

`[PRACTITIONER]` **Farewell as a tool argument** (ElevenLabs, Retell). The model
never sequences anything; it writes the goodbye into the call and the app speaks
it. ElevenLabs' `end_call` takes `message`: "A farewell message to send to the
user before ending the call."
(<https://elevenlabs.io/docs/eleven-agents/customization/tools/system-tools/end-call>).
Retell's equivalent: "The message you will say before ending the call with the
customer" (<https://docs.retellai.com/build/single-multi-prompt/end-call>).

`[PRACTITIONER]` **Phrase-triggered app-side hangup** (Vapi). `endCallPhrases`:
"a list containing phrases that, if spoken by the assistant, will trigger the
call to be hung up." The model just talks; the platform detects and acts.
(<https://docs.vapi.ai/api-reference/assistants/create>)

`[PRACTITIONER]` **App-side deterministic speech**, explicitly recommended *over*
prompt instructions. Deepgram: "The model may generate text like 'Let me check
the weather systems…' *before* the function executes" — their remedy is
server-side suppression plus `InjectAgentMessage` with `behavior: "queue"` to
inject filler, described as reliable *because* it is server-injected
(<https://developers.deepgram.com/docs/prompting-voice-agents>). Vapi: "For slow
tools, use tool `messages` instead of prompt instructions… configure a
`request-start` message on the tool itself." Pipecat: push a `TTSSpeakFrame`
from inside the handler
(<https://docs.pipecat.ai/pipecat/learn/function-calling>). LiveKit's
`with_filler` draws the line explicitly: it "plays audio directly through
`session.say()` during quiet gaps, **bypassing the LLM**"
(<https://livekit.com/blog/async-tools-voice-agents>).

### 2.8 Can a mini-tier model narrate-then-call in one response?

**No source directly measures this.** That is a genuine gap, and I want to be
clear about it rather than paper over it. The available proxies all point the
same way:

- `[PRACTITIONER]` Full-tier models already fail 15–33% of the time (§2.2).
- `[PRACTITIONER]` Deepgram, on small models generally: "A prompt that says
  'call ALL FOUR functions' may result in a smaller model calling one and
  stopping. **This isn't a prompt problem — it's a model capability
  limitation.**" (<https://developers.deepgram.com/docs/prompting-voice-agents>)
- `[PRACTITIONER]` The richest mini failure taxonomy: "The Mini can detect a
  pattern… but it doesnt action on it"; "The mini is unable to count. So a
  prompt that says 'allow max 2 tries and then call off topic' that works
  perfectly in the realtime model, the mini cannot do"; overall "the mini does
  60% of what the realtime does."
  (<https://community.openai.com/t/giving-up-on-realtime-mini/1379423>)

> `[INFERENCE]` That middle failure — *detection succeeds, the action doesn't
> fire* — is structurally identical to "say goodbye, THEN call the tool." Do not
> bet an irreversible action on it.

> **Verdict on Q2.** The app-driven two-step is the documented protocol, the
> reference framework's shipped default, and the only mechanism that makes
> ordering deterministic. It is **not** a fallback or a hack; treating it as one
> is the actual error. The correct division of labour: **the model decides the
> farewell's wording and timing; the app guarantees the farewell finishes before
> the robot sleeps.**

---

## Q3. Tool design for voice models, 2026

### 3.1 Count `[OFFICIAL]` / `[PRACTITIONER]`

Settled in prior research: "aim for fewer than 20 functions available at the
start of a turn." **No vendor doc states a hard limit.** The only numeric
thresholds come from third-party blogs — "accuracy degrading measurably once
tool counts pass roughly 10 to 15"
(<https://machinelearningmastery.com/the-complete-guide-to-tool-selection-in-ai-agents/>)
— which cite unnamed benchmarks and should be read as directional only.

More interesting is Retell's *architectural* threshold: abandon single-prompt
agents for flow-based ones at "More than 3-4 conditional paths," "Using 5+
different functions/tools," or "Tracking multiple variables throughout the
conversation" (<https://docs.retellai.com/build/prompt-engineering-guide>).

`[INFERENCE]` Our `open_toolbox` mechanism is already the right architecture —
it is OpenAI's "Dynamic Conversation Flow" by another name. The open question is
whether the always-on core (27 tools) is still too wide.

### 3.2 Description style `[OFFICIAL]` / `[PRACTITIONER]`

The per-tool block, verbatim from OpenAI:

```
## check_outage(address)
Use when: user reports connectivity issues or slow speeds.
Do NOT use when: question is billing-only.

## refund_credit(account_id, minutes)
Use when: confirmed outage > 240 minutes in the past 7 days.
Do NOT use when: outage is unconfirmed; route to Diagnose → check_outage first.
```

> "As use cases grow more complex and the number of available tools increases,
> it becomes critical to explicitly guide the model on when to use each tool and
> just as importantly, when not to."
> "You can also add instructions on sequences of tool calls (after Tool call A,
> you can call Tool call B or C)."
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[PRACTITIONER]` **Use action verbs, not returns-language** — the most concrete
actionable tip found:

> "A description like 'respond naturally to the query field' can cause the model
> to skip the function and generate a text answer." Fix: "Use action-oriented
> verbs: 'call this to…,' 'retrieve…,' 'look up…' — not 'returns…' or
> 'provides…'"
> — <https://developers.deepgram.com/docs/prompting-voice-agents>

`[PRACTITIONER]` **Atomicity and distinct names.** Vapi: "Each tool does one
thing. Prefer `get_slots`, `book_slot`, `confirm_booking` over a single combined
tool with a `mode` parameter"; "Use descriptive, distinct names. `lookup_account`
beats `api_call`." LiveKit: "use clear naming conventions (e.g., `order_create`,
`order_update`, `order_cancel`)."

`[PRACTITIONER]` **A real tension worth knowing about.** Vapi says fix the
description, not the prompt: "If the LLM consistently picks the wrong tool or
passes bad parameters, the problem is almost always in the tool description —
not the prompt." Retell and Deepgram say the opposite — Retell: "LLMs often
struggle to determine when to call tools based solely on tool descriptions…
Always specify exact conditions for tool usage in your prompts. Reference tools
by their exact names." `[INFERENCE]` The reconcilable synthesis, which also
matches prior research's precedence advice: **do both, worded identically, and
reference the tool by its exact registered name in the prompt.**

### 3.3 Stay in-distribution on tool names `[OFFICIAL]`

Genuinely useful and easy to miss:

> "`gpt-realtime-1.5` has been trained to effectively use the following common
> tools. If your use case needs similar behavior, **keep the names, signatures,
> and descriptions close to these** to maximize reliability and to be more
> in-distribution."
>
> ```
> # answer(question: string)
> # escalate_to_human()
> # finish_session()
> Description: Call this when a customer says they're done with the session or
> doesn't want to continue. If it's ambiguous, confirm with the customer before
> calling.
> ```
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

> `[INFERENCE]` **`finish_session()` is the trained-in name for exactly our
> `go_to_sleep` shape.** Worth an A/B: keeping our user-facing concept
> ("sleep") in the description while moving the *registered name* toward the
> in-distribution one is a near-zero-cost change with a plausible reliability
> win. Caveat: this list is documented for 1.5, and I found no equivalent list
> for 2.x — so treat the transfer as untested.

### 3.4 Enums and arguments — thin evidence

`[OFFICIAL]` "Use enums for bounded choices (e.g., time windows)"; confirm
high-precision identifiers before calling.

`[PRACTITIONER]` Describe each enum member, don't just list it: rather than "The
status type," write "'pending' = not yet shipped; 'in_transit' = shipped but not
delivered… Use 'all' if the user has not specified a status"
(<https://www.kn8.ai/blog/webmcp-tool-design-best-practices>). ElevenLabs: "Add
descriptions to all parameters… Format expectations, required vs. optional
fields, and acceptable values."

> **Honest gap:** no voice-agent vendor doc addresses enums vs free-form
> arguments. A third-party claim of "enum collapse" (models defaulting to the
> most normal enum value more often than free-form would) could not be
> corroborated against a primary source and should not be relied on. This is the
> weakest subsection in the doc.

### 3.5 Stopping fictional physical-action claims `[OFFICIAL]`

OpenAI ships a ready-made prompt block for this, and its *diagnosis* is the
valuable part:

> "Realtime models are eager to help. **If the prompt mentions a tool that is
> not actually available, or if the tool list does not match the prompt, the
> model may invent a tool name or pretend it completed the action.**"
>
> ```
> ## Tool Availability
>
> Use only the tools that are explicitly provided in the current tool list.
>
> Do not invent, assume, or simulate tools. If a tool is mentioned in the
> instructions but is not present in the tool list, treat it as unavailable.
>
> If the user requests an action that requires an unavailable tool:
>
> 1. Do not pretend to complete the action.
> 2. Briefly explain that the tool is not available.
> 3. Offer the closest supported next step.
>
> Only say an action was completed after the relevant tool call succeeds.
> ```
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

Corroborated across vendors: ElevenLabs — "Always call this tool before
providing order information—never rely on memory or assumptions"; Deepgram —
"Always call the function first, then respond based on the results — never guess."

`[INFERENCE]` **The diagnosis matters more than the block for us.** A prompt that
describes a capability not currently in the tool list invites simulation — and
our `open_toolbox` design deliberately hides tools that the prompt still
describes in prose (行程、待辦、電視、NAS…). That is precisely the named
condition for invented actions. The `open_toolbox` prose is not wrong, but it
needs the Tool Availability block beside it to keep "describe a gated tool" from
sliding into "pretend to have used it."

The structural mechanism remains the JSON envelope (prior research §C3):

> "During training, tool outputs commonly look like JSON objects with named
> fields. If your tool returns a raw string and separately asks the model to
> 'repeat exactly,' the model may be more prone to paraphrasing, truncation, or
> blending in its own preamble."

`[INFERENCE]` Generalize this past `move_head`: **every tool that causes physical
motion should return what actually happened**, in named fields the model is
instructed to render. Fabricated action narration is not fixable by prohibition;
it is fixable by making the true fact the easiest thing to say.

### 3.6 Give the model a tool instead of a prohibition `[OFFICIAL]`

> "If the latest audio is silence, background noise, hold music, TV audio, side
> conversation, or speech not addressed to you, call `wait_for_user`." Plus: "do
> not respond conversationally after calling this tool."

`[INFERENCE]` We already ship exactly this, and it is worth naming as a
*generalizable technique* rather than a one-off: **when you want the model not
to do something, give it a valid alternative action to take.** That is the
positive-form escape hatch for the banlist problem in §4.4 — and the pattern
most likely to fix the rules we currently express as 「不要說…」.

---

## Q4. Instructing small / distilled realtime models (mini tier)

### 4.1 What the tier is and what it trades `[OFFICIAL]`

Changelog, 2026-07-06:

> "Released GPT-Realtime-2.1, an updated realtime reasoning model with improved
> alphanumeric recognition, silence and noise handling, and interruption
> behavior. Also released GPT-Realtime-2.1 mini, a faster, lower-cost distilled
> reasoning model for realtime voice applications."
> — <https://developers.openai.com/api/docs/changelog>

Both: 128,000-token context, 32,000 max output, knowledge cutoff 2024-09-30,
text+audio+image in / text+audio out, reasoning-token support, function calling,
prompt caching. Mini is roughly 6–7× cheaper on text and ~3× on audio.
— <https://developers.openai.com/api/docs/models/gpt-realtime-2.1>,
<https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini>

> **Notable:** the model pages do **not** enumerate a capability delta. On paper
> mini carries the same modality and feature matrix; the only official
> differentiator is positional ("distilled", "faster, lower-cost"). The
> capability framing lives in the prompting guide's model-selection table —
> which still reads `gpt-realtime-2` vs `gpt-realtime-1.5` and **has not been
> refreshed for the 2.1 generation.** `[INFERENCE]` So the widely-repeated claim
> that mini trades away "reasoning, tool use, instruction following" is a
> reasonable reading of OpenAI's positioning, but it is not a 2.1-specific
> published statement. Practitioner evidence (§4.2) is the stronger support.

### 4.2 What practitioners report failing on mini `[PRACTITIONER]`

The richest taxonomy, from a team that abandoned the tier:

> "The Mini can detect a pattern, example it can detect that I am scamming it -
> but it doesnt action on it."
> "The mini is unable to count. So a prompt that says 'allow max 2 tries and
> then call off topic' that works perfectly in the realtime model, the mini
> cannot do."
> "Ignores scope boundaries."
> "I would say the mini does 60% of what the realtime does."
> — <https://community.openai.com/t/giving-up-on-realtime-mini/1379423>
> (two practitioners, no benchmark — but specific and testable)

`[INFERENCE]` The generalizable shape: **mini fails at rules requiring state
accumulation across turns, and at detect-then-act rules where detection succeeds
but the action doesn't fire.** Both are the shape of our hardest rules.

Prompts are also not portable across mini generations: a developer reported
2.1-mini refusing a tool that `gpt-realtime-mini` called correctly with identical
prompt and tools; the community reply was "The prompt that you had for mini needs
to be adjusted to work for gpt-2.1-mini." Thread unresolved.
<https://community.openai.com/t/model-gpt-realtime-2-1-mini-not-calling-function-tools-in-sip-realtime-while-gpt-realtime-mini-works-with-the-same-prompt-tools/1386141>

### 4.3 What works `[OFFICIAL]`

From OpenAI's own PDF, *Seven tips for prompting voice agents with the Realtime
API* (<https://cdn.openai.com/API/docs/realtime-prompting-guide.pdf>) and the
cookbook:

- **Precision over volume.** "small wording changes or unclear instructions can
  shift behavior a lot" — their example: changing "inaudible" → "unintelligible"
  "significantly improved how the model handled noisy inputs."
- **Bullets over paragraphs.** "Realtime models have shown to follow short
  bullet points better than long paragraphs."
- **Examples over rules.** "The model learns style from examples. Give short,
  varied samples for common conversation moments" — "Sample phrases (vary, don't
  always reuse)."
- **Capitalize critical rules**, and "convert non-text rules (such as numerical
  conditions) into text before capitalization" — i.e. "IF MORE THAN THREE
  FAILURES THEN ESCALATE", not "IF x > 3".
- **A `## Variety` rule** against robotic repetition.
- **A dedicated `## Language` section** only if you see unwanted switching, and
  "Make sure it doesn't conflict with other rules."

`[INFERENCE]` The "convert numeric rules to text" tip lands differently given
§4.2's finding that mini cannot count: spelling thresholds out in words is a
cheap, directly-targeted mitigation for a documented mini weakness.

### 4.4 What fails — and the banlist finding `[PRACTITIONER]` / `[OFFICIAL]`

This is the most consequential Q4 finding for our codebase.

> "Long enumerated 'never say X, Y, Z' lists are an anti-pattern. Every banned
> phrase is a token in the model's active context — and under output
> uncertainty, **recently-activated tokens can be over-sampled, so the verbose
> ban effectively becomes a menu of likely outputs.**" Recommendation: "Prefer a
> short positive principle… over an exhaustive negative enumeration."
> — <https://github.com/VapiAI/docs/blob/main/fern/prompting-guide.mdx>

Independently corroborated: "Listing 20 banned phrases backfires. Under
uncertainty, the model over-samples recently seen tokens, so your banlist reads
like a menu of things to say." Keep to "four or five, plus one line on what to
do instead." (<https://relinns.com/blogs/guide-to-voice-ai-prompting>)

And first-party on the same axis: "Be careful with constraint words such as
`must`, `only`, `never`, and `always`."

> `[INFERENCE]` **Our hardening block does the banned thing, in the banned way.**
> It enumerates specific forbidden utterances — 「不要說『我在這裡』『我沒聽清楚』
> 『慢慢來』」, 「不要使用『讓我想想』『我看一下喔』」, 「後面不要再補『還需要我
> 幫你什麼嗎？』」— which is precisely the "menu of likely outputs" pattern.
> The mechanism claim (token over-sampling) is a practitioner hypothesis rather
> than a measured result, so I flag it as such — but two independent sources
> assert it, OpenAI's constraint-word warning points the same direction, and our
> own observed failure mode is *those exact banned phrases appearing every
> turn*. That convergence between an external prediction and our own bug is the
> strongest signal in this document. Converting these to positive rules plus a
> `wait_for_user`-style alternative action (§3.6) is a high-value experiment.

Other documented failures: long prose rules; vague constraint language ("be
concise"); overlapping hard constraints with no priority; narrow wording that
under-triggers (§1.3); tools named in the prompt but absent from the tool list
(§3.5); and `[PRACTITIONER]` **example phrases mistaken for triggers** —
"literal transition matching" on 2.1-mini
(<https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896>).
We already defend against that last one — 「以上只是語氣示範，不是要你等到聽見
這些句子才這樣講」— which is the correct mitigation and should be kept.

### 4.5 Repetition and placement

**Repetition of critical rules: recommended, with a caveat.** `[PRACTITIONER]`
ElevenLabs: "Repeating the most important 1-2 instructions twice in the prompt
can help reinforce them" because "models may prioritize recent context over
earlier instructions"; also "Highlight critical steps by adding 'This step is
important' at the end of the line"
(<https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide>).
LiveKit: "State the rule explicitly… Show examples… Restate the rule in a
section with more examples" — "the model almost always needs more redundancy
than you expect" (<https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic>).

**But Deepgram disagrees** — define tone "once at the start, not repeated across
sections," warning repetition "dilutes signal." `[INFERENCE]` The reconcilable
version: **repeat one or two critical *behavioral* rules; do not repeat
*stylistic* guidance.** This also aligns with prior research's advice to state a
rule identically in both the system prompt and the tool description.

**Placement (top vs bottom): unsupported.** `[PRACTITIONER]` The claim "put the
heaviest rules at the top and bottom, since models weight the edges of a prompt
more than the middle" rests on **a single uncited blog**
(<https://relinns.com/blogs/guide-to-voice-ai-prompting>). No first-party source
confirms it. `[INFERENCE]` Treat as folklore and an A/B hypothesis at best. What
*is* documented is that authority comes from **labeling and sectioning** — "Use
short, labeled sections. The model should be able to find the relevant
instructions quickly" — not from position.

---

## Q5. Reasoning effort vs latency and tool reliability

### 5.1 Levels and default `[OFFICIAL]`

| Effort | "Use when" (verbatim) | Example |
|---|---|---|
| `minimal` | "Lowest latency matters most and the task is simple." | Smart-home commands, timers, simple calendar checks |
| `low` | "You need responsiveness plus basic reasoning." | Customer support, order lookup, simple policy questions |
| `medium` | "The assistant must reason through multi-step tasks." | Technical support, diagnostics, complex routing |
| `high` | "Deeper reasoning materially improves success." | High-precision workflows, escalation decisions |
| `xhigh` | "Maximum reasoning is worth added latency and cost." | Complex planning, critical triage, high-stakes orchestration |

> "Start with `low` for most production voice agents."
> — <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

Default confirmed by OpenAI staff: "For Realtime models that support reasoning,
the default reasoning level is `low` when no value is specified."
— <https://community.openai.com/t/gpt-realtime-model-default-reasoning/1387803>

> **Source discrepancy, flagged:** Azure Foundry documents only
> `minimal|low|medium|high` and a **256,000**-token context; OpenAI's own pages
> document `xhigh` and **128,000**. Trust OpenAI's pages for the OpenAI
> endpoint.

### 5.2 Measured cost `[PRACTITIONER]`

The only concrete numbers found (The Batch, 2026-05-15, reporting OpenAI's
benchmark disclosures):

> "1.12 seconds to first audio at minimal reasoning, 2.33 seconds at high
> reasoning"

against the standard that conversational voice "benefit[s] from latency lower
than 500 milliseconds". Benchmark spread by effort, same source:

- Conversational Dynamics (turn-taking, pausing, interruptions): "GPT-Realtime-2
  set to **minimal** reasoning led with 96.1 percent"
- Big Bench Audio (QA): "**high** reasoning tied Google's Gemini 3.1 Flash Live
  Preview… (96.6 percent)"
- Scale AI Audio MultiChallenge (instruction retention, memory, coherence):
  "**xhigh** reasoning placed first (48.45 percent average pass rate)"

— <https://www.deeplearning.ai/the-batch/openai-challenges-speech-to-speech-leaders>

`[OFFICIAL]` Separately, 2.1 "reduced p95 latency by at least 25% across Realtime
voice models through improved caching"
(<https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896>).

> `[INFERENCE]` This is the sharpest trade-off in the doc and it is not a free
> lunch: **the effort level that wins on conversational feel is the one that
> loses on instruction retention, and vice versa.** A companion robot needs
> both. That argues for keeping the conversational path fast and moving hard
> sequencing off it — composite tools, app-side ordering — rather than buying
> reliability with latency everywhere.

### 5.3 Prompt-side reasoning control `[OFFICIAL]`

The API knob pairs with a `## Reasoning` prompt block:

> "For direct answers, simple lookups, and short confirmations, respond quickly
> and do not reason. For multi-step tasks, tool decisions, troubleshooting, or
> escalation, reason before acting."

`[INFERENCE]` This makes effort *conditional within a turn*, which is cheaper
than raising the session-wide level. We do not use it at all.

### 5.4 Effort × tool reliability

`medium` is placed at "The assistant must reason through multi-step tasks" —
the closest official link between effort and tool sequencing. **No source
publishes tool-call accuracy by effort level.** `[INFERENCE]` Open empirical
question; it is a one-field experiment and should be measured on-robot rather
than argued.

---

## Q6. Non-English (Chinese) voice agents

### 6.1 English is the default and must be explicitly overridden `[OFFICIAL]`

> "A user's accent is not the same as their intended language. A user may speak
> English with a Hindi, Spanish, French, or **Mandarin** accent and still expect
> English responses."

Avoid, verbatim: "Mirror the user. / Respond naturally in the user's language. /
Switch languages when appropriate. / Sound local. / Adapt to the user's accent."
— "These are too broad. The model may interpret accent, filler words,
backchannels, or isolated foreign words as a reason to switch languages."

The sanctioned block (invert English↔Chinese for our case):

```text
## Language

Default to English unless the user clearly uses another language.

Switch languages only when:
- the user explicitly asks to use another language;
- the user provides a substantive utterance in another language. A substantive
  utterance means the user gives a complete request, question, or correction in
  another language, not just a greeting, name, address, filler word, or borrowed
  phrase.

Do not switch languages based on:
- accent; pronunciation; filler words; short backchannels; names; addresses;
  isolated foreign words.

If uncertain, ask:
"Would you like me to continue in English or [LANGUAGE]?"
```

Plus, and we do **not** have this: **"Keep preambles, spoken bridges,
tool-related messages, and final answers in the same language."**
— <https://developers.openai.com/api/docs/guides/realtime-models-prompting>

`[INFERENCE]` Our hardening block's `### 語言` section is already close to a
faithful translation of this guidance, including the accent/filler/backchannel
exclusions. That is a genuine strength. The gap is the cross-channel clause
above — with `phase`/commentary now in play (§2.3), "preamble in one language,
final answer in another" is a real and newly-relevant failure mode.

### 6.2 Should the prompt itself be in Chinese or English? — genuinely unsettled

`[OFFICIAL]` **There is no official OpenAI guidance on the language of the
instructions field.** A targeted search found none. What *is* observable is that
every OpenAI example prompt, including the multilingual ones, is written in
English with the target language named inside it — the cookbook's role line is
"You are french quebecois speaking customer service bot." So the *sanctioned
pattern by demonstration* is an English prompt that names the language.

`[PRACTITIONER/RESEARCH]` Pulling the other way, *Cross-Lingual Prompt
Steerability* (arXiv:2512.02841, 2025-12-02), across five languages, three LLMs,
three benchmarks:

> "English-prompt setting exhibits slightly worse overall performance compared
> to Same-language setting, yielding lower average accuracy and consistency, as
> well as significantly higher accuracy variance."

— <https://arxiv.org/html/2512.02841>

> `[INFERENCE]` **Honest verdict: the evidence does not settle this, and I would
> not churn the prompt over it.** The study is on text LLMs rather than
> speech-to-speech, calls the gap marginal, and says nothing about distilled
> mini models. OpenAI demonstrates the opposite convention without ever
> defending it. Our current all-Chinese prompt is defensible — the study's
> *lower variance* finding is mildly encouraging given that inconsistency is our
> complaint — but it is not clearly optimal either.
>
> The one narrow experiment I would actually run: **write tool-selection rules
> in English while keeping persona and style in Chinese.** Tool names, enum
> values, and schemas are already English; a Chinese rule referencing an English
> tool name is the exact cross-lingual seam most likely to under-trigger, and
> §1.3's stricter literal matching makes that seam sharper on 2.x. That is a
> targeted test, not a rewrite.

### 6.3 Mini-tier non-English regression `[PRACTITIONER]`

Carried forward from prior research and still unresolved: language drift on 2.1
despite explicit instructions; "2.1 mini is so nice fastest reasoning model, but
on other languages except english is very very bad"; and a Romanian production
deployment reporting fabrication against supplied context after a forced
snapshot migration. No staff resolution on any thread.

`[INFERENCE]` The honest position: **some of what we are trying to fix by
prompting may be a model-tier limitation.** The decisive experiment is not
another prompt revision — it is running the same prompt on full
`gpt-realtime-2.1` and measuring the delta.

---

## What this means for Reachy

Prioritized. None requires a new dependency.

1. **Invert `go_to_sleep`: tell the model *not* to speak when calling it, and
   generate the farewell from the tool's return value.** Replace the prose rule
   「先簡短道別再調用」with LiveKit's shipped shape — description ends "Don't
   generate any other text or response when the tool is called"; the
   `function_call_output` carries an instruction like 「跟使用者道別」; the app
   waits for that speech to complete, then sleeps. This is simultaneously the
   OpenAI protocol (`function_call_output` → `response.create`) and the
   reference framework's default. `[OFFICIAL]`+`[PRACTITIONER]`, highest
   confidence in this doc. Same shape applies to any other
   speak-then-irreversibly-act tool.

2. **Know the override rule before reaching for per-response `instructions` —
   which #1 will tempt us to do.** Raw `response.create.instructions`
   *replaces* session config for that response: persona, Chinese pinning and
   anti-fabrication rules all vanish. Either re-send what matters, or carry the
   instruction in the `function_call_output` / a conversation item (which is how
   LiveKit does it, and why its version stays additive). `[OFFICIAL]`.
   **Checked against our code (2026-09-01): not currently a bug** — every
   `_safe_response_create()` call site in `huggingface_realtime.py` is bare, and
   `instructions` is set only at session level in `openai_realtime.py:683`. This
   is a guardrail for the change in #1, not an existing defect.

3. **Do a subtractive pass on the enumerated banlists.** Two independent
   practitioner sources plus OpenAI's constraint-word warning say a long "never
   say X, Y, Z" list becomes "a menu of likely outputs" — and our observed
   failure is those exact banned phrases recurring. Convert to positive rules
   plus an alternative action, keeping four or five bans at most.
   `[PRACTITIONER]` mechanism (hypothesis, not measured) but it *predicts our
   actual bug*, which is why it ranks this high.

4. **Add the `# Message Channels` and `# Preambles` sections.** "Tool calls
   happen in the commentary channel. If you want the assistant to say something
   before, during, or after tool use, specify that behavior in relation to the
   commentary channel" is the 2.x control surface for this whole problem class,
   and we have no prompt block addressing it. Pair with client-side handling of
   the `phase` field — verify what our WebSocket handler does with multi-item
   responses before further verbosity surgery, since we may be judging brevity
   against preamble+answer concatenated. `[OFFICIAL]`.

5. **Add the `## Tool Availability` block, and treat it as an `open_toolbox`
   safety rail.** OpenAI's diagnosis is that a prompt describing capabilities
   absent from the current tool list invites the model to *simulate* them —
   which is structurally what our toolbox gating creates. `[OFFICIAL]` block,
   `[INFERENCE]` on the connection to our design.

6. **Move preamble sample phrases into tool descriptions, tagged per tool.**
   Mark slow tools (`camera`, `look_around`, search, `nas` query)
   PREAMBLES with sample phrases in the description; mark fast ones PROACTIVE.
   Explicitly **not** for `go_to_sleep` — see #1. `[OFFICIAL]`.

7. **Return ground truth from every physical-action tool.** Extend the
   verbatim-envelope idea to motion: every tool that moves the robot returns
   what actually moved, in named JSON fields. Fabricated action narration is not
   fixable by prohibition; it is fixable by making the true fact the easiest
   thing to say. `[OFFICIAL]` pattern, `[INFERENCE]` on the generalization.

8. **Add the cross-channel language clause and a `## Reasoning` block.** "Keep
   preambles, spoken bridges, tool-related messages, and final answers in the
   same language" closes a newly-relevant gap now that commentary is a separate
   channel. The `## Reasoning` block steers effort per-turn, which is cheaper
   than raising the session level. Both `[OFFICIAL]`, both cheap.

9. **A/B two near-free experiments:** rename `go_to_sleep` toward the
   in-distribution `finish_session` (keeping "sleep" in the description), and
   test `reasoning.effort` on-robot. Published data shows `minimal` wins
   conversational dynamics while `xhigh` wins instruction retention at ~2×
   time-to-first-audio; **no source publishes tool-call accuracy by effort**, so
   measure rather than argue. `[OFFICIAL]` naming guidance (documented for 1.5
   only), `[PRACTITIONER]` latency numbers.

10. **Run the same prompt on full `gpt-realtime-2.1` once, as a diagnostic.**
    Mini is positioned as distilled, practitioners put it at "60% of what the
    realtime does" with a failure taxonomy matching ours, and non-English mini
    regressions are independently reported and unresolved. If full-2.1 fixes our
    failures, this is a cost decision, not a prompting problem — and we should
    stop spending prompt revisions on it. `[OFFICIAL]` positioning,
    `[INFERENCE]` on the test.

**Deliberately not recommended:** rewriting the prompt into English. The
evidence is genuinely split (§6.2) and our Chinese language-control block is
already close to official guidance. The narrow experiment — English for
tool-selection rules only — is worth a trial; a wholesale switch is not
supported.

---

## Confidence and gaps

**Strong (`[OFFICIAL]`, directly quoted, mostly verified by direct fetch):**
prompt section skeleton; "start simple" length discipline; per-response
`instructions` override semantics (two independent sources, one spec-generated);
the two-step function-calling protocol; the commentary-channel control surface
and `phase`; preamble use/don't-use rules and style limits; Tool Availability
block; `Use when / Do NOT use when` format; reasoning effort table and `low`
default (staff-confirmed); English-default language policy; 2.1 release facts.

**Strong (`[PRACTITIONER]`, verified by direct fetch of source + docs):**
LiveKit's `end_call` description and `end_instructions` default — I fetched both
the Python source and the docs page independently and they corroborate.

**Moderate:** latency-by-effort numbers are second-hand reporting of OpenAI
benchmarks, not a primary OpenAI page. The Vapi/relinns banlist *mechanism*
(token over-sampling) is a stated hypothesis, not a measured result — I rank it
highly because it predicts our observed bug, not because it is proven. Mini
failure taxonomy rests on two practitioners without a benchmark.

**Thin / explicitly unresolved:**
- **Whether mini-tier models can reliably narrate-then-call in one response —
  no source measures this.** Inferred from adjacent failures only.
- **Tool-call accuracy as a function of reasoning effort — not published
  anywhere I looked.**
- **Rule placement (top vs bottom) — rests on one uncited blog. Treat as
  folklore.**
- **Enums vs free-form arguments for voice agents — no vendor doc addresses it;
  the "enum collapse" claim could not be corroborated.**
- **Tool-count hard limits — no vendor states one;** the 10–15 / 15–20 figures
  are third-party citing unnamed benchmarks.
- **Prompt language (Chinese vs English) — one text-LLM study, marginal effect,
  contradicted by OpenAI's demonstrated convention. Genuinely unsettled.**

**Source-access notes:** `platform.openai.com/docs/*` and `openai.com/index/*`
return HTTP 403 to automated fetch; their content is cited via mirrors
(core42, the openai-python spec, Microsoft Foundry) and search summaries, which
matched on every field I could cross-check. `learn.microsoft.com/…/realtime-2`
timed out on direct fetch here but was read successfully by a subagent.
**Two Foundry/OpenAI discrepancies flagged in §5.1** (context window, `xhigh`).

**No DevDay 2026 realtime write-up exists** in indexed form. OpenAI's audio
learning page links a "DevDay — realtime breakout" video with no date and no
transcript; the changelog notes that realtime prompting guidance was *moved
into* the "Using realtime models" guide in May 2026, which appears to be where
that material now lives.

**Generational hazard worth repeating:** the preamble/filler recipes in §2.4 are
from the `gpt-realtime-1.5` sections of the guide; §2.3 and §2.5 are the 2.x
guidance. Blog posts quoting "OpenAI says put filler phrases in your prompt" are
usually quoting the older tier.

## Sources

- <https://developers.openai.com/api/docs/guides/realtime-models-prompting>
- <https://developers.openai.com/api/docs/guides/realtime-conversations>
- <https://developers.openai.com/api/docs/changelog>
- <https://developers.openai.com/api/docs/models/gpt-realtime-2.1>
- <https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini>
- <https://developers.openai.com/api/reference/resources/realtime/server-events>
- <https://developers.openai.com/api/reference/resources/realtime/client-events>
- <https://developers.openai.com/api/reference/ruby/resources/realtime>
- <https://developers.openai.com/cookbook/examples/realtime_prompting_guide>
- <https://developers.openai.com/cookbook/examples/realtime_out_of_band_transcription>
- <https://cdn.openai.com/API/docs/realtime-prompting-guide.pdf>
- <https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/realtime/realtime_response_create_params.py>
- <https://www.core42.ai/compass/documentation/realtime-api-reference> (API reference mirror)
- <https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/realtime-2>
- <https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio>
- <https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/beta/tools/end_call.py>
- <https://docs.livekit.io/agents/prebuilt/tools/end-call-tool/>
- <https://docs.livekit.io/agents/logic/tools/definition/>
- <https://docs.livekit.io/agents/build/audio/>
- <https://livekit.com/blog/async-tools-voice-agents>
- <https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic>
- <https://github.com/VapiAI/docs/blob/main/fern/prompting-guide.mdx>
- <https://docs.vapi.ai/api-reference/assistants/create>
- <https://developers.deepgram.com/docs/prompting-voice-agents>
- <https://developers.deepgram.com/docs/voice-agent-inject-agent-message>
- <https://docs.pipecat.ai/pipecat/learn/function-calling>
- <https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide>
- <https://elevenlabs.io/docs/eleven-agents/customization/tools/system-tools/end-call>
- <https://docs.retellai.com/build/prompt-engineering-guide>
- <https://docs.retellai.com/build/single-multi-prompt/end-call>
- <https://prod.agora.io/en/blog/gpt-realtime-2-is-here-and-preambles-change-how-voice-agents-feel>
- <https://www.deeplearning.ai/the-batch/openai-challenges-speech-to-speech-leaders>
- <https://arxiv.org/html/2512.02841>
- <https://relinns.com/blogs/guide-to-voice-ai-prompting>
- <https://machinelearningmastery.com/the-complete-guide-to-tool-selection-in-ai-agents/>
- <https://www.kn8.ai/blog/webmcp-tool-design-best-practices>
- <https://legacy.patrickmichael.co.za/how-do-you-end-calls-smoothly-vapi-complete-guide-professional-voice-agent-call-endings>
- <https://community.openai.com/t/giving-up-on-realtime-mini/1379423>
- <https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896>
- <https://community.openai.com/t/model-gpt-realtime-2-1-mini-not-calling-function-tools-in-sip-realtime-while-gpt-realtime-mini-works-with-the-same-prompt-tools/1386141>
- <https://community.openai.com/t/gpt-realtime-model-default-reasoning/1387803>
- <https://community.openai.com/t/realtime-api-preamble-inconsistent/1361953>
- <https://community.openai.com/t/realtime-api-sometimes-creates-speech-before-a-tool-call-sometimes-doesnt/1153507>
- Prior repo research: `docs/research-mini-tool-calling-2026-08.md`
