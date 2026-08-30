# Research: OpenAI Realtime API — turn detection, interruption, verbosity (2026-08-30)

Refresh of `research-realtime-voice-best-practices.md` (2026-08-25), scoped to
the name-gated barge-in / patience / verbosity wave. Sources: current OpenAI
docs at `developers.openai.com` (platform.openai.com URLs now 301 there), the
GA `openai-python` type stubs (main branch + the installed `openai 2.28.0`),
and vendor issue trackers. Every claim below was verified 2026-08-30.

## 1. Turn detection

`session.audio.input.turn_detection` is still exactly `server_vad |
semantic_vad | null` — no new modes
(openai-python `types/realtime/realtime_audio_input_turn_detection.py`).

**`server_vad`**: `threshold` (default 0.5), `prefix_padding_ms` (300),
`silence_duration_ms` (**server default 500**; we ship 800),
`create_response` (true), `interrupt_response` (true), `idle_timeout_ms`
(unset; commits an *empty* audio item and force-answers — still do not enable,
and it is server_vad-only).

**`semantic_vad`**: `eagerness` (`low|medium|high|auto`, auto=medium),
`create_response`, `interrupt_response`. **No** threshold / prefix_padding /
silence_duration. Key nuance: eagerness tunes the **maximum** wait (8 s / 4 s
/ 2 s for low/medium/high), not a fixed delay — a clearly-finished sentence
still turns fast at `low`; the 8 s only applies when the classifier is unsure.
So `eagerness: low` is cheaper than a flat `silence_duration_ms` bump, which
taxes every turn. Not documented as Mandarin-tuned → must be A/B'd on-device
(`VOICE-SEMANTIC-VAD-AB`).
Source: https://developers.openai.com/api/docs/guides/realtime-vad

Docstring (both modes): with `interrupt_response=false` +
`create_response=true`, the auto-response of a turn that commits while a
response is active **"may fail to create a response"** — our barge watchdog's
raison d'être, now confirmed in the official schema text.

**gpt-realtime-2.1** (2026-07-06): improved "silence and noise handling, and
interruption behavior"; ≥25 % p95 latency cut; 128k context / 32k max output.
New session field **`reasoning: {"effort": minimal|low|medium|high|xhigh}`** —
official guidance: "Start with `low` for most production voice agents".
Higher effort = more pre-speech latency + more output tokens. We never set it
today. 2.x also emits **preambles** ("commentary" phase items — "Let me think
about that…") **by default**; a `# Preambles` prompt section suppresses them.
Sources: https://developers.openai.com/api/docs/models/gpt-realtime-2.1 ·
https://developers.openai.com/api/docs/guides/realtime-models-prompting

## 2. Interruption semantics — the WebSocket asymmetry

Official recipe (conversations guide): on `speech_started` → stop local
playback → **send `conversation.item.truncate`**. On WebRTC/SIP the server
truncates unplayed audio automatically; **on WebSocket it never does — even
with `interrupt_response: true`**. The client owns playback, so only the
client knows how much was heard. We (and upstream Pollen) send **no truncate
at all**, so the model's context keeps every word it generated, heard or not.
Source: https://developers.openai.com/api/docs/guides/realtime-conversations

`conversation.item.truncate` schema (GA stub, verbatim constraints):
`item_id` (assistant message items only), `content_index` (**always 0**),
`audio_end_ms` (inclusive; **server errors if it exceeds the item's real
audio duration** — always round DOWN). Success ⇒ `conversation.item.truncated`;
the server **deletes the item's text transcript** so unheard text leaves the
context. `item_id` rides on every `response.output_audio.delta`.

Gotchas: `response.cancel` with nothing active ⇒ benign
`response_cancel_not_active` (we already swallow it);
`conversation_already_has_active_response` when `response.create` races a
cancel (we already retry); community reports truncation still leaves ~5–10 s
of unheard text in some sessions (unresolved as of 2026-03) — truncate is an
improvement, not a guarantee:
https://community.openai.com/t/realtime-api-interruptions-dont-properly-trim-the-transcript/1000703
`output_audio_buffer.clear` is WebRTC/SIP-only — not for our transport.

## 3. Verbosity control

Field is **`max_output_tokens`** on the GA session object (and per
`response.create`) — `max_response_output_tokens` is the dead beta name.
Integer 1–4096 or `"inf"` (default). Counts audio tokens: assistant speech ≈
1 token / 50 ms ⇒ **~20–25 tokens per spoken second** incl. transcript text.
Hitting the cap yields `response.done` with `status:"incomplete"`,
`status_details.reason:"max_output_tokens"` — an abrupt mid-word cut, no
wrap-up (LiveKit hit this: https://github.com/livekit/agents/issues/5808).
⇒ Use it as a **safety rail (~800–1000 ≈ 35–45 s)**, never as the brevity
mechanism, and log `status_details.reason` or the cut is invisible.

Brevity itself is prompt-side. Official `## Verbosity` skeleton: direct
answers "1-2 short sentences"; "Ask one question at a time"; tool results
"Summarize the result first, then give only the next useful action";
troubleshooting one step at a time. Plus: suppress preambles for direct
answers; keep `reasoning.effort` low.

## 4. "Respond only when addressed"

No new API primitive (nothing like Gemini's proactive_audio; no server wake
word). The two sanctioned shapes remain (a) `create_response:false` + client
gate + explicit `response.create` (= our party mode) and (b) the
`wait_for_user` no-op tool (= our solo hardening). Neither stops the server
cancelling an in-flight reply — that lever is `interrupt_response:false` +
client decision, which we already run. **Name-gated barge-in is therefore
necessarily client-side, on transcript text.** True acoustic wake words mean
an on-device spotter (LiveKit shipped `livekit-wakeword`); short names like
"Reachy" degrade spotter accuracy — out of POC scope, transcript gating fits
our architecture.

## 5. Input transcription

`AudioTranscription` now: `model` (adds **`gpt-live-transcribe`**,
`gpt-4o-transcribe-diarize`, `gpt-realtime-whisper`, …), `language`,
`languages` (plural — gpt-live-transcribe uses this), `prompt`, `keywords`,
and **`delay`: `minimal|low|medium|high|xhigh`** — "controls how long the
model waits before emitting transcription text": the direct knob on how early
partial deltas arrive. Current guidance: `gpt-live-transcribe` for streaming
partials; `gpt-transcribe` (what we ship) is positioned for committed-turn
transcription, i.e. its partials effectively arrive post-commit. No published
ms figures — benchmark on-device. `gpt-live-transcribe` returns no
timestamps/confidences (transcription logprobs: treat as unavailable).
Source: https://developers.openai.com/api/docs/guides/realtime-transcription

## 6. Corrections to earlier research docs

- `research-realtime-voice-best-practices.md:261-266` — "`gpt-transcribe` is
  the recommended replacement": superseded; `gpt-live-transcribe` is the
  realtime pick. The doc predates `delay`/`languages` entirely.
- `:112` — "verify our `conversation.item.truncate` accounting": answered —
  we have none; it's a gap, closed by this wave.
- `:197-208` — "stay on gpt-realtime-2.1" vs code default
  `gpt-realtime-2.1-mini` (`openai_realtime.py:54`): doc/code disagree;
  `VOICE-MINI-MODEL` A/B row still owed.
- `research-conversation-app.md:22` — SemanticVad also accepts
  `create_response`/`interrupt_response` (code already relies on this).
- Azure claims 256k context for 2.x; OpenAI's model card says 128k — treat
  OpenAI as authoritative.
