# Pause, quiet while a video plays, group barge-in, and cheaper QA — plan (2026-10-31)

Operator asks (2026-10-31):

1. Real pause.
2. While a video plays, answer only when called.
3. A plan for the group-mode gap.
4. Stop stops everything.
5. Classify every gap as a model limit, a prompt issue or a bug, and fix the
   prompt and bug ones.

Also: QA spent too many realtime tokens, so this plan budgets them.

Evidence so far, all gathered without a realtime session unless noted:

- **TV probes** through HA directly (`scratchpad/pause_probe.py`).
- **A read-only code survey** of the handler by an Opus subagent (file:line in
  the review log).
- **One app session**, for the home-video probe only.

## What the TV does (measured 2026-10-31)

| Receiver | pause | resume | stop |
|---|---|---|---|
| Default Media Receiver (NAS clips) | `paused`, position held (33.76 s) | `playing` from the same point | `media_stop` works |
| YouTube app | `paused` | `media_play` → `buffering` (this TV's normal YouTube "playing") | `media_stop` ignored; `turn_off` closes the app |

**Bug found by the probe: Reachy's sleep kills a NAS clip.**

- The clip is streamed by the app's own server (`settings_app` on :7860;
  `media_store.mount_media_routes` → `nas_stream.mount_stream_route`).
- Sleep stops the app. The clip played about 30 s from its buffer, then went
  `buffering`, then `idle`, then `off`, and it could no longer be resumed.
- The staged path (`HANOVA_NAS_STREAM=0`) is served by the same app, so it
  doesn't survive either.

## Classification of every open gap

| # | Gap | Class | Action |
|---|---|---|---|
| 1 | `stop` with music and a TV video both playing stops only the music | bug (vs the operator's rule "stop stops everything") | **fix** W1 |
| 2 | No pause or resume | missing feature | **build** W2 |
| 3 | Reachy answers room/TV speech while a video plays | missing rule (gate, code) | **build** W3 |
| 4 | Group mode: about 1.6 s of any speech (the TV soundtrack too) cancels Reachy's reply | design gap (code) | **fix** W4 |
| 5 | A NAS clip dies about 30 s after Reachy sleeps; a paused clip can't resume after sleep | bug (lifecycle) | **fix** W6 (a): the NAS serves the clip; operator NAS step |
| 6 | Search "NY" no longer matches the label "OpeningNY" (1.35.0 regression, 1 clip) | bug | **fix** W5 |
| 7 | `response_cancel_not_active` reaches the transcript as an `[error]` line | bug (benign race surfaced) | **fix** W5 |
| 8 | The goodbye hit the 6 s drain cap while still speaking | bug (cap below a normal goodbye) | **fix** W5 |
| 9 | Sleep summary timed out twice, so the visit's 上次聊天 was lost | bug (no retry when the network is flaky at sleep) | **fix** W5 |
| 10 | The transcript copy of a tool result shows `周…` escapes | bug (cosmetic; the model gets UTF-8) | **fix** W5 |
| 11 | `camera` on 「我們在哪裡」 during a clip | tool choice by mini (two text revisions didn't move it) | **shape change** W7: `video_on_tv` in the camera and look_around results; mostly moot after W3 |
| 12 | Asked its mode, 「我不確定」 (1 run) | prompt (the mode heading isn't tied to 「聊天模式」) | measure in the batched pass; fix if it fails at least 2/3 |
| 13 | Mini searches in Chinese first (「英國」), one extra call | model limit (it ignores "in English words"), which the tool can absorb | **tool/data fix** W5: Chinese folder names in the operator's `folder_meta.json`, matched by the tool |
| 14 | Simplified characters in some replies | model limit (mini mirrors simplified transcripts) | record; no reliable prompt fix |
| 15 | Daemon wedge on app stop (02:54 today) | upstream daemon bug (app-only rule) | avoid it in QA (W8: no app restarts); restart is authorized |
| 16 | Group gate never tested with real speech; nightly pass and watchdog not yet seen unattended | verification gaps | W8 audio injection; read the 03:30 log and the watchdog state tomorrow |

## Work items (WIP=1, in this order)

### W1 — `stop` stops everything (`tools/stop_music.py`)

- Stop the speaker **and** the TV in the same call. Each half is read back
  (`stop_tv_playback`, D-042), and the result names both: `stopped:
  ["speaker", "tv"]`.
- It stops whatever the TV is playing, including what Reachy didn't start
  (operator: "stop need to stop everything").
- It clears the pause state and the video session (W2, W3).
- Tests: both playing, either one, neither, TV still playing, HA down.

### W2 — pause and resume

**Tool surface.** `music` gets `pause` and `resume`, making `play | pause |
resume | stop`.

- That is four actions, against the skill's 2–3. The exception is recorded
  because mini's first call for 「停止」 and 「暫停」 was `music` 6/6, and the
  four are one verb family.
- The alternative is a 20th tool (`playback`), which needs the operator's OK.
  Not chosen.

**Speaker.** Reuse `PLAYER.pause_for_speech` / `resume_after_speech`, with a
`user_paused` state so a speech-duck never auto-resumes music the person paused.

**TV pause.**

- `media_pause`, then read back `paused`. If it's still in playback, report it.
- On this TV pause works for both receivers, so there is no app-closing
  fallback.

**TV resume.**

- `media_play`, then read back `playing` or `buffering`.
- A NAS clip resumes only while the app that streams it is alive (W6).

**The result** names what paused or resumed, and the title.

**`nas` text.** Drop D-042's "stopped, not paused" note. A stop is still a
stop; a pause is now a pause.

**Tests:** speaker, TV, both, nothing to pause, resume with nothing paused, and
a read-back failure.

### W3 — while a video plays, answer only when called (code at the gate)

**What a "video session" is.**

- Set on the success returns of `play_nas_video`, `nas_skip` and `play_video`.
- Cleared by `stop`, at shutdown, and when HA reads the TV as no longer in
  playback (`idle` / `off`).
- It is **not** cleared by `on_session_started`, so a websocket reconnect
  keeps it.
- Paused counts as active.
- The handler reaches it through `deps` (the `sleep_requested` seam
  precedent), or through `hanova.nas`, which adds no import cycle.

**The gate.** `_answer_gate_accepts` (hrt:1743): while the session is active,
every mode accepts only a turn with an address name or a control phrase.

- The follow-up window is off.
- One-on-one's "substantive" is off.
- Denied turns are logged as `video gate: no answer while a video plays`.

**Staleness.** A clip that ends on its own would otherwise keep Reachy quiet.

- An unnamed turn while the session is older than 20 s triggers one HA read
  (1.5 s timeout) before the deny.
- `idle` or `off` clears the session and the normal rules apply.
- Named turns never pay for the read.

**Barge-in during a video:** covered by W4 (TV audio must not cut Reachy off).

**Tests:** each mode with a session active or inactive; a stale session
cleared by the read-back; a reconnect keeps the session.

### W4 — group barge-in by name, not by persistence

- **Today:** `_party_barge_confirm` (hrt:2107-2129) cancels the reply after
  1.6 s of any open speech. No transcript is consulted.
- **Change:** in GROUP, and in any mode while a video session is active,
  unaddressed speech never cancels the reply. Reachy talks through it, as D-028
  decision 2 asks: "like a person telling a story".
- **What does cancel:**
  - the late-interrupt path at `transcription.completed` (hrt:4502-4519),
    when the transcript holds a name or a control phrase;
  - the partial-commit path (hrt:2687), when a partial carries the name.
- **No pause in GROUP.** The pause-then-decide machine would stutter on a
  continuous TV soundtrack, pausing up to 4 s at each `speech_started` (survey
  §2e).
- **Trade-off, accepted:** a follow-up sentence without the name, spoken over a
  reply, is not answered, because the person was talked through. The fix is
  the name.
- **Code:**
  - `_start_party_barge_timer` arms only when the D-032 knob says room barge
    is persistence-based. It defaults to name-based from now on.
  - New env `REALTIME_PARTY_BARGE=name|persistence`, default `name`, so the old
    behaviour is one variable away.
  - RECORD keeps its own rule (survey §2a).
- **Tests:** `test_party_mode.py:106-124, 275-441` move to the name rule, and
  the persistence branch is kept under the knob.

### W5 — small bugs

1. Tokens split camelCase before lowering (`OpeningNY` → `opening`, `ny`).
2. `response_cancel_not_active` is logged at debug and never put on the
   transcript queue.
3. The sleep drain cap is raised from 6 s to the measured goodbye length plus
   margin (from the journal's farewell durations), still bounded.
4. Sleep summary: on a timeout, write the visit transcript to the instance dir
   and summarize it at the next wake. It stays bounded and is deleted after
   use.
5. The transcript-copy `json.dumps` uses `ensure_ascii=False`.
6. Chinese folder names: an optional `place_zh` in the operator's
   `folder_meta.json`, carried by `index.py` into the index and searched by
   `_words_match`. The data stays in `~/HomeAssistant`, not in this repo.

### W6 — NAS clip after Reachy sleeps: the NAS serves it (operator: option a)

**Options weighed:** (a) the NAS's own web server serves the cast copies;
(b) sleep keeps the app's media server alive, code-heavy; (c) Reachy warns
before sleeping and the clip still dies. **Operator chose (a), 2026-10-31.**

**NAS check (read-only, 2026-10-31):**

- It is a QNAP. Its Web Server (Apache) is already on: HTTP on 80 answers 403
  at the root (no index, no listing), HTTPS on 8081. WebDAV is off. The admin
  UI is on 8080.
- The Web Server serves only its own `Web` share. QNAP documents virtual hosts
  with roots under that share, and its docs don't say whether the picker
  accepts another share's folder.
- The cast-copy folder is in another share on the same volume (identical size
  and free space): 2,760 MP4s, 233 GB.

**Operator step, in order of preference:**

1. **A virtual host pointing at the cast-copy folder** (Control Panel →
   Applications → Web Server → Virtual Host), HTTP on a free port. Nothing
   moves.
2. **If the picker lists only the `Web` share:** move the cast-copy folder into
   it (same volume, so a rename) and point the Mac pipeline's output there.

**App side:** `HANOVA_NAS_HTTP_BASE`. When set, `nas play` casts the NAS URL
after a HEAD probe; a failed probe logs and falls back to the robot stream.

**Verification:**

- The NAS URL answers 200/206 to a range request, and its root answers 403
  (no listing).
- Tier 1: a clip plays, Reachy sleeps, and the clip keeps playing past 60 s.
- Pause, then sleep, then resume.

**Exposure:** the same as today's robot stream, LAN-only and unauthenticated.
The port must not be forwarded by the router or published by myQNAPcloud.

### W7 — prompt and tool text, measured by the replay harness

- **Retracted finding (2026-10-31).** "The robot's persona is two months stale"
  was wrong. Since v1.25.0 the app reads `~/.config/reachy_companion/`, and its
  `persona.md` is the current repo copy (sha `b5dee4e`, synced 2026-10-30). The
  stale copy was the pre-migration leftover in the package directory, which
  nothing reads. `persona_sync.sh` now reads the instance directory.
- **Gap #11 (camera on 「我們在哪裡」).** The cause is not a persona rule.
  - Text revision 1 (1.35.0): the TV contrast in `camera` → 3/4.
  - Revision 2: the trip's `place` in the `nas` results, and the same contrast in
    `look_around` → 1/3.
  - Next, a shape change: while a video plays, the `camera` and `look_around`
    results carry `video_on_tv` (title and place), so the camera path answers
    right too.
- **Pause chosen as stop.**
  - Revision 1, the enum glosses: 4/6 (one-on-one block).
  - Revision 2, the pause/stop contrast: 2/6 (group).
  - Next, a shape change: `stop` keeps a bookmark of a video Reachy started, and
    `resume` re-casts it and seeks, so a misheard pause costs a moment and not
    the place.
- **Mode question.** 3/3 in group: no change needed.

### W8 — QA, by tier, with a realtime budget

**Tier 0 — unit tests.** Every W1–W5 change. Free.

**Tier 1 — device, no model.** A test-only RPC `qa.tool` (`console.py`),
registered only when `/tmp/reachy_qa` exists as the app starts. It is driven by
`rpc.py tool NAME ARGS`.

- It calls a tool by name with arguments, exactly as the model would, and
  returns the result.
- It drives W1/W2/W3 on the real TV and the NAS stream with zero model tokens.

**Tier 2 — audio path, a few tokens. Not built (2026-10-31): W3/W4 with real speech is the operator's check in normal use.** A test-only RPC `qa.inject_audio`
(survey §3).

- WAVs come from macOS `say -v Meijia`: with and without 「瑞奇」, over a
  playing clip, and in GROUP.
- Frames replace mic frames inside `read_microphone`, paced in real time, with
  trailing silence.
- It checks the W3/W4 gate and barge wiring through server VAD and
  transcription. A denied turn costs transcription only.
- About 8 utterances in all.

**Tier 3 — model choice, batched once after W1–W7: a replay harness, not the app.**

- `qa_replay.py` runs on the robot under the apps venv as its own process. The
  API key stays in the instance `.env`. There is no app session, restart or
  greeting, and no wedge risk.
- **Session.** It builds the session exactly as the app does: the installed
  wheel's handler, `_get_session_config`, the instance persona, the 19 tools,
  and the GROUP mode block. It sets `output_modalities: ["text"]` and opens a
  fresh session per run.
- **Context.** Each case seeds its history as items: e.g. a `nas play` call, its
  canned success result, and the spoken reply. Then it sends the line under
  test and records the first tool call and its arguments, plus the text.
- **Cases, up to 3 runs each:**
  - 「暫停」 / 「先暫停一下影片」 → `music pause`;
  - 「繼續播放」 → `music resume`;
  - 「停止播放」 with both playing → one `music stop`;
  - 「瑞奇，我們在哪裡」 during a clip → answered from the title, no `camera`;
  - the mode question;
  - a Chinese trip request → `nas play`;
- **Size.** About 30 short text turns on a shared, cached prefix. No audio is
  produced.

**Budget.**

- Today's QA was about 40 fresh voice sessions, each with a greeting and spoken
  replies.
- This plan is about 15 text turns, plus about 8 transcribed utterances, plus
  0 for the device tier.
- Audio output, the dominant cost, drops to about zero.

**Stop rule:** if a Tier-3 case passes 2/2 and its change wasn't aimed at it,
the third run is skipped.

## Order and gates

```
W5.1 → W1 → W2 → W3 → W4 → W5.2-6 → W7 → (build 1.36.0, deploy once)
→ W8 tier 1 → tier 2 → tier 3
```

- One deploy, then all verification.
- Records: D-044, CHANGELOG, feature_list, progress.
- W6's app side ships in 1.36.0. It takes effect once the operator's NAS step
  is done and `HANOVA_NAS_HTTP_BASE` is set on the robot.

## Review log (self-review against PRD, research, `reference/`)

1. **Reuse-first.**
   - Pause/resume use HA `media_player` services and the existing music player
     duck path; there is no new playback stack.
   - Barge uses the existing late-interrupt and partial-commit paths (D-028);
     there is no new VAD.
   - Audio injection feeds the existing mic path.
   - Result: no recreation of SDK or app audio/motion. Accepted.
2. **Tool count stays 19.** `music` going to 4 actions is a recorded exception
   (mini's first call 6/6). Accepted; the alternative is a 20th tool, which
   needs the operator.
3. **W3 staleness.** Without the HA read-back, a clip ending on its own would
   keep Reachy quiet indefinitely. Added the 20 s / 1.5 s read on unnamed turns.
4. **W4 trade-off.** Unaddressed speech over a reply is talked through and not
   answered later (survey §2c). Accepted as D-028's posture, and stated to the
   operator.
5. **W4, RECORD.** It shares `_party_mode` and keeps its own behaviour (survey
   §2a). Explicit in the code.
6. **QA hooks are test-only.** They are registered only with the flag file in
   `/tmp` (not persistent; the harness creates and removes it), and they are
   not in the model's tool list.
7. **Verification coverage.** Every item has a test tier. W6 is tier 1 after
   the operator's NAS step.
8. **Operator decisions (2026-10-31):** `music` with four actions, W6 option
   (a), and the W4 trade-off, all accepted. The operator asked that the NAS be
   checked; it was, read-only (W6).
9. **Persona drift: retracted.** The check had read the package directory's
   pre-migration copy, not the instance directory (W7).
10. **Tier 3 moved off the app.** The replay harness replaces `qa.reset` and an
    app text mode. It is the same handler code and session builder, but it
    opens its own sessions, so there are no app restarts (the wedge) and no
    greetings.
