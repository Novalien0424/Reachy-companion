# Mini context diet: fewer tools, no boxes, each fact once — plan (2026-10-30)

Status: done (2026-10-30). Step 0 (TV as home device), Step 1 (1.30.0) and
Step 2 (1.31.0) are built, installed and live-tested. Fixed prefix 9,866 →
5,821 tokens with all 19 tools loaded. Results, fixes and open items are in
D-039. Not done: hiding `home_control` when its device list is empty (moot
with the TV configured).

Operator, 2026-10-30:
- "seems we have way too many tools for mini model, I can give up
  calendar/tasks/notion".
- "keep those [drive, email_send, draw_picture], TV should be one of them
  [home_control devices], but still think how we can minimize the context
  send to mini model, think deep from 1st principal and read mini model
  instructions".

## What the model is sent (measured 2026-10-30)

From the robot's per-response `Response usage` lines (207 responses since
10-26) and o200k token counts of the shipped text:

| Part | Tokens | Notes |
|---|---|---|
| Fixed prefix at session start | **9,866** | every response, before anything is said |
| — 22 core tool schemas | ~5,660 | `dance` 674 (upstream move list in a param description), `open_toolbox` 522, `look_around` 407 |
| — persona (`persona.md`) | ~2,620 | `## Tools` 954 restates tool descriptions; `Tool Result Conventions` 756 explains rare statuses |
| — system rules (`hardening_block`) | ~1,810 | `Tool Availability` 453 exists only because of the boxes; language rule also in the persona |
| Media box open | +~770 | 3 schemas, plus one extra model hop |
| One `nas list` (Japan) | +~2,500 | stays in the conversation for the rest of the session |
| Largest response seen | 14,322 | the fixed prefix is 70–100 % of every input |

Audio input stayed small (12 responses carried audio, at most 60 tokens). No
response carried an image. Sessions are short.

## What OpenAI says (2026-10-30 refresh, research-instructing-realtime-voice-2026-09)

- **Cost is not the reason.** Cached text is $0.06 per 1M tokens on mini, so
  the 9.9k prefix is nearly free. The reason is quality: mini's tradeoff is
  "instruction following and function calling", and "fewer tokens returned
  by function calls lead to better quality responses".
- "Start simple. Do not over-prompt upfront."
- "Aim for fewer than 20 functions available at the start of a turn."
- "Combine functions that are always called in sequence."
- "Shortening descriptions where possible."
- "you only provide what's relevant to the active phase."
- Changing tools mid-session "will reduce the cache rate".

## First principles

1. **The prefix is read on every response.** It is cached, so it costs
   almost nothing, but the model still reads all of it before every answer.
   A small distilled model loses instructions in long context first.
   Measured: leaner prompts scored 10–15 % better, and adherence decays over
   turns (research-instructing-realtime-voice-2026-09).
2. **Each fact lives once, where the model looks for it.** Choosing a tool:
   its description. Reading a result: the result itself (named fields, and
   errors written as advice). Who Reachy is and how it talks: the
   instructions. A copy elsewhere costs tokens on every turn, and two copies
   drift apart.
3. **Rare rules belong to the moment they apply.** A status seen once a week
   (`away_from_home`, `body_too_long`) is explained in that result's error
   text. Explaining it in the prompt costs tokens on every turn.
4. **The first call finishes the job.** Mini fails between calls, not at
   picking them. Every box is a forced extra call.
   - **Phases stay; boxes go.** OpenAI's "only what's relevant to the active
     phase" is kept where the *app* knows the phase: conversation modes
     (record mode sees 4 tools).
   - A box makes the *model* ask for its own tools. That is the extra call,
     and with it a mid-session tools change that lowers the cache rate.
   - At 19 tools, everything fits under the "fewer than 20" line at once.
5. **Results are sized to the next sentence.** A result holds what Reachy
   will say or need for the next call, never a record dump that then sits in
   the history.

## Design

### Step 0 — the TV as a home device (config only, today)

- On the robot, set `HA_ENTITIES` to `{"電視": <HANOVA_CAST_ENTITY>}`. A
  script on the robot copies the value, so the entity never leaves it. Then
  restart the app, because `home_control` builds its device enum at start.
- 「把電視關掉」 → `home_control turn_off 電視`. This closes the "no TV stop"
  gap with no new tool and is DEMO-5's real device.
- Other devices (lights and so on) when the operator names them.

### Step 1 — 30 tools → 19, all always loaded, no boxes (1.30.0)

- **Remove (6):**
  - `calendar`, `tasks`, `notion_add`: operator.
  - `task_status`, `task_cancel`: cancelling needs a `tool_id` from status
    first, a two-call chain. Neither was called in the journal.
  - `open_toolbox`: the toolbox mechanism goes with it.
- **Merge (9 → 4),** each a family over the original modules (D-029
  pattern):
  - `dance` + `stop_dance` + `stop_emotion` → `dance` {`play`, `stop`}. `stop` ends any
    motion, so the model never has to know which one is running.
  - `move_head` + `head_tracking` → `head` {`turn`, `follow`,
    `stop_following`}.
  - `remember` + `forget` → `memory` {`save`, `forget`}.
  - `who_is_this` + `remember_face` → `face` {`identify`, `remember`}.
- **Kept as is (15):**
  - `camera` and `look_around`: look_around is the turn-then-look composite
    that fixed the look-bug.
  - `play_emotion`: DEMO-2's spontaneous call, kept single-purpose.
  - `search_web`, `home_control`, `music`.
  - `set_conversation_mode`, `summarize_conversation`, `wait_for_user`,
    `go_to_sleep`.
  - `youtube`, `nas`, `draw_picture`, `drive`, `email_send`.
- **Record mode** keeps its allowlist, minus the task tools (4 tools).
- **Names change in the persona and the prompt in the same release.** The
  prompt must never name a tool the session lacks.

### Step 2 — each fact once (1.31.0)

- **Persona:**
  - `## Tools` is deleted, except the parts about conversation itself: a
    recognized person's last chat as something to bring up, and the
    `go_to_sleep` farewell rule.
  - `Tool Result Conventions` moves to where each status is produced:
    - `away_from_home`, `tv_not_responding`, `home_status_unknown`,
      `action_in_flight`, `body_too_long` and `unavailable` → each error
      string says what happened and what to tell the user.
    - `needs_confirmation`, `retryable`, BCC and Drive restore → the
      `email_send` and `drive` descriptions, which are read when the tool is
      called.
  - One line stays: a tool's `error` text is advice for you, so follow its
    meaning.
- **System rules:**
  - `Tool Availability` → one sentence: what isn't in your tool list, say
    you can't do.
  - The duplicated language rule becomes one copy.
- **Schemas:**
  - `dance` lists move names only, dropping upstream's per-move description
    text (the family schema carries it, so `dance.py` stays upstream-identical).
  - "Do NOT use" clauses that pointed at merged siblings go.
- **Results:** `nas list` returns trips only when several match. For one
  trip it returns the clip titles without paths: `play` takes `top_folder` or
  `keyword`, so paths aren't needed.

### Step 2 additions from the compact-text research (docs/research-compact-tool-text-2026-10.md)

- **Tools are read as a TypeScript-like render, not JSON.** The robot's
  usage log only adds up that way: the 22 tools cost about 4.9k, not 5.7k.
  So the prose instructions are the bigger share. Sizes are measured as the
  first `in=` of a fresh session.
- **Descriptions** keep purpose, scope, contrast with each sibling, and
  grounding rules for return fields (EasyTool; MCP-GRANITE).
  - **Example utterances:** one Chinese anchor, and only where a confusion
    was actually observed. Lists of example phrases and English examples
    go: examples raise selection whether or not the tool fits (Tool
    Preferences, EMNLP 2025).
  - **No assertive cues** (ALWAYS, NEVER, FIRST, RIGHT NOW, "Always use this
    tool"). They are measured over-triggering levers, so scope is written as
    a condition instead.
  - **Each fact once per schema**, e.g. the direction gloss only in the
    parameter description.
  - **Kept:** enum value glosses and the English-search glosses (巴黎 →
    Paris). Chinese words copied into English arguments is the measured
    failure (MLCL, ACL 2026).
- **Families stay at 2–3 actions.** Task-level families beat single-purpose
  tools on small models, while a single all-in-one tool raised no-call runs
  from 10 % to 28 % (MCP-GRANITE). No further merging.
- **The system rules block is the next target** (about 57 rules). The
  measured failure mode is rules left out as the count grows (IFScale), so
  each rule must earn its place or merge with a twin. The persona draft
  holds about 23.
- **No telegraphic or token-deletion compression** (LLMLingua corrupts
  instructions and parameters), and the persona stays in Chinese. English
  would save only about 20 %, and there is no accuracy evidence for it.
- **`home_control` with no devices** renders as a free string. With the TV
  configured that's moot, but the tool is excluded when its device list is
  empty.

### Expected size (measured after each step, not promised)

Fixed prefix ≈ 6.5–7k tokens with all 19 tools present (tools ≈ TS render). Today it is 9,866 with
22 tools, and about 10.6k with the media box open. History growth per `nas
list` goes from about 2.5k to under 0.5k.

### Not in this plan

- **Reasoning effort** (robot: `medium`, the operator's 09-01 accuracy
  choice). OpenAI now documents `low` as both the default and the voice-agent
  starting point. It is output and latency, not context, and the skill allows
  a change only by an on-robot A/B that measures hallucination, adherence and
  selection.
- **Session truncation** (opt-in knob already shipped): sessions peak at
  14k, nowhere near the window.
- **`REALTIME_IMAGE_DETAIL=low`:** no images in the journal to measure. It
  stays an A/B candidate.

## Verification

- **Unit:**
  - Tool counts: 19 / record 4.
  - Every family validates `action` only, and the delegates are unchanged.
  - `dance stop` stops both motions.
  - No prompt or persona names a removed tool.
  - Each moved status has its advice in the error string.
  - `nas list` shape.
  - Gate: pytest, ruff, mypy.
- **Measure:** first `Response usage in=` of a fresh session, before and
  after each step.
- **Live** (fresh session per case, mic muted; checked after Step 1 and
  again after Step 2):
  - 「把電視關掉」 → TV off (Step 0).
  - DEMO-2: an emotion move in conversation.
  - DEMO-3: `camera`.
  - 「轉到右邊看看有誰」 → `look_around`.
  - 「你認得我嗎」 → `face identify`.
  - 「記住我喜歡咖啡」 / 「忘掉這件事」 → `memory`.
  - 「跳支舞」 → 「停」 → `dance stop`.
  - DEMO-4: news → `search_web`.
  - The NAS V1–V4 set and YouTube.
  - `email_send` to the operator's own address, stopping at
    `needs_confirmation`: the summary is read verbatim and nothing is sent.
  - Record mode on, a summary, back to one-on-one.

## Review log

Self-review before execution, against the PRD, the research notes and
`reference/`:

1. **Reuse-first.**
   - `dance`, `stop_dance`, `stop_emotion`, `move_head`, `head_tracking`,
     `remember` and `forget` are upstream-derived
     (`reference/reachy_mini_conversation_app/.../tools/`). Each family calls
     those classes unchanged (`tool_family.dispatch_family`), so upstream
     ports still diff file for file.
   - The one composite is `dance stop`, which calls both stop delegates.
2. **The `dance` name collides.** The registry keys on `Tool.name`
   (`core_tools.py:419`), and the convention is filename == `Tool.name`
   (`music.py`). The family needs `dance.py`, so upstream's `Dance` class
   moves unchanged to `dance_moves.py`. Ports then diff that path.
   - `head`, `memory` and `face` are new names and collide with nothing.
3. **`task_status` and `task_cancel`** are injected into every profile by
   upstream (`core_tools.py:444`). They stay registered, so the manager still
   works, and are only excluded from `session.tools` (`toolboxes.py`).
   - `record_mode.RECORD_TOOL_ALLOWLIST` and the record-mode prompt line
     that names them change with it.
4. **PRD.**
   - F-P1 lists calendar, tasks and Notion as ported Skills; an amendment
     line records the operator's removal.
   - F-K3 (one MCP integration) stays met by web search.
   - DEMO-2 keeps a single-purpose `play_emotion`.
   - DEMO-5 gains its real device in Step 0.
5. **Instructing skill.**
   - The Tool Availability rule was mandatory *with box gating*. After Step
     1 there is no gating, so one sentence remains (don't simulate a missing
     tool).
   - The verbatim and confirmation render rules move into the
     `email_send`/`drive` descriptions, which are a higher-authority
     surface, so the result flags keep a defined meaning.
   - Status advice moves into error strings ("errors are advice addressed to
     the model").
6. **D-029 decision 7.** Families validate only `action`. The prerequisite
   rows move from removed names to the delegates the families call.
   - Calendar, tasks and Notion leave `TOOL_PREREQS`, and their modules are
     deleted, not just unlisted. Unused code doesn't stay (end-of-session
     rule).
7. **Persona sync.** `persona.md` changes in both steps and reaches the
   robot only by scp plus sha check (D-016), in the same deploy as the wheel.
   A stale persona naming removed tools is a hard gate.
8. **Steps without a verification:** none. Every step has a size
   measurement and live rows.
9. **Risk.** `face identify` must keep winning over `camera` for identity
   questions. The emphatic rule moves from the persona into `face`'s
   description. Live row 「你認得我嗎」 checks it.
10. **Size claims are estimates.** The ~7k target comes from per-section
    token counts (o200k), not from a run. Each step's first
    `Response usage in=` is the real number.
11. **The contrary official line.** OpenAI's function-calling guide puts
    when/when-not rules in the system prompt. Under the TS render the
    descriptions sit right after the instructions in the same developer
    message, so one copy in the description keeps a single source without
    losing placement.
