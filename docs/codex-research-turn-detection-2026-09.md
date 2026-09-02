# Turn-detection over-commit — consolidated research (2026-09-01)

**Provenance, honestly stated:** the dedicated Codex web-research run died
twice in the CLI's remote-compact step (404 from
`chatgpt.com/backend-api/codex/responses/compact`) before writing its
report. This note consolidates (a) findings SALVAGED from the second run's
transcript (LiveKit/Pipecat source reading, before death at 360k tokens)
and (b) the repo's own dated research, which turned out to already answer
Q1. Claims are marked DOCUMENTED / INFERRED / SALVAGED.

## Q1 — the turn_detection parameter surface (Sept 2026)

DOCUMENTED (`docs/research-realtime-api-2026-08.md`, verified 2026-08
against openai-python `types/realtime/realtime_audio_input_turn_detection.py`
and https://developers.openai.com/api/docs/guides/realtime-vad):

- Modes are still exactly `server_vad | semantic_vad | null`. No new modes,
  no new knobs since 2025.
- `server_vad`: `threshold` (0.5), `prefix_padding_ms` (300),
  `silence_duration_ms` (server default 500), `create_response`,
  `interrupt_response`, `idle_timeout_ms` (server_vad-only; commits an
  EMPTY item and force-answers — do not enable).
- `semantic_vad`: `eagerness` (`low|medium|high|auto`), `create_response`,
  `interrupt_response`. **Nothing else** — no threshold, no minimum
  speech duration, no silence knob, no commit delay.
- `eagerness` tunes the **maximum** wait (~8s/4s/2s for low/medium/high),
  applied only while the classifier is UNSURE. A turn that *sounds
  finished* commits immediately at any eagerness.

INFERRED (fits the field evidence exactly): 「你。」「就是。」 carry terminal
prosody, so the semantic classifier judges them complete and commits
<400ms — `eagerness=low` cannot help, because low only extends the unsure
case. **There is no server-side knob left to turn.** The remaining levers
are client-side (we own `response.create` since D-029's
`create_response=false`) or a VAD-type revert (rejected: D-028 shipped
server_vad/1000ms first and the operator deliberately moved to
semantic_vad; a flat silence bump taxes every turn and still has no
semantic understanding).

## Q2 — client-side mitigation patterns (SALVAGED from the dead run)

SALVAGED (Codex read `livekit-agents .../voice/agent_activity.py`,
`audio_recognition.py`, the openai realtime plugin, and Pipecat
`base_smart_turn.py` before dying; its recorded conclusion):

> "The LiveKit pattern is not a literal 'wait after OpenAI committed'
> hook. Their robust path is to delay the client-side end-of-turn decision
> before committing to a realtime model, and to cancel that pending EOU
> task when speech starts again, which naturally merges the continuation
> into one local turn. Pipecat's Smart Turn source shows the same broad
> shape: VAD detects silence, then an audio EOT model judges the whole
> current turn buffer."

- LiveKit: a pending end-of-utterance task with a delay; renewed speech
  CANCELS it, so fragment+continuation become one turn. Latency cost = the
  delay, paid on every turn. Failure mode: a genuinely finished turn waits
  the full delay.
- Pipecat Smart Turn: silence-triggered, then an audio end-of-turn model
  judges the whole buffer. Not copyable for us (requires their EOT model);
  architecturally it confirms "judge completeness over the WHOLE turn, not
  the last silence".
- UNVERIFIED (run died before confirming): whether either stack applies a
  min-duration/min-length gate on commits, and their exact default delays.

**Mapping to our stack (INFERRED, ours to test):** we cannot delay the
server's *commit* (no knob, Q1), but we own the *answer*: with
`create_response=false`, holding off our `response.create` after
`input_audio_buffer.committed` and skipping it when
`input_audio_buffer.speech_started` arrives inside the window reproduces
LiveKit's cancel-on-continuation semantics one seam later. The fragment
and its continuation are consecutive committed user items in conversation
history, so the single eventual response reads the whole thought. The
transcript for a committed item may lag the commit
(`conversation.item.input_audio_transcription.completed` is async), so the
gate must key on commit/speech events, not transcripts.

## Ranked interventions for our stack

1. **Commit hold-off window (client, env-tunable, 0=off):** delay our
   `response.create` ~600–900ms after commit; a `speech_started` inside
   the window skips the response so the continuation merges. Expected:
   fragment answers disappear; cost: up to the window per turn; risk:
   sluggish feel → tune down.
2. **Rung-2 backstop:** restate the unclear-audio→ask-again rule at a
   placement that survives long sessions (compliance decays by turn) —
   the RCA-5 phantom refusal violated the shipped rule.
3. **On-robot A/B only if 1 disappoints:** server_vad/1000ms vs
   semantic_vad/low WITH the hold-off (the existing
   `VOICE-SEMANTIC-VAD-AB` row already owns this comparison).
4. **Do NOT:** enable `idle_timeout_ms`; raise eagerness; add
   transcript-based gates (transcripts lag commits).
