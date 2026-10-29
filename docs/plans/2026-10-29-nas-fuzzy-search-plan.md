# NAS search that survives loose, Chinese requests — plan (2026-10-29)

Operator request (2026-10-29): "search NAS in conversation in a fuzzy way as
intended". Intent of record (original `nasvideo/query.py`): *the LLM extracts
year/place/keyword; the tool does the lookup.*

## Observed (live session, v1.26.0, `conversation.say`)
1. 「我們有沒有在日本拍的家庭影片？」 → `nas query keyword="日本"` → count 0 →
   Reachy said there are none. The index is catalogued in English ("Japan",
   10 trips). Same for 「女兒小時候的影片」 → `keyword="女兒"` → 0, while a
   41-clip folder has place "daughter's growth".
2. 「去年拍的影片」 → `year 2025` (relative date resolved) → 40 clips, the
   cap, sorted by folder: the model saw Brisbane and Nagoya and never learned
   2025 also holds Sri Lanka and UK.
3. Matching is one raw substring: "chiang mai" misses `2024 Chiang Mai`
   only by luck of spacing; "Tokyo Okinawa" misses "Tokyo & Okinawa".

Rung 1 (the tool) for all three; nothing in the prompt.

## Change
- `hanova/nas.py`: matching ignores case, spacing and punctuation and needs
  every word of the needle somewhere in the record (any order); the keyword
  blob also covers `category` and `year`. `trip_summary()` groups matches
  by trip.
- `nas_video_query`: `count` = every matching clip; `trips` = every
  matching trip (folder, year, place, country, clips); `videos` = the first
  `limit` clips as before. No match → `all_trips`, the whole catalogue.
- `play_nas_video` / `nas_play_folder` `no_match` → also `all_trips`.
- `nas` family description + `place` / `keyword` parameter descriptions:
  the archive is catalogued in English, translate first (examples use places
  not in the archive, R10); on no match pick from `all_trips`.

## Verify
Unit tests per rule; suite, ruff, mypy. On robot, the same three sentences:
the model reaches the Japan trips (directly in English or through
`all_trips`), the daughter folder, and all four 2025 trips; then one
「播…」 cast that HA confirms (`playing`/`buffering`) on the TV.

## Self-review log
1. *Code for a behavior fix?* The failure is the tool's: a catalogue in a
   language the tool never states, and a return that hides trips. Rung 1
   (description, return shape, matching), not the prompt, not a zh→en table
   in code (a keyword list; rejected). Accepted.
2. *R10 (no real archive places in descriptions)*: examples are 巴黎 →
   Paris and 生日 → birthday, neither in the index. Accepted.
3. *Token cost*: `all_trips` only on no match (51 rows, ~2k tokens);
   `trips` is at most one row per trip. Accepted.
4. *AND-of-words stricter than before?* A single-word needle matches
   whenever the old substring did (squashing only removes separators);
   multi-word needles that used to need exact adjacency now match in any
   order. Filler words can still fail an AND; the model then gets
   `all_trips`. Accepted.
5. *Robot config drift found on the way* (not code): `HA_URL` still pointed
   at HA's pre-2026-10-25 address and `HANOVA_CAST_ENTITY` at `…_3`, gone
   since HA's 10-25 rebuild (D-022 runbook case). Both instance `.env` lines
   repointed with dated backups beside the file. Recorded in D-037.
6. *1.27.2 retest (after the daemon restart): the description mirror did not
   hold* — the model passed a found clip's path to `tv play_video`. Still
   rung 1, one step down: the tool validates its argument and refuses a
   query that names a NAS clip with a corrective error naming `nas play`
   (the skill's "errors are advice addressed to the model"), not a silent
   redirect. Kept narrow on purpose: exact names, or a file name with its
   extension inside a phrase; a bare place stays a YouTube search. Checked
   against the real index on the robot (4/4 observed, 0/15 ordinary).
   Accepted (v1.27.3).
7. *R10 for the `open_toolbox` line*: it names no archive place. Accepted.
