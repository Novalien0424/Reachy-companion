# Compact tool/prompt text for gpt-realtime-2.1-mini: research notes (2026-10-30)

Scope: new or contradicting evidence vs. docs/research-instructing-*.md, research-mini-tool-calling-2026-08.md,
codex-research-instructing-2026-09.md. Grades: OFFICIAL / PEER-REVIEWED / PREPRINT / PRACTITIONER / MEASURED (ours).
No paid API was called. Token counts: o200k_base (tiktoken) and openai-harmony (o200k_harmony) renders.

## A. How the model actually sees our tools (MEASURED + OFFICIAL; new)

- OFFICIAL, function-calling guide (https://developers.openai.com/api/docs/guides/function-calling):
  "Under the hood, functions are injected into the system message in a syntax the model has been trained on."
- OFFICIAL renderer for OpenAI's open-weight reasoning models (openai-harmony, PyPI `openai-harmony`): tools become a
  TypeScript namespace in the developer message *after* `# Instructions`:
  `// description` / `type look_around = (_: { // param desc \n direction: "left" | ..., question?: string }) => any;`
  Channels are analysis/commentary/final ("Calls to these tools must go to the commentary channel"), which matches the
  2.x realtime commentary-channel behavior. Applying it to gpt-realtime-2.1-mini is INFERENCE, tested below.
- MEASURED calibration against the robot's own usage log (robot journal, fresh session, in=9898, in_cached=0):
  persona 2,623 + hardening 1,813 + mode rules 225 = 4,661 known instruction tokens.
  - 22 tools as JSON dump = 5,660 -> total 10,321 > 9,898 measured: impossible.
  - 22 tools as harmony TS = 4,893 -> total 9,554, leaving 344 for hidden system text + memory + first item. Fits.
  - Conclusion: tools are rendered TS-like; the plan's JSON counts overstate tool cost by ~14% (~770 tokens).
    The instructions (prose) are a larger share of the prefix than the plan's table implies.
  - Caveat: assumes persona_body.txt/hardening.txt are what ran; the in_cached=6848 break after a box opens could
    not be reconciled (instructions are re-sent on every session.update, incl. memory, so the break may sit inside
    the instructions). The input_tokens endpoint (POST /v1/responses/input_tokens) could verify exactly but it is
    an OpenAI call; not used (zero-cost rule) and realtime support is undocumented.
- Construct costs (harmony TS marginal tokens / JSON dump tokens; schema_mechanics.out):
  | construct | TS | JSON |
  |---|---|---|
  | tool, no params (4-word desc) | 11 | 21 |
  | + 1 string param | 18 | 42 |
  | + `required` | +0 | +7 |
  | + 4-word EN param description | +5 | +9 |
  | 5-value enum, bare | 35 | 67 |
  | 5-value enum + bilingual gloss in property description | 59 | 96 |
  | 5-value enum as oneOf/const + description | 51 | 115 — VALUES LOST: renders `any // 左邊` |
  | integer with min/max/default | 24 | 60 — min/max DROPPED, default shown as comment |
  | nested object (2 numbers) | 29 | 74 |
  | empty `enum: []` (home_control today) | 21 | 60 — renders as free `string` |
  | 20-name enum (dance) | 156 | 167 |
  | 42-name enum (play_emotion) | 228 | 217 |
  Rules that follow: `required` is free; an enum value costs ~4-5 tokens; a value gloss ~5 more; never use
  oneOf/const for enum descriptions; min/max/pattern-type constraints are invisible to the model (state in
  description if it matters, validate in code); JSON punctuation is not what we pay for — words are.

## B. Language (MEASURED; lang_tokens.out)

Same content, o200k: EN 104, zh-TW 131, mixed (EN frame + zh anchors) 109 tokens -> zh-TW = 1.26x EN, mixed = 1.05x.
(cl100k zh would have been ~1.8x.) Whole draft persona: zh 580 vs my EN translation 457 (-21%).
Accuracy evidence:
- PEER-REVIEWED MLCL, "Lost in Execution" (ACL 2026, https://aclanthology.org/2026.acl-long.2039.pdf): GPT-5/5-mini/
  5-nano, Qwen3 8B-80B, Llama-3.1 8B/70B, DeepSeek-V3.2; BFCL queries in zh/hi/ig. With Chinese queries, intent and
  tool selection mostly succeed; the dominant failure is "parameter value language mismatch" (Chinese words copied
  into args that must be English) and it is most prevalent for Chinese. Explicit "output values in English"
  prompting reduces it but "compliance is inconsistent"; for GPT-5 mini/nano it is "substantially reduced".
  -> Description language need not be Chinese for selection; argument-language instructions are load-bearing.
- PEER-REVIEWED Tool Preferences (EMNLP 2025, https://arxiv.org/html/2505.18135v2): "multilingual" description edit
  ~1.0x selection for GPT-4.1 and 0.94x for Qwen2.5-7B: language of the description barely moves selection.
- Prior doc 6.2 (cross-lingual steerability) stands: unsettled; no reason to translate the persona for 21%.

## C. Description length and content (sub-q 1-3)

- PEER-REVIEWED/workshop EasyTool (arXiv 2401.06201, ICLR'24 workshop): rewriting raw docs into "description +
  functionality guidelines (with parameter examples)" cut ToolBench tool tokens 2,530->748 (-70.4%), RestBench
  3,881->103 (-97.4%). Vicuna-7B and Mistral-Instruct-7B went from 0.0 to ~70 pass rate (DFSDT), success 0->37/47;
  ChatGPT success 15.0->52.8; GPT-4 46.0->70.0. FuncQA (only names given -> EasyTool ADDS usage scenarios):
  Vicuna-30B one-hop 45.0 (ReAct 4-shot) -> 65.0, tool error 20.3%->10.2%. Lesson: concise = remove noise
  (IDs, URLs, boilerplate), keep purpose + scenario + parameter usage; from a names-only baseline, adding text helps.
- PREPRINT MCP tool descriptions are smelly (arXiv 2602.14878, Feb 2026; GPT-4.1, Qwen3-Coder-480B, GLM-4.5,
  Qwen3-Next-80B-A3B): components Purpose, Guidelines, Limitations, Parameter explanation, Length, Examples.
  Full augmentation: +5.85 pp median success but +67.5% execution steps; regressed in 16.67% of cases.
  Ablation: removing Examples -> no significant change (Cochran Q p>0.20). Purpose+Guidelines beat full in Finance
  (67.5 vs 57.5); Location Navigation needed the full description (31.0 vs 17.8-26.7 for every reduced config).
  "Purpose" is mandatory; Limitations statements that are vague hurt. -> SHORTER CAN HURT in some domains.
- PEER-REVIEWED Tool Preferences (EMNLP 2025, 17 models): identical tools, edited description only.
  assertive cues 7.48x (GPT-4.1), 7.84x (Qwen2.5-7B); usage examples 1.13x / 1.60x; lengthening 1.32x / 1.01x;
  shortening 1.02x / 0.93x; negative cue ("should not be called") 0.03x; combined 12x.
  -> examples and assertive phrasing are *attractors* independent of fit (over-matching risk between siblings);
  length per se doesn't help the 7B model's selection; negative clauses are very strong suppressors.
- PREPRINT Trace-Free+ (arXiv 2602.20426): rewriter ADDS selection scope ("Do not use for ..."), parameter
  constraints, cross-tool dependencies; Qwen3-4B StableToolBench 37.4->40.1, RestBench 54.1->62.1. -> adding the
  right detail helps a 4B model.
- PREPRINT Hsieh et al. (arXiv 2308.00675, Google): docs alone match 12-16-shot demos; docs >> demos at scale;
  demos without docs have high variance.
- OFFICIAL OpenAI function calling: "Include examples and edge cases, especially to rectify any recurring
  failures." / "Adding examples may hurt performance for reasoning models." / "Use the system prompt to describe
  when (and when not) to use each function." Reasoning best practices: "Try zero shot first, then few shot if needed".
- OFFICIAL OpenAI voice prompting (https://developers.openai.com/api/docs/guides/voice-prompting): "The model closely
  follows sample phrases"; "it may overuse them"; "Remove overlapping always, never, only, and must rules unless they
  are truly required"; no length limit stated; tool examples there show four sample phrases (preambles, not triggers).
- OFFICIAL vendor Anthropic advanced tool use (https://www.anthropic.com/engineering/advanced-tool-use): *input*
  (argument) examples "improved accuracy from 72% to 90% on complex parameter handling"; 1-5 per tool, only where
  usage isn't obvious from the schema. Note: argument examples != user-utterance trigger examples.

Synthesis: selection signal = name + first sentence (purpose) + scope/contrast with the confusable sibling.
Examples help arguments (format, language mapping) and fix specific observed confusions; as trigger lists they
attract calls and are officially flagged as possibly harmful on reasoning models. No measured "sweet spot" length
exists; the measured regularity is: remove noise and duplicates, keep purpose + conditions + argument mapping.

## D. Consolidation (sub-q 5)

- PEER-REVIEWED MCP-GRANITE (MASCOTS 2026, arXiv 2609.24161): 9 local models 268M-20.9B, 81 scenarios, 4 levels.
  L4 8-10 primitives TC 0.42 / ArgAcc 0.20; L3 4 task-level tools with action params TC 0.49 / ArgAcc 0.40;
  L2 2 tools TC 0.42 / F1 best 0.71; L1 1 monolithic TC 0.36, zero-tool-call 28.2% (vs 10.2%).
  <8B models: ArgAcc .121 (L4) -> .313 (L3). A 3.2B model at the right granularity beat a 20.9B at the wrong one.
  -> supports the plan's 2-3-action families; argues against further merging (e.g. a single "media" tool).

## E. Persona / system prompt (sub-q 6)

- PREPRINT (NeurIPS 2025 listing) IFScale (arXiv 2507.11538): simultaneous instructions, accuracy at 10/50/100:
  gpt-4.1-nano 94/73/52; gpt-4o-mini 94/66/42; claude-3.5-haiku 98/78/43; llama-4-scout 100/54/27; o3 ~100/100/98.
  Small models decay early ("exponential"), violate later-listed instructions more (last/first-third error ratio
  1.4-2.2 at 50-100), and err by omission. Our counts (rule_count.out, bullets+sentences): persona_body ~87,
  hardening ~57, step2 persona ~23 (+ ~5 conditions per tool description). Caveat: IFScale rules are all active at
  once; ours are mostly conditional, so the transfer is directional.
- PEER-REVIEWED Zheng et al. (EMNLP Findings 2024, arXiv 2311.10054): persona/role lines don't improve factual
  accuracy (162 roles, 4 families). -> Persona text buys voice/style only; one role line suffices.
- Voice/realtime-specific: only OpenAI's voice prompting guide (above); no measured study found.

## F. Results and errors (sub-q 7)

- PREPRINT Fission-GRPO (arXiv 2601.15625): after a tool error "smaller models often fall into repetitive invalid
  re-invocations" (Qwen3-8B). -> an error must name the fix and the next action; a bare status invites a loop.
- Established (prior docs): fewer tokens in function outputs -> better responses; MCP errors-as-advice.

## G. Compression techniques (sub-q 8)

- EasyTool App. D: LLMLingua on tool docs "can not be applied ... it may compress some tokens in parameters and
  functions, which are essential for successful tool execution."
- LLMLingua FAQ (PRACTITIONER/vendor, https://github.com/microsoft/LLMLingua/blob/main/Transparency_FAQ.md): the
  instruction module is highly sensitive to compression.
- Workshop paper, empirical study (arXiv 2505.00019; GPT-3.5, GPT-4o-mini, Claude-3-Haiku): on short contexts quality
  declines as compression rises; GSM8K best compressor 0.297 vs original 0.337; hallucination up for most methods;
  only long-context QA benefits.
- PREPRINT Telegraph English (arXiv 2605.04426): structured symbolic rewriting beats LLMLingua-2 token deletion by
  up to 11 pp on smaller models; no instruction-following data.
- PREPRINT Baxi CDCT (arXiv 2512.17920): constraint compliance has a U-curve; worst at medium (~27-word) prompts.
- PRACTITIONER Mantel "caveman" rewrite of an agent prompt: flat eval quality, little method detail.
- Chinese: no articles to drop; telegraphic Chinese saves little; deleting 不/只/除非 flips rules.
  -> Semantic rewrite (dedupe, drop padding) yes; token deletion / telegraphese for rules no.

## H. Compact variants (compact_variants.json / .out)

| tool | TS now | TS compact | JSON now | JSON compact |
|---|---|---|---|---|
| look_around (EN + 1 zh anchor) | 360 | 172 | 407 | 217 |
| look_around (all zh) | 360 | 185 | 407 | 231 |
| set_conversation_mode | 299 | 146 | 338 | 184 |
| music | 287 | 125 | 336 | 170 |
| dance, names only (plan Step 2) | 621 | 230 | 674 | 261 |

## I. Where the evidence disagrees with our current approach

1. Token accounting by JSON dump (plan table) overstates tools ~14%; use TS render or `in=`.
2. Trigger lists in descriptions (look_around 6 utterances, set_conversation_mode 9, camera 4, move_head 6, music 6)
   contradict the house rule and OpenAI's reasoning-model note; English duplicates of Chinese examples are pure cost.
3. Assertive/capitalised cues (who_is_this "Always use this tool — instead of the camera tool", FIRST, NEVER,
   ALWAYS, RIGHT NOW) are measured 7-8x attractors on a 7B model -> over-triggering of `face identify` is a risk;
   the plan moves this emphasis into `face`; state it as a scope condition instead.
4. Facts duplicated inside one schema (direction gloss in description and in param description).
5. home_control `enum: []` renders as `target: string` -> invites invented device names when none configured.
6. OpenAI says to put when/when-not in the system prompt; the plan puts it in descriptions. Under harmony the
   descriptions sit right after the instructions in the same developer message, so "each fact once" in the
   description is fine — but it is a known contrary official line; record it.
7. Hardening (~57 rule units, 1,813 tokens) is now the larger rule load once the persona is cut to ~580.
