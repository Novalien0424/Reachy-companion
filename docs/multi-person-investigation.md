# Multi-person performance investigation (2026-08-24)

## Evidence (persistent journal, session 11:53–12:20)

122 user turns over 3 days; the multi-party session shows:

- **Every room utterance became a turn addressed to the robot.** Committed
  "user" turns include pure backchannel and laughter: 「四十」「嗯嗯嗯」
  「哈哈哈」×2「欸」「呵」「嗯」 — arriving every 1–4 s during group chat.
- **Every utterance killed the in-flight reply.** Truncated assistant turns,
  same window: 「我還是不能噴珍—」「好，我來處理一下這個—」「SELF-DESTRUCT
  SEQUENCE AR—」「現在是「已經武裝但還—」. Mechanism: server
  `interrupt_response=true` cancels the response on `speech_started`, and the
  client flushes the playback queue on the same event.
- **User-perceived stalls of 17–111 s** between a direct question and any
  completed spoken answer (worst: 「Richie你可以播麒麟王的歌吗?」→ 111 s).
- The model itself narrated the failure: 「大家聊得很熱鬧，我就先安靜一下」.

## Current config (as deployed)

`server_vad`, threshold 0.5 (default), prefix 300 ms, silence 800 ms,
`interrupt_response=true`, **no `input_audio_noise_reduction` at all**,
`semantic_vad` reachable via `REALTIME_VAD_TYPE` but unused. Client barge-in
flushes unconditionally on `speech_started`.

## Root causes, ranked

1. **No addressee discrimination** — the API answers every committed turn;
   in a group, most turns are not for the robot.
2. **Hair-trigger barge-in** — any VAD blip cancels the current reply; in
   cross-talk the robot can never finish a sentence.
3. **No noise reduction + default threshold** — laughter, backchannel and
   distant speech all commit as turns. The TV the robot itself casts to is an
   additional always-on noise source in the same room.

## Improvement plan (tiers; see progress.md for status)

- **T1 — config hardening (small, immediate):** `input_audio_noise_reduction:
  far_field` (we are a far-field device; documented to improve VAD accuracy),
  `REALTIME_VAD_THRESHOLD` 0.5→0.7, A/B `semantic_vad` eagerness=low for
  fragment suppression. All env-tunable; one code line for noise reduction.
- **T2 — debounced barge-in:** stop flushing on `speech_started` alone;
  cancel the reply only when the interruption proves real (speech persists
  ~300 ms / transcript non-trivial). Requires `interrupt_response=false` +
  client-side cancel. Trade-off: barge-in latency +~0.5–1 s, robustness way up.
  Industry practice: sensitivity should not be uniform — conservative while
  speaking confirmations, responsive when listening.
- **T3 — party mode (the dramatic multi-person fix):** `create_response:
  false`; the client decides when to answer: addressed-by-name (Reachy/瑞奇/
  Richie…), direct second-person question, or a short continuity window after
  the robot's last exchange. Otherwise it listens silently, like a polite
  human in a group. Optionally a voice-toggled 派對模式 that applies T1+T3
  live via session.update.

Sources: OpenAI realtime guides (noise reduction, semantic VAD, thresholds,
create_response), 2026 barge-in implementation guides (false-barge-in on side
conversations, staged sensitivity, diarization-or-gate recommendation).

## Addendum (2026-08-25): field-practice research and what shipped against it

A follow-up research pass, `docs/research-realtime-voice-best-practices.md`,
compared the T1-T3 stack above against 2026 vendor/OpenAI/research practice.
Verdict: T1-T3 was directionally exactly what the field converged on, with
real gaps — and the failure mode this file diagnosed is the *industry norm*,
not a config mistake: Sierra/Princeton's τ-Voice benchmark scores OpenAI's
realtime stack at 6% selectivity (responds to ~94% of backchannel/non-directed
speech) with no frontier speech-to-speech model solving it at the model
layer. The research doc's §8 ranked recommendations 1-8 map one-to-one onto
the eight tasks of `docs/plans/2026-08-25-voice-robustness-plan.md`
(D-023 in `DECISIONS.md` records the implementation):

| § 8 rank | Recommendation | Shipped as |
|---|---|---|
| 1 | `wait_for_user` tool + silence/unclear-audio/language prompt blocks | Task 3 — `wait_for_user` tool + `prompts._HARDENING_BLOCK` |
| 2 | False-interruption rollback (resume the cancelled sentence, no transcript within ~2s) | Task 8 — solo pause-then-decide barge-in, `REALTIME_BARGE_ROLLBACK_TIMEOUT_S` |
| 3 | Boot sequence: `turn_detection:null` → warm-up → buffer clear → greeting → VAD on; TTS onset ramp | Tasks 5+6 — onset amplitude ramp + boot gate |
| 4 | Transcription migration: `gpt-transcribe` + `keywords` + prompt | Task 4 |
| 5 | Face-orientation as an address-gate input | Task 7 — party gate face signal |
| 6 | Backchannel lexicon + min-words in the gates (solo debounce too) | Task 2 — `audio/backchannel.py`, consumed by Tasks 7 and 8 |
| 7 | Richer interaction-state gate (8s-style rolling state, session-boundary reset) | **Not implemented.** The existing 20s follow-up window (T3) stands unchanged; a richer state machine is future work, not scoped into this round. |
| 8 | On-device A/Bs: noise-reduction off/near/far downstream of the XVF3800; server_vad vs. semantic_vad(low) | **Not implemented as code** — both are already env-switchable (`REALTIME_NOISE_REDUCTION`, `REALTIME_VAD_TYPE`+`REALTIME_VAD_EAGERNESS`); the A/B itself is an on-robot measurement task, tracked as `feature_list.json` rows `VOICE-NOISE-REDUCTION-AB` and `VOICE-SEMANTIC-VAD-AB` |
| 9 | Watchlist only (realtime diarization, Qwen speaker-lock, Seeduplex, SAA Chinese support) | Intentionally unimplemented — no action item, revisit only if the watched signals change |

Model choice was also re-litigated in the same pass (§4.4): gpt-realtime-2.1
stays the base model family — fastest top-tier latency, most mature tool/MCP
story for our 39 tools — but Task 1 switches the *default* to the
**gpt-realtime-2.1-mini** variant on cost grounds ($10/$20 vs $32/$64 per 1M
audio tokens), with `REALTIME_MODEL=gpt-realtime-2.1` as the one-line revert
if mini's tool-selection quality does not hold up on-robot (feature_list row
`VOICE-MINI-MODEL`).

**Honest boundary:** every item in the table above that shipped is verified
only against unit tests and a fake connection — see `progress.md`'s
2026-08-25 section and the `implemented-unverified` rows in
`feature_list.json` (`VOICE-MINI-MODEL` through `VOICE-SEMANTIC-VAD-AB`) for
the exact on-robot checks still owed.
