# reachy_companion — local agent contract

This package is **our fork of Pollen Robotics' official Reachy Mini Conversation
App** (Apache-2.0, see `LICENSE`), produced with the SDK scaffolder and adapted
in place — decision **D-001** in `C:\Project\Reachy-mini\DECISIONS.md`.

The root `C:\Project\Reachy-mini\CLAUDE.md` governs this subtree. This file only
records what differs locally. `C:\Project\Reachy-mini\reference\reachy_mini_conversation_app`
is the read-only upstream clone — diff against it to see what we changed, and
never edit or commit it.

## Layout

- `src/reachy_companion/` — package source. Realtime loop in
  `huggingface_realtime.py`; config in `config.py`; tools in `tools/`.
- `profiles/` — personalities, at the **app root**, not under `src/`. This is
  what `config.py` resolves and what `setup.py` packages into the wheel.
- `tests/` — pytest suite, mirrors `src/`.

## Locked profile

`config.LOCKED_PROFILE = "_reachy_companion_locked_profile"`. We ship as a
single-persona app, so the profile at
`profiles/_reachy_companion_locked_profile/profile.md` is authoritative and
overrides `REACHY_MINI_CUSTOM_PROFILE`. A profile directory needs a `profile.md`
(TOML front matter + instruction body); the legacy `instructions.txt` /
`tools.txt` pair is dead and is read by nothing.

## Reuse first

Prefer adapting upstream code over rewriting it: keep changes to the smallest
surface that achieves the goal, so future upstream fixes stay easy to port.

## Tests

```powershell
python -m pytest        # run from reachy_companion/
```

The baseline is **green**. `tests/conftest.py` skips an explicit, exhaustive list
of 30 upstream tests that assume an unlocked, user-switchable profile; they are
incompatible with `LOCKED_PROFILE` by design. Anything else red is a real
regression. See that file's comment before editing the list.
