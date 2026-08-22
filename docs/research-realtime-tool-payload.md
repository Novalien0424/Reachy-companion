# Research note — tool/prompt payload vs `gpt-realtime-2.1` (D-019)

Date: 2026-08-22. Question under review: the port grew the session tool
surface from 17 to 39 tools — does that hurt the latency-first realtime model,
and should the app load compact tool descriptions up front with full schemas
injected on invocation? Decision recorded as **D-019** in `DECISIONS.md`.
This note keeps the measurements and sources.

## Measured: what this app actually sends

Method: the app's own `core_tools`/`prompts` code imported with a temp
instance path and no network; token counts with `tiktoken` `o200k_base`.

| Payload | bytes (compact UTF-8) | o200k tokens |
|---|---:|---:|
| tools[] — 39 tools, zero config | 18,669 | 4,292 |
| tools[] — 39 tools, fully configured | 18,815 | 4,349 |
| — ported-22 subset | 8,740 | 2,050 |
| — pre-port-17 subset | 9,930–10,076 | 2,244–2,301 |
| instructions (profile body, current) | 4,094 | 1,063 |
| **session-open total (post-port)** | ~22,900 | **~5,400** |
| session-open total (pre-port) | 11,184 | 2,575 |

Facts about the send path (file:line refs are as of merge `5601738`):

- Tools and instructions go out **once per connection** in one
  `session.update` (`huggingface_realtime.py:866-877`). The only other
  `session.update` call sites change voice or instructions, never tools.
  Nothing resends per turn; there is no truncation/trimming machinery.
- All 39 tools ship regardless of configuration (R5 registers unconfigured
  tools so they can answer `unavailable` with the blocking key).
- The largest single tool is pre-port `dance` (2,412 bytes — 13% of the whole
  array); the five smallest are under 230 bytes each.
- The 22 ported tools are more token-compact than the 17 pre-existing ones —
  the R10 ≤120-char description rule did its job.

## Researched: how the Realtime API prices this

- **Model card** (`gpt-realtime-2.1`): 128k context, 32k max output; supports
  `function_calling` and `prompt_caching`; text $4/1M in ($0.40 cached),
  audio $32/1M in ($0.40 cached).
  https://developers.openai.com/api/docs/models/gpt-realtime-2.1
- **The release's only latency claim** — "reduced p95 latency by at least 25%
  across Realtime voice models" — is attributed to **improved caching**.
  https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896 (2026-07-06)
- **The whole conversation is re-sent to the model each response**; caching is
  best-effort prefix matching, and "the best strategy for maximizing cache
  rate is keep a session's history static … changing [instructions/tools]
  mid-session will reduce the cache rate for subsequent turns."
  https://developers.openai.com/api/docs/guides/realtime-costs
- **Tool definitions, ordering and schemas are part of the cached prefix.**
  https://developers.openai.com/api/docs/guides/prompt-caching
- **Reported ceiling:** instructions+tools max ~16,384 tokens on the GA
  realtime model, failing by silent truncation; community reports it
  persisting on gpt-realtime-2 despite the 128k window. Unconfirmed for 2.1.
  https://developers.openai.com/blog/realtime-api ;
  https://community.openai.com/t/realtime-api-instruction-limit-16-384-tokens-is-too-low-for-production-voice-agents-with-tool-calling/1378932 (Apr–Jul 2026)
  → We sit at ~5.4k, about 1/3 of that line. Verify on-device.
- **Accuracy, not latency, is the documented risk at our count:** "Aim for
  fewer than 20 functions available at the start of a turn" (soft), "keep the
  tool surface narrow."
  https://developers.openai.com/api/docs/guides/function-calling ;
  https://developers.openai.com/api/docs/guides/realtime-mcp
  Anthropic pins degradation at 30–50 tools.
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool

## The proposed pattern, and why not here

"Compact description now, schema on invocation" is a real 2025–26 pattern —
OpenAI `tool_search`/`defer_loading` (Responses API, gpt-5.4+; defers primarily
the parameter schema) and Anthropic's Tool Search Tool (measured MCP-eval gains
79.5%→88.1% on Opus 4.5; ~85% tool-token reduction). **Both designs append
discovered schemas at the END of context specifically to protect the cached
prefix, and neither exists on the Realtime endpoint.** Realtime's
`session.tools` lives IN the prefix, so a hand-rolled
`open_toolbox → session.update → retry` loop would (a) bust the prefix cache
for the rest of the session — documented — converting a one-time prefill cost
into a recurring per-turn one at audio-token prices, and (b) add a full extra
inference round trip of audible silence per first family use. OpenAI's own
two-stage realtime pattern (chat-supervisor) openly concedes the symptom:
responses "start with 'Let me think'".
https://github.com/openai/openai-realtime-agents

The realtime-native SOTA for large tool surfaces is **specialist handoffs**:
`session.update` swaps of instructions+tools at intent boundaries, a few per
conversation, amortizing the cache penalty (Agents SDK realtime guide).
https://openai.github.io/openai-agents-python/realtime/guide/
A per-turn `response.tools` scoping experiment is a documented API surface with
undocumented cache behavior — measure before relying on it.

## Follow-ups adopted in D-019

1. During the Task 15 on-robot pass: log `cached_tokens` per turn; verify no
   silent truncation (exercise a tool near the END of the tools array).
2. The 33-row wake test is the tool-selection accuracy benchmark; only real
   misrouting there justifies the handoff split.
3. Free win any time: trim the `dance` schema (~570 tokens).
