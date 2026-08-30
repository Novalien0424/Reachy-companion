# Human-Like Conversation: Research Summary & Actions (2026-08-31)

Operator ask (2026-08-30): Reachy should (1) barge-in only when addressed —
"like a human, listen for barge-in if 'REACHY' is mentioned"; (2) not rush to
reply "before speaker is silent"; (3) stay talkative in character but stop
"obviously speaking too much". Full detail: research in
`research-realtime-api-2026-08.md`, execution in
`plans/2026-08-30-name-gate-patience-plan.md` (3 Codex review rounds, 26
findings ruled). This file is the compact record of *what we learned* and
*what we changed*.

## Research, compact

| Fact (verified 2026-08-30, current OpenAI docs + installed SDK) | Consequence for us |
|---|---|
| No server-side wake word or "respond only when addressed" primitive exists in the Realtime API. | Name-gating is client-side, on transcript text — it slots into our existing pause-then-decide barge machine. |
| On **WebSocket**, the server never trims an interrupted reply — the client must send `conversation.item.truncate` (WebRTC/SIP get it automatically). Neither we nor upstream Pollen ever sent it. | After every interruption the model believed it said the *whole* reply — then talked as if it had. A real "speaks too much / repeats itself" driver. |
| `truncate` constraints: assistant items only, `content_index: 0`, `audio_end_ms` **inclusive** and a server **error** if it exceeds real duration; success deletes the unheard text from context. | Always round heard-time DOWN (device buffer + resampler priming are estimates). Never truncate on a rollback path — it is irreversible and rollbacks resume the audio. |
| `semantic_vad` `eagerness` sets a **maximum** wait (low=8s, medium=4s, high=2s), not a fixed delay — finished-sounding sentences still turn fast. Not documented Mandarin-tuned. | The better "wait for me to finish" mechanism on paper; kept one env flip away for a live A/B rather than shipped blind. Shipped default: `server_vad` with silence 800→1000 ms (>~1100 ms makes every turn sluggish). |
| `max_output_tokens` (GA name; 1–4096 or `"inf"`; ~20-25 tokens/spoken second) cuts replies **mid-word** with `status: incomplete` when hit. | It is a runaway-monologue safety rail (900 ≈ 40 s), never the brevity mechanism. Brevity is prompt work. |
| Official verbosity guidance: "define what concise means **in context**" — different lengths for direct answers, tool results, troubleshooting. gpt-realtime-2.x also generates **preambles** ("let me think…") by default, and has a new `reasoning.effort` field (recommended `low` for voice agents). | Prompt teaches calibration, not caps (operator direction 2026-08-31: a flat 1–2 sentence rule is "over strict"). Preambles suppressed. `reasoning.effort` pinned to `low` so a server default change cannot add pre-speech latency. |
| Transcription: `gpt-live-transcribe` + new `delay` knob is now the streaming-partials pick; our `gpt-transcribe` yields partials effectively post-commit. GA deltas are **incremental** chunks. | Partial-transcript fast path added (name in a delta commits immediately); found and fixed a latent bug — our accumulator kept only the latest fragment, so a split name (瑞+奇) could never match. `REALTIME_TRANSCRIPTION_DELAY` added for the future A/B. |
| With `interrupt_response=false` + `create_response=true`, the auto-response of a turn committed mid-reply "may fail to create" (official schema text). | Confirms our barge watchdog design; the new late-interrupt path arms it too, so an addressed turn can never end in silence. |

## Actions: what makes it more human

**Listening while talking (like a person telling a story):**
- Someone speaks while Reachy talks → the reply *pauses* (instantly silent),
  and Reachy decides from the words: its **name** (瑞奇/Reachy/…) or a **stop
  phrase** (停/閉嘴/stop — these always win, gate or no gate) → yields the
  floor; anything else → resumes its sentence as if nothing happened.
- Unaddressed chatter that drags on doesn't hold the reply hostage: after
  `REALTIME_BARGE_MAX_PAUSE_MS` (4 s) Reachy resumes talking *through* the
  side conversation — and still yields late if the name turns out to have
  been said (late-interrupt path).
- `REALTIME_SOLO_NAME_GATE=0` restores interrupt-on-any-speech.

**Not jumping in (turn patience):**
- End-of-turn silence window 800→1000 ms; `semantic_vad eagerness=low`
  ("wait up to 8 s if the speaker sounds unfinished") staged for on-device
  A/B (`VOICE-SEMANTIC-VAD-AB`).
- `reasoning.effort=low` pinned — fast to start speaking, never slower later.

**Honest memory of the conversation (no phantom monologues):**
- On every *committed* interruption we now send `conversation.item.truncate`
  with the milliseconds actually heard (enqueued − outstanding − device
  buffer − slack, floored) — the model's context matches the listener's ears,
  so it stops referring back to things it never finished saying.

**Talk like a friend, sized to the moment (no caps):**
- Prompt teaches 長度跟著內容走: one-line answers when one line suffices;
  real explanations/stories when the topic deserves them; what gets cut is
  the filler — repeating the speaker, restating itself, unasked background,
  preambles, reading tool data verbatim. One clarifying question at a time.
- Persona keeps its own calibration lines untouched; adds only "open with
  the point, no 「讓我想想」 preamble".
- `max_output_tokens=900` as a loud-logged safety rail only.

## Verification

Suite-level: full pytest (baseline 1468/30) + new gate/truncate/patience
tests, ruff, mypy --strict. Human-level rows (`feature_list.json`):
`VOICE-NAME-GATE`, `VOICE-LATE-INTERRUPT`, `VOICE-TRUNCATE`,
`VOICE-PATIENCE`, `VOICE-BREVITY` — all ride the **seventeenth install**;
persona additions need the operator scp+sha re-sync.
