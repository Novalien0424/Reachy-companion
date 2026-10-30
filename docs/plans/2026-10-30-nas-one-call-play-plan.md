# NAS: one call plays, shorter media surface — plan (2026-10-30)

Status: built and installed as 1.29.0 (wheel `dadb2dc3…`), 2026-10-30
06:08 UTC. All checks passed after a daemon restart: V1 3/3, V2, V3 (D-038).
V4 failed at first because error results lost their `candidates` in
transport. Fixed in 1.29.1 (wheel `35379bea…`), and V4 passed 2/2 after
that. 1.29.1 also renamed `remaining` to `clips_left`, after Reachy read the
count as seconds.

Operator, 2026-10-30: "I have to run on mini model, you need to consider the
mini model limitations and keep your tools correct/concise and fit the model
without way too much instructions."

## What the mini model does (live, 1.28.3–1.28.5)

- **The answer turn searches first.** After 「家裡的影片庫」 it ran `nas query`
  first in 6 of 6 sessions. The 1.28.4 recipe ("play in one call with
  `play_folder` + `place`") was never used.
- **Every miss is a stop between two calls.** Both follow-through failures
  ended right after `query`, once with 「接下來我就幫你開始播」 and no call.
- **The surface pushes toward searching:**
  - `query` is the only action that fits a loose request. `play` is "one
    clip", and `play_folder` reads as needing a folder name.
  - The `open_toolbox` media line says "open it and search".
  - The `nas` description is 2,038 characters of rules.
- **An untranslated word ends the search.** 「沖繩」 → 0 matches → 「查不到」,
  although `all_trips` listed the trip.

## Design (rung 1, the tool)

Fit the tool to what the model does: the call it reaches for first finishes
the job, and the text gets shorter.

1. **`nas` has three actions: `play`, `list`, `skip`.**
   - `play` finds and plays in one call, from `place`/`year`/`keyword`
     (English) or from a `path`/`top_folder` an earlier result gave.
     - One trip matches: it plays the first matching clip and queues the
       rest of the trip.
     - Several trips match: it returns `candidates` (one summary row per trip)
       and casts nothing. The error says "several trips match; ask which one,
       then play it by top_folder".
     - Nothing matches: the miss result (item 2).
     - `play_folder` merges into `play`, and `nas_play_folder.py` is deleted.
   - `list` (was `query`) says what is there and plays nothing.
   - `skip` is unchanged.
2. **A miss answers with what fixes it.** Both actions share it.
   - Chinese search words that match nothing →
     `error: "nothing is catalogued under 「…」: the archive is in English, so
     search again in English"`.
   - Any other miss → `error: "no_match"` plus `all_trips`.
   - The test is an observable fact (CJK characters in the words), not a
     word list. `path` and `top_folder` are exempt, because real folder
     names contain Chinese.
3. **Short descriptions.**
   - `nas` drops from about 2,000 to about 800 characters: use-when, ask
     first, do-NOT-use, one line per action, English search, and "offer
     YouTube when nothing fits".
   - `youtube` drops from about 1,000 to about 650 characters. The
     mirrored ask clause becomes "ask 影片庫 or YouTube and wait for the
     answer, then do what they asked".
   - `limit` leaves the schema. The parameter text is one short line each.
4. **No push toward searching elsewhere.**
   - The `open_toolbox` media line drops "open it and search".
   - The prompt's two media bullets become one: open the box, call the
     source's tool when the source is clear, otherwise ask, and act once
     they answer.

Rejected:
- Auto-play after `list` (code deciding intent, D-030).
- A `play: true` flag on `list` (`play` already is that).
- More description text (the measured failure is too much of it).

## Verification

- **Unit tests:**
  - `play` with `place`: one trip → casts and queues; several → `candidates`,
    no cast; miss → `all_trips`.
  - Chinese miss → the translate error, without `all_trips`.
  - `play` with `top_folder` → the trip from its first clip.
  - No selector → error.
  - The action enum is `play`/`list`/`skip`; the description is under the
    old length; the ask clause stays mirrored.
  - Gate: pytest, ruff and mypy, each with its exit code.
- **Live** (fresh session per run, mic muted, TV off after each cast):

| Check | Request | Pass when |
|---|---|---|
| V1 ×3 | 「電視上放沖繩的影片」 → 「家裡的影片庫」 | asks first; the answer turn plays (`play`), the trip named from `top_folder`; 3/3 |
| V2 | 「家裡的影片庫有沖繩的影片嗎？」 | `list` only, and the trip is found (after a translate retry, if needed) |
| V3 | 「幫我從NAS找沖繩的影片，放一段到電視上」 | playing |
| V4 | 「從影片庫放日本的影片」 | `candidates` → Reachy asks which trip; nothing cast |

## Review log

1. **Reuse-first.** Not robot-facing (no SDK or app layer). The merge reuses
   `filter_index`, `trip_summary`, `folder_playlist` and `stage_and_cast`
   unchanged.
2. **D-029 decision 7.** The family still validates only `action`. The
   merged logic lives in the `play` delegate (`play_nas_video.py`), after its
   prerequisite and home checks, so the order of answers is unchanged.
3. **Prerequisite rows.** `nas_play_folder` leaves `TOOL_PREREQS` and the
   `nas` group. The row was identical to `play_nas_video`'s, so no
   configuration loses coverage.
4. **R10.** No real archive place name enters a description. The error
   string quotes the user's own words at run time.
5. **Session state.** `play` with `top_folder` starts the trip session at
   clip 0, as `play_folder` did. `play` from filters starts at the clip's
   position, as `play` did. `skip` is unchanged.
6. **Tool count** unchanged (22 / 25 / 27 / 30). `persona.md` names no
   action.
7. **Ask placement.** The ask now sits after the box opens: the prompt's one
   media bullet and the tool descriptions. 1.28.5 run 3 asked before
   opening, which made the answer turn a three-call chain.
