# Media tools split by source — plan (2026-10-29)

Status: approved 2026-10-29 ("Go"); built and deployed as 1.28.0 (D-038). Live
cases 1–5 passed; case 6 is blocked by a daemon wedge. Supersedes the routing
half of 1.27.3 (D-037, "tv refuses a NAS clip").

## The failure

Operator, 2026-10-29: Reachy should know that 「幫我從 NAS 找」「幫我從影片庫找」
is a different request from 「幫我從 YouTube 找」 / "search YouTube for me".

Two live runs of 「把我們在沖繩拍的家庭影片播一段到電視上」, robot journal:

- 1.27.2 (08:33 UTC): the model sent a NAS clip's path to `tv play_video` twice
  (the YouTube search found nothing), then tried `tv show`. Nothing played.
- 1.27.3 (08:45:46–54 UTC): `open_toolbox media` → `nas query` ×2 (correct) →
  `tv play_video` with the found clip's path → refused by the 1.27.3 guard →
  `nas play` → playing.

Both runs searched the right source and then picked the wrong tool to play the
clip. The guard turned that into a refused call, costing about 2.5 s. The model
still makes the wrong choice.

## Cause

The two media tools are named on different axes. `tv` is named for *where the
video goes*. `nas` is named for *where it comes from*. A home-video request
always involves both ("our video" and "the TV"), so at the play step both names
match. For gpt-realtime-2.1-mini, which takes requests literally, `tv` +
`play_video` reads as "play a video on the TV". It wins over a do-NOT-use line
in `tv`'s description, and that line was already there in both runs.

The 1.27.3 guard has the opposite problem. It refuses a YouTube search whose
words match a home clip's spoken title. So if the user says "search YouTube for
<words that happen to be a clip title>", code would decide the source against
what the user asked for.

## The rule

Tools are split by **source**, and the source the user names decides the tool.
The TV is where every one of them plays, and both descriptions say so, so the
word 電視 points to neither.

- The user names YouTube, the internet, or public content (an MV, a trailer, a
  show, a channel) → `youtube`.
- The user names the NAS, 影片庫, or the family's own recordings (我們拍的,
  家裡的, a trip they took) → `nas`.
- When the named source has nothing: say so, and offer the other source.
  Switch only if the user says yes.
- When no source is named and nothing gives it away: ask one short question
  (要找家裡影片庫的，還是 YouTube 上的？).

## Changes (rung 1: the tool, then rung 2: the context)

1. **`youtube` tool** (new `tools/youtube.py`, about 30 lines). It takes only
   `query` and delegates to the untouched `PlayVideo` instance, so its
   `settings.tool_status("play_video")` row, the home-network branches and the
   tests all stay the same. The description has use-when / do-NOT-use pairs
   that match `nas`'s. Every example uses a made-up place, never a real
   archive place name (R10).
2. **`nas` description reframed.** It leads with 家裡的影片庫 (the NAS). Use-when
   gains the named-source cases, and the description says both this tool and
   `youtube` play on the same TV, so pick by where the video comes from. The
   search guidance (English catalogue, `all_trips`) stays as it is.
3. **Pictures: register the existing `show_on_tv` module directly.** The module
   is already on disk with its own name and prerequisite row. Its one-line
   description gets use-when / do-NOT-use pairs, saying that a picture is drawn
   and a video is never played.
4. **Retire the `tv` façade.** Delete `tools/tv.py`. Add `tv` to
   `_RETIRED_TOOL_NAMES` (`tests/test_profile.py:193`) so no profile or prompt
   can name it again.
5. **Media box becomes `("youtube", "nas", "show_on_tv")`.** Tools in session:
   22 at startup (unchanged), 25 with media open (was 24), 30 with both boxes
   (was 29). `test_the_documented_surface_sizes_hold` and the `toolboxes.py`
   docstring are updated to match.
6. **Narrow the 1.27.3 guard.** `play_video` keeps refusing a NAS path or file
   name. That is argument validation, because no user ever asks YouTube for a
   path. It stops matching spoken titles, so any words in an explicit YouTube
   request reach YouTube.
7. **Prompt surfaces name the sources.**
   - `prompts.py:87`, the Tool Availability media line.
   - `persona.md:101`, both the repo copy and the robot instance copy, synced
     together.
   - `profile.md`: `default_tools`, and the boxed-tools line at :72.
   - The `open_toolbox` media category line: "videos and pictures on the TV,
     from YouTube or from the family's 影片庫 on the NAS".
8. **Records.**
   - D-038 (media tools are split by source; supersedes D-029's `tv` family);
   - a `feature_list.json` row;
   - progress, CHANGELOG, handoff;
   - version 1.28.0, because the tool surface changes.

## Verification

- **Unit tests:**
  - registry names and box contents;
  - `youtube` delegates to `PlayVideo`, and the prerequisite check runs before
    argument checks;
  - `youtube` refuses a NAS path;
  - `youtube` searches a phrase that equals a clip's title;
  - the retired-name tripwire covers `tv`.
  - Gate: pytest, ruff, mypy, each run with its exit code.
- **On the robot:** deploy, sync `persona.md`, then confirm the startup log
  says `persona: instance persona.md` and that the robot copy no longer names
  `tv`. Then run fresh scripted sessions over `/rpc` `conversation.say`, one
  request each:
  1. 「幫我從 NAS 找沖繩的影片，放一段到電視上」 → `nas query` → `nas play`.
  2. 「幫我從影片庫找我們在沖繩拍的」 → `nas` only.
  3. 「幫我從 YouTube 找沖繩美ら海水族館的影片，放到電視上」 → `youtube` only.
  4. "search YouTube for a cat video and put it on the TV" → `youtube` only.
  5. 「從 NAS 找周杰倫的 MV」 (not in the archive) → `nas query` → Reachy says it
     isn't there and offers YouTube. No `youtube` call.
  6. 「電視上放沖繩的影片」 (no source named) → one short question, no tool call.

  A run passes when every turn calls only the named source's tools, with zero
  refused calls and no `show_on_tv` call. The turns cost normal realtime API
  usage. No image generation is triggered (the `show_on_tv` 400 is still
  open). The TV is turned off afterwards if it was off before.

## Out of scope

- TV audio taken as user turns (`TV-AUDIO-PHANTOM-TURNS`).
- No way to stop or pause the TV.
- The `show_on_tv` image 400.

These are tracked separately.

## Review log

1. **Renames are an A/B, not a rule** (reachy-instructing-model). This one
   isn't a naming-convention bet. It removes a conflict between two tool names
   that failed the same way in two recorded runs. The scripted run above is
   the measurement against those runs. The old name is retired by the
   tripwire, not kept as an alias, because an alias would put the conflict
   back.
2. **D-029 decision 7 (the façade adds no validation).** `youtube` is not a
   family: it has no `action` and passes everything through to `PlayVideo`.
   The prerequisite-before-arguments order stays, and a test pins it.
3. **Surface-size counts are asserted in tests and stated in D-029.** The
   tests and the `toolboxes.py` docstring are updated. D-029 keeps its
   historical numbers, and D-038 states the new ones.
4. **Narrowing the guard loses the title catch.** Residual risk: the model
   sends a family clip's title to `youtube`, and a stranger's video plays.
   `youtube` returns the YouTube `title` in a named field, so Reachy can say
   what actually played. Watch for this in the live run; don't add code for
   it ahead of evidence.
5. **A stale instance `persona.md` would still name `tv`.** A prompt that names
   a missing tool invites the model to act it out, so the persona sync and its
   check are part of the deploy.
6. **R10.** No real archive place name goes into any tool description. The
   test utterances above go only to the robot, never into a tracked
   description.
7. **`show_on_tv` keeps "tv" in its name.** It could attract video requests.
   Scripted cases 1–4 check that it is never called. If it is, rename it.
8. **Found while building: `show_on_tv` can't be registered as itself.** It is
   on `_RETIRED_TOOL_NAMES`, and original modules keep descriptions of at most
   120 characters (`test_every_ported_description_is_short_and_identifier_free`).
   Resolution: a `draw_picture` wrapper over `ShowOnTv`, like `youtube` over
   `PlayVideo`, through the new `tool_family.forward_to`. This also settles
   item 7: the picture tool's name has no "tv" in it.
9. **Found while building: `tv` can't join the substring tripwire.**
   `tv_not_responding` is a live status that `persona.md` teaches. Resolution:
   a separate whole-word check over profiles, the hardening block,
   `persona.md` and every tool spec.
10. **Found while building: three sibling descriptions named `tv`.**
    `music`, `drive` and `home_control` now say `youtube`, and the symmetry
    test is updated to match.

