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
