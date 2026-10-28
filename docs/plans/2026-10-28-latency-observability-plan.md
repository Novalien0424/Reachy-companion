# First-audio latency growth — observability and bounded-context levers (2026-10-28)

Problem (RCA 2026-09-04 Finding 4): first audio after the user transcript grew
2.3 → 10.6 s over 22 minutes while `response.created` stayed ~990 ms. Survey
(this session): nothing is ever deleted from the conversation; images enter at
full frame / default `high` detail and stay; tool outputs are `json.dumps`
with `ensure_ascii=True` (Chinese → `\uXXXX`); MCP search results carry
`content_blocks` + `text` (+ `structured_content`); rolled-back user audio
stays; truncation is never set; no usage is logged, so reasoning vs prefill is
unknown. Session: `gpt-realtime-2.1-mini`, robot `.env` effort `medium`.

House rule (`reachy-instructing-model`): session-config and context changes
that can alter behavior ship only behind knobs until an on-robot A/B measures
first-audio latency, tool hallucination and instruction adherence together.

## L1 — usage telemetry (ungated, no behavior change)
One INFO line per `response.done` (`huggingface_realtime.py` ~4137): response
id, status, input total / cached / text / audio / image tokens, output total /
text / audio, and any extra usage fields the server sent
(`usage.model_dump()` keys beyond the typed ones, e.g. reasoning). Tag the
existing first-audio latency line (~4508) with the response id and whether
the turn involved a function call.

## L2 — plain-UTF-8 tool outputs (default on)
`json.dumps(..., ensure_ascii=False)` for `function_call_output` (~3711).
Byte-for-byte the same JSON semantics; the model reads 「台北」 instead of
`台北` — fewer tokens, nothing for the model to decode. Verify: tests
pin the output string; on-robot journal shows the same tool behavior (search,
camera, home control) with smaller `text_tokens` on L1 lines.

## L3 — A/B knobs, default off (current behavior unchanged)
- `REALTIME_TRUNCATION_POST_INSTRUCTIONS=<tokens>` (+ optional
  `REALTIME_TRUNCATION_RETENTION_RATIO`, default 0.8): when set, session
  config carries `truncation={"type":"retention_ratio", …,
  "token_limits":{"post_instructions":N}}`; dropped in the session-config
  fallback path like `reasoning` if the server rejects it.
- `REALTIME_IMAGE_DETAIL=low|high|auto`: when set, camera `input_image` items
  carry that `detail`.
Verify: config tests for set/unset/invalid values (invalid → warning +
unset, never a crash), fallback strips truncation.

Not in this wave: deleting old image items (needs item-id tracking), slimming
MCP returns (rung-1 shape change with its own verification), deleting
rolled-back items (紀錄模式 / `summarize_conversation` depend on them), the
reasoning-effort A/B (instance `.env` only, no code).

## Self-review log
1. *L1 changes behavior?* Logging only, INFO, one line per response. No.
2. *L2 is a model-visible change* — accepted by default because it is the same
   JSON value; the skill's verification is the post-deploy journal check and
   L1's token counts. Rejected alternative: gating it, which would leave the
   known token inflation in place for no measured risk.
3. *L3 defaults* — off, so deploying changes nothing until the operator runs
   the A/B with L1 data to choose N.
