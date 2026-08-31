# Research: Instructing LLM agents that choose their own tools and their own words

Date: 2026-09-01. Scope: web research only, no code changes.
Question, in the operator's words: *"the model will understand the intent but decide
which tools to call; the model will decide what to say."* What is the September-2026
state of the art for **instructing** such a model, rather than hard-coding behavior
around it?

Companion doc: `docs/research-mini-tool-calling-2026-08.md` covers model-specific
Realtime/mini failure modes and OpenAI Realtime prompt mechanics. This doc is the
general, cross-vendor layer underneath it, and deliberately does not re-litigate those
findings — **except in two places where new evidence contradicts them**, flagged as
⚠️ CONFLICT.

**Evidence labels:**
- `[OFFICIAL]` — model vendor or protocol spec: Anthropic, OpenAI, MCP.
- `[PRACTITIONER]` — credible framework docs (LiveKit, Vapi, LangChain, Pipecat),
  widely-cited repos/essays. Company-published but not the model vendor.
- `[RESEARCH]` — peer-reviewed or arXiv, with dates and venues where known.
- `[INFERENCE]` — my reasoning, not sourced.

Sourcing honesty: items I could not verify to a primary source are marked **thin** or
**unverified** inline, and there is a "do not cite" list at the end. I did not fill
gaps with plausible-sounding citations.

---

## Q1. Instructing vs. orchestrating: where does the 2026 line sit?

### The short answer

There is a real, consistent consensus, and it is **not** "wrap the model in code."
It is a three-tier escalation ladder that no single source states in full but that
every major source implies:

1. **Fix the instructions and the tools first** — and tool design outranks prompt
   wording (see Q2, where the research is emphatic about this).
2. **Fix the context** — what tokens the model actually had, and *where* they were.
3. **Reserve code for the execution boundary** — approval, interruption, timing,
   irreversibility — not for deciding what to do or say.

### The evidence

`[OFFICIAL]` OpenAI states the ladder almost literally. From *A practical guide to
building agents* (p.16): *"Use multiple agents if improving tool clarity by providing
descriptive names, clear parameters, and detailed descriptions doesn't improve
performance."* Fix tool clarity **first**; split the system only if that fails.
Notably, "add deterministic code" is not a rung on the ladder at all.
<https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf>

`[OFFICIAL]` And earlier (p.11): *"Define clear actions: Make sure every step in your
routine corresponds to a specific action or output… Being explicit about the action
(and even the wording of a user-facing message) leaves less room for errors in
interpretation."*

`[OFFICIAL]` The crispest statement of "let the model drive; code only interrupts" is
OpenAI's description of its own Agents SDK (p.31): it *"relies on optimistic execution
by default… the primary agent proactively generates outputs while guardrails run
concurrently, triggering exceptions if constraints are breached."* Guardrails are
**concurrent tripwires**, not gates in the decision path.

`[OFFICIAL]` OpenAI also argues against pre-declaring the flow (p.20): *"Some
frameworks are declarative, requiring developers to explicitly define every branch,
loop, and conditional in the workflow upfront… this approach can quickly become
cumbersome and challenging as workflows grow more dynamic and complex."*

`[OFFICIAL]` The counterweight, also OpenAI (p.6): *"Before committing to building an
agent, validate that your use case can meet these criteria clearly. Otherwise, a
deterministic solution may suffice."* So determinism is the default *for choosing
whether to build an agent at all* — but having chosen one, you do not re-introduce
determinism into its decisions.

`[OFFICIAL]` Anthropic's canonical distinction: *"Workflows are systems where LLMs and
tools are orchestrated through predefined code paths. Agents, on the other hand, are
systems where LLMs dynamically direct their own processes and tool usage, maintaining
control over how they accomplish tasks."* Paired with *"we recommend finding the
simplest solution possible, and only increasing complexity when needed."* Read
carefully this is a complexity argument, not a pro-code argument — orchestration
machinery is complexity too.
<https://www.anthropic.com/engineering/building-effective-agents>

`[OFFICIAL]` **The most direct vendor statement that misbehavior is an instruction
problem** is Anthropic's multi-agent research post: their strategy is *"instilling
good heuristics rather than rigid rules."* Also *"Effective prompting relies on
developing an accurate mental model of the agent"* and *"The Claude 4 models can be
excellent prompt engineers. When given a prompt and a failure mode, they are able to
diagnose why the agent is failing and suggest improvements."*
<https://www.anthropic.com/engineering/multi-agent-research-system>

`[OFFICIAL]` **The "right altitude" principle is an explicit warning against
hard-coding logic into prompts** — the failure mode is not only "too little
instruction" but "too much, too rigid." At one extreme *"engineers hardcod[e] complex,
brittle logic in their prompts to elicit exact agentic behavior"*; at the other they
*"provide vague, high-level guidance that fails to give the LLM concrete signals."* The
target: *"specific enough to guide behavior effectively, yet flexible enough to provide
the model with strong heuristics to guide behavior."*
<https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>

`[PRACTITIONER]` The clearest single sentence found anywhere, from LangChain: *"Use
deterministic code for steps with clear requirements, and give the LLM control where
the application must interpret unstructured input or choose the next action."* Plus a
sharp discipline test: *"Do not outsource judgment you cannot evaluate. If you wouldn't
recognize a correct answer, neither will the agent."*
<https://www.langchain.com/blog/what-is-an-agent>

`[PRACTITIONER]` **12-factor agents** is the most-cited practitioner framing and is
often read as pro-code; read closely it is narrower. Factors: 1. Natural Language to
Tool Calls · 2. Own your prompts · 3. Own your context window · 4. Tools are just
structured outputs · 5. Unify execution state and business state · 6.
Launch/Pause/Resume with simple APIs · 7. Contact humans with tool calls · 8. Own your
control flow · 9. Compact Errors into Context Window · 10. Small, Focused Agents · 11.
Trigger from anywhere · 12. Make your agent a stateless reducer.
<https://github.com/humanlayer/12-factor-agents>

- Factor 4 draws the actual line: *"Tools don't need to be complex. At their core,
  they're just structured output from your LLM that triggers deterministic code."*
  The LLM decides *what*; your code controls *how*.
- Factor 8 ("own your control flow") is the strongest pro-code factor but is about the
  **execution/interruption boundary**: *"we need to be able to interrupt a working
  agent and resume later, ESPECIALLY between the moment of tool **selection** and the
  moment of tool **invocation**."*
- Factor 2 argues the *opposite* direction: *"Don't outsource your prompt engineering
  to a framework"* — own prompts precisely so instruction stays your lever.
- Factor 7 matters for conversation boundaries: even *contacting a human* should be a
  tool call the model emits, not a code branch.
- **Thin:** factor 12 is two images and the line "This one is mostly just for fun."
  Do not build an argument on it.

`[PRACTITIONER]` The live dissent — the "bitter lesson" camp — argues even execution
scaffolding should be deleted over time. Lance Martin (LangChain) documents his own
`open_deep_research` post-mortem: hand-designed workflows that avoided tool calling
*became the bottleneck* as models improved; the fix was removing structure, not adding
it. Credible as self-critique from inside a graph-orchestration framework, but
single-voice.
<https://rlancemartin.github.io/2025/07/30/bitter_lesson/>
OpenAI's Codex lead, more bluntly: *"If you rely on complex scaffolding to build AI
agents you aren't scaling you are coping."* **Caveat:** podcast transcript, directional
only.
<https://linearb.io/dev-interrupted/podcast/openai-codex-thibault-sottiaux-agentic-autonomy>

`[RESEARCH]` A 2026 result that lands squarely on this axis: *The Bitter Lesson of Tool
Calling* (arXiv 2608.06370, Aug 2026) finds **programmatic (code) tool calling matched
or beat JSON tool calling in 13 of 14 models** under parallel fan-out on BFCL v4
(GPT-5.6 family **+10.6%** over the JSON baseline), and *"remained stable during
context rot testing while baseline degraded 2.3% on average."*
<https://arxiv.org/abs/2608.06370>
`[INFERENCE]` Not directly actionable for a Realtime voice app — we cannot swap to code
execution — but it is evidence that *giving the model a more expressive action channel*
beats constraining it, which is the same direction as everything above.

`[PRACTITIONER]` Counterweight from the other side — Cognition's *Don't Build
Multi-Agents* — argues for hard architectural constraint, but note what it constrains:
*context flow*, not behavior. *"Share context, and share full agent traces, not just
individual messages"*; *"Actions carry implicit decisions, and conflicting decisions
carry bad results."*
<https://cognition.com/blog/dont-build-multi-agents>

### Is there an articulated principle?

**No source states the operator's three-clause formulation** ("let the model decide;
make the tools honest; reserve code for safety rails"). Do not attribute it. The three
closest genuine articulations are:

1. `[OFFICIAL]` OpenAI: **"optimistic execution by default"**, guardrails as concurrent
   tripwires.
2. `[PRACTITIONER]` LangChain: deterministic code for clear requirements, LLM control
   for interpretation and next-action choice.
3. `[OFFICIAL]` Anthropic: **"instilling good heuristics rather than rigid rules."**

`[INFERENCE]` The operator's formulation is a fair *synthesis* of these and is
consistent with all three. Present it in our docs as our own house rule informed by the
sources, not as a quotation from the field.

`[OFFICIAL]` One genuine, non-negotiable code rung, from the MCP spec: *"For trust &
safety and security, there **SHOULD** always be a human in the loop with the ability to
deny tool invocations."* Safety rails in code are protocol-level guidance, not taste.
<https://modelcontextprotocol.io/specification/2026-07-28/server/tools>

---

## Q2. Tool design as the primary instruction surface

This is the strongest-sourced section in the doc, and 2026 research makes a claim
stronger than the vendor docs do: **tool schema and description quality are measurable
accuracy levers, while rephrasing the prompt is close to noise.**

### The headline research result

`[OFFICIAL]` Berkeley's BFCL v4 prompt-variation ablation is the cleanest evidence:
- Prompt style is **not** the lever — plaintext → Markdown conversion and instruction
  rephrasing produce *"no consistent performance trends"*, and models *"demonstrate
  robust behavior across these prompt variations."*
- But **serialization format is**: *"performance is higher when the model is prompted
  to return function calls in Python or JSON format, compared to either of the XML
  formats"*, and *"performance is highest with functions in JSON format, lower with
  XML, and lowest with Python."*
- And **small models are where format choices bite**: requiring `<TOOLCALL>` wrapper
  tags caused only a *"slight performance drop"* for capable models but *"significant
  performance drops"* for Llama-3.1-8B-Instruct and BitAgent-8B; CoALM-70B went to
  *"near-zero performance."*
<https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html>

> `[INFERENCE]` This is the single most budget-relevant finding in the doc. If we have
> ten hours, they should go into tool names, schemas and returns, not into rewording
> the system prompt.

### Names

`[RESEARCH]` *Don't Adapt Small Language Models for Tools; Adapt Tool Schemas to the
Models* (arXiv 2510.07248, Oct 2025, rev. Apr 2026) identifies **schema misalignment**:
*"models hallucinate plausible tool names that are absent from the provided tool
schema, due to different naming conventions internalized during pretraining."*
Renaming tools to match pretraining conventions gave *"improvements of up to **17%**,
with schema misalignment errors reduced by **80%**"* — with **no training**.
<https://arxiv.org/abs/2510.07248>

> `[INFERENCE]` Design rule: **name a tool what the model already expects it to be
> called.** Our `move_head` / `look_around` naming should be sanity-checked against
> conventional names in the model's likely pretraining distribution rather than against
> our internal vocabulary.

`[OFFICIAL]` Anthropic on namespacing: *"prefix names with the service (for example,
`github_list_prs`, `slack_send_message`)"*, and, measurably, *"We have found selecting
between prefix- and suffix-based namespacing to have non-trivial effects on our
tool-use evaluations."*
<https://www.anthropic.com/engineering/writing-tools-for-agents>

`[OFFICIAL]` MCP name constraints: 1–128 chars, case-sensitive, ASCII letters/digits/
`_`/`-`/`.` only, unique per server.

### Descriptions

`[RESEARCH]` *Learning to Rewrite Tool Descriptions for Reliable LLM-Agent Tool Use*
(arXiv 2602.20426, Feb 2026, rev. Apr 2026): *"Tool descriptions are often written for
human developers and tolerate ambiguity that agents cannot resolve, particularly as the
number of candidate tools grows."* Automatically rewriting descriptions alone reduced
*"accuracy degradation by **29.23%**"* as catalogs scale and improved *"average
query-level success by **60.89%** on StableToolBench."*
<https://arxiv.org/abs/2602.20426>

`[RESEARCH]` And the failure this addresses dominates at scale: LiveMCPBench (arXiv
2508.01780, Aug 2025) with 527 tools across 70 servers found *"[r]etrieval errors
account for nearly half of all failures"* — best model Claude-Sonnet-4 at **78.95%**,
most models 30–50%. The hard part is picking the right tool, not calling it correctly.
<https://arxiv.org/abs/2508.01780>

`[OFFICIAL]` Anthropic is categorical: *"**Provide extremely detailed descriptions.**
This is by far the most important factor in tool performance."* Contents: what the tool
does, when it should be used **and when it shouldn't**, what each parameter means, and
*"[a]ny important caveats or limitations, such as what information the tool does not
return."* Target: *"Aim for at least 3–4 sentences for each tool description, more if
the tool is complex."*
<https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools>

`[OFFICIAL]` Framing: *"think of how you would describe your tool to a new hire on your
team.… Consider the context that you might implicitly bring — specialized query
formats, definitions of niche terminology, relationships between underlying resources —
and make it explicit."* With a measured payoff: *"Claude Sonnet 3.5 achieved
state-of-the-art performance on the SWE-bench Verified evaluation after we made precise
refinements to tool descriptions."*

`[OFFICIAL]` **Tension worth knowing:** OpenAI's 2026 model-guidance page pulls the
other way — *"Expose only tools relevant to the task, and keep their descriptions
concise and precise"* — and lists *"simplifying tool descriptions"* among the changes
producing its lean-prompt gains.
<https://developers.openai.com/api/docs/guides/prompt-guidance>
`[INFERENCE]` Reconcilable: both are attacking *low-information* text. Anthropic fights
under-specified one-liners; OpenAI fights boilerplate and prompt/tool duplication. The
shared rule is **high information density per token** — trigger conditions present,
prose padding absent.

### Parameters and schemas

`[RESEARCH]` **An important limit on "put it in the tool definition."** IFEval-FC
(arXiv 2509.18420, Sep 2025) tests format instructions *embedded in JSON schema
parameter descriptions* and finds that *"even state-of-the-art proprietary models,
including GPT-5 and Claude 4.1 Opus, frequently fail to follow basic formatting
rules"* there — a gap existing benchmarks (BFCL, τ²-Bench, ACEBench) do not measure.
Single-author preprint; no per-model scores in the abstract.
<https://arxiv.org/abs/2509.18420>

> `[INFERENCE]` The useful split: use the **description** for *when to use this tool*
> (where F18/F17 show large gains), and use **schema constraints** — enums, `required`,
> strict types — for *what shape the argument takes*. Prose format rules buried in a
> parameter description are the weakest of the three and should not be relied on.

`[OFFICIAL]` Anthropic: *"Input parameters should be unambiguously named: instead of a
parameter named `user`, try a parameter named `user_id`,"* and *"Avoid ambiguity by
clearly describing (and enforcing with strict data models) expected inputs and
outputs."* Anthropic now also ships `input_examples` — schema-validated example inputs
on the tool definition — for *"tools with complex inputs, nested objects, or
format-sensitive parameters"*, costed at *"~20–50 tokens for simple examples, ~100–200
tokens for complex nested objects."* Stated priority: *"Prioritize descriptions, but
consider using `input_examples` for complex tools."*

### Return values — what the tool RETURNS teaches the model what to say

The operator's specific question. **No source runs the clean ablation** ("same prompt,
different return shape, measure what the model says"), but the surrounding evidence is
strong and consistent.

`[OFFICIAL]` The sharpest quantified claim: *"We've found that merely resolving
arbitrary alphanumeric UUIDs to more semantically meaningful and interpretable language
(or even a 0-indexed ID scheme) **significantly improves Claude's precision in
retrieval tasks by reducing hallucinations**."* Changing only the return representation
changed the hallucination rate.
<https://www.anthropic.com/engineering/writing-tools-for-agents>

`[OFFICIAL]` The field-selection principle is explicitly about *what the model will go
on to say*: return *"only high signal information"*; *"eschew low-level technical
identifiers (for example: `uuid`, `256px_image_url`, `mime_type`). Fields like `name`,
`image_url`, and `file_type` are much more likely to **directly inform agents'
downstream actions and responses**."*

`[RESEARCH]` The strongest *causal* evidence that structure beats prose comes from the
grounding literature (see Q4): free-text extraction of a decision carries **22–26%
inconsistency** versus **~1%** when the model emits a machine-checkable field.
<https://arxiv.org/abs/2606.00476>

`[OFFICIAL]` Format is empirical, not settled: *"Even your tool response structure —
for example XML, JSON, or Markdown — can have an impact on evaluation performance:
there is no one-size-fits-all solution."* BFCL v4's ranking (JSON > XML) is the best
default available.

`[OFFICIAL]` OpenAI makes the return contract part of the description: *"Tool
descriptions should document their expected return fields, types, and error
behavior."*

`[OFFICIAL]` MCP formalizes it: tools may declare an `outputSchema`, results carry
`structuredContent` validated against it, with the stated benefit of *"[g]uiding
clients and LLMs to properly parse and utilize the returned data."* Note the
compatibility rule that is good practice anyway: *"a tool that returns structured
content SHOULD also return the serialized JSON in a TextContent block."*

`[OFFICIAL]` Token control belongs in the return: *"pagination, range selection,
filtering, and/or truncation with sensible default parameter values."* Claude Code caps
tool responses at 25,000 tokens. Anthropic also suggests a `response_format` enum
letting the *agent* pick `"concise"` vs `"detailed"` (~⅓ token saving in their example).

### Error messages written for the model

`[OFFICIAL]` MCP draws the line by *who can act on the error*. **Protocol errors** are
*"issues with the request structure itself that models are less likely to be able to
fix."* **Tool execution errors** *"contain actionable feedback that language models can
use to self-correct and retry with adjusted parameters"*, returned in-band with
`isError: true`. The rule: *"Clients **SHOULD** provide tool execution errors to
language models to enable self-correction."* Their example reads as advice, not a stack
trace: *"Invalid departure date: must be in the future. Current date is 08/08/2025."*

`[OFFICIAL]` Anthropic agrees: *"you can prompt-engineer your error responses to
clearly communicate specific and actionable improvements, rather than opaque error
codes or tracebacks."*

`[OFFICIAL]` MCP extends this to state expiry — *"A call against an expired or unknown
handle should return a tool execution error that says so, so the model can recover by
creating a new one"* — and to policy that lives in the description: a retention policy
*"should be stated in the creation tool's description… so the model can see it when
deciding to create state."*

### Combining vs splitting tools

`[OFFICIAL]` Both vendors say **combine**, and both give selection ambiguity — not
token cost — as the reason.

Anthropic: *"More tools don't always lead to better outcomes. A common error we've
observed is tools that merely wrap existing software functionality or API endpoints."*
Prescribed shape: *"Instead of implementing a `list_users`, `list_events`, and
`create_event` tools, consider implementing a `schedule_event` tool which finds
availability and schedules an event."* In the docs: *"Consolidate related operations
into fewer tools… group them into a single tool with an `action` parameter. Fewer, more
capable tools reduce selection ambiguity."*

`[OFFICIAL]` The decisive test, and the most useful sentence in this section: *"If a
human engineer can't definitively say which tool should be used in a given situation,
an AI agent can't be expected to do better."*
<https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>

`[OFFICIAL]` Counter-constraint: *"Make sure each tool you build has a clear, distinct
purpose."*

`[RESEARCH]` On how many to expose at once, the verified result is *How Many Tools
Should an LLM Agent See? A Chance-Corrected Answer* (arXiv 2605.24660, May 2026):
adaptive shortlists reached **93.1% vs 87.1%** selection accuracy against fixed lists,
and **90.3% coverage using ~7 tools instead of 50**. Already cited in our mini doc
(§A1). *Unknown authors/venue — treat as suggestive.*

> ⚠️ **Do not cite** the widely-circulated "BFCL: 43% → 2% accuracy when tools go from
> 4 to 51" or "740 tools: 0–20%" figures. Both appear in secondary blogs; **no primary
> source was found.** The verified tool-count evidence is the three items above.

`[OFFICIAL]` **Untrusted-input note:** MCP requires that *"clients MUST consider tool
annotations to be untrusted unless they come from trusted servers."*

---

## Q3. System-prompt engineering in 2026: what actually changed

**The headline change since 2024 is a reversal of direction: write LESS, not more.**
Both major vendors published this independently in 2026, and OpenAI attached numbers.
The research adds a second, sharper message: *where* an instruction sits matters far
more than how it is phrased.

### The lean-prompt turn

`[OFFICIAL]` OpenAI, *Model guidance* (GPT-5.6 family, July 2026): *"configurations
with leaner system prompts improved evaluation scores by roughly **10–15%** while
reducing total tokens by **41–66%** and cost by **33–67%**."* Same page: *"Results will
vary by workload, so treat these ranges as directional."*
<https://developers.openai.com/api/docs/guides/prompt-guidance>

Keep: *"Domain context, hard constraints, approval boundaries, and success criteria"*;
*"Name safe local actions explicitly"*; and the operative rule — ***"state each
instruction once."*** Remove: *"repeated instructions and examples"* and complex tool
descriptions.

`[OFFICIAL]` A specific warning that matches our class of bug: *"Repeating instructions
such as 'ask first,' 'do not mutate,' or 'wait for approval' can cause unnecessary
approval requests for safe, expected actions."* Repetition does not reinforce — it
**over-triggers**.

`[OFFICIAL]` Anthropic reaches the same place: *"prompting is converging with context
engineering for Claude 5 generation models — less scaffolding, more curation"*; *"the
best prompt isn't the longest or most complex. It's the one that achieves your goals
reliably with the minimum necessary structure"*; *"Don't over-engineer: Longer, more
complex prompts are NOT always better."*
<https://claude.com/blog/best-practices-for-prompt-engineering>

`[OFFICIAL]` Anthropic's "minimal" carries an important qualification: *"you should be
striving for the minimal set of information that fully outlines your expected behavior.
(Note that **minimal does not necessarily mean short**; you still need to give the
agent sufficient information up front…)"* Method: start minimal with the best model,
then add instructions and examples **against observed failure modes**.

`[OFFICIAL]` The companion shift is **dialing back emphasis**: *"If your prompts were
designed to reduce undertriggering on tools or skills, these models may now
overtrigger. The fix is to dial back any aggressive language. Where you might have said
'CRITICAL: You MUST use this tool when…', you can use more normal prompting like 'Use
this tool when…'."*
<https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices>

`[OFFICIAL]` OpenAI's Realtime guide says the same about constraint words: *"Be careful
with constraint words such as `must`, `only`, `never`, and `always`. Use them when the
behavior is truly required, not as general emphasis."*
<https://developers.openai.com/api/docs/guides/realtime-models-prompting>

> ⚠️ **CONFLICT with our mini doc (§C1, §A3).** That doc quotes the OpenAI *Realtime
> cookbook* endorsing *"Use capitalized text for emphasis"* and concludes *"for a mini
> model, redundancy is cheaper than a precedence gamble."* The 2026 general guidance
> from **both** vendors says the opposite. Both are `[OFFICIAL]`. The realtime/mini
> guidance is more specific to our deployment; the lean guidance is newer and
> quantified. `[INFERENCE]` Treat "dial it back" as the direction to A/B, not as a
> mandate to strip emphasis today — and settle it on our own transcripts.

### Where instructions sit beats how they are worded

This is the biggest addition the research tier makes, and it partly overturns the
intuition that prompt wording is the main lever.

`[OFFICIAL]` BFCL v4 (above): instruction rephrasing shows *"no consistent performance
trends."*

`[RESEARCH]` *Positional Failures in Long-Context LLMs* (arXiv 2605.23170, May 2026)
measures what does matter. Moving the task from end-of-context to the middle cost
**−12 to −86pp at 8K, −20 to −84pp at 32K, and up to −94pp at 64K** (Qwen 2.5-7B: 94%
→ 0%). Two operationally important details: *"76% of middle-position errors match
surrounding filler text versus 22% at end position"* — the model answers with nearby
text instead of the instruction — and **duplicating the task at the end of context
restored near end-level accuracy (within ±4pp)**, proving the loss is positional, not a
capability ceiling. (DeepSeek-V3.2 showed ~0pp, so this is model-dependent.)
<https://arxiv.org/abs/2605.23170>

`[RESEARCH]` Chroma's *Context Rot* (Jul 2025, 18 models, industry lab,
non-peer-reviewed) adds: *"model performance varies significantly as input length
changes, even on simple tasks"*; *"Even a single distractor reduces performance relative
to baseline"*; and, counterintuitively, *"models perform worse when the haystack
preserves a logical flow of ideas. Shuffling the haystack… consistently improves
performance."* On LongMemEval they saw *"significantly higher performance on focused
prompts compared to full prompts"* — trimming irrelevant history beats stuffing it in.
<https://www.trychroma.com/research/context-rot>

`[OFFICIAL]` Anthropic frames the same phenomenon as budget: *"LLMs have an 'attention
budget'… Every new token introduced depletes this budget."*

`[OFFICIAL]` The long-input ordering rule that survives everywhere: *"**Put longform
data at the top:** Place your long documents and inputs near the top of your prompt,
above your query, instructions, and examples."* With a number: *"Queries at the end can
improve response quality by up to 30 percent in tests."*

### Instruction decay across a conversation — the numbers

`[RESEARCH]` **SysBench (arXiv 2408.10943, Aug 2024, PKU/Baichuan) is the single most
useful number for system-prompt design.** 500 hand-written system messages with
multi-turn conversations. GPT-4o leads at **CSR 87.1% / ISR 76.4% / SSR 54.4%** — and
per-round compliance falls from **84.8% at round 1 to 33.7% by round 5**. *"The best
SSR [session satisfaction rate] is only 54.4%"* — the best model fully honors the
system prompt across a whole conversation barely half the time. Style and format
constraints are hardest; background/role constraints easiest (85–94% CSR).
<https://arxiv.org/abs/2408.10943>

`[RESEARCH]` **Multi-IF (arXiv 2410.15553, Meta, Oct 2024) is the one that should
worry us most**, because it breaks out language: *"All the models tested showed a
higher rate of failure in executing instructions correctly with each additional turn.
For example, o1-preview drops from 0.877 at the first turn to 0.707 at the third
turn… **languages with non-Latin scripts (Hindi, Russian, and Chinese) generally
exhibit higher error rates**."* Chinese is our primary scenario.
<https://arxiv.org/abs/2410.15553>

`[RESEARCH]` *LLMs Get Lost in Multi-Turn Conversation* (arXiv 2505.06120,
Microsoft/Salesforce, May 2025; 200,000+ simulated conversations): **39% average drop**
single-turn → multi-turn, and the mechanism is *"a minor loss in aptitude and a
significant increase in unreliability… when LLMs take a wrong turn in a conversation,
they get lost and do not recover."* Models assume early, commit prematurely, then
over-rely.
<https://arxiv.org/abs/2505.06120>

`[RESEARCH]` MultiChallenge (arXiv 2501.17399, Scale AI, ACL Findings 2025): every
frontier model under 50%, top scorer 41.4% (Claude 3.5 Sonnet). Its first failure axis
is literally **instruction retention**.
<https://arxiv.org/abs/2501.17399>

`[RESEARCH]` And IFBench (arXiv 2507.02833, Ai2/UW, NeurIPS 2025 D&B) shows benchmark
scores overstate the real thing: frontier models *"score below 50%"* on 58 held-out
constraints versus ~90% on IFEval; RLVR moved Tülu-3-8B **82.4 → 92.2 on IFEval but
only 28.9 → 45.9 on IFBench**. Do not read high IFEval scores as instruction-following
reliability.
<https://arxiv.org/abs/2507.02833>

> **Resolution of a thin claim.** The widely-repeated cadence *"re-inject a condensed
> reminder every 3–5 turns"* traces only to low-quality SEO content — **do not cite the
> cadence**. But the *underlying technique is now well-supported*: the positional-failure
> paper shows duplicating the instruction near end-of-context recovers nearly all lost
> accuracy, and SysBench/Multi-IF show why it is needed. Re-injection is sound; the
> specific number is invented. Note it does sit in tension with OpenAI's *"state each
> instruction once"* — `[INFERENCE]` these are reconcilable if you state each rule once
> *per generation*, refreshed by position, rather than stacking duplicates in one prompt.

`[RESEARCH]` "Instruction fade-out" is at least a named, engineered-around phenomenon
in production coding agents, countered by *"event-driven system reminders"* (arXiv
2603.05344, Mar 2026). **Thin:** single-author work-in-progress experience report, no
ablation isolating the reminder's effect. Design-pattern citation only.

### Positive vs negative phrasing

`[RESEARCH]` The strongest evidence is about **negation specifically**: *Multi-Constraint
State Tracking with Negation* (ACL 2026 SRW; 100,847 questions, 12 domains, 14 models)
finds negation is *"a dominant failure mode, causing accuracy reductions of **23-32%**
across models."* SRW venue, so a lower peer-review bar, but it is the cleanest number
on negation available.
<https://aclanthology.org/2026.acl-srw.119/>

`[OFFICIAL]` The vendor rule matches. Anthropic: *"**Tell Claude what to do instead of
what not to do.** Instead of: 'Do not use markdown in your response' Try: 'Your response
should be composed of smoothly flowing prose paragraphs.'"* OpenAI similarly favors
naming *"safe local actions explicitly"* over listing prohibitions.

`[OFFICIAL]` **Rationale beats prohibition**, and Anthropic's example is ours verbatim
(TTS output):
> Less effective: `NEVER use ellipses`
> More effective: `Your response will be read aloud by a text-to-speech engine, so
> never use ellipses since the text-to-speech engine will not know how to pronounce
> them.`
> *"Claude is smart enough to generalize from the explanation."*

> `[INFERENCE]` Note the honest nuance: the "more effective" version **still contains
> "never"**, and Anthropic's own recommended sample prompts use negatives freely ("DO
> NOT use ordered lists…"). The real rule is not "never use negatives" — it is that **a
> negative must carry its reason and its scope.** A bare ban generalizes badly; a ban
> with a stated cause generalizes well.

### Few-shot examples vs abstract rules — this changed in 2026

`[OFFICIAL]` Anthropic still rates examples highly: *"Examples are one of the most
reliable ways to steer Claude's output format, tone, and structure"* — relevant,
diverse, structured, *"Include 3–5 examples for best results."* The 2026 blog is
stingier and states a ramp: *"Start with one example (one-shot). Only add more examples
(few-shot) if the output still doesn't match your needs,"* warning that *"Claude 4.x and
similar advanced models pay very close attention to details in examples."*

`[OFFICIAL]` And the anti-pattern, plainly: *"teams will often stuff a laundry list of
edge cases into a prompt in an attempt to articulate every possible rule the LLM should
follow… **We do not recommend this.**"* Instead: *"curate a set of diverse, canonical
examples."* / *"For an LLM, examples are the 'pictures' worth a thousand words."*

`[RESEARCH]` **But the optimization literature now tilts toward prose rules over
demonstrations.** GEPA (arXiv 2507.19457, **ICLR 2026 Oral**; Agrawal et al., incl.
Khattab/Zaharia/Potts — the DSPy authors) *"outperforms GRPO by 6% on average and by up
to 20%, while using up to **35x fewer rollouts**"*, and beats *"the leading prompt
optimizer, MIPROv2, by over 10%."* Critically for this question, GEPA optimizes
**instructions only** and its prompts are *"up to **9.2× shorter** than those from
MIPROv2"* (which jointly optimized instructions + bootstrapped demos). The stated cause:
*"reflectively evolved instructions now demonstrate a lower generalization gap,
underscoring both advancements in model capabilities"* — attributed to *"recent advances
in the instruction-following and self-reflective abilities of LLMs."*
<https://arxiv.org/abs/2507.19457> · prior SOTA MIPRO: <https://arxiv.org/abs/2406.11695>

> `[INFERENCE]` The 2024→2026 arc is the finding: MIPROv2 needed demos; GEPA dropped
> them and won. Instruction-following got good enough that demonstrations stopped
> paying for their tokens. **This strengthens the "calibration principles" half of the
> operator's standing rule and mildly weakens the "few-shot examples" half.** It does
> not eliminate examples — GEPA's instructions are *machine-optimized against a metric*,
> which we are not doing — but it does mean: prefer a well-stated principle to a pile of
> examples, and add examples only where a principle demonstrably failed.

---

## Q4. Making the model ground claims in evidence rather than narrate fiction

Our failure mode: the robot says "I looked to the right" when no motion tool ran. The
2026 material treats this as a named, researched class — and delivers one finding that
**contradicts a recommendation in our own mini doc.**

### It has a name and a taxonomy

`[RESEARCH]` MIRAGE-Bench (arXiv 2507.21017, Berkeley, Jul 2025) is *"the first unified
benchmark for eliciting and evaluating hallucinations in interactive LLM-agent
scenarios."* Taxonomy: agentic hallucinations are actions unfaithful to *"(i) task
instructions, (ii) execution history, or (iii) environment observations."* The paper's
canonical example is an agent that **hallucinates a successful navigation step despite
observing that it failed, then builds on the false success** — which is our bug almost
exactly. No aggregate rate in the abstract; the taxonomy is the citable contribution.
<https://arxiv.org/abs/2507.21017>

### ⚠️ More reasoning causes MORE tool hallucination

`[RESEARCH]` *The Reasoning Trap* (arXiv 2510.22977, Oct 2025, rev. Apr 2026),
introducing SimpleToolHalluBench: *"progressively enhancing reasoning through RL
increases tool hallucination proportionally with task performance gains… training on
non-tool tasks (e.g., mathematics) still amplifies subsequent tool hallucination…
appearing when reasoning is instilled via supervised fine-tuning **and when it is
merely elicited at inference by switching from direct answers to step-by-step
thinking**." And on fixes: *"revealing a fundamental reliability-capability trade-off:
reducing hallucination consistently degrades utility."*
<https://arxiv.org/abs/2510.22977>

`[RESEARCH]` Corroborating from the instruction side: MathIF (arXiv 2505.14810, May
2025) finds *"a consistent tension between scaling up reasoning capacity and maintaining
controllability, as models that reason more effectively often struggle to comply with
user directives,"* and that models *"tuned on distilled long chains-of-thought or
trained with reasoning-oriented reinforcement learning often degrade in instruction
adherence."*
<https://arxiv.org/abs/2505.14810>

> ⚠️ **CONFLICT with our mini doc §C4 / action #7**, which recommends A/B-ing
> `reasoning.effort: "medium"` to fix multi-step tool selection. These two papers say
> raising reasoning effort should be expected to **increase** fabrication and
> **decrease** instruction adherence — precisely our failure modes 2 and 3 — while
> improving the multi-step capability of failure mode 1. `[INFERENCE]` The experiment is
> still worth running (it is one config field), but it must be measured against **all
> three** failure modes, not just tool selection. Do not ship a reasoning-effort bump on
> the strength of a chaining improvement alone.

### The instruction-hierarchy fact that matters most

`[OFFICIAL]` OpenAI's Model Spec (2026-08-18) places tool output at the bottom of the
authority ladder — Root → System → Developer → User → Guideline, then ***"No Authority:
assistant and tool messages; quoted/untrusted text and multimodal data in other
messages."***
<https://model-spec.openai.com/2026-08-18.html>

> `[INFERENCE]` This cuts both ways. Tool results are **evidence, not instructions** — a
> tool must not be able to issue commands. But it also means a `next_step` field
> smuggled into a tool return (a fallback our mini doc §B3 suggests) is officially
> *no-authority* text and should be expected to underperform a real instruction or a
> forced `tool_choice`. **Return facts the model must speak about, not orders it should
> obey.**

`[OFFICIAL]` The honesty rules the model is trained against: *"Do not lie"* — *"The
assistant should not assert things it does not know"* — plus *"Express uncertainty"* and
avoidance of *"deception by action or omission."* Under "Control and communicate side
effects" (Root), the spec mandates documenting executed actions for legibility.

> **Unverified, dropped:** search summaries claimed the 2026 spec makes honesty
> explicitly outrank confidentiality. I fetched the spec and could not confirm any such
> section.

### Techniques, ranked by strength of evidence

1. `[RESEARCH]` **Force a machine-checkable decision field instead of free text.** The
   strongest number available: agents execute their *stated* decisions almost perfectly
   — **0.7% inconsistency (Claude Haiku 4.5), 1.4% (DeepSeek-Reasoner)** — but
   **22–26%** when the conclusion must be extracted from free text rather than a
   machine-checkable field. *Doing What They Say, Not What They Reason* (arXiv
   2606.00476, May 2026). *Single-author preprint, one task family — but the effect size
   is large and the mechanism is plausible.*
   <https://arxiv.org/abs/2606.00476>
2. `[RESEARCH]` **Instruct mechanical rule application.** Same paper: an explicit "apply
   the rule mechanically" instruction cut rule misapplication from **13.9% to 6.8%**.
   Notably, when Haiku misapplied a rule it erred conservatively **99.5%** of the time.
3. `[OFFICIAL]` **Semantic returns instead of opaque identifiers** — the one quantified
   vendor intervention: resolving UUIDs to meaningful names *"significantly improves
   Claude's precision in retrieval tasks by reducing hallucinations."*
4. `[OFFICIAL]` **Structured returns with explicit named fields** — MCP `outputSchema` +
   `structuredContent`. Give the fact a field name; do not make the model extract it
   from prose. (Same mechanism as #1, from the protocol side.)
5. `[OFFICIAL]` **Verbatim-speak flags in a tool-result envelope** — OpenAI Realtime's
   `{response_text, require_repeat_verbatim: true}` pattern with the matching render
   rule in both the Tools section and the tool definition. Documented in our mini doc
   §C3.
6. `[OFFICIAL]` **Investigate-before-asserting.** Anthropic ships a sample prompt worth
   adapting from files to robot actions: *"Never speculate about code you have not
   opened… Never make any claims about code before investigating unless you are certain
   of the correct answer."*
7. `[OFFICIAL]` **Suppress process narration explicitly.** GPT-5.1 guide: *"Do not
   include process/tooling narration… If checks succeed silently, don't mention them."*
   Narration never produced cannot be false.
   <https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-1_prompting_guide>
8. `[OFFICIAL]` **Only claim completion after success** (Realtime guide): *"Only say an
   action completed after the tool call succeeds."*
9. `[PRACTITIONER, thin]` **Trace verification / "tool receipt" checking** — post-hoc
   verification that every claimed action appears in the tool-call log. Surfaced in
   secondary write-ups and unverified 2026 preprint titles. Real direction, not a
   citable recipe.

> `[INFERENCE]` **The structural fix outranks all nine.** If the action and the
> observation happen in the *same* tool call returning `{"direction_moved": "right"}`,
> there is no window in which the model can invent a movement. That is the composite-tool
> recommendation from our mini doc (§B2), and Q4's evidence independently supports it:
> grounding is best achieved by making the ground truth a **required field of the
> return**, not by adding a rule.

### One more finding that names our exact operating condition

`[RESEARCH]` τ²-bench (arXiv 2506.07982, Sierra/Princeton, Jun 2025) measures agents
that must follow policy *while conversing*: pass^1 falls **74% (retail) → 56% (airline)
→ 34% (telecom)**, and *"shifting from no user operation (No-User) to a collaborative
setup (Default) where the agent must guide the user results in a substantial drop in
pass^1 (**18% drop for gpt-4.1 and 25% drop for o4-mini**)."*
<https://arxiv.org/abs/2506.07982>

> `[INFERENCE]` This is the closest published analogue to our robot: **policy compliance
> collapses precisely when the agent must talk to someone while acting.** It is a strong
> argument that rule-following degradation in our app is a structural property of
> conversational agency, not a defect in our prompt — and therefore that structural
> fixes (composite tools, schemas, narrower surfaces) should be preferred to more rules.

---

## Q5. Behavior at conversation boundaries (greetings, goodbyes, mode switches)

The operator's sharp sub-question: **is a system-initiated "now say goodbye" generation
turn instructing, or hard-coding?** The evidence gives a clear answer.

### The frameworks encode this as an explicit binary

`[PRACTITIONER]` LiveKit Agents is the cleanest articulation, exposing both options as
separate calls:
- *"To have the agent speak a predefined message, use `session.say()`. This triggers the
  configured TTS to synthesize speech and play it back to the user."* … *"For example,
  it might greet the user at the start of a session… For fixed phrases like these, you
  can cache TTS and use pre-synthesized audio to avoid redundant TTS calls and reduce
  latency."*
- *"To make conversations more dynamic, use `session.generate_reply()` to prompt the LLM
  to generate a response."*

**Note the stated rationale for `say()` is latency, not correctness or control.**
<https://docs.livekit.io/agents/build/audio/>
*(Partial-verification flag: the "greet the user at the start of a session" sentence
reproduced verbatim once and was paraphrased on a second fetch — high but not absolute
confidence on exact wording.)*

`[PRACTITIONER]` **But LiveKit's own canonical examples do not use `say()` for
greetings.** The quickstart and every handoff example use an instructed generation turn:
```python
await session.generate_reply(instructions="Greet the user and offer your assistance.")
```
and on entering a new agent at a mode switch:
```python
async def on_enter(self) -> None:
    await self.session.generate_reply(
        instructions="Greet the user warmly and offer your assistance."
    )
```
with mode-switch instructions like *"Introduce yourself as a billing specialist and ask
how you can help with their account."*
<https://docs.livekit.io/agents/start/voice-ai/> ·
<https://docs.livekit.io/agents/logic/agents-handoffs/>

`[PRACTITIONER]` Vapi encodes the choice as a three-value enum:
`'assistant-speaks-first'` (static `firstMessage`), `'assistant-waits-for-user'`, and
`'assistant-speaks-first-with-model-generated-message'` — the last described as *"a
message generated by the model based on the conversation state."*
*(Flag: the reference page exceeded fetch size; enum values corroborated across Vapi's
indexed docs and SDK references, but the stated default `'assistant-speaks-first'`
should be re-confirmed against the live page before relying on it.)*
<https://docs.vapi.ai/api-reference/assistants/create>

`[PRACTITIONER]` **The sharpest production data point in the whole search:** for *silent
handoffs*, Vapi's own guidance flips to model-generated — destination `firstMessage` set
to empty string plus
`firstMessageMode: "assistant-speaks-first-with-model-generated-message"`, transfer
announcements nulled out.
<https://docs.vapi.ai/squads/silent-handoffs>

> **Static text for the cold open; model-generated for the warm handoff.** A cold start
> has no conversational context to respect, so a canned line is harmless. A boundary
> occurring *mid-conversation* does have context, and a canned line there sounds broken.

`[PRACTITIONER]` Retell exposes the same binary — *"Dynamic message: the agent generates
its own opener each call, based on your prompt"* vs *"Custom message: the agent reads a
fixed message you type"* — plus `begin_message_delay_ms`. Note **what is solved in code
there is timing, not wording.**
<https://docs.retellai.com/build/single-multi-prompt/configure-basic-settings>

`[PRACTITIONER]` Pipecat supports both, and a v1.4.0 default change is instructive:
`TTSSpeakFrame`'s `append_to_context` now defaults to `True`, so a hard-coded utterance
**is** added to conversation context after being spoken. A hard-coded greeting the model
didn't know it had said was evidently a real bug class.
<https://docs.pipecat.ai/pipecat/learn/text-to-speech>

### On our own stack

`[OFFICIAL]` OpenAI Realtime supports precisely the instructed-turn pattern: a
`response.create` event accepts an `instructions` field, and *"[t]hese override the
session's base instructions for that specific response only."* There is also an
out-of-band mode (`"conversation": "none"`) for classification-style side calls that
should not pollute history.
<https://developers.openai.com/api/docs/guides/realtime-conversations>
**Honest gap:** that page gives **no** explicit guidance on speaking first at session
start, and none on injecting mid-conversation system messages. The circulating
"greet first" recipe is community consensus, not spec.

`[OFFICIAL]` OpenAI's Realtime prompting guide treats greetings as a *prompt section*
with sample phrasings — and actively fights fixed text: *"Do not repeat the same
sentence twice. Vary your responses so they don't sound robotic,"* and *"Below are
sample examples that you should use for inspiration. DO NOT ALWAYS USE THESE EXAMPLES,
VARY YOUR RESPONSES."* Generally: *"Replace broad guidance like 'be helpful' with clear
trigger, action, and exception rules: when to act, what to do, and when not to do it."*
<https://developers.openai.com/api/docs/guides/realtime-models-prompting>

### So: instructing or hard-coding?

`[INFERENCE]`, but well-supported: **a system-triggered generation turn carrying an
instruction is classified by current practice as instructing, not hard-coding.** The
distinction the field actually draws is *who composes the words*:

| | Who decides *when* | Who composes *the words* | Verdict |
|---|---|---|---|
| `say("你好")` / static `firstMessage` | code | code | hard-coding |
| `generate_reply(instructions="Greet warmly")` | code | model | **instructing** |
| Model emits `end_call` tool | model | model | instructing |
| Prompt section: "when the user leaves, …" | model | model | instructing |

`[PRACTITIONER]` 12-factor's factor 7 supports the third row as the preferred pattern
for boundaries generally: even *contacting a human* should be a tool the model emits
rather than a code branch.

`[RESEARCH]` `[INFERENCE]` There is also an argument *for* the system-triggered turn
from the decay literature: a `response.create` with fresh per-response `instructions`
places the relevant rule at the very end of context, which the positional-failure
result (arXiv 2605.23170) shows is the strongest position available. A boundary
instruction delivered this way should be expected to outperform the same rule sitting in
a system prompt twenty turns back — where SysBench puts compliance near 33.7%.

> **Thin sourcing flag — goodbyes specifically.** Session end is consistently
> *implemented* as an `end_call`-style tool the model invokes, with the orchestration
> layer intercepting and terminating. But **no first-party vendor essay recommends this
> as best practice**; it is the observed default across Vapi/Retell/LiveKit tool
> catalogs. Searches on goodbye/session-end best practice returned mostly low-quality
> SEO content. This area is genuinely under-documented — our choices here should be
> justified by our own transcripts, not by appeal to consensus.

---

## Q6. Instructing small / distilled models

`[OFFICIAL]` Vendors state the trade-off plainly. Anthropic's tool docs: *"Use the
latest Claude Opus model… for complex tools and ambiguous queries; it handles multiple
tools better and seeks clarification when needed. **Use Claude Haiku models for
straightforward tools, but note they may infer missing parameters.**"*
<https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools>

> `[INFERENCE]` "May infer missing parameters" is a precise and under-appreciated
> failure description: the small tier does not *stall* on ambiguity, it **guesses**.
> That is exactly how a confident false narration is produced. The mitigation is to make
> guessing impossible — required enums, strict schemas — not to instruct against it.

`[OFFICIAL]` OpenAI's positioning of `gpt-realtime-2.1-mini` names the same three axes
we are failing on: it trades *"reasoning, tool use, instruction following, and
voice-agent behavior"* against latency and cost (mini doc §D).

### What degrades first — now with evidence

Ordered by strength of the evidence I could verify:

1. `[RESEARCH]` **Negation and multi-constraint tracking.** Negation is *"a dominant
   failure mode, causing accuracy reductions of 23-32% across models"*, with accuracy
   *"dropping below 35% at the highest level"* of constraint difficulty (ACL 2026 SRW).
   This is the cleanest single number on what breaks, and it indicts prohibition-style
   rules directly.
   <https://aclanthology.org/2026.acl-srw.119/>
2. `[RESEARCH]` **Format/serialization robustness, disproportionately at small scale.**
   BFCL v4: `<TOOLCALL>` wrapper tags cost capable models only a *"slight performance
   drop"* but caused *"significant performance drops"* for 8B models and near-zero
   performance for one 70B model. Small tiers have no margin for format friction.
3. `[RESEARCH]` **Long reasoning chains actively hurt small students.** *Small Models
   Struggle to Learn from Strong Reasoners* (arXiv 2502.12143, UW, ACL Findings 2025):
   models ≤3B get **worse** from long chain-of-thought traces produced by strong
   teachers, and do better on *shorter, simpler* chains matched to their capacity.
   Combined with The Reasoning Trap (Q4), this is a real caution against "make it think
   harder" as a mini-tier fix.
   <https://arxiv.org/abs/2502.12143>
4. `[RESEARCH]` **Non-English, per additional turn.** Multi-IF: non-Latin scripts
   (including Chinese) show higher error rates, and every model degrades monotonically
   with each turn. Corroborated by two `[COMMUNITY]` non-English production reports in
   our mini doc §D2.
5. `[RESEARCH]` **Multi-step tool chaining, via compounding error.** Student-centred
   distillation work (arXiv 2509.14257, ICML 2026) identifies *compounding errors* as
   the stated failure mode of naive distillation; a related result (arXiv 2605.07725)
   names it for tool use specifically — *"erroneous tool calls cascade across subsequent
   reasoning steps, progressively amplifying student-teacher divergence."* This is the
   research-tier explanation for the `[COMMUNITY]` chaining failures in our mini doc §B1.

### Mitigations, ranked by evidence

1. `[RESEARCH]` **Fix the schema, not the model.** PA-Tool: **+17% accuracy, −80%
   schema-misalignment errors** from renaming tools to match pretraining conventions,
   with zero training. Highest measured return of any intervention in this doc.
2. `[OFFICIAL]` **Remove the decision.** Composite tools folding a known sequence into
   one call — *"Combine functions that are always called in sequence"* — requires no
   reasoning from the model at all.
3. `[RESEARCH]` **Rewrite tool descriptions** (+60.89% query-level success on
   StableToolBench; −29.23% accuracy degradation as catalogs grow).
4. `[RESEARCH]/[OFFICIAL]` **Shrink the per-turn tool surface** (<20 functions;
   adaptive shortlists 93.1% vs 87.1%). Mini doc §A1.
5. `[RESEARCH]` **Constrain via schema, not prose.** Strict enums and `required`
   parameters convert "may infer missing parameters" from a silent guess into a
   validation error the model can self-correct from — MCP: tool execution errors exist
   *"to enable self-correction."*
6. `[RESEARCH]` **Positive phrasing** (negation costs 23–32%).
7. `[OFFICIAL]` **Simplicity over cleverness in the prompt** — the lean-prompt evidence
   is from frontier models, and `[INFERENCE]` should apply at least as strongly where
   attention budget is scarcer.

> **Honest gap.** There is still no rigorous public study isolating *which
> instruction-following capability degrades first under distillation*, with numbers,
> across tiers of the same family. The ordering above is assembled from several adjacent
> results plus vendor positioning. It is a well-supported working hypothesis, not a
> measured ranking.

---

## What this means for Reachy

Ten recommendations, prioritized. 1–4 are where I would spend the next block of time.

1. **Adopt the escalation ladder as a written house rule in `CLAUDE.md`.** When the
   robot misbehaves: (a) fix the tool — name, schema, description, *return shape*;
   (b) fix what was in context and *where*; (c) only then add code, and only at the
   execution boundary (safety, irreversibility, timing, interruption). This is OpenAI's
   own ladder, on which "add code" never appears. Present the operator's three-clause
   principle as **our** rule — no source states it, so do not cite it as consensus.

2. **Spend effort on schemas and returns, not on rewording the prompt.** This is the
   clearest budget signal in the research: BFCL v4 finds instruction rephrasing produces
   *"no consistent performance trends"*, while renaming tools to match pretraining
   conventions bought **+17% / −80% misalignment errors**, and rewriting descriptions
   bought **+60.89%** query-level success. Sanity-check `move_head` / `camera` /
   `look_around` naming against conventional names, not our internal vocabulary.

3. **Make the ground truth a required field of the tool return.** The composite
   `look_around(direction) → {"direction_moved": ...}` recommendation from the mini doc
   is independently supported by Q4's strongest number: free-text extraction of a
   decision carries **22–26% inconsistency** versus **~1%** for a machine-checkable
   field. Adopt the rule: **if the robot may say it, a tool must have returned it in a
   named field.**

4. **Deliver boundary and mode rules as per-response `instructions`, not as system-prompt
   clauses.** SysBench puts system-prompt compliance at **84.8% at round 1 → 33.7% by
   round 5**; the positional-failure result shows restating the task at end-of-context
   recovers nearly all lost accuracy (within ±4pp). `response.create` with per-response
   `instructions` puts the rule in the strongest position available *and* counts as
   instructing rather than hard-coding. Follow Vapi's implicit rule: static text only for
   the cold open; every mid-conversation boundary is an instructed generation turn.

5. **Rewrite tool errors as advice addressed to the model.** MCP is explicit that tool
   execution errors exist *"to enable self-correction."* `"Head is already at the right
   limit; call camera to see what is there"` teaches a recovery; a traceback teaches
   nothing. Audit every tool's failure path.

6. **Re-scope the `reasoning.effort` experiment (mini doc §C4) before running it.** Two
   independent 2026 results say more reasoning **increases** tool hallucination and
   **decreases** instruction adherence — our failure modes 3 and 2 — while helping
   multi-step selection, our failure mode 1. Measure all three or the result will
   mislead. Related: small models do *worse* with long reasoning chains, so "think
   harder" is not the mini-tier fix it appears to be.

7. **Keep the operator's no-numeric-caps / no-keyword-lists rule; it is now
   well-supported, with one update.** Anthropic names the "laundry list of edge cases"
   as an anti-pattern; GEPA (ICLR 2026 Oral) found optimized *instructions alone* beat
   instruction-plus-demonstrations while being **9.2× shorter**. That strengthens the
   "calibration principles" half of the rule and mildly weakens the "few-shot examples"
   half: prefer a well-stated principle, and add examples only where a principle
   demonstrably failed. Where examples stay, **label them as style examples, not
   conditions** — our mini doc records literal transition-matching on 2.1-mini.

8. **Attach the reason to every negative rule, and prefer positive assertions.**
   Negation costs **23–32%** accuracy across 14 models. Anthropic's TTS example is ours
   verbatim: `NEVER use ellipses` underperforms the same ban with its cause stated.
   Sweep the prompt for bare prohibitions; negatives are fine, *unexplained* negatives
   are not.

9. **Convert "may infer missing parameters" into a validation error.** Strict schemas and
   required enums on every robot-action tool. The small tier's characteristic failure is
   confident guessing; schema is the only mechanism that makes guessing structurally
   impossible, and instructions against it are the weakest layer. Note also that
   *format* rules buried in parameter descriptions are poorly followed even by frontier
   models (IFEval-FC) — use enums, not prose, for shape.

10. **Do not smuggle instructions into tool returns.** The 2026 Model Spec puts tool
    messages at **"No Authority"** — below every human instruction level. A
    `next_step: "call camera now"` field is officially non-authoritative and should be
    expected to underperform a forced `tool_choice` or a real instruction. Return *facts
    to speak about*, not *orders to obey*. (This qualifies mini doc §B3 item 2.)

**Context for expectations, not a recommendation:** τ²-bench shows policy compliance
dropping **18–25 points** simply because the agent must guide a user while acting. Some
of our rule-following gap is a structural property of conversational agency, not a
defect in our prompt. That is an argument for structural fixes over more rules — and for
setting a realistic acceptance bar in PRD §8.

---

## Confidence notes

**Strong (multiple independent sources, or quantified primary results):**
tool schema/name/description quality as the dominant accuracy lever (BFCL v4 + PA-Tool +
Trace-Free+); consolidation over proliferation; error messages written for the model;
the 2026 lean-prompt turn; system-prompt compliance decaying sharply across a
conversation (SysBench, Multi-IF, MultiChallenge, LLMs-Get-Lost all agree); positional
recovery by restating near end-of-context; negation as a dominant failure mode;
structured decision fields over free text; tool messages having no instruction
authority.

**Medium (one good source, or official-but-unquantified):**
that tool *return* design shapes what the model says — the UUID→semantic-name result is
real and quantified, but nobody has run the clean ablation the operator's question
implies; the framework binary at conversation boundaries (well-documented as an API
choice, less well-argued as a recommendation); the GEPA instructions-beat-demos result
(strong venue, but its instructions are machine-optimized against a metric, which we are
not doing).

**Weak / flagged in-line:**
goodbye and session-end best practice (genuinely under-documented in credible writing);
the ranking of what degrades first in small models (assembled from adjacent results, not
measured within one model family); trace-verification / "tool receipt" tooling (real
direction, unverified papers); the faithfulness-gap and IFEval-FC numbers (single-author
preprints, large effect sizes, one task family each); arXiv 2605.24660 tool-count result
(authors/venue unverified).

**Dropped as unverifiable:** the claim that the 2026 Model Spec makes honesty explicitly
outrank confidentiality (search summaries asserted it; the spec fetch did not support
it).

### ⚠️ Do not cite

- **"BFCL: 43% → 2% accuracy when tools go from 4 to 51"** and **"740 tools: 0–20%."**
  Widely circulated in secondary blogs; **no primary source found.** Use the three
  verified tool-count results in Q2 instead.
- **"Re-inject the system prompt every 3–5 turns."** SEO-only sourcing. The *technique*
  is supported (arXiv 2605.23170); the *cadence* is invented.
- Several 2026 single-author preprints surfaced in search but not individually verified:
  IF-RewardBench, AgentHallu, "Operational Hallucination and Safety Drift," "Calibrated
  Enough to Know, Not Calibrated to Act," "Analyzing the Narration Gap in LLM-Solver
  Loops," "Compaction as Epistemic Failure," "Invocation-Level Reliability." Named here
  only so a future reader knows they were seen and not relied upon.

### ⚠️ Conflicts with `research-mini-tool-calling-2026-08.md`

Two, both flagged inline above:
1. **§C1/§A3 capitals-and-redundancy** vs the 2026 lean-prompt / dial-back-emphasis
   guidance from both vendors. Both `[OFFICIAL]`; unresolved; only our transcripts settle
   it.
2. **§C4 action #7 (`reasoning.effort: "medium"`)** vs The Reasoning Trap and MathIF,
   which predict more fabrication and worse instruction adherence. Run the experiment,
   but measure all three failure modes.

---

## Sources

**Official — Anthropic**
- <https://www.anthropic.com/engineering/writing-tools-for-agents>
- <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
- <https://www.anthropic.com/engineering/building-effective-agents>
- <https://www.anthropic.com/engineering/multi-agent-research-system>
- <https://claude.com/blog/best-practices-for-prompt-engineering>
- <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices>
- <https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools>

**Official — OpenAI**
- <https://developers.openai.com/api/docs/guides/prompt-guidance>
- <https://developers.openai.com/api/docs/guides/prompt-engineering>
- <https://developers.openai.com/api/docs/guides/realtime-models-prompting>
- <https://developers.openai.com/api/docs/guides/realtime-conversations>
- <https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-1_prompting_guide>
- <https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf>
- <https://openai.github.io/openai-agents-python/agents/>
- <https://model-spec.openai.com/2026-08-18.html>

**Official — protocol / benchmark maintainers**
- <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- <https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html>

**Practitioner**
- <https://github.com/humanlayer/12-factor-agents>
- <https://www.langchain.com/blog/what-is-an-agent>
- <https://rlancemartin.github.io/2025/07/30/bitter_lesson/>
- <https://cognition.com/blog/dont-build-multi-agents>
- <https://docs.livekit.io/agents/build/audio/>
- <https://docs.livekit.io/agents/start/voice-ai/>
- <https://docs.livekit.io/agents/logic/agents-handoffs/>
- <https://docs.vapi.ai/api-reference/assistants/create>
- <https://docs.vapi.ai/squads/silent-handoffs>
- <https://docs.retellai.com/build/single-multi-prompt/configure-basic-settings>
- <https://docs.pipecat.ai/pipecat/learn/text-to-speech>
- <https://linearb.io/dev-interrupted/podcast/openai-codex-thibault-sottiaux-agentic-autonomy>

**Research — instruction following & decay**
- <https://arxiv.org/abs/2311.07911> — IFEval (Zhou et al., Google, Nov 2023).
- <https://arxiv.org/abs/2507.02833> — IFBench (Pyatkin et al., Ai2/UW, NeurIPS 2025 D&B).
- <https://arxiv.org/abs/2408.10943> — SysBench (Qin et al., PKU/Baichuan, Aug 2024).
- <https://arxiv.org/abs/2501.17399> — MultiChallenge (Scale AI, ACL Findings 2025).
- <https://arxiv.org/abs/2410.15553> — Multi-IF (Meta, Oct 2024).
- <https://arxiv.org/abs/2505.06120> — LLMs Get Lost in Multi-Turn (May 2025).
- <https://arxiv.org/abs/2505.14810> — MathIF, reasoning vs controllability (May 2025).
- <https://arxiv.org/abs/2402.10962> — Li et al., Instruction (In)Stability, COLM 2024.
- <https://arxiv.org/abs/2512.14754> — Dong et al., Reliability in Instruction-Following
  (Dec 2025 / rev. May 2026); up to 61.8% drops from nuanced rewording.
- <https://www.trychroma.com/research/context-rot> — Chroma, Jul 2025.
- <https://arxiv.org/abs/2605.23170> — Positional Failures in Long-Context LLMs (May 2026).
- <https://arxiv.org/abs/2603.05344> — instruction fade-out / system reminders (thin).

**Research — prompt optimization**
- <https://arxiv.org/abs/2507.19457> — GEPA (ICLR 2026 Oral).
- <https://arxiv.org/abs/2406.11695> — MIPRO (EMNLP 2024).

**Research — tool use**
- <https://arxiv.org/abs/2506.07982> — τ²-bench (Sierra/Princeton, Jun 2025).
- <https://arxiv.org/abs/2509.18420> — IFEval-FC (Sep 2025).
- <https://arxiv.org/abs/2510.07248> — PA-Tool / adapt schemas to models (Oct 2025).
- <https://arxiv.org/abs/2602.20426> — Trace-Free+, rewriting tool descriptions (Feb 2026).
- <https://arxiv.org/abs/2508.01780> — LiveMCPBench (Aug 2025).
- <https://arxiv.org/abs/2605.24660> — How Many Tools Should an Agent See? (May 2026).
- <https://arxiv.org/abs/2608.06370> — The Bitter Lesson of Tool Calling (Aug 2026).

**Research — grounding & hallucination**
- <https://arxiv.org/abs/2507.21017> — MIRAGE-Bench (Berkeley, Jul 2025).
- <https://arxiv.org/abs/2510.22977> — The Reasoning Trap / SimpleToolHalluBench (Oct 2025).
- <https://arxiv.org/abs/2606.00476> — Faithfulness gap, structured vs free-text (May 2026).

**Research — small / distilled models**
- <https://aclanthology.org/2026.acl-srw.119/> — Multi-constraint tracking with negation.
- <https://arxiv.org/abs/2502.12143> — Small Models Struggle to Learn from Strong
  Reasoners (ACL Findings 2025).
- <https://arxiv.org/abs/2509.14257> — Student-centred distillation (ICML 2026).
- <https://arxiv.org/abs/2605.07725> — SOD, tool-call error cascade.
</content>
