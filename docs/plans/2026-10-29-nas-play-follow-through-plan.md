# NAS play follow-through — design (2026-10-29)

Status: built and installed as 1.28.4 (wheel `d5295120…`) on 2026-10-29.
Live-checked 2026-10-30 (results in D-038). The checks found an ask-first
regression from this plan's 「要放就直接放」, fixed in 1.28.5. Since 1.28.4,
follow-through after the source question is 4 of 5. The model never used
the one-call recipe: the answer turn ran `query` first in 6 of 6 sessions.
Superseded by `2026-10-30-nas-one-call-play-plan.md` (1.29.0). The operator runs
on mini only, so the tool was fitted to the model instead.

## The failure

1.28.3, fresh sessions, request 「電視上放沖繩的影片」:
1. Reachy asks the source (correct).
2. The user answers 「家裡的影片庫」.
3. Reachy calls `nas query` Okinawa.

What happened next differed by run:
- **Run 1:** it described the trip's highlights and stopped. Nothing played.
- **Run 3:** `nas play` with a path → playing (correct).
- **Earlier, 1.28.2:** `nas play` with only `top_folder`, which cast the wrong
  clip (now refused, 1.28.3).

## Diagnosis

- **The play request is a turn behind.** The answer turn holds no play verb,
  so the model must carry 「電視上放」 across the question it just asked.
- **The tool surface makes every play a two-step chain.** `path` says "Exact
  clip path from a call with action=query. Preferred." `top_folder` says
  "Folder name from a call with action=query. Preferred." Both teach "query,
  then play". After step 1 the model holds a result it can talk about, and
  stopping there is the mini tier's known weak spot: reachy-instructing-model
  says no reliable tool chaining, so compose sequences into one call.
- **The model wants a single call.** In the 1.28.2 run it already reached
  for one-step `play` + `top_folder`.
- **The one-call path exists, but nothing teaches it.** `play_folder` with
  `place` resolves the trip, starts it from the first clip, queues the rest
  for `skip`, and returns `candidates` instead of guessing when several trips
  match. On the robot's real index: Okinawa → 1 trip, Tokyo → 3, Japan → 9.

## Design (best fit for gpt-realtime-2.1-mini)

**Rung 1, the tool:**
1. **Recipe by intent in the `nas` description** (it replaces the "Pick
   `action`" list).
   - To put videos on the TV, play in one call: `play_folder` with `place`
     (or `year`) for a trip, or `play` with `keyword`/`place` for one
     particular clip. Pass `path` or `top_folder` only when an earlier result
     gave one.
   - `query` is for when the user wants to know what is there or to choose;
     it is not a step before playing.
   - `skip` moves on.
2. **Parameter text stops steering to query-first.**
   - `path`: "Exact clip path, as `query` returns it."
   - `top_folder`: "Exact trip folder name, as `query`, `candidates` or
     `all_trips` give it."
   - "Preferred" is dropped from both.
3. **`play` success names its trip.** It adds `top_folder`, as `play_folder`
   already does, so the trip Reachy names comes from the result and not from
   the request (the 1.28.2 run said "Okinawa" over a Taiwan clip).

**Rung 2, the context:**
4. **Carry the request across the question.**
   - The mirrored ask-first clause (`youtube` and `nas`) ends: "…call neither
     tool until they answer, then do what they first asked: if it was to put
     something on, play it."
   - The prompt's media rule says the same: 「聽到回答就照原本的要求做：要放就直接放」.

Rejected:
- Code that auto-plays after `query`. That has code deciding intent, which
  breaks D-030's LLM-first rule.
- A `next_step` field in the query result. Tool output holds no authority
  (Model Spec), and policy belongs in the description.
- A new "find and play" action. `play_folder` + `place` already is that
  composite, and another action grows the enum.
- Raising reasoning effort, or switching to the full-tier model. Neither
  comes before rungs 1–2, and both are A/B-gated.

## Verification

**Unit tests:**
- no "Preferred" or "from a call with action=query" in the `nas` schema;
- the recipe names `play_folder` with `place`;
- `play` returns `top_folder`;
- the ask-first clause stays mirrored and carries "then do what they first
  asked".

**Live:** fresh session per row, mic muted, each stop's body kept. If the
daemon wedges, stop and report; restarting needs a new authorization.

| Check | Request | Pass when |
|---|---|---|
| V1 ×3 | 「電視上放沖繩的影片」 → question → 「家裡的影片庫」 | A `nas play`/`play_folder` in the answer turn, the Okinawa trip `playing`, and the spoken trip = the returned `top_folder`. 3/3 required. |
| V2 | 「家裡的影片庫有沖繩的影片嗎？」 | `query` only, no play (browse intent kept). |
| V3 | case 1 「幫我從NAS找沖繩的影片，放一段到電視上」 | `playing`, in one or two calls. |

## Review log

1. **R10.** The recipe names no place, so no real archive place name gets in.
2. **D-029 decision 7.** Only description strings change in the delegate
   schemas. The `top_folder` return is a new fact, not validation; the order
   of prerequisite and argument checks is unchanged.
3. **Tool count unchanged** (22 / 25 / 27 / 30). `persona.md` is unchanged.
4. **Shared `place`.** Its text comes from `NasVideoQuery` (English,
   translate first) and applies to `play_folder` too, so the one-call path
   still translates 沖繩 → Okinawa.
5. **Ambiguity.** Japan → 9 trips makes `play_folder` return `candidates`
   without casting. That is the wanted behavior (Reachy asks), not a
   regression.
6. **Over-playing risk.** The recipe could make a browse question play
   something. V2 checks that.
