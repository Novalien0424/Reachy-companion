"""Ported HomeAssistant-Nova service layers (D-018).

Adapted from the operator's `HomeAssistant-Nova` repo (read-only clone at
`reference/HomeAssistant-Nova`). Every personal identifier the upstream code
hardcoded -- calendar id, task-list id, Drive folder id, account address, HA
entity ids, NAS share and credentials -- is read from the environment through
`reachy_companion.hanova.settings` instead. Nothing in this package embeds a
private value, and no tool description in `reachy_companion/tools/` may either.
"""
