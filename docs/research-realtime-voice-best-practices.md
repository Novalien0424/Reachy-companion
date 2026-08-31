# Realtime voice-agent best practices — research synthesis (2026-08-25)

> **2026-08-30: partly superseded.** The §transcription and §truncate guidance
> below is superseded in parts by `research-realtime-api-2026-08.md` —
> `gpt-live-transcribe` (plus the new `delay` knob) is now the streaming-partials
> pick rather than `gpt-transcribe`, and the "verify our
> `conversation.item.truncate` accounting" gap is closed: we had none, and the
> name-gate/patience wave added it (D-028). That doc's §6 lists every correction
> line by line; read it alongside this one.

Three parallel research passes (OpenAI Realtime API specifics; vendor-neutral
turn-taking/barge-in practice; the realtime model landscape), synthesized
against this repo's current design (T1–T3 multi-person hardening, shipped
2026-08-24 — see `docs/multi-person-investigation.md`, `progress.md`).

Evidence tiers used throughout: **[OFFICIAL]** OpenAI docs/SDK schema/cookbook;
**[VENDOR]** other vendors' docs; **[PRACTITIONER]** blogs/community threads;
**[RESEARCH]** papers. Full source URLs at the end of each section.

---

## 1. Verdict on our current design

Our shipped T1–T3 stack is **directionally exactly what the field converged
on** in 2026 — with real gaps.

| Ours (shipped) | Field verdict |
|---|---|
| `far_field` noise reduction on every session | Correct and officially sanctioned: NR runs **pre-VAD** and is framed by OpenAI as false-positive reduction. Caveat: nobody has measured NR stacked downstream of a hardware DSP (our XVF3800) — must be A/B'd on-device (§6). |
| `REALTIME_VAD_THRESHOLD=0.7` | Inside the practitioner band for noisy far-field (0.6–0.75). Consider also `silence_duration_ms` 700–900 (we run 800 — already good). |
| Debounced barge-in, 400 ms sustained-while-audible | Matches/exceeds the standard 200–300 ms minimum-duration guard ([PRACTITIONER] Future AGI: that guard alone cuts false barge-ins 60–80%). Missing: the **rollback** half (§3.1). |
| Party mode: `create_response=false` + client-side address gate | This is the "app owns the turn decision" architecture every serious 2026 stack uses. OpenAI documents the enabling semantics: with both flags false, "the model will never respond automatically but VAD events will still be emitted." |
| Name/control-phrase gate + 20 s follow-up window | A crude-but-real version of the field's strongest published result: interaction-state context (§4.1). |

And the failure we diagnosed is **the industry-measured norm, not a config
mistake**: Sierra/Princeton's τ-Voice benchmark (arXiv:2603.13686, Mar 2026)
scores OpenAI's realtime stack at **6% selectivity** — it responds to ~94% of
backchannels and non-directed speech — with a 14% inappropriate-interrupt
rate. No frontier speech-to-speech model solves this at the model layer. The
universal fix is an external gate cascade, which is what party mode is.

The canonical 2026 pipeline (every serious stack):

```
mic → [hardware AEC/beamform] → [background-VOICE suppression] → [VAD]
    → [semantic/acoustic end-of-turn model] → [addressee / interaction-state gate]
    → [state-dependent interruption policy] → commit turn
```

We have the first, third, and (in party mode only) fifth and sixth boxes.

---

## 2. Highest-leverage gap #1: the official `wait_for_user` pattern

**[OFFICIAL]** OpenAI's realtime prompting guide
(developers.openai.com/api/docs/guides/realtime-models-prompting, applies to
gpt-realtime-2.x) now ships a purpose-built addressee-gating pattern: a no-op
tool the model calls to end a turn **silently**:

```json
{ "name": "wait_for_user",
  "description": "Call this when the latest audio does not need a spoken response, such as silence, background noise, hold music, TV audio, side conversation, or speech not addressed to the assistant. This tool helps end the turn without a spoken reply.",
  "parameters": { "type": "object", "properties": {}, "required": [] } }
```

Paired prompt block (verbatim): *"If the latest audio is silence, background
noise, hold music, TV audio, side conversation, or speech not addressed to
you, call `wait_for_user`. Do not respond conversationally after calling this
tool. Do not say 'I'm here,' 'I didn't catch that' … Resume normal responses
only when the user clearly addresses you or asks for help."*

Why this matters for us specifically:

- It works in **solo mode** — no toggle needed. Our party gate only protects
  us when someone remembers to say 「開派對模式」; `wait_for_user` covers the
  default mode, and OpenAI wrote it for exactly our room ("TV audio, side
  conversation").
- Realtime models are bad at "do nothing"; giving them an affirmative action
  that ends the turn works where prompt-only suppression fails.
- Every call is a countable journal signal — tunable, like our
  "party gate: denied ambient turn" lines.

Companion blocks from the same guide, both directly relevant:

- **"Unclear Audio"**: only respond to clear audio; if unclear, ask a short
  clarifier; *never repeat the same clarifier twice*; *do not guess*; *do not
  reason on unclear audio*; do not call tools on it. (Cookbook finding:
  wording is load-bearing — swapping "inaudible"→"unintelligible" measurably
  improved noisy-input handling.)
- **Language block with the "substantive utterance" rule**: switch language
  only on a complete request/question in the other language, "not just a
  greeting, name, filler word, or borrowed phrase" — never on accents,
  backchannels, or isolated foreign words. This is a stronger form of our
  D-language pinning (persona.md) and hardens against the TV or a guest
  flipping the session out of Taiwan Mandarin.

---

## 3. Highest-leverage gap #2: make misclassification cheap (rollback)

### 3.1 False-interruption rollback

**[SHIPPED — LiveKit]** `false_interruption_timeout` (default 2.0 s) +
`resume_false_interruption: true`: if an "interruption" produces no transcript
within the timeout, declare it false and **resume the sentence from where it
stopped**. This is the step nearly every home-robot stack skips, and it is
the single change that turns backchannel/laughter misclassification from
catastrophic (killed sentence) into cheap (0.5 s hiccup). Field consensus is
blunt: nobody classifies backchannels reliably today (Krisp lists it as
roadmap; LiveKit's "adaptive" mode is cloud-only and opaque; τ-Voice shows
frontier S2S models failing it) — so the honest posture is *make
misclassification cheap*, not *make classification correct*.

Our current party-mode debounce refuses to cancel on short blips (good), but
once we cancel, the sentence is gone. Resume-from-position is the missing
half. Related repair discipline **[PRACTITIONER, Hamming/WorkAdventure]**: on
a real interruption, put in history only the text the user actually *heard*
(truncate at played-sample position, not generated position); we should
verify our `conversation.item.truncate` accounting does this.

### 3.2 Backchannel heuristics worth adding to the gate

- Min-words gate (2–3 words) alongside min-duration — the single most
  deployed mechanism (Vapi `numWords`, Pipecat, LiveKit).
- Mandarin backchannel lexicon for the denial list: 嗯 嗯嗯 對 對對 好 好的 是
  是喔 喔 欸 哦 唔 — build it out from our own journal (we already logged
  「四十」「嗯嗯嗯」「哈哈哈」「欸」「呵」 committing as turns).
- Duration guard: speech &lt;400 ms with no content word ⇒ backchannel.
- Backoff window (~1 s) after honoring an interrupt to stop cancel-restart
  oscillation (Vapi `backoffSeconds`).
- Mandarin caveat **[RESEARCH]**: Mandarin backchannels are monosyllabic,
  low-energy, tonally ambiguous, and F0-based prosody features trained on
  English transfer poorly (F0 is lexical in Mandarin). Every English-trained
  prosody gate must be validated before being trusted.

---

## 4. Multi-person: what the field adds beyond our party mode

### 4.1 Interaction-state context beats the classifier

**[RESEARCH — the strongest single result found]** Attention Labs SAS
(arXiv:2604.08412, Apr 2026 — **benchmarked on Reachy Mini hardware**,
runs on ARM Cortex-A72, &lt;20 MB, no GPU): a 3-stage addressee gate
(beamform → 435K-param prosody classifier → 85K-param causal transformer over
an **8-second rolling interaction-state window**). Ablation: removing the
temporal-context stage costs **−0.38 F1** (0.86 → 0.57 audio-only) — the
rolling conversation state is worth more than the classifier and the
beamformer combined, because "turn that on" / "what did you say" are
acoustically identical across addressees and only history resolves them.
VAD-only addressee routing scores **0.15 F1**.

Implication for us: our 20 s follow-up window is a 1-bit version of this.
A richer hand-written state machine (who spoke last, how long ago, was the
robot addressed recently, is the robot mid-task) over the signals we already
have would capture much of that −0.38. SAS's own stated weakness applies to
us too: no session-boundary reset ⇒ carry-over context can suppress a new
person's first command — a daily event in a family home.

Commercial form: **SAA** (launched 2026-06-24; hosted; LiveKit/Pipecat/
OpenAI-Realtime reference integrations; fails closed). Blocker for us:
**English-primary — cross-lingual recall is an acknowledged open limitation,
Chinese is not claimed.** A research bet, not a drop-in.

### 4.2 The camera is our biggest unused signal

- SAS audio+video fusion: F1 0.86 → **0.95**; video's gain concentrates
  exactly where audio fails (+0.14 F1 at 4 speakers/high noise).
- **[RESEARCH — directly analogous robot]** Ghent/imec multi-party social-
  robot study (Frontiers in Robotics &amp; AI, 2026-04-15, Furhat + ReSpeaker):
  face recognition identified speakers at **80–95%** while voice ID managed
  **18–27%** with 77% of utterances unrecognized; they gave vision priority
  outright, and recommend up-weighting vision during overlapping speech.
- **[SHIPPED precedent]** Alexa's visual device-directedness: 80% reduction
  in false wakes from ambient noise, 42% from the device's own output;
  Alexa+ (2025-26) gates wake-word-free barge-in on *being in frame and
  facing the device*.

We already run YuNet face detection, SFace recognition, and face tracking.
"A face is present and oriented toward Reachy" as an input to the party gate
(or as a solo-mode gate while the TV plays) is the highest-value cheap signal
available — the SDK gives us head-pose-relative face position for free.

### 4.3 Diarization / speaker-lock status

- **OpenAI Realtime has no usable diarization as of Aug 2026.** The schema
  forward-declares it (`gpt-4o-transcribe-diarize`,
  `…transcription.segment.speaker`) but access errors out
  (community #1373561, unresolved). Plan as if absent; watch that event.
- The strongest *shipped* multi-speaker primitive anywhere is **Alibaba
  qwen-audio-3.0-realtime's speaker enhancement**: enroll up to 5 reference
  clips, the session locks onto that voice and "blocks other voices and
  background noise." For a personal robot with a known operator this is
  arguably a better shape than generic addressee detection. Costs: +330 ms
  latency vs OpenAI, China-region API, smart_turn-mode only. Worth a
  benchmark, not a migration.
- Gemini Live's `proactive_audio` (model decides not to answer non-directed
  speech) is the only model-layer solution — but it is stuck on the
  2.5-Flash preview model; **Google did not carry it into Gemini 3.1**
  (listed "Not supported" on the 3.1 model card), it has no independent
  evaluation, and 3.1's conversational-dynamics score (74.3%) and latency
  (2.99 s) are far behind. **Do not switch for it.**

### 4.4 Model choice: stay on gpt-realtime-2.1

Artificial Analysis S2S Index (Jul 2026): Qwen Audio 3.0 Plus 84.1% &gt;
Grok Voice 82.9% &gt; **gpt-realtime-2.1 79.1%** — but 2.1 is the fastest
top-tier (1.21 s vs Qwen 1.54 s), has the most mature tool/MCP story
(our 38 tools depend on it), and 2.1's release notes specifically claim
improved "silence and noise handling, and interruption behavior."
Note the index tests **zero** noise/far-field/multi-speaker conditions;
MUSA (arXiv:2605.17225) shows audio-LLMs collapsing 95.5%→24.2% under
cocktail-party speech — the number that actually predicts our risk.
Watch: ByteDance **Seeduplex** (native full-duplex, Chinese-first, claims
−50% false replies/false barge-ins; China-cloud only for now).

---

## 5. Boot-up mishearing: named failure, concrete fixes

The field names our startup symptom precisely **[PRACTITIONER, voice-ai
primer Jun 2026]**: *"transitions from silence to speech playout are
particularly challenging [for AEC] — voice agents often interrupt themselves
right when starting to talk because echo cancellation allows initial speech
audio to feed back into the microphone."* The AEC adaptive filter (XVF3800
included) needs convergence time at every silence→playout edge — and boot is
the biggest such edge, stacked with servo noise from the wake choreography
and cold pipeline init (3–8 s in comparable stacks).

Recommended boot sequence (assembled from official pieces + field practice —
no single official document covers startup):

1. Open the session with **`turn_detection: null`** — the one configuration
   in which the server *cannot* commit a turn regardless of what the mic
   hears. (Also the community's field-proven workaround for
   `interrupt_response:false` being unreliable — thread #1369161; we are on
   WebSocket so the server can't stop our speaker, but it can still commit
   garbage turns.)
2. Warm up ~0.5–1.5 s past DSP settle (measure ours), then
   **`input_audio_buffer.clear`**.
3. Fire the greeting (`conversation.item.create` + `response.create`) while
   VAD is still off — this also structurally eliminates the known
   greeting-double-fire race with `create_response`.
4. Only after greeting playback completes, `session.update` VAD on with the
   tuned threshold.

Plus two cheap hardware-adjacent mitigations:

- **Ramp TTS output amplitude in over ~50–150 ms** instead of starting at
  full scale — directly targets AEC non-convergence at onset; trivially
  implementable in our VoiceFX chain.
- De-sensitize barge-in for the first ~300 ms of each playout (blanking
  window); we already require 400 ms sustained speech in party mode — extend
  the same idea to onset in solo mode.

Also: transient noises (coughs, clicks, servo whine) get *elevated* speech
confidence specifically at utterance starts, so the first-utterance-after-
boot is structurally the highest-risk moment of the session. A boot grace
window is well-justified engineering, not a hack.

**Do not enable `idle_timeout_ms`** (new 2026 param): it exists to make the
model speak into silence — our bug as a feature.

---

## 6. Mishearing (ASR) improvements — mostly new since July 2026

1. **Our input transcription model is retiring.** We pin
   `model="gpt-4o-transcribe"` (`huggingface_realtime.py:399`). OpenAI's
   deprecation notice (2026-07-20): whisper-1 / gpt-4o-transcribe retire
   through 2026→2027-01-20; **`gpt-transcribe`** is the recommended
   replacement (Common Voice 22-lang WER 19.3% vs whisper-1's 40.4%).
   Migration is required anyway — and unlocks the two items below.
2. **`keywords` biasing (new July 2026)**: `transcription.keywords` — feed
   it the robot's names (Reachy/瑞奇/Richie/里奇/小瑞/瑞曲), room/device
   names, family names. This did not exist when we built the app; it
   directly targets our name-gate's dependence on the name being transcribed
   correctly.
3. **`languages: ["zh","en"]`** multi-hint for Taiwan code-switching (test
   whether `zh-TW` is accepted — docs say regional locale codes are
   supported but don't enumerate). Free-form `prompt` context: OpenAI
   measured semantic accuracy 38.5%→44.6% with context.
4. **Transcription logprobs (confidence)**: session
   `include: ["item.input_audio_transcription.logprobs"]` — the closest
   thing to a mishearing detector the API offers; gate clarification on
   low-margin chunks. Caveat: `gpt-live-transcribe` returns no confidence;
   verify which model actually emits logprobs.
5. **Transcript ≠ ground truth** [OFFICIAL]: the input transcript comes from
   a separate ASR model and "may diverge" from what the realtime model
   heard. Our party-mode address gate keys off this transcript — a Mandarin
   mis-transcription of 「瑞奇」 makes the gate deny an addressed turn. The
   cookbook's escape hatch is **out-of-band transcription** (second
   `response.create` with `conversation: "none"` asking the same model that
   heard the audio to transcribe verbatim) — negligible cost at home-robot
   volume.
6. **A/B `far_field` NR downstream of the XVF3800** — open question no
   source resolves: OpenAI's NR was trained on raw far-field audio; ours
   arrives beamformed/AEC'd/denoised. Measure false-`speech_started` rate
   *and* Mandarin WER for {off, near_field, far_field}; they can move in
   opposite directions.
7. **Mandarin turn-detection reality check**: Smart Turn v3.1's accuracy
   gains were EN/ES only — Chinese still runs on synthetic-TTS training
   data; TEN Turn Detection (Agora) is the one open Chinese-first
   turn-detector; fine-tuning Smart Turn on our own Taiwan Mandarin audio is
   a documented open path. And **never use an LLM to classify turn-ends**:
   JAL-Turn's 12 ms/100M-param specialist beats GPT-5.1 by 7 points at 100×
   the speed.

---

## 7. Evaluation: how to know any of this works

Build the regression set on **Full-Duplex-Bench v1.5**'s four event
categories (ICASSP 2026): (1) real user interruption, (2) backchannel,
(3) user talking to someone else, (4) background speech — recorded in our
actual room, in Mandarin, measuring stop-latency and response-latency, with
false positives and false negatives as separate scenarios and one parameter
changed at a time. Track: false-interruption rate (&lt;2% is the production
bar; &gt;5% "feels broken"), missed-interruption rate, resume-success rate,
repeated-user-speech rate. OpenAI's eval cookbook adds: audio-audit 1–5% of
sessions end-to-end, because transcripts produce both false passes and false
fails.

---

## 8. Ranked recommendations

Priority order by (evidence strength × effort):

1. **`wait_for_user` tool + OpenAI's silence/noise + unclear-audio + language
   prompt blocks** — official, solo-mode coverage, one tool + persona edit.
2. **False-interruption rollback** — resume the cancelled sentence when an
   interruption yields no transcript within ~2 s; verify truncation uses
   played-position. Makes every gate error cheap.
3. **Boot sequence** — `turn_detection:null` → warm-up → buffer clear →
   greeting → VAD on; TTS onset amplitude ramp in VoiceFX.
4. **Transcription migration** — `gpt-4o-transcribe` → `gpt-transcribe` (it
   is retiring regardless) + `keywords` (robot names!) + `languages` +
   logprobs.
5. **Face-orientation as an address-gate input** — reuse the existing face
   pipeline; the single strongest multi-person signal we own and don't use.
6. **Backchannel lexicon + min-words in the gates** (solo debounce too, not
   just party) + ~1 s re-speak backoff after honored interrupts.
7. **Richer interaction-state gate** — extend the 20 s window toward an
   8 s-style rolling state (who/when/addressed-recently), with an explicit
   session-boundary reset.
8. **On-device A/Bs** — far_field-vs-near_field-vs-off downstream of the
   XVF3800; server_vad-vs-semantic_vad(low) on our own Mandarin audio
   (semantic_vad has no threshold knob and reportedly misses 「對」/「好」).
9. **Watchlist, no action**: realtime diarization (`…transcription.segment`
   lighting up), Qwen speaker-lock benchmark, Seeduplex availability outside
   China, SAA Chinese support. Do not switch to Gemini for proactive audio.

Explicit non-recommendations: `idle_timeout_ms`; trusting
`interrupt_response:false` semantics beyond what we verify ourselves;
LLM-based turn classification; model migration.

---

## Sources (primary)

OpenAI: realtime-vad, realtime-conversations, realtime-transcription,
realtime-models-prompting (wait_for_user, unclear-audio, language blocks),
API changelog + deprecations, cookbook (Realtime prompting / out-of-band
transcription / eval guide), openai-python realtime types (authoritative
schema), community threads #1369161 (interrupt_response unreliable),
#1373561 (diarize access), gpt-realtime-2.1 announcement (2026-07-06),
gpt-transcribe announcement (2026-07-29).

Turn-taking/barge-in: LiveKit Turn Detector v1 + TurnHandlingOptions docs +
false_interruption_timeout; Pipecat Smart Turn v3.1 (daily.co blog,
2025-12-03) + interruption strategies (note: local min-words gate is
bypassed by server-VAD realtime models); Vapi voice-pipeline configuration
(start/stopSpeakingPlan, backoffSeconds); Krisp turn-taking + BVC
(3.5× fewer false VAD triggers, pre-VAD placement); Deepgram Flux;
TEN VAD/Turn Detection; Hamming interruption runbook; Future AGI barge-in
guide (2026); voiceaiandvoiceagents.com primer (Jun 2026, AEC onset
transient); Picovoice VAD guide (2026-01).

Research: τ-Voice arXiv:2603.13686 (6% selectivity); SAS arXiv:2604.08412
(Reachy Mini benchmark, 8 s context ablation); Ghent multi-party robot,
Frontiers Robotics &amp; AI 2026-04-15 (face ≫ voice ID); Full-Duplex-Bench
v1.5 arXiv:2507.23159; MUSA arXiv:2605.17225 (cocktail-party collapse);
JAL-Turn arXiv:2603.26515; Easy Turn arXiv:2509.23938 (Mandarin corpus);
FireRedASR2 arXiv:2603.10420; Amazon Science multiparty Alexa (2021).

Landscape: Artificial Analysis speech-to-speech index; Gemini Live
capabilities + gemini-3.1-flash-live-preview model card (proactive_audio
"not supported"); Vertex proactive-audio doc; Alibaba qwen-audio-realtime
user guide (speaker enhancement); Seeduplex (IT之家 2026-04-09, API
2026-06-18); Attention Labs SAA launch (2026-06-24).
