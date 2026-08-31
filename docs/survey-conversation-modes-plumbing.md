# Survey: plumbing a "conversation modes" feature must reuse

Read-only survey, 2026-08-31. Scope:
`reachy_companion/src/reachy_companion/`. Paths below are relative to that
directory unless noted. Target feature: three modes — **one-on-one** (today's
solo), **group** (today's party), **record** (quiet transcription + speak only
when addressed by name to summarize).

Everything mode-related today is **one boolean**, `HuggingFaceRealtimeHandler.
_party_mode` (`huggingface_realtime.py:528`), read at ~20 sites. A mode manager
either replaces that boolean with an enum or wraps it; there is no other mode
concept in the app.

---

## 1. Party mode — full lifecycle

### 1.1 The tool (the voice switch)

- `tools/party_mode.py:21-47` — `PartyMode(Tool)`, `name = "party_mode"`.
- Description verbatim (`tools/party_mode.py:25-29`):
  > "Change how Reachy participates while it STAYS awake: in a group conversation it answers only when addressed by name and otherwise listens quietly. 多人聊天場合開啟；結束時關閉。 Not for ending the interaction or sleeping — that is go_to_sleep."
- Schema: single required boolean `enabled` (`tools/party_mode.py:30-39`).
- Body is a two-liner: refuses when the seam is unwired, else
  `return deps.set_party_mode(enabled)` (`tools/party_mode.py:43-47`).
- The seam: `ToolDependencies.set_party_mode: Callable[[bool], dict] | None`
  (`core_tools.py:56`), assigned per handler build in
  `main.py:269` (`deps.set_party_mode = handler.set_party_mode`) — rewired on
  every handler rebuild, same pattern as `deps.go_to_sleep` (`main.py:358`).
- Counterpart description in `tools/go_to_sleep.py:15-19` explicitly
  cross-references `party_mode` ("not to keep participating in a different
  way"). A third mode needs the same disambiguation text or the model will
  confuse record mode with sleep.

### 1.2 `set_party_mode` — what flips (`huggingface_realtime.py:607-652`)

Synchronous by design (tools run on the handler loop; the session update is
`ensure_future`d, not awaited — `:649-650`).

| line | action |
|---|---|
| `:616-617` | idempotent no-op when unchanged → `{"status": "unchanged"}` |
| `:618` | `self._party_mode = enabled` |
| `:619` | `_party_speech_open = False` |
| `:624` | `_barge_speech_open = False` (solo flag, stale-True hazard) |
| `:631` | `_barge_late_eligible = False` |
| `:632` | `_party_utterance_seq += 1` — invalidates every sleeping barge timer |
| `:633-638` | if a solo pause is open (`_barge_paused or _barge_pending`) → `_resume_playback(rolled_back=True)`; the flip removed every timer that could resolve it |
| `:644` | `_barge_resumed_response_id = None` |
| `:648` | `_party_last_accept_at = monotonic() if enabled else None` — entering party **opens** the follow-up window for whoever toggled it |
| `:649-650` | `asyncio.ensure_future(self._push_turn_detection_update())` |
| `:651-652` | logs `party mode ON/OFF`, returns `{"ok", "status": "party_on"/"party_off", "party_mode"}` |

**Session-boundary reset:** `_party_reset_for_new_session()`
(`huggingface_realtime.py:654-667`) clears `_party_last_accept_at`,
`_party_speech_open`, bumps `_party_utterance_seq`. Called once near the top of
`_run_realtime_session` (`:2304`), for first session and every reconnect. Note
`_party_mode` itself is **not** reset — the mode survives reconnects; only turn
state is cleared. A mode manager should preserve that property.

### 1.3 The server-side flip: turn detection

- Base `_push_turn_detection_update()` is a no-op (`huggingface_realtime.py:669-676`).
- Real implementation `openai_realtime.py:489-516`: sends a **narrow**
  `session.update` of the whole `audio.input` block only (never `model` or
  `voice`). Defers entirely while the boot gate is closed (`:500-508`).
- The config it sends comes from `_turn_detection(party)`
  (`openai_realtime.py:184-227`):
  - `server_interrupts = not party and not _solo_client_barge()`
    (`:203`) → `interrupt_response` on the VAD param.
  - **party only:** `server["create_response"] = False` (`:226`, semantic
    variant `:213`). This is the single switch that makes the server stop
    auto-answering committed turns.
  - Solo keeps `create_response` absent → server auto-answers every commit.
- Config-build site: `openai_realtime.py:416` —
  `cfg["audio"]["input"]["turn_detection"] = _turn_detection(getattr(self, "_party_mode", False))`,
  inside `_get_session_config` (`openai_realtime.py:397-435`), which the boot
  gate overrides with `None` on the first session (`:405-414`).

### 1.4 The address gate — `_party_gate_accepts` (`huggingface_realtime.py:752-775`)

Order is binding and documented as such:

1. `_PARTY_CONTROL_RE.search(text)` → **accept** (`:763-764`). Regex at
   `:95`: `停|閉嘴|闭嘴|安靜|安静|睡覺|睡觉|別唱|别唱|stop|quiet|shut\s*up` (IGNORECASE).
2. `is_backchannel(transcript)` → **deny** (`:765-766`), beats even a live
   follow-up window. (`audio/backchannel.py:54`.)
3. any name in `_party_names()` → accept (`:767-768`).
4. follow-up window: `_party_last_accept_at` within `_party_followup_s()`
   → accept (`:769-771`).
5. face gate: `_party_face_gate_enabled() and self._face_engaged() and
   is_substantive(transcript)` → accept, with an INFO line (`:772-774`).
6. else deny (`:775`).

`_face_engaged()` (`:777-806`) is a **non-blocking cached read** of the daemon's
tracking state — `deps.reachy_mini.get_tracked_face(wait=False)` (`:797`);
requires `detected`, freshness ≤ `_party_face_fresh_s()` (`:802`), and
`abs(face.x) <= _party_face_center()` (`:806`). Any exception → `False`.

`_party_names()` (`huggingface_realtime.py:112-114`): reads
`REALTIME_PARTY_ADDRESS_NAMES`, comma-split, `.strip().casefold()`; default
`_PARTY_NAMES_DEFAULT = "reachy,richie,ritchie,瑞奇,里奇,小瑞,瑞曲"` (`:92`).
It has a **second consumer**: `openai_realtime._transcription()` uses it as the
default `keywords` bias list (`openai_realtime.py:166`) — "a name the robot
listens for is also a name the transcriber is biased toward hearing".

### 1.5 Where the gate runs, and how party creates responses

All inside the `transcription.completed` branch of the event loop
(`huggingface_realtime.py:2563-2658`):

- **Deny path** (`:2597-2607`): logs `party gate: denied ambient turn`, calls
  `on_turn_without_response(self.deps)` (closes the music-duck phase —
  `hanova/music_hooks.py:261`), pushes the transcript to
  `output_queue` as `AdditionalOutputs({"role": "user", ...})`, calls
  `_emit_transcript("user", transcript, True)`, then `continue`. **It does not
  call `record_transcript`** — denied speech never enters the sleep-summary
  buffer (see §4).
- **Accept path** (`:2653-2658`): sets `_party_last_accept_at = monotonic()`
  then `await self._safe_response_create()` — "create_response is off in party
  mode: this turn was addressed to us, so answer it — through the sender queue,
  never the raw connection". `_safe_response_create` (`:1669-1674`) just puts
  kwargs on `_pending_responses`; the serial worker `_response_sender_loop`
  (`:2052-2131`) does the actual `connection.response.create()` with the
  one-active-response protocol.
- `transcription.failed` in party mode → `on_turn_without_response(deps)` only
  (`:2660-2666`).

### 1.6 Party barge-in (debounced, never pauses)

- `speech_started`, party branch (`:2402-2411`): sets `_party_speech_open =
  True`, bumps `_party_utterance_seq`, calls `on_user_speech_candidate(deps)`
  (ducks music but **not** `audio_drain.note_cleared()` —
  `hanova/music_hooks.py:248-258`), and arms `_start_party_barge_timer()` only
  if `_robot_audible()`.
- `speech_stopped` (`:2419-2425`): `_party_speech_open = False`; solo branch
  gated off by `if not self._party_mode`.
- `_start_party_barge_timer` (`:808-814`) → `_party_barge_confirm(seq)`
  (`:816-837`): sleeps `_party_confirm_s()`, re-verifies mode + seq +
  `_party_speech_open` + `_robot_audible()`, then measures
  `(self._audio_item_id, self._heard_audio_ms())` **before** cancelling, does
  `_cancel_active_response()`, `self._clear_queue()`, `_truncate_heard_audio()`.

### 1.7 Party env knobs

| env | reader | default |
|---|---|---|
| `REALTIME_PARTY_DEFAULT` | `_party_default_on()` `:98-99` | off |
| `REALTIME_PARTY_BARGE_CONFIRM_MS` | `_party_confirm_s()` `:102-104` | 400 |
| `REALTIME_PARTY_FOLLOWUP_S` | `_party_followup_s()` `:107-109` | 20 |
| `REALTIME_PARTY_ADDRESS_NAMES` | `_party_names()` `:112-114` | see `:92` |
| `REALTIME_PARTY_FACE_GATE` | `:320-322` | True |
| `REALTIME_PARTY_FACE_FRESH_S` | `:325-327` | 3.0 |
| `REALTIME_PARTY_FACE_CENTER` | `:330-332` | 0.4 |

---

## 2. Solo name gate / barge state machine

### 2.1 The gate predicates

- `_solo_client_barge()` `huggingface_realtime.py:124-126` — env
  `REALTIME_SOLO_CLIENT_BARGE`, default True. Off = pre-Task-8 path (server
  interrupts, flush on first syllable).
- `_solo_name_gate()` `:129-137` — env `REALTIME_SOLO_NAME_GATE`, default True.
  Only meaningful when client barge is on.
- `_gate_text_accepts(text) -> (bool, reason)` `:140-152` — control phrase →
  `(True, "control phrase")`; any `_party_names()` hit → `(True, "name")`;
  else `(False, "unaddressed")`. **Shared by solo and party**; a record mode's
  "call my name to summarize" check should reuse this verbatim.

### 2.2 State fields (all `__init__`, `huggingface_realtime.py:552-600`)

`_barge_paused` `:558`, `_barge_pending` `:559`, `_barge_speech_open` `:560`,
`_barge_confirm_task` / `_barge_rollback_task` / `_barge_watchdog_task`
`:561-563`, `_barge_cooldown_until` `:564`, `_barge_response_seen` `:565`,
`_barge_paused_response_id` `:568`, `_barge_partial_committed_item` `:573`,
`_barge_resumed_response_id` `:579`, `_barge_late_eligible` `:585`,
`_held_audio: deque[QueueItem]` `:587`, plus truncate accounting
`_audio_item_id` / `_audio_item_enqueued_ms` / `_barge_paused_item_id` /
`_barge_paused_heard_ms` `:597-600`.

### 2.3 Flow

1. `speech_started` → `_solo_speech_started()` `:977-1018`. Legacy branch
   (`:988-996`) flushes immediately. Gate branch: `_barge_speech_open = True`,
   `_barge_late_eligible = self._robot_audible()` (`:1005`, decided **at onset
   and nowhere else**), `on_user_speech_candidate(deps)`, cooldown return
   (`:1007-1012`), silence return (`:1013-1014`), then `_pause_playback()` +
   `_barge_pending = True` + seq bump + `_arm_barge_confirm()`.
2. `_pause_playback()` `:840-863` — sets `_barge_paused`, stashes the paused
   response id / item id / heard-ms, `audio_drain.note_paused(True)`.
   Audio diversion happens in `ConversationHandler.emit()`
   (`conversation_handler.py:82-116`): while `_barge_paused`, audio tuples go
   into `_held_audio` (`:111-115`); non-audio outputs still flow.
3. `speech_stopped` → `_solo_speech_stopped()` `:1020-1033`: clears
   `_barge_speech_open`, swaps the confirm timer for the rollback timer.
4. `_confirm_solo_barge(seq)` `:1056-1086` — gate ON sleeps
   `_barge_max_pause_s()` then **rolls back** (`:1077-1084`); gate OFF sleeps
   `_barge_confirm_s()` then commits.
5. `_maybe_commit_on_partial(partial, item_id)` `:1253-1275` — called from the
   transcription-delta branch (`:2553`). Commits early on a name/control in a
   partial; records `_barge_partial_committed_item`.
6. `_resolve_solo_barge(transcript)` `:1277-1324` — the verdict on
   `transcription.completed`, run **before** the empty-transcript `continue`
   (`:2580-2583`). Accepted → `_commit_solo_barge()`; rejected →
   `_resume_playback(rolled_back=True)`, clears resumed-id and eligibility,
   emits the transcript, returns True so the caller `continue`s (and therefore
   **skips `record_transcript`**).
7. `_commit_solo_barge()` `:1135-1205` — cancel, flush via `_clear_queue`,
   `_resume_playback(rolled_back=False)` (drops held audio), cooldown, arm
   watchdog, truncate.
8. `_late_solo_interrupt()` `:1207-1251` — fires from the completed branch
   (`:2618-2628`) when the pause is over but the committed turn addresses the
   robot while audible and `_barge_late_eligible`.
9. `_resume_playback(rolled_back=True)` `:865-909` — re-arms the onset ramp via
   `_notify_response_started()` and calls `on_turn_without_response(deps)`.

### 2.4 Solo env knobs

| env | reader | default |
|---|---|---|
| `REALTIME_SOLO_CLIENT_BARGE` | `:124-126` | 1 |
| `REALTIME_SOLO_NAME_GATE` | `:129-137` | 1 |
| `REALTIME_BARGE_CONFIRM_MS` | `_barge_confirm_s()` `:166-191` | 1600 |
| `REALTIME_BARGE_MAX_PAUSE_MS` | `_barge_max_pause_s()` `:194-203` | 4000 |
| `REALTIME_BARGE_ROLLBACK_TIMEOUT_S` | `:206-208` | 2.0 |
| `REALTIME_BARGE_COOLDOWN_MS` | `:211-213` | 800 |
| `REALTIME_VAD_SILENCE_DURATION_MS` | `_vad_silence_duration_ms()` `:155-163` | 1000 (`:235`) |
| `REALTIME_VAD_TYPE` / `_THRESHOLD` / `_PREFIX_PADDING_MS` / `_EAGERNESS` | `openai_realtime.py:205-227`, `:67-73` | server_vad / 0.5 / 300 / auto |
| `REALTIME_BOOT_GATE`, `REALTIME_BOOT_GATE_TIMEOUT_S` | `:519`, `:2384` | True / 8.0 |
| `REALTIME_ONSET_RAMP_MS` | `openai_realtime.py:568` | 120 |
| `REALTIME_MIN_TURN_CHARS` | `audio/backchannel.py:71` | 2 |

Misconfiguration warner: `warn_if_barge_confirm_races_vad()` `:239-274` (called
from `_turn_detection`, `openai_realtime.py:204`).

**What a mode manager must own per mode:** `_party_mode` (or its successor),
the `_turn_detection(party=…)` argument, whether `_solo_speech_started` or the
party branch runs at `speech_started`, whether the gate runs at
`transcription.completed`, and whether the accept path calls
`_safe_response_create()`.

---

## 3. Transcripts

### 3.1 Emission sites

`_emit_transcript(role, text, final)` is defined on the base handler,
`conversation_handler.py:59-66`; it forwards to `_transcript_observer`
(`:38`), installed via `set_transcript_observer` (`:55-57`).

Call sites in `huggingface_realtime.py`:

| line | role | context |
|---|---|---|
| `:1321` | user | solo barge rolled back — transcript preserved even though the turn is discarded |
| `:2606` | user | party-gate **denied** ambient turn |
| `:2645` | user | normal accepted user turn |
| `:2682` | assistant | `response.output_audio_transcript.done` |

Each of those (except `:1321`, which does both in the same block) is paired
with `await self.output_queue.put(AdditionalOutputs({"role": ..., "content":
...}))` at `:1320`, `:2605`, `:2644`, `:2679-2681`. Also `user_partial` at
`:1588` (debounced), and tool notices as `role="assistant"` at `:2788-2795`
and `:2222-2229`.

Consumers:
- `LocalStream._dispatch_transcript` → JSON-RPC broadcast
  `conversation.transcript` (`console.py:169-175`); wired in
  `_attach_observers_to_handler` (`console.py:160-167`), re-wired on every
  handler install (`console.py:144-148`).
- `console.play_loop` logs `AdditionalOutputs` content lines at INFO,
  truncated to 500 chars (`console.py:930-938`).

### 3.2 Existing in-memory history

**Yes, exactly one, and it is the sleep-summary buffer:**
`ToolDependencies.session_transcript: deque[tuple[role, text, monotonic]]`
with `maxlen=40` (`core_tools.py:78`; literal must match
`sleep_summary.TRANSCRIPT_MAX_ITEMS = 40`, `sleep_summary.py:32`).
Deliberately **not** cleared on reconnect — "the unit is the visit"
(`core_tools.py:65`). Appended only via `sleep_summary.record_transcript`
(`sleep_summary.py:45-72`), called from exactly two sites:
`huggingface_realtime.py:2651` (user, accepted turns only) and `:2683`
(assistant). Party-denied and rolled-back turns `continue` before reaching it.

There is no other conversation history in the app; the model's own context on
the server is the only full record.

### 3.3 Transcription config

`openai_realtime._transcription()` (`openai_realtime.py:149-181`), attached at
`openai_realtime.py:423`:

- model: `REALTIME_TRANSCRIPTION_MODEL` or `_DEFAULT_TRANSCRIBE_MODEL =
  "gpt-transcribe"` (`:140`).
- `language`: `config.REALTIME_TRANSCRIPTION_LANGUAGE` (`:161`).
- new-model extras, skipped for `_LEGACY_TRANSCRIBE_MODELS =
  ("gpt-4o-transcribe", "whisper-1")` (`:139`, `:162-163`):
  - `keywords` — `REALTIME_TRANSCRIPTION_KEYWORDS` or `_party_names()`
    (`:164-170`);
  - `prompt` — `REALTIME_TRANSCRIPTION_PROMPT` or
    `"與家用陪伴機器人的台灣中文對話"` (`:141`, `:171-174`);
  - `delay` — `REALTIME_TRANSCRIPTION_DELAY` ∈ minimal/low/medium/high/xhigh
    (`:146`, `:175-180`). Lower delay = the name reaches
    `_maybe_commit_on_partial` sooner.
- Rejection fallback: `_session_config_fallback` retries once with
  `gpt-4o-transcribe` and drops `reasoning` (`openai_realtime.py:437-465`).

Base (HF) config hard-codes `gpt-4o-transcribe`
(`huggingface_realtime.py:1466-1469`) — not the live path.

Partial-delta accumulation differs per backend:
`huggingface_realtime._record_partial_transcript_delta` replaces
(`:1594-1602`); the OpenAI override **appends** (`openai_realtime.py:467-487`).

---

## 4. Sleep summary (D-027) — `sleep_summary.py`

### 4.1 API and model

`write_sleep_summaries(deps, *, client=None) -> int`
(`sleep_summary.py:120-177`). **Not** the realtime API — a plain Chat
Completions call:

```
client.chat.completions.create(
    model=_default_model(),                       # sleep_summary.py:154
    messages=[{system: _SYSTEM_PROMPT}, {user: user_prompt}],   # :155-158
    response_format={"type": "json_object"},       # :159
)                                                  # wrapped in asyncio.wait_for
```

- model: `_default_model()` `:75-77` — `MEMORY_LAST_CHAT_MODEL` or
  **`"gpt-5-mini"`**.
- client: `client` arg, else `hanova.images.build_client()`
  (`sleep_summary.py:142-144`; `hanova/images.py:28-49` — `AsyncOpenAI` from
  `OPENAI_API_KEY`, `None` when unset). Used as `async with client:`
  (`:151`) so the pool closes.
- timeout: `MEMORY_LAST_CHAT_TIMEOUT_S`, default 8.0, clamped 1-30 (`:150`).
- kill switch: `MEMORY_LAST_CHAT_ENABLED`, default True (`:123`).
- system prompt `_SYSTEM_PROMPT` `:35-42` — Traditional-Chinese "memory
  archivist"; one ≤50-char 上次聊天 line per listed person; JSON object
  `{"人名": "摘要"}`; explicit instruction not to attribute lines to a person
  unless the record names them.
- Never raises: the whole body is inside `try/except Exception`
  (`:175-177`).

### 4.2 Inputs

`transcript = list(deps.session_transcript)` (`:126`) — the shared 40-line
deque described in §3.2. Rendered as `"user: …" / "reachy: …"` lines
(`:148`), prefixed with `在場的人：<names>` (`:149`).

Names come from `_people_in_window(deps, transcript)` (`:86-117`): all of
`deps.recognized_people` while the deque is not yet full; once full, only
people whose `deps.recognized_at[name]` is ≥ the oldest retained line's stamp.
`record_transcript` refreshes the current speaker's stamp (`:67-72`) so a long
visit doesn't filter out its own speaker.

### 4.3 Output path

Per name: `_replace_last_chat_fact(deps.instance_path, name, summary)` on a
thread (`:173`), which writes into the **people store**
`people.v1.json` (`people.py:44`, path resolved by
`people.people_path_for_instance`, `people.py:111-118`) via
`add_person_fact` / `forget_person_fact` (`people.py:330`, `:381`), keeping
exactly one fact prefixed `上次聊天` (`sleep_summary.py:33`, formatted by
`format_last_chat_fact` `:80-83`). Cap `MAX_FACTS_PER_PERSON = 20`
(`people.py:51`).

### 4.4 Trigger

`HuggingFaceRealtimeHandler.shutdown()` (`huggingface_realtime.py:2920-2974`):

```
if self.deps.sleep_requested and not self._sleep_summary_done:   # :2941
    self._sleep_summary_done = True                              # :2942
    written = await write_sleep_summaries(self.deps)             # :2943
```

`_sleep_summary_done` guards the double-shutdown case (`:601-604`).
`deps.sleep_requested` (`core_tools.py:82`) has exactly one writer:
`go_to_sleep_and_stop_app` in `main.py:316`. Settings/backend restarts also
reach `shutdown()` and must not summarize — hence the flag.
Placement is deliberate: after `on_session_shutdown` (music stop, `:2930`) and
before `connection.close()` (`:2959-2967`), because the summarizer can take
seconds (`:2937-2940`).

**Reuse verdict for record mode:** the summarization approach transplants
almost unchanged — swap `_SYSTEM_PROMPT`, feed a longer transcript buffer, and
return the text instead of writing a person fact. The two things that need
changing are (a) the 40-line cap on `session_transcript`, which is far too
small for a meeting recording, and (b) the fact that party-denied and
rolled-back turns never reach `record_transcript` — record mode wants *all*
room speech, which is the opposite of today's rule
(`huggingface_realtime.py:2646-2651`).

---

## 5. Session instructions / prompts

### 5.1 Composition

`prompts.get_session_instructions(instance_path)` (`prompts.py:72-98`):

1. `_active_profile().instructions` (`prompts.py:68-69`, `:77-78`) — resolved
   by `profile_store.read_profile`, with `persona.apply_persona_override`
   overlaying an operator `persona.md` (`persona.py:175-190`; lookup
   `persona.py:71-80`; `PERSONA_FILE` env override `persona.py:46`). Fallback
   chain: bad/incomplete profile → packaged default (`prompts.py:83-88`) →
   `RuntimeError` (`:90`).
2. `+ "\n\n" + hardening_block()` (`prompts.py:92-94`) —
   `_HARDENING_BLOCK` at `prompts.py:25-58`, disabled by
   `REALTIME_PROMPT_HARDENING=0` (`prompts.py:61-65`). Sections: 不需要回應的
   聲音 (points at `wait_for_user`), 聽不清楚時, 語言, 回答長度. The last
   bullet (`prompts.py:56-57`) already encodes the name-gate behavior in prose.
3. `format_memory_for_prompt(instance_path)` **prepended** if non-empty
   (`prompts.py:95-97`; `memory.py:199`) — global memory facts from
   `memory.v1.json` (`memory.py:19`).

Consumed at `huggingface_realtime.py:1460` inside `_get_session_config`
(`:1456-1479`), which the OpenAI subclass extends (`openai_realtime.py:397`).

Sibling resolvers: `get_session_voice` (`prompts.py:101-108`),
`get_session_greeting_prompt` (`prompts.py:111-117`, default at `:20-23`).

### 5.2 Mid-session updates

Yes — three distinct `session.update` shapes exist:

| purpose | site | payload |
|---|---|---|
| full session init | `huggingface_realtime.py:2327` (fallback `:2334`) | whole config |
| voice only | `change_voice`, `:1516-1525` | `audio.output.voice` |
| **instructions + voice** | `apply_personality`, `:1553-1563` | `instructions=…`, `audio.output.voice` — then unconditionally `_restart_session()` (`:1569`) |
| turn detection only | `openai_realtime.py:511-513` | `{"type": "realtime", "audio": {"input": audio_input}}` |

So **instructions can be updated live** (`:1553-1563` proves the shape works),
but the only existing caller pairs it with a session restart. The narrow-update
precedent to copy for a mode-scoped prompt is
`_push_turn_detection_update` (`openai_realtime.py:489-516`) — it sends the
whole nested `audio.input` block rather than a single leaf, deliberately, so a
server treating the nested object as a replacement cannot strip siblings. The
same caution applies to a `tools` update.

### 5.3 Does party mode change the prompt?

**No.** `set_party_mode` (`:607-652`) touches no prompt state and
`_get_session_config` interpolates `_party_mode` only into `turn_detection`
(`openai_realtime.py:416`). The model is never told it is in party mode. That
is a gap a record mode probably cannot live with: "stay quiet, only summarize
when called" is behavior the model itself must know about, so record mode is
the first mode that needs a per-mode instruction block (via a narrow
`session.update` with `instructions`, or a session restart).

---

## 6. Tool registry

### 6.1 How tools register (`tools/core_tools.py`)

- One file per tool under `tools/`; **filename must equal `Tool.name`** —
  the loader imports `reachy_companion.tools.<tool_name>`
  (`core_tools.py:447`) for each name in the profile's list.
- A tool is a `Tool` subclass (`core_tools.py:106-137`) with class attrs
  `name`, `description`, `parameters_schema`, optional
  `needs_response: ClassVar[bool] = True` (`:119`) and
  `_auto_register: ClassVar[bool] = True` (`:118`). `spec()` (`:125-132`)
  produces the `ToolSpec` (`:97-104`).
- Discovery: `_tool_classes_from_module` picks up every concrete,
  auto-registering `Tool` subclass **defined in that module**
  (`core_tools.py:256-274`).
- Which names load: `_read_profile_tool_names` (`core_tools.py:366-390`) =
  the active profile's `default_tools` + every `SystemTool` value
  (`:376`; `tools/tool_constants.py` → `task_status`, `task_cancel`) +
  external tools when `AUTOLOAD_EXTERNAL_TOOLS` (`:378-387`).
- `initialize_tools(instance_path, force=False)` (`:470-522`) rebuilds
  `ALL_TOOLS` when the signature `_tool_registry_signature` (`:354-362`:
  profile, profiles dir, tools dir, autoload flag, instance path) changes.
  Duplicate `Tool.name` → `RuntimeError` (`:344-349`).
- Out-of-band seam: `register_extra_tool(tool)` (`:158-168`) + `EXTRA_TOOLS`
  (`:155`) — survives every rebuild, merged after (`:508-516`), name
  collisions dropped with a warning rather than raising. Used for MCP tools.
- Readers: `get_tool_specs(exclusion_list=None)` (`:525-530`) —
  **note the existing `exclusion_list` parameter** — and `get_tools()`
  (`:533-537`). Dispatch: `dispatch_tool_call` (`:568-570`).

The current profile list is in
`reachy_companion/profiles/_reachy_companion_locked_profile/profile.md`
(TOML front matter `default_tools`, ~40 entries incl. `party_mode`,
`go_to_sleep`, `wait_for_user`).

### 6.2 Adding / replacing a tool

Drop `tools/<name>.py` with one `Tool` subclass whose `name == <name>`, add
`<name>` to the profile's `default_tools`. If it needs handler state, add an
optional callable field to `ToolDependencies` (`core_tools.py:37-95`) and
assign it in `main.py`'s `build_handler` — exactly what `set_party_mode`
(`core_tools.py:56`, `main.py:269`) and `go_to_sleep` (`core_tools.py:47`,
`main.py:358`) do. Set `needs_response = False` for tools that must not
trigger a spoken follow-up (`tools/go_to_sleep.py:21`,
`tools/wait_for_user.py:20`); enforced at `huggingface_realtime.py:2277`.

### 6.3 Per-mode tool enablement

**Not supported today.** The tool list is computed once per session:
`tool_specs = get_tool_specs()` at `huggingface_realtime.py:2315`, passed into
`_get_session_config` (`:2325`) and sent in the initial `session.update`
(`:2327`). Nothing re-sends `tools` mid-session — `_push_turn_detection_update`
deliberately builds the config with `tool_specs=[]` and forwards **only**
`audio.input` (`openai_realtime.py:509-513`).

Three viable routes, cheapest first:
1. Leave all tools registered and let per-mode **instructions** discourage the
   wrong ones (matches how the app already handles `wait_for_user`).
2. Add a `tools` key to a narrow `session.update` on mode flip — the shape is
   already proven by `apply_personality` for `instructions`.
3. `get_tool_specs(exclusion_list=[...])` (`core_tools.py:525`) at session
   build + `_restart_session()` (`:1635-1667`) on flip — heaviest, drops the
   websocket.

---

## 7. `go_to_sleep` quiesce points

### 7.1 The current path

`tools/go_to_sleep.py:28-38` — `needs_response = False` (`:21`), body is
`await asyncio.to_thread(deps.go_to_sleep)` (`:35`). So the goodbye is whatever
the model spoke **in the same response before calling the tool**; nothing asks
for a follow-up, and nothing waits for that audio.

`main.py:304-356` `go_to_sleep_and_stop_app()`, non-blocking lock (`:306`):

| line | step |
|---|---|
| `:312` | `go_to_sleep_requested.set()` |
| `:316` | `deps.sleep_requested = True` (the D-027 gate) |
| `:322` | `robot.disable_wobbling()` |
| `:326` | `movement_manager.stop(reset_to_neutral=False)` |
| `:329` | **`robot.goto_sleep()` — immediate, no audio wait** |
| `:336` | `app_lifecycle.request_stop_current_app(robot, logger)` |
| `:339` / `:342` | `app_stop_event.set()` or `stream_manager.close()` |

`stream_manager.close()` (`console.py:830-860`) stops recording and playback
media pipelines **first** (`:843`, `:848`), then sets the stop event and cancels
tasks. Handler `shutdown()` runs from the async runner (`console.py:826`) — and
that is where the sleep summary happens (`huggingface_realtime.py:2941`).
Sync fallback for timeout paths: `app_lifecycle.run_go_to_sleep_tool`
(`app_lifecycle.py:78-84`, `asyncio.run(GoToSleep()(deps))`).

### 7.2 (a) Flush the player queue

`LocalStream.clear_audio_queue()` — **`console.py:862-892`**. Order matters and
is documented:

1. `handler.on_external_interrupt()` first (`console.py:876-878`) — before
   anything is flushed, so held audio cannot be resurrected;
2. SDK `audio.clear_player()` (fallback `clear_output_buffer()`)
   (`console.py:879-885`);
3. `_drain_output_queue()` — drains in place, never replaces the queue object,
   because `emit()` may be awaiting it (`console.py:886-888`, `:894-903`);
4. `audio_drain.note_cleared()` (`console.py:892`).

Reachable from the handler as `self._clear_queue` — installed at
`console.py:147`; the OpenAI subclass wraps it to also reset the voice filter
and output resampler (`openai_realtime.py:294-317`, `_reset_output_pipeline`
`:354-363`). This is the correct flush for "abandon the goodbye"; it is the
wrong one for "let the goodbye finish".

### 7.3 (b) Stop mic input / stop committing turns

Two independent, already-shipped primitives:

- **Mic-level:** `LocalStream._mic_muted` (`console.py:131`), checked in
  `record_loop` (`console.py:912`), toggled by JSON-RPC `conversation.mic`
  (`console.py:616-621`). Cheapest hard stop — frames never reach
  `handler.receive`.
- **Server-level (the boot gate's technique):** set turn detection to `None`
  and clear the input buffer. See `_finish_boot_gate`
  (`huggingface_realtime.py:679-713`) for the exact ordering — the input buffer
  is cleared **before** turn detection changes (`:703-707`, `:712`) — and
  `openai_realtime.py:405-414` for the `turn_detection = None` config branch,
  pushed via `_push_turn_detection_update` (`openai_realtime.py:489-516`).
  `_boot_gate_active` (`huggingface_realtime.py:519`) is the flag; a record
  mode or a sleep quiesce wants the same flag shape.

Also relevant: `receive()` early-returns when `self.connection is None`
(`huggingface_realtime.py:2893-2894`, `openai_realtime.py:527-528`).

### 7.4 (c) Disarm the barge machine

- `on_external_interrupt()` — `huggingface_realtime.py:911-947`. **Synchronous,
  safe from a non-loop thread** (it marshals cancels through
  `_cancel_barge_task`, `:289-316`) and safe from inside a barge timer (never
  cancels its own task). Clears all three timers, `_barge_paused`,
  `_barge_pending`, `_barge_speech_open`, the paused/resumed response ids, the
  partial-committed marker, `_barge_late_eligible`, `_held_audio`, the truncate
  accounting, and `audio_drain.note_paused(False)`.
- `_barge_shutdown()` — `:960-975`, async: calls `on_external_interrupt()` then
  `await asyncio.gather(*tasks)` so no timer outlives the session. Already
  called from the session `finally` (`:2835`) and from `shutdown()` (`:2952`).
- `_barge_reset_for_new_session()` — `:949-958`, adds cooldown/response-seen
  reset.

For go_to_sleep specifically, `on_external_interrupt()` is the right call
because it is thread-safe and `go_to_sleep_and_stop_app` runs on a worker
thread (`tools/go_to_sleep.py:35`).

### 7.5 (d) Delay the sleep pose until the goodbye has drained

`hanova/audio_drain.py` is the module; it is a process-global tracker with a
lock (`:55-78`).

Available waiting primitives:

| primitive | line | meaning |
|---|---|---|
| `is_audible()` | `:223-239` | True if paused, or local queue non-empty, or `outstanding_s > 0.25`, or `monotonic() < _DRAINED_AT`. **Generation-free — the right predicate when you just want "has the speaker gone quiet".** |
| `wait_drained(generation, timeout_s)` | `:259-268` | polls `_is_drained` every 20 ms; needs a generation token |
| `outstanding_s()` | `:205-208` | enqueued-but-not-yet-sinked seconds |
| `device_buffered_s()` | `:211-220` | estimated seconds still inside the device buffer, `max(0, _DRAINED_AT - now)` |
| `begin_response()` / `close_response(gen)` | `:93-106`, `:167-171` | open/close a generation |
| `note_enqueued` / `note_chunk` / `note_queue_empty` | `:109-153` | the accounting, fed by the event receiver and `play_loop` |
| `note_cleared()` | `:174-196` | flush: close + mark every generation interrupted |
| `note_paused(bool)` | `:155-164` | barge-pause hold |
| `was_interrupted(gen)` | `:199-202` | tells "closed by flush" from "closed by finishing" |
| `reset()` | `:80-90` | session start / tests |

Two existing waiter patterns to copy:

1. **Poll-`is_audible`-with-a-cap** — `_boot_gate_release_after_drain`
   (`huggingface_realtime.py:715-730`):
   ```
   deadline = time.monotonic() + _BOOT_GATE_DRAIN_CAP_S     # 3.0 s, :414
   while audio_drain.is_audible() and time.monotonic() < deadline:
       await asyncio.sleep(_BOOT_GATE_DRAIN_POLL_S)         # 0.1 s, :413
   ```
   This is the **closest match for delaying the sleep pose**: it needs no
   generation token, it is already bounded, and its comment states the exact
   rationale ("`response.done` means the model finished emitting, not that the
   speaker finished playing").

2. **Generation-scoped `wait_drained`** — `music_hooks._resume_when_drained`
   (`hanova/music_hooks.py:316-359`), the source of the
   "music resume still waiting for the turn's audio to drain: %.0fs elapsed,
   %.2fs outstanding" line (`:346-350`). It loops on
   `wait_drained(generation, _DRAIN_REPORT_EVERY_S)` and only ever gives up on
   session change (`:339-341`) or supersession (`:342-344`) — deliberately
   unbounded otherwise. Scheduled from `on_assistant_turn_ended`
   (`:277-313`), which is called at `response.output_audio.done`
   (`huggingface_realtime.py:2436`) and `response.done` (`:2485`).

Generation tokens come from `on_response_created()` (`music_hooks.py:195`,
called at `huggingface_realtime.py:2450`); `on_response_audio` feeds
`note_enqueued` (`music_hooks.py:205`, called `:2700-2703`); `play_loop` feeds
`note_chunk` (`console.py:963-966`) and `note_queue_empty`
(`console.py:927`, `:976`).

**Concrete gap:** nothing between the `go_to_sleep` tool call and
`robot.goto_sleep()` (`main.py:329`) consults any of these — and because
`GoToSleep.needs_response = False`, there is not even a follow-up response
whose drain could be awaited. A "say goodbye, then sleep" sequence needs
either (i) the tool to `await` a bounded `is_audible()` poll before returning
(it is already async, `tools/go_to_sleep.py:28`), or (ii) the goodbye to be
injected as its own response via `say()` (`huggingface_realtime.py:1676-1696`)
with the pose deferred behind that response's drain.

---

## Cross-cutting notes for the mode manager

- **Single source of truth today** is `_party_mode` (`:528`), a bool. Sites
  that branch on it: `:1073`, `:1099`, `:1118`, `:1264`, `:1286`(comment),
  `:2402`, `:2422`, `:2597`, `:2619`, `:2629`, `:2653`, `:2662`, plus
  `openai_realtime.py:416`. An enum swap touches all of them.
- **Mode survives reconnects** by design; only turn state is reset
  (`_party_reset_for_new_session` `:654-667`, `_barge_reset_for_new_session`
  `:949-958`, both called at `:2304`/`:2307`). Preserve that.
- **`deps.current_person` is cleared on every session** (`:548`, `:2314`) —
  including reconnects. A record mode that attributes lines to speakers cannot
  lean on it (`sleep_summary.record_transcript` already works around this,
  `sleep_summary.py:53-72`).
- **Both gates share `_gate_text_accepts`/`_party_names`/`_PARTY_CONTROL_RE`**
  (`:140-152`, `:112-114`, `:95`). Record mode's "call my name" trigger should
  reuse them rather than add a fourth name list — the transcription keyword
  bias (`openai_realtime.py:166`) is derived from the same list.
- **Every mode-relevant knob degrades with a warning, never raises**
  (`audio/envparse.py` helpers `env_bool` / `env_int` / `env_float`); keep that
  contract for any new mode knob.
