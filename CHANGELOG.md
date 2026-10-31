# Changelog

Reachy Companion versions map to on-robot installs: the minor number is the
install that shipped the release (`1.17.0` = the seventeenth install), patch
numbers are fix-only redeploys. Versions before 1.17.0 were assigned
retroactively — the wheel said `1.0.0` for the first sixteen installs — and
their entries below are compact summaries reconstructed from `progress.md`
and `DECISIONS.md` (D-numbers cite the design records). There is no `1.18.0`:
the eighteenth install was a metadata-only redeploy that re-shipped the
`1.17.0` wheel, so it carried no release of its own.

## [1.36.7] — 2026-10-31 · long replies finish, 「我們」 names the source, a quieter journal

Deployed as the forty-fourth install (wheel sha `3dc93b1b…`). 1.36.4 (`a4cb5956…`),
1.36.5 (`6370acfd…`) and 1.36.6 (`e1d16490…`) were the measuring steps of the
same day. Decision D-045.

- **The reply rail is 4096 tokens (about 160 s), up from 900.** A news rundown
  and a requested story were cut mid-word; on 1.36.7 both finished live.
- **「我們」 counts as naming the source.** 「放我們去波士頓旅行的影片」 is meant
  for the family's own videos; a place alone still asks. Replay 12/24 from 0/4,
  but live 0/3: recorded at mini's ceiling.
- **The NAS index refresh no longer logs SMB handshakes** (the NAS address and
  the SMB username were in every wake's journal).
- **The dance description asks for the reply in the same turn.** Kept for a
  measured side effect on the video source choice; the dance itself still
  goes silent 3 times in 4.

## [1.36.3] — 2026-10-31 · a trip asked for by a slightly wrong name still plays

Deployed as the fortieth install (wheel sha `6f821c41…`). Decision D-044.

- **`top_folder` is matched loosely.** Case, spacing and punctuation are
  ignored, and a name that fits two folders matches neither. Live on 1.36.2,
  「換成我們去波士頓的影片」 failed twice: mini copied a folder name that runs the
  year into the place with a space added between them.
- **A `top_folder` that names no folder is searched.** The search uses the
  place or keyword beside it, or the name itself; the several-trips guard
  still applies. Live, `top_folder="England"` had hidden `place="England"`.
- Live rerun of the failed session: England played, the switch to Boston played
  in one call, and 「停」 stopped it.

## [1.36.2] — 2026-10-31 · Reachy says "it's on" sooner

Deployed as the thirty-ninth install (wheel sha `fe5341f2…`). Decision D-044.

- **The cast read-back polls every 0.5 s instead of 2 s.** On an idle
  receiver a NAS clip played 1.1 s after the call, but the 2 s poll held the
  tool until 2.8 s, and with it Reachy's confirmation. Now the tool returns
  within about 0.3 s of the picture. YouTube and picture casts share the poll.

## [1.36.1] — 2026-10-31 · home videos outlive Reachy's sleep

Deployed as the thirty-eighth install (wheel sha `ddbcde53…`). Decision D-044.

- **The NAS serves the clip (W6, live).** The operator set up a QNAP virtual
  host on the cast-copy folder, and the instance `.env` has
  `HANOVA_NAS_HTTP_BASE`. Its root answers 403 with no listing; a clip answers
  a range request with 206.
  - pause, resume, stop and the bookmark resume all ran over the NAS route;
  - a clip kept playing 90 s after Reachy went to sleep (it used to die after
    about 30 s);
  - a clip paused before sleep stayed paused through 60 s of sleep and resumed
    after the next wake.
- **A video resumed after a wake is recorded again.** Waking had forgotten
  the video, so the quiet-while-a-video-plays rule (W3) and the stop bookmark
  were off for it. `resume` now records a video the app has no record of: a
  NAS clip by our URL, a YouTube video by the app, anything else as a plain
  `tv` video (quiet-gated, never bookmarked). Live: `recorded as nas`, and
  the next stop kept its place.
- `device_check.sh sleep | paused-sleep`, and `tv_state.py` prints the port
  the TV fetches from.

## [1.36.0] — 2026-10-31 · pause, quiet while a video plays, group barge by name

Deployed as the thirty-seventh install (wheel sha `c712306b…`). Decision D-044.

- **Pause and resume.** `music` is now `play | pause | resume | stop`.
  - Pause and resume act on the speaker and the TV alike, and each is read back.
  - On the real TV a NAS clip paused at 12.5 s, held for 5 s, and resumed from
    12.7 s. YouTube did the same.
- **Stop stops everything.** One `music stop` ends the speaker and the TV
  together (`stopped: ["speaker", "tv"]`, live).
  - It keeps a bookmark of a video Reachy started, so 「繼續」 after a stop
    re-casts it and seeks back ("where it stopped", live for NAS and YouTube).
    This also makes mini's misread 「暫停」 (mini picks `stop` about 1 time in 3)
    cost a moment rather than the place.
- **Quiet while a video plays.** While a video Reachy started is on the TV
  (paused counts), 一對一 and 多人 answer only a turn that names Reachy or says
  a control phrase. A clip that ends on its own reopens the normal rules after
  one TV read.
- **Group barge by name.**
  - In 多人, and in 一對一 during a video, unaddressed speech no longer cuts
    Reachy off. Before, 1.6 s of any speech did, the TV soundtrack included.
  - A name or 「停」 still does.
  - While a video plays, soundtrack speech no longer delays the answer to a
    named turn.
  - `REALTIME_PARTY_BARGE=persistence` restores the old rule.
- **The NAS can serve the clip** (`HANOVA_NAS_HTTP_BASE`), so a video survives
  Reachy's sleep. It is off until the operator's QNAP virtual host is set up.
- **Answers about the clip.** The `nas` results carry the trip's `place`. While
  a video plays, `camera` and `look_around` also return `video_on_tv`, so
  「我們在哪裡」 is answered from the trip even via the camera (replay 3/3,
  against 1/3).
- **Search.**
  - camelCase labels match again (`OpeningNY` for "NY"; a 1.35.0 regression).
  - Chinese place names (`place_zh`, from the operator's index) match Chinese
    queries.
- **Small fixes:**
  - `response_cancel_not_active` no longer shows as a transcript error;
  - the sleep goodbye drain cap is 10 s (was 6);
  - a sleep summary that times out is kept and written at the next start;
  - the transcript's tool-result copy is readable UTF-8.
- **QA without voice sessions:**
  - `qa.tool` (test-only RPC, only while `/tmp/reachy_qa` exists);
  - `device_check.sh`, which ran every playback path on the real TV and speaker
    with zero model tokens;
  - `qa_replay.py`, which runs model-choice cases as text-only sessions built
    from the installed wheel: about 50 short turns in all, no audio.
- Suite 2272 / 31; ruff and mypy clean.

## [1.35.0] — 2026-10-31 · the right trip, found the first time

Deployed as the thirty-sixth install (wheel sha `810050c2…`). Decision D-043.

- **A UK search finds the UK trip.** Its index text now names the countries it
  covers and its main places. Boston's text no longer says "New England",
  which had made an 「England」 search find only Boston. Live: the UK trip was
  found in 4 of 4 runs, against 2 of 4.
- **Short search words match whole words.** "UK", "NY" and "USA" no longer
  match inside longer place and clip names.
- **Text for mini:**
  - `nas` searches each new trip request afresh, and skips only when asked.
  - `camera` is not for the video on the TV: that answer is the clip's title.
- **Live, 4 fresh runs per case:**
  - The Boston follow-up was answered right 4/4.
  - 「我們在哪裡？」 during a clip never skipped (0/4), and was answered from
    the title 3/4 (camera 1/4).
  - 「下一段」 still skipped 4/4.
- The fixed prefix is unchanged (about 5,960–6,010).

## [1.34.0] — 2026-10-31 · 「停」 really stops the video

Deployed as the thirty-fifth install (wheel sha `bee8d2ee…`). Decision D-042.

- **Telling Reachy to stop now stops the video on the TV.** `music stop` stops
  the speaker music, or, when none is playing, the TV.
  - It tries a normal stop first, then closes the casting app if the TV keeps
    playing, as the YouTube app did.
  - It reads the TV back before saying it stopped.
  - Before, it only knew the speaker, and Reachy said 「停下來了」 while a home
    video kept playing.
- **Reachy says exactly what happened:**
  - what it stopped;
  - that nothing was playing;
  - that the video did not stop and the remote can stop it;
  - that a stopped video starts from the beginning when played again.
- Live: 6 of 6 stop requests went to `music stop`, and every video ended (NAS
  in ~2 s, YouTube in ~5 s). Nothing-playing and 「暫停」 answers were true.
- The fixed prefix grew by 112 tokens (5,872 → 5,984).

## [1.33.0] — 2026-10-31 · new home videos arrive on their own

Deployed as the thirty-fourth install (wheel sha `c0bd5d93…`). Decision D-041.

- **Each wake picks up the latest video index from the NAS.** The operator's
  nightly Mac pass now rebuilds the index, makes TV copies of new files and
  leaves the index in the share. Reachy reads it in the background at start.
  - It swaps the copy in only when it is newer and sane.
  - It keeps the old copy as `nas-video-index.json.prev`.
  - It never delays the wake.
  - `HANOVA_NAS_INDEX_REFRESH=0` turns it off.
- New folders need no hand edits: the year and place come from the folder name.
- Why: the 2026 Boston trip (with its Canada leg) was on the NAS, but the
  robot's index dated from June, so Reachy said there was no such trip.

## [1.32.0] — 2026-10-31 · Reachy answers when it is called

Deployed as the thirty-third install (wheel sha `5700c98a…`). Decision D-029,
amendment 2026-10-31.

- **Reachy starts in 多人聊天模式.** It answers when someone says its name
  (Reachy, Richie, Ritchie, 瑞奇, 里奇, 小瑞, 瑞曲) or 「停」, and keeps
  answering for 20 s after it last answered. Other speech in the room gets no
  reply. 一對一聊天模式 is still one spoken request away. Before, a morning
  with several people in the room had Reachy answering side conversation and
  being cut off by it about a dozen times in five minutes.
- **A face alone no longer counts as calling Reachy.** People face the robot
  while talking to each other. `REALTIME_PARTY_FACE_GATE=1` restores it.
- The instance `.env` now sets `REALTIME_DEFAULT_MODE=group`, and the
  `set_conversation_mode` text names group as the start mode.
- The fixed prefix grew by 14 tokens (5,858 → 5,872): the group rules block is
  longer than the one-on-one block.
- Live: a fresh session boots as `Tools in session (group, startup, 19)`. The
  gate is unit-tested; real room speech is checked in the next conversation.

## [1.31.3] — 2026-10-30 · Reachy says something while the TV comes on

Fix redeploy of the thirty-second install (wheel sha `f886ff8e…`). Decision
D-040.

- **「把電視打開」 gets a reply right away.** The TV takes 4–7 s to come on,
  and Reachy used to stay silent until it had, because the rules treat home
  control as a quick action. The tool's description now says turning a
  device on can take up to ten seconds and counts as slow work. Live:
  - a short line first in 5 of 5 turn-ons, against 1 of 4 before (for
    example 「好，來開一下電視，稍微等一下。」);
  - none of those lines claimed the TV was on before it was;
  - in a warm session the first sound now comes about 3 s after the request
    instead of about 9 s;
  - turning off stays quick and mostly silent until the answer.
- **The journal shows what a preamble said.** Preamble text stays out of the
  transcript and memory as before, but now gets a `Preamble transcript`
  journal line, so a live check can confirm it doesn't claim a result early.
- The fixed prefix grew by 31 tokens (5,827 → 5,858).

## [1.31.2] — 2026-10-30 · a slow answer is checked, a refusal is named

Fix redeploy of the thirty-second install (wheel sha `bb955ab7…`). Decision
D-040.

- **A command Home Assistant didn't answer in time is still checked.** The
  tool used to say the device was not switched; a slow TV can still come on,
  so it now reads the device back and reports what it shows.
- **A refusal is called a refusal.** HA answering 400 or 401 used to come out
  as "not responding". Now Reachy hears that HA refused the command.
- Installed while Reachy slept. A direct on-robot call against the real TV
  returned `state: off, was_already: off`.

## [1.31.1] — 2026-10-30 · a device is "on" only when it reads on

Fix redeploy of the thirty-second install (wheel sha `73ec8d60…`). Decision
D-040.

- **Reachy checks the device before saying it's done.** `home_control` used
  to report success as soon as Home Assistant answered, and HA answers yes
  whether or not the device moved. It now reads the device before the
  command and again until it settles, and returns that state. A device that
  ignores the command, is offline or isn't known to HA comes back as a
  failure that says what to tell the person. Toggling is judged against the
  state before the call, and a device already in the asked state says so
  (`was_already`). Live: TV on 3/3, off 3/3, already off 3/3, and a device
  Home Assistant doesn't have 3/3, each said truthfully.
- **「把電視打開」 no longer times out.** The TV took 9.0 s to answer turn-on
  in a probe, past the old 8 s limit, so the tool reported an error for a TV
  that did come on. The limit is now 20 s.
- **No LAN address in errors.** An HTTP error used to carry Home Assistant's
  full URL into the model's result and the log. Requests now go through the
  shared HA client, which redacts it, and the result no longer carries the
  entity ID.

## [1.31.0] — 2026-10-30 · Reachy reads less and says it right

Deployed as the thirty-second install (wheel sha `9e6e945b…`). Decision D-039.

- **What Reachy reads before every answer dropped by 41 %** (9,866 → about
  5,800 tokens). Every rule is now written once, where it's needed: tool
  descriptions are shorter and lost their lists of example phrases, the
  persona keeps who Reachy is, and the advice for a failure comes with the
  failure itself.
- **Email read-back is complete.** Reachy reads one sentence naming the
  recipient, the copies, the subject and the whole message before asking to
  send.
- **Failures Reachy can fix, it fixes.** A search in Chinese, or a song query
  that finds nothing, is retried on its own instead of being handed back to
  you.
- **Asking 影片庫 or YouTube is skipped when you've already named one.**

## [1.30.0] — 2026-10-30 · every tool at hand, and the TV turns off by voice

Deployed as the thirty-first install (wheel sha `4382beb5…`). Decision D-039.

- **No more toolboxes.** All 19 tools are always available, so playing a
  video no longer needs an extra step first.
- **Calendar, tasks and Notion are gone**, as decided.
- **Fewer tools to choose from.** One `dance` that also stops any motion,
  one `head`, one `memory` and one `face` replace eight separate tools.
- **「把電視關掉」 works.** The TV is now a home device.

## [1.29.1] — 2026-10-30 · Reachy sees the trips it should ask about

Deployed as the thirtieth install (wheel sha `35379bea…`). Decision D-038.

- **"Which trip?" now offers real trips.** When several trips matched, Reachy
  asked which one but made up the choices (once offering 花蓮 for Japan). The
  list of matching trips never reached it. It does now, and the same fix
  delivers the choices from every tool that asks back: calendar and task
  deletes, unknown home devices.
- **No more "15 seconds left".** After starting a trip Reachy read the number
  of clips left as seconds. The field is now called `clips_left`.

## [1.29.0] — 2026-10-30 · one call puts the trip on the TV

Deployed as the twenty-ninth install (wheel sha `dadb2dc3…`). Plan:
`docs/plans/2026-10-30-nas-one-call-play-plan.md`. Decision D-038.

- **「家裡的影片庫」 now puts the video on in one step.** Reachy always
  looked a trip up before playing it, and sometimes stopped after the lookup
  and only talked about the trip. Asking it to play is now a single step: it
  finds the trip and starts it. When several trips match, it asks which one.
  Asking what's there is a separate step that never plays anything.
- **Searching in Chinese no longer ends in 「查不到」.** The video library is
  catalogued in English. A Chinese word that finds nothing now tells Reachy
  to search again in English, instead of answering that nothing is there.
- **Shorter instructions.** The NAS tool's text went from about 2,000 to 720
  characters and the YouTube tool's from about 1,000 to 560. The prompt
  says the media rule once.

## [1.28.0] — 2026-10-29 · the source you name decides where Reachy looks

Deployed as the twenty-eighth install (wheel sha `52dd3f1a…`), then fix redeploys
1.28.1 (`52dc3577…`), 1.28.2 (`950010c2…`), 1.28.3 (`20ee340a…`), 1.28.4 (`d5295120…`) and 1.28.5 (`c2114015…`). Plan:
`docs/plans/2026-10-29-media-source-routing-plan.md`. Decision D-038.

- **「從 NAS 找」 and 「從 YouTube 找」 are different requests.** The TV tool
  was named after the screen, so after finding a family clip on the NAS,
  Reachy reached for it to put the clip "on the TV" and searched YouTube for
  a file path. The video tools are now named for where a video comes from:
  `youtube`, and `nas` for the family's 影片庫. Both play on the same TV, and
  `draw_picture` puts a new picture there.
- **Reachy asks when it can't tell.** If you don't say where and nothing shows
  whether it's your own video or something public, Reachy asks which one you
  mean. If the source you named has nothing, it says so and offers the other
  one, and switches only if you agree.
- **A YouTube request goes to YouTube.** The 1.27.3 check refused YouTube
  searches whose words matched a home clip's title. It now refuses only a NAS
  file path or file name, so whatever you ask YouTube for is searched.
- **1.28.1 — 「電視上放花蓮的影片」 gets a question, not a guess.** Live, a
  request naming only a place went straight to YouTube. The YouTube tool's
  example 「電視上放周杰倫的 MV」 had the same shape as the request. Both video
  tools now open with the same "ask first" rule for a bare place, trip, year
  or event, and no example leans on the TV.
- **1.28.2 — opening the video tools no longer forbids the question.** 1.28.1
  still went straight to YouTube. The toolbox rule (「載入後同一輪直接呼叫，不要再
  問使用者一次」) told Reachy never to ask after opening a box, which overrode
  "ask first". It now forbids only asking for the go-ahead again, and the
  prompt asks "影片庫 or YouTube?" before Reachy opens the box.
- **1.28.3 — `nas play` no longer picks a clip on its own.** Live, `play` was
  called with only a trip folder (an argument of `play_folder`). That matched
  the whole archive, so the archive's first clip, from Taiwan, went to the TV,
  and Reachy called it the Okinawa trip. Now it plays nothing and says to pass
  a clip's path from `query`, or to use `play_folder` for a whole trip.
- **1.28.4 — after you say 「家裡的影片庫」, Reachy plays it.** Once it had
  asked where to look, Reachy sometimes found the trip and only described it.
  The video tool taught a two-step search-then-play for every request, and
  the request to play was a turn behind. Now it plays a trip in one call,
  searching only when you want to see what's there, and it remembers what you
  asked for when it asks where to look. A clip that starts playing also
  reports which trip it belongs to.
- **1.28.5 — the question comes before the video again.** In live checks of
  1.28.4, one of three sessions skipped the 「影片庫還是 YouTube？」 question
  and played a YouTube travel guide: the new wording 「要放就直接放」 ("just
  play it") read as permission to play at once. The rule now says to wait
  for the answer before calling either video tool, then play from the
  source chosen. Live: no video tool before the answer in 3 of 3 sessions.
  Still open: after the answer, Reachy always searches first, and in 1 of 3
  sessions it said it would play and stopped there.

## [1.27.0] — 2026-10-29 · finding the family's videos the way people ask

Deployed as the twenty-seventh install (wheel sha `4a874796…`), then fix
redeploys 1.27.1 (`20d9ad6f…`), 1.27.2 (`cecd7f1a…`) and 1.27.3 (`394994fa…`). Plan:
`docs/plans/2026-10-29-nas-fuzzy-search-plan.md`. Decision D-037.

- **「日本的影片」 finds the Japan trips.** The video archive is catalogued in
  English, so Reachy now translates what you say before searching, and when
  a search still misses it gets the whole list of trips to choose from
  instead of telling you the video does not exist. Live: 日本, 女兒小時候,
  去年, 看企鵝那次 and 沖繩 all found the right trips.
- **Every matching trip is mentioned.** Long answers used to stop at 40
  clips, so 2025 sounded like Brisbane and Nagoya only; now Reachy hears
  every trip that matched (Brisbane, Nagoya, Sri Lanka, UK).
- **Loosely typed folder names match.** "chiang mai", "sri lanka" or
  "tokyo okinawa" find their trips however the folder was spelled.
- **1.27.1 — typing to Reachy no longer garbles the conversation.** A
  message sent from the web UI or the phone was written to the OpenAI link
  from the wrong thread and could corrupt it; it now goes through the
  conversation's own loop.
- **1.27.2 — home videos are played from the NAS, not looked up on
  YouTube.** Reachy had once sent a home clip's name to the YouTube search
  and played a stranger's video; the two TV tools now explain to the model
  which one plays which.
- **1.27.3 — a home clip handed to the YouTube search is sent back to the
  NAS.** The 1.27.2 explanation was not enough: on the retest Reachy found
  the Okinawa clips and again passed one to the YouTube search. Now the TV
  search refuses a clip it recognises from the archive (by its path, file
  name or title) and tells Reachy to play it from the NAS. A plain place
  name still searches YouTube. Reachy also no longer asks for a file name
  before searching the home videos.

Robot configuration (no release): the Home Assistant address and the TV's
Home Assistant entity in the robot's settings were updated after Home
Assistant moved on 2026-10-25; until then every home, TV and NAS request
answered that the robot could not tell whether it was at home.

## [1.26.0] — 2026-10-29 · MCP v2, leaner web results

Deployed as the twenty-sixth install (wheel sha `69b0b60b…`, backup
`20261029T044542Z-12819`), clean boot 04:46 UTC robot time. Plan:
`docs/plans/2026-10-29-mcp-v2-search-trim-plan.md`. Decision D-036.

- **Web search results cost the model about 60% fewer tokens.** Each result
  used to arrive twice, plus four fields naming the tool that was just
  called; now the model reads it once (a live Taipei-weather search: 1,700 →
  729 characters). Nothing the model could use is removed: failures still
  say what went wrong, and images or other non-text content still come
  through.
- **The MCP client moves to the v2 SDK**, as in the official app: it speaks
  the 2026-07-28 MCP spec and every older revision, and stays compatible with
  the official conversation app's current release in the shared app
  environment. Remote MCP connections verify against the app's own, current
  CA bundle, like the OpenAI link.

## [1.25.0] — 2026-10-28 · data folder, Wi-Fi recovery, faster ears

Deployed as the twenty-fifth install (wheel sha `091137c8…`, backup
`20261028T142759Z-5241`), clean boot 14:29 robot time; first start migrated
7 items to `~/.config/reachy_companion`.

Plans: `docs/plans/2026-10-28-instance-path-plan.md`,
`…-wifi-recovery-plan.md`, `…-latency-observability-plan.md`,
`…-local-vad-listening-plan.md`. Decisions D-034, D-035.

- **Reachy's memory now lives outside the app.** Settings, persona, enrolled
  faces, people, Google and Drive grants and the NAS index move once, on the
  first start, to `~/.config/reachy_companion` on the robot. Reinstalls and a
  Bluetooth software reset no longer touch them, and deploys no longer need a
  restore step.
- **The antennas react the moment you start talking.** A small voice detector
  runs on the robot's own microphone (Silero, ported from the official app),
  so the listening pose starts on your first syllable instead of after a
  round trip to OpenAI. It stays out of the way while Reachy itself talks.
- **An outage no longer floods the robot's log.** When OpenAI is unreachable
  the app retries at growing intervals (5 s up to 60 s) and warns once per
  10 minutes, so the log keeps the evidence of what went wrong.
- **Wi-Fi recovery (opt-in).** A watchdog checks the network every 2 minutes,
  even while Reachy sleeps, and reconnects or restarts the Wi-Fi radio when
  the router stops answering (at most 4 times an hour). It runs once the
  operator adds its crontab line (D-034).
- **Answers about long conversations and web results cost fewer tokens.**
  Tool results reach the model as plain Chinese text instead of `\u` escapes.
  Each reply now logs its token usage, and two opt-in switches
  (`REALTIME_TRUNCATION_POST_INSTRUCTIONS`, `REALTIME_IMAGE_DETAIL`) are ready
  for the latency A/B; nothing changes until they are set.
- **Camera colours are right** — fixed by the SDK 1.13 upgrade (standard
  YCbCr JPEGs, checked on the robot).

## [1.24.0] — 2026-10-28 · upstream refresh

Deployed as the twenty-fourth install (wheel sha `1d1c98aa…`, backup
`20261028T130625Z-2812`), clean first boot 13:07 robot time, on the robot's
daemon upgraded the same day from v1.10.0rc5 to v1.13.0 with telemetry off
(D-033). The app now requires `reachy-mini>=1.13.0`.

Ported from the official Conversation App v1.1.1 and SDK v1.13.0 (plan
`docs/plans/2026-10-28-upstream-refresh-plan.md`):

- **Trusts certificates from the app's own, current CA bundle.** After the
  emSign CA compromise, the app requires `certifi>=2026.10.19`, and the OpenAI
  realtime link verifies against that bundle instead of the robot's system
  store.
- **A dropped Wi-Fi link no longer leaves the robot deaf.** A microphone send
  stuck for 5 seconds now drops the connection, and the robot reconnects at
  once instead of waiting minutes for TCP to give up.
- **No false start at boot.** Microphone audio captured while the audio
  pipeline warms up is discarded, so startup noise cannot open a turn.
- **Steadier listening.** The microphone is read on its own thread, so a slow
  read no longer delays playback, the realtime events or the barge-in timers;
  and the antennas can no longer stick in the listening pose after a quick
  on-off-on.
- **Failures are reported as failures.** A remote MCP tool call that errors
  (e.g. Notion, web search) is now recorded as failed, with the server's
  message, instead of as completed.
- **A damaged tool manifest costs only the remote tools.** A corrupt or
  half-written tool-Spaces manifest is logged and skipped instead of leaving
  the robot with no tools at all.
- **Faster first answer after boot** (heavy imports warm up during robot init)
  and a web console that reconnects when it loads before the app is ready.

## [1.23.0] — 2026-09-05 · solo interruption

Deployed as the twenty-third install (wheel sha `70516de8…`, backup
`20260905T042941Z-70561`), clean first boot 05:30 robot time.

Design record D-032, from `docs/rca-solo-interrupt-2026-09-04.md`: in the
2026-09-04 session, 19 of 22 attempts to interrupt failed, and the old reply
played out before the new answer.

- **In 一對一聊天模式, anything you actually say stops the reply.** Until now
  only the robot's name or 「停」 could interrupt it; 「等一下」 or 「你就播吧」
  left it talking. In a one-person room there is nobody else you could be
  talking to, so any real sentence now stops it — a 「嗯」 or a cough still does
  not. `REALTIME_SOLO_NAME_GATE=1` restores the old, story-telling behaviour for
  a noisy room; 多人聊天模式 and 紀錄模式 are unchanged.
- **A long sentence stops it too.** Speech that runs past about 1.6 seconds now
  stops the reply on the spot, instead of waiting for a transcript that cannot
  arrive in time and letting the reply resume over you at four seconds.
- **It remembers what you heard, not what it was going to say.** An
  interruption cuts the robot's own memory of the reply at the point that
  actually reached your ears, so asking 「你剛剛講到哪」 afterwards refers to the
  part you heard — and the conversation carries on from your latest sentence,
  never from where the old reply left off.
- **Interrupting stops whatever is speaking.** A follow-up after a tool, an
  earlier answer still playing, the wake-up greeting: all of them stop now,
  instead of going quiet for a moment and then finishing themselves.
- **It answers you once.** The repair that covers an interrupted turn no longer
  races the turn's own answer, so the same sentence is not answered twice.
- **Saying the name mid-reply is fast again.** The name showing up in a partial
  transcript stops the reply immediately in every mode, not just in the
  name-gated one.
- **Reachy now boots into 一對一聊天模式.** Operator instruction 2026-09-04
  (D-029 decision 5, amended): the code default, the `set_conversation_mode`
  description and the dead-knob warning all follow; `REALTIME_DEFAULT_MODE=group`
  restores the room posture. The robot's instance `.env` already carries the
  new value, so the installed 1.22.0 wheel boots solo before this ships.

### For contributors

- The RCA this release answers: `docs/rca-solo-interrupt-2026-09-04.md` — why
  solo-mode interruption was hard (name gate + a pause cap that fires before
  the transcript exists) and why the old reply played out before the new answer
  (rollback resumes fully-buffered audio; the new answer queues behind it),
  plus first-audio latency growing 2 s → 10 s over a session, which is NOT
  addressed here.
- New INFO journal lines: `late solo interrupt declined (audible=<bool>,
  verdict=<reason>)` — the missing evidence for RCA Finding 3's open case —
  `solo barge: cancelling a newer response (<id>) the user talked over`, `late
  solo interrupt held: the barge watchdog already answered this turn`,
  `accepted turn already answered by the barge watchdog`, and `ignoring tool
  call from cancelled response <id>`. `conversation.item.truncate refused` moves
  from DEBUG to INFO: that refusal is the one case where the unheard tail
  survives in the model's context.
- `late solo interrupt (…)` gains `substantive` as a reason; the pause and the
  late path now share one `_solo_interrupt_verdict`.
- Barge state that used to be session-wide is stamped per input item —
  late eligibility, the pause's utterance, and which utterance the repair
  watchdog answered — because `transcription.completed` can land after the next
  utterance has already started.
- Plan and review log: `docs/plans/2026-09-05-solo-interrupt-plan.md` (rev 3;
  two Codex rounds, 15 findings, 15 accepted, 0 rejected).

## [1.22.0] — 2026-09-02 · calibration and tool-surface symmetry

Deployed as the twenty-second install (commit `5d0e9e2`, wheel sha `4ba4e698…`).
Design record D-031 (addendum). A same-day follow-up to 1.21.0 after the
operator asked whether the 700 ms hold-off has a research basis (it did not).

- **It now measures its own pauses.** Every time the hold-off merges a
  fragment with its continuation the journal records how long the pause really
  was and how long the robot had been holding; and when someone resumes
  speaking within two seconds after a window already expired, it says so. A
  handful of real turns will show whether 700 ms should move.
- **Its tools say what they are not for.** Eleven always-on tools that had a
  one-line description now carry matching use-when / do-not-use pairs, so
  emotion versus dance, stop-emotion versus stop-dance, remember versus forget,
  and status versus cancel are told apart by the description rather than by
  luck.
- **It varies how it says things.** A new rule asks for different lead-ins and
  acknowledgements from turn to turn, because the lead-ins are audible now and
  the same 「我查一下」 every time sounds like a recording.

### For contributors

- `docs/research-holdoff-calibration-2026-09.md` records the external evidence
  for the window and the audit of the repo's research docs; D-031's addendum
  lists the seven next-wave candidates in priority order.
- New INFO journal lines: `turn hold-off: awaiting continuation (…) gap=<ms>
  held=<ms>` and `turn hold-off: late continuation <ms> ms after the window
  (window=<ms> ms)`.

## [1.21.0] — 2026-09-02 · the field-test fix wave

Deployed as the twenty-first install (commit `c3e46cf`, wheel sha `a96e06ed…`).
Design record D-031. Fixes three of the six findings from the 2026-09-01
field test, in the operator's order.

- **It waits for you to finish the sentence.** Mid-sentence pauses were being
  committed as turns — 「你。」「就是。」 got answers in under half a second, and
  the rest of the sentence got a second answer. After it accepts a turn the
  robot now holds its reply for a short window; if you keep talking, the
  fragment and the continuation are answered together. If what followed was
  only a cough, the original question is still answered. The window is
  `REALTIME_COMMIT_HOLDOFF_MS` (default 700 ms; 0 restores the old behaviour).
- **It tells you it is looking before a slow lookup.** Web searches used to
  mean eight to ten seconds of silence because every pre-tool remark was
  swallowed on purpose. Those remarks are audible again, the instructions say
  where they belong (before slow work — search, finding music, remote services
  — and not before quick moves), and they never end up in the room log or the
  sleep-time memory as if they were answers.
- **It sends "play that" to the speaker, not to the search engine.** The
  search and music tools now name the boundary between them, and YouTube
  playback requests belong to `music`.
- **It reads the "if you did not hear it, ask" rule last.** Moved to the end
  of the instructions, restated positively, so it is the freshest rule at the
  moment a fragment arrives.
- **「睡覺吧」 ends cleanly.** The robot's own daemon hangs up on the stop
  request a moment early, and that used to be mistaken for a failure that
  switched the microphone back on seconds before the app died. The early
  hang-up is now understood as the stop succeeding, the microphone stays off
  once sleep has begun, and a stuck motion writer can no longer abort the
  pose or the shutdown cleanup.
- **It gets a second chance to remember the visit.** The end-of-visit summary
  was a single eight-second call; one timeout lost the memory. It now retries
  once, briefly.

### For contributors

- New env knob `REALTIME_COMMIT_HOLDOFF_MS`; documented in `.env.example` and
  the README table.
- Commentary-phase items: audio passes, transcript withheld. The journal lines
  changed accordingly (`commentary-phase item … is audible; transcript
  withheld`, `withholding commentary-phase transcript for item …`); the old
  `suppressing …` / `dropping commentary-phase audio …` lines are gone.
- Bundled tool-space specs override a manifest's cached description at read
  time, so description edits reach a robot that already has a manifest.
- `persona.md` and the locked profile changed one line each; the instance
  persona must be re-synced at deploy (it was, sha in `progress.md`).
- `request_stop_current_app` catches `URLError`, `HTTPException` and `OSError`
  and logs the exception type; the C6 unmute recovery covers only pre-pose
  failures; the sleep summary retries a `TimeoutError` once with a fixed 4 s
  budget.
- Plan and review log: `docs/plans/2026-09-01-field-test-fixes-plan.md`
  (rev 3, two Codex rounds under the new 2-round cap, 14 findings, 0 rejected).

## [1.20.0] — 2026-09-01 · the LLM-first instructing wave

Deployed as the twentieth install (commit `…`, wheel sha `…`).
Design record D-030.

The rule this release is built on: the model decides what to say and which tools
to call; the app instructs it and holds the safety rails.

- **It says goodbye before it lies down.** 「睡覺吧」 used to produce a tool call
  with no words at all, and the robot posed in silence. Now the sleep tool only
  mutes the microphone and hands the turn back; the goodbye gets a response of
  its own that nothing else can ride along with, and the body waits for that
  sentence to finish playing before it moves. The inactivity timeout, which has
  nobody to say goodbye to, still just lies down.
- **Its head actually turns when you tell it to look somewhere.** 「看右邊」 sent
  the right command every time and the daemon's face tracker overrode it within
  a frame, so the photo came back showing whoever was straight ahead. Manual head
  moves now hold the head against face-following for as long as they need it, and
  hand it back exactly as they found it — off stays off.
- **It stops guessing at bad arguments.** A direction it does not recognise, the
  string "false" where a yes/no belonged, a toolbox that does not exist: all of
  them used to be quietly coerced into *something*. They now come back as a
  correction naming what is allowed, so the robot can fix its own mistake in the
  same breath.
- **It stops claiming things it has only started.** "Looking right" was said the
  moment the movement was queued. The robot now reports what is actually true —
  the direction it asked for — and the prompt tells it that is what to say.
- **It reads one set of instructions instead of two.** The character file said
  "use the calendar", the system rules said "open the toolbox first"; the
  character file said "follow whatever language they use", the system rules said
  "stay in Taiwanese Mandarin". One authority each now, all in Taiwan Traditional
  Chinese, with the sentence-count rules replaced by "length follows content".
- **What it remembers about you is labelled as background, not as orders** — and
  says out loud that what you tell it now wins.

### For contributors

- The `finish_session` rename is an A/B alias, not the shipped name. It is
  exposed only when `INSTRUCTING_FINISH_SESSION_ALIAS` is set; the default
  surface still carries `go_to_sleep`.
- Two controller fix loops are part of the reviewed state: the Task 3 farewell
  tests now patch the dispatcher's imported `core_tools` module rather than a
  reloadable string path, and Task 12 reconciled the commentary-only test with
  the request-scoped response waiter while removing the last stale
  `run_go_to_sleep_tool` comment.

## [1.19.0] — 2026-08-31 · the conversation-modes wave

Deployed as the nineteenth install (commit `…`, wheel sha `…`).
Design record D-029.

Reachy now has three ways of being in a room, and you switch between them by
saying so.

- **It wakes up in 多人聊天模式.** In a room with several people it listens
  quietly and answers when you say its name — the failure it used to have was
  waking up ready to answer every overheard sentence. 「切到一對一聊天模式」
  gives you the old always-answering behaviour back when it is just the two of
  you, and 「進入紀錄模式」 turns it into a silent scribe that writes the whole
  meeting down and reads a summary back when you ask (「瑞奇幫我總結」). That
  record lives in memory only and is wiped when you leave the mode or go to
  sleep.
- **It stops answering things you did not say to it.** Every reply is now the
  robot's own decision rather than the server's reflex, which also kills the
  double answer: talk over Reachy and the sentence it was saying resumes — no
  second full answer queued behind it.
- **It turns its head when you tell it to look somewhere.** 「轉到右邊去看看
  有誰」 moves first and describes second, instead of describing whatever was
  already in front of it.
- **It finishes saying goodbye before it lies down.** 「睡覺吧」 mutes the
  microphone, lets the goodbye play out, and only then takes the sleep pose —
  no more cut-off farewells, and no turn opened by the goodbye's own echo.
- **It picks the right tool more often, because it sees 22 instead of 41.**
  The calendar/to-do/drive/email/Notion family and the TV/NAS-video family load
  the moment a request needs them, six overlapping tool families became one
  tool each, and three tools nobody used were retired. Music stays always
  loaded, so 「音樂關掉」 never has to wait for anything.
- **And it stops muttering to itself.** The 2.x model's 「讓我想想…」-style
  preambles are dropped before they reach the speaker.

### For contributors

- `ConversationMode` enum + `set_conversation_mode` replace the `party_mode`
  boolean; `REALTIME_DEFAULT_MODE` replaces `REALTIME_PARTY_DEFAULT`, which is
  now a dead knob that warns. New: `REALTIME_ONE_ON_ONE_ANSWER_GATE`,
  `RECORD_SUMMARY_TIMEOUT_S`, `SLEEP_GOODBYE_DRAIN_CAP_S`.
- All live `session.update`s go through one ordered, acknowledged,
  single-flight mechanism with an unmatched-acknowledgement debt counter — mode
  flips and toolbox opens await it, so the model never speaks under a session
  shape the server has not applied yet.
- Three Codex review rounds, 45 findings, 45 accepted. Suite 1746/30.

## [1.17.0] — 2026-08-31 · the human-like-conversation wave

Deployed as the seventeenth install (commit `26573f0`, wheel sha `c5cccfa0…`).
Design record D-028.

Reachy now handles a room full of talk the way a person telling a story does.

- **Say its name to interrupt.** While Reachy is speaking, only 「瑞奇」/"Reachy"
  (or a stop phrase — 停/閉嘴/stop, which always win) takes the floor away.
  A cough, an 「嗯」, or a sentence aimed at someone else just pauses the reply
  for a beat, then it carries on. Talk past it for more than ~4 seconds and it
  resumes right through the side conversation — and still yields if the name
  turns out to have been said (`REALTIME_SOLO_NAME_GATE`,
  `REALTIME_BARGE_MAX_PAUSE_MS`).
- **It stops rushing you.** End-of-turn silence window raised to 1 second, and
  the session pins `reasoning.effort=low` so replies start fast and stay fast.
  The `semantic_vad eagerness=low` "wait until you actually sound finished"
  mode is one env flip away for a live A/B.
- **It remembers only what you heard.** Every committed interruption now sends
  `conversation.item.truncate`, so a reply you cut off no longer lives on in
  the model's memory as if it had been finished — the thing that made Reachy
  refer back to explanations nobody heard.
- **It talks like a friend, sized to the moment.** The prompts teach length
  calibration (one-line answers for one-line questions, real explanations when
  the topic deserves them) and ban the filler: preambles (「讓我想想…」),
  repeating what you just said, unasked background. No sentence caps — a
  900-token safety rail (`REALTIME_MAX_OUTPUT_TOKENS`) only catches runaway
  monologues, loudly.

### For contributors

- Client-side barge decisions gained a late-interrupt path with a careful
  response-id lifecycle (eligibility captured at speech onset), partial-
  transcript fast commit, and heard-audio accounting for truncate
  (stash-at-pause, always rounded down). 100+ new tests; suite 1571/30.
- `REALTIME_BARGE_CONFIRM_MS` is now gate-off-only; the confirm-vs-silence
  startup warning is semantic-VAD-aware (fixes a recorded defect).
- New knobs documented in `.env.example` and the README table.

## Earlier installs (retroactive)

- **1.16.0 — 2026-08-30 · engagement memory** (D-027): the sleep-time
  last-chat summary (「上次聊天…」 written per recognized person when you send
  Reachy to sleep by voice), open-loop-first memory guidance, and the Mac-side
  `consolidate.py` store tidy-up CLI.
- **1.15.0 — 2026-08-28/29 · person memory + Mac backend** (D-025, D-026):
  per-person fact stores with personalized wake greetings, still-pose face
  enrollment with snapshots, and the `companion_backend/` FastAPI app for
  managing people/photos/facts from a Mac, with guarded push/import/merge.
- **1.14.0 — 2026-08-27 · face-recognition fix wave** (D-024): identity
  routing (「你記得我嗎」 goes to `who_is_this`, never the camera), the
  extended wake window for late recognition, threshold retuning.
- **1.13.0 — 2026-08-27 · voice robustness** (D-023): the pause-then-decide
  solo barge-in with false-interruption rollback, `wait_for_user` prompt
  hardening, far-field noise reduction, `gpt-transcribe` with keyword biasing.
- **1.12.0 — 2026-08-24/25 · multi-person & media hardening** (D-022, party
  plan): party mode (address-gated answers in a group), TV-cast churn fixes,
  music barge-in coordination.
- **1.9.0 – 1.11.0 — 2026-08-21/23 · the tool wave** (D-018…D-021): the
  HomeAssistant-Nova port — 39 tools spanning home control, music, TV/NAS
  video, calendar/tasks/email/Notion/Drive with spoken confirmation gates —
  persona v2, the coral V13 voice, static tool registration for latency.
- **1.7.0 – 1.8.0 — 2026-08-20 · character** (D-016, D-017): the persona
  externalized to an editable on-robot `persona.md`, and the VoiceFX
  comb/soft-knee chain behind Reachy's voice.
- **1.2.0 – 1.6.0 — 2026-08-17/19 · memory, faces, first hardening**
  (D-009…D-015): first on-robot deploys and the backup/restore ritual, WSOLA
  pitch shift, `remember`/`forget` fact memory, on-device face recognition
  with enrolment-by-name, the adversarial-audit fix wave.
- **1.0.0 – 1.1.0 — 2026-08-16 · foundation** (D-001…D-008): scaffold from
  Pollen Robotics' official conversation app, the `gpt-realtime-2.1` backend,
  the locked Chinese-first profile.
