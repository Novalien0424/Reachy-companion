# Research note — is 700 ms the right answer hold-off? (2026-09-02)

**Question (operator):** does the `REALTIME_COMMIT_HOLDOFF_MS` default of 700 ms
have a research basis?

**Short answer:** the number itself does not. It is the midpoint of the
"~600–900 ms" range in `docs/codex-research-turn-detection-2026-09.md`
(Ranked interventions, item 1), which that document does not source; the same
document marks the vendor defaults it wanted to cite as *UNVERIFIED (run died
before confirming)*. What follows is the external evidence gathered afterwards,
and what it implies. The shipped answer is: keep 700 as a defensible first
value, **measure the operator's real continuation gaps** (calibration telemetry
added in v1.21.1), then set the knob from data.

## What the repo's own research says (audit, 2026-09-02)

An Opus audit of all seven research docs found no numeric basis: "700"
appears in none of them; the only window figure is
`docs/codex-research-turn-detection-2026-09.md:79-80` ("~600–900ms", inside a
section headed INFERRED), and `:62-63` marks the vendor defaults as
"UNVERIFIED (run died before confirming)". Two in-repo numbers cut the other
way and bound the window from above: conversational voice "benefit[s] from
latency lower than 500 milliseconds"
(`docs/research-instructing-realtime-voice-2026-09.md:855-856`), and the
operator's own feel threshold — "roughly 1100 ms is where patience turns into
lag" (`feature_list.json`, VOICE-PATIENCE). The only other "700" in the repo
(`docs/research-realtime-voice-best-practices.md:30`, a 700–900 ms
`silence_duration_ms` band) is a `server_vad` knob, inapplicable under the
shipped `semantic_vad`.

## External evidence

- **Production voice stacks use a comparable client-side delay.** LiveKit Agents'
  endpointing waits after detected silence before closing the turn:
  `min_delay` **0.5 s** and `max_delay` **3.0 s** by default (0.3 / 2.5 s when
  their audio turn-detector model is on), with a `dynamic` mode that adapts
  inside that range to the speaker's own pause pattern.
  [Turn handling options](https://docs.livekit.io/reference/agents/turn-handling-options/),
  [Turn-taking tuning](https://docs.livekit.io/agents/logic/turns/tuning/). Our
  window sits one seam later (after the server's own semantic VAD has already
  committed and the transcript has arrived), so it is additive to the server's
  wait, not a replacement — the closest analogue is their 0.5 s minimum.
- **Within-speaker pauses are multimodal.** Heldner & Edlund (2010, *Journal of
  Phonetics* 38) — the standard corpus study — report inter-speaker *gaps* with
  medians of 110–130 ms and modes near 200 ms, while within-speaker *pauses*
  cluster around **~150 ms, ~500 ms and ~1500 ms**.
  [Paper (PDF)](https://staff.fnwi.uva.nl/r.fernandezrovira/teaching/cosp/cosp2016/docs/HeldnerEdlund2010.pdf),
  [summary](https://journalofcognition.org/articles/10.5334/joc.268).
- **Mandarin pause lengths by boundary type.** Yang (Interspeech 2005) on
  Mandarin discourse: average pause at major boundaries **0.49 s**, minor
  boundaries **0.33 s**, non-boundary (hesitation-type) pauses **0.25 s**, with a
  skewed, near-lognormal distribution; Mandarin conversational work classes
  pauses as brief (<200 ms), medium (200–1000 ms) and long (>1000 ms).
  [Yang 2005](https://www.isca-archive.org/interspeech_2005/yang05_interspeech.pdf),
  [Wang 2008](https://www.isca-archive.org/speechprosody_2008/wang08b_speechprosody.pdf).

## What that implies for the knob

- A 700 ms window covers the ~500 ms pause cluster and the average Mandarin
  major-boundary pause (0.49 s) with margin. That is the "I paused to breathe
  or at a comma" case, and it is the majority of within-sentence pauses.
- It does **not** cover the ~1500 ms cluster — thinking pauses such as 「就是…」.
  Covering those would need ≥1.5 s on every turn, which is where LiveKit's
  `max_delay` lives and which the operator has already judged sluggish
  (research doc §1: the ~1100 ms VAD-silence knee). The rung-2 rule (ask again
  on a fragment, now the last system-layer text) is the designed backstop for
  that cluster.
- Every turn pays the window, on top of semantic VAD's own detection time.
  Human gaps are ~100–200 ms; anything we add makes the robot "polite", not
  "instant". That is an accepted trade (the operator chose patience twice).
- Therefore: the right default is the *operator's* pause distribution, not a
  literature average. v1.21.1 logs, at INFO, the measured gap whenever the
  window merges a continuation, and a `late continuation` line whenever speech
  resumes within 2 s after a window expired (the "too short" signal). A dozen
  real turns will show whether 700 should move toward 500 or toward 1000.

## Rejected for now

- LiveKit-style *dynamic* adaptation of the window to the speaker: worth it only
  if the measured distribution turns out bimodal for the operator; adds state
  and a new failure mode (a window that drifts long after a thoughtful turn).
- A minimum-duration gate on fragments: needs committed-audio duration the
  handler does not track (plan rev 3 A2, dropped).
