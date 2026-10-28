# reachy_companion — local agent contract

This package is our fork of Pollen Robotics' official Reachy Mini Conversation
App (Apache-2.0, see `LICENSE`), produced with the SDK scaffolder and adapted
in place (D-001 in `../DECISIONS.md`). The root `../CLAUDE.md` governs this
subtree; this file records only what differs locally.

`../reference/reachy_mini_conversation_app` is the read-only upstream clone:
diff against it to see what we changed, and never edit or commit it. Keep our
changes to the smallest surface that achieves the goal, so upstream fixes stay
easy to port.

## Layout

- `src/reachy_companion/` — package source. `openai_realtime.py`
  (`OpenAIRealtimeHandler`) is the live realtime handler; it subclasses the
  upstream-derived `HuggingFaceRealtimeHandler` in `huggingface_realtime.py`,
  which still holds the conversation loop and our turn, hold-off and barge-in
  logic. Config in `config.py`; tools in `tools/`.
- `profiles/` — personalities, at the app root, not under `src/`. This is what
  `config.py` resolves and what `setup.py` packages into the wheel.
- `tests/` — pytest suite, mirrors `src/`.

## Locked profile

`config.LOCKED_PROFILE = "_reachy_companion_locked_profile"`. We ship as a
single-persona app, so `profiles/_reachy_companion_locked_profile/profile.md`
is authoritative and overrides `REACHY_MINI_CUSTOM_PROFILE`. A profile
directory needs a `profile.md` (TOML front matter + instruction body); the
legacy `instructions.txt` / `tools.txt` pair is dead and read by nothing. The
operator-edited persona on the robot is the instance `persona.md` (D-016).

## Tests

`.venv/bin/python -m pytest -q` from this directory. `tests/conftest.py` skips
an explicit list of 30 upstream tests that assume an unlocked, user-switchable
profile; they are incompatible with `LOCKED_PROFILE` by design. Read that
file's comment before editing the list.
