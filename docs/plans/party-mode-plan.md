# Plan: multi-person hardening (T1 noise/VAD config, T2 debounced barge-in, T3 party gate)

Evidence and tiers: docs/multi-person-investigation.md. Model: gpt-realtime-2.1,
openai-python SDK with NoiseReduction (near_field/far_field) on audio.input.

## T1 — config (default-on, all modes)
- `openai_realtime._get_session_config`: set `cfg["audio"]["input"]["noise_reduction"]`
  from env `REALTIME_NOISE_REDUCTION` = far_field (default) | near_field | off.
- Robot .env: `REALTIME_VAD_THRESHOLD=0.7` (code already reads it; default 0.5 unchanged).
- No semantic_vad default change (env A/B already possible).

## T2+T3 — "party mode", a runtime mode confined to multi-person situations
Default (solo) mode keeps today's exact behavior: server_vad,
interrupt_response=true, create_response=true, instant client queue-flush on
speech_started. Party mode changes turn_detection via mid-session
`session.update` to interrupt_response=false + create_response=false and
switches the client policy:

- **Debounced barge-in (T2)**: on speech_started while a response is active,
  do NOT flush; start a confirm timer (`REALTIME_PARTY_BARGE_CONFIRM_MS`,
  default 400). If speech_stopped arrives first -> ignore (blip; robot keeps
  talking). If the timer fires with speech still open -> real interruption:
  `connection.response.cancel()` (tracked response id from response.created),
  flush the local queue (existing clear path, which already feeds
  audio_drain.note_cleared), resume listening pose.
- **Addressed gate (T3)**: with create_response=false the server commits turns
  and transcribes them but never auto-answers. On
  `conversation.item.input_audio_transcription.completed`, decide:
  respond iff (a) transcript contains an address name
  (`REALTIME_PARTY_ADDRESS_NAMES`, default "reachy,richie,ritchie,瑞奇,里奇,小瑞"),
  or (b) within the follow-up window (`REALTIME_PARTY_FOLLOWUP_S`, default 20)
  of the last response this client created, or (c) contains a control phrase
  (停/閉嘴/安靜/睡覺 list) so stop commands always work. On pass ->
  `connection.response.create()`. On fail -> nothing; the turn stays in
  context so the model has ambient awareness when next addressed.
- **Mode switching**: new tool `party_mode {enabled: bool}` (38th tool; added
  to the locked profile default_tools). The tool calls a
  `set_party_mode` callable injected via ToolDependencies (same pattern as
  go_to_sleep); the handler flips its flag and issues session.update with the
  new turn_detection. `REALTIME_PARTY_DEFAULT` (off) sets the session-start
  mode. Persona: tool routing + party behavior paragraph
  (被叫名字/直接被問才回答; 別人聊天時安靜聽).

## Files
- openai_realtime.py: noise reduction; turn_detection(party) variants;
  response-id tracking; party barge timer + gate + session.update; state.
- huggingface_realtime.py: policy seams at speech_started/stopped and
  transcription.completed (call overridable handler hooks, default = legacy).
- tools/party_mode.py + core_tools deps field + main.py injection.
- profiles/_reachy_companion_locked_profile/profile.md: default_tools += party_mode.
- persona.md: tool guidance + conventions.
- .env.example: five new keys documented.
- Tests: config emission per mode; debounce (blip vs sustained); gate
  (name/follow-up/control/deny); tool + injection; session.update payload.

## Risks / notes
- Transcription latency (~0.5-1 s) delays party-mode replies; accepted, solo
  mode unaffected.
- response.cancel with no active response is a server error -> guard by
  tracked id and swallow the "no active response" error class.
- A committed-but-untranscribed turn (transcription failure event) in party
  mode would never trigger a gate decision: also handle
  `input_audio_transcription.failed` as gate-deny (log only).
- Wake greeting uses response.create already (synthetic turn) — unaffected.

## Review log — Codex round 1 (2026-08-24): 8 findings, all ACCEPTED

1. Gate `response.create` goes through `_safe_response_create()` / the
   response-sender queue, never the raw connection (races with greeting/tool
   follow-ups, `conversation_already_has_active_response`).
2. Mid-session mode switch sends a NARROW `session.update` containing only
   `audio.input.turn_detection` (full VAD fields preserved from
   `_turn_detection(party)`); never `model` or `voice`.
3. The follow-up window is refreshed ONLY by gate-accepted user turns —
   responses created for the wake greeting or tool follow-ups do not open it
   (track response origin on the sender queue entries).
4. A confirmed barge whose turn is then gate-denied (or whose transcription
   fails) ends with no next response: that path must explicitly close the
   turn for the music hooks (schedule the drain-then-resume) so a ducked
   track is never stranded.
5. Split the speech hooks: `speech_started` in party mode ducks music only
   (candidate); `audio_drain.note_cleared()` + queue flush move to the
   CONFIRMED-interruption path. Solo mode keeps the existing single hook.
6. Debounce triggers on "robot audible" = audio_drain not drained OR response
   in flight — not on response lifecycle alone (queued PCM outlives
   response.done).
7. `transcription.completed`'s reset of `_in_flight_tool_calls` /
   `_tool_batch_needs_response` becomes gate-aware: denied ambient turns do
   not touch tool-batch state (solo behavior unchanged).
8. Timer callbacks re-verify (party still on, same utterance generation,
   still audible) before acting; maintain a small set of cancelled response
   ids and drop their late audio deltas at the enqueue site.

Music-duck note (design decision): party mode keeps duck-on-speech for ASR
quality; the resume path (fixed earlier today) plus finding-4's no-answer
turn close makes the track come back between utterances.

## Review log — round 2 (2026-08-24): DID NOT COMPLETE

Codex round 2 produced no output in 70+ minutes and was killed. Judgment
(contract: Codex advises, Claude decides): round 1's 8 findings were all
accepted and are implemented with unit tests (tests/test_party_mode.py);
review stops here. Residual risk: the live-session semantics of the narrow
session.update and of response.cancel under real cross-talk have unit
coverage and an on-robot smoke test, but their first genuine exercise is the
next real multi-person session.
