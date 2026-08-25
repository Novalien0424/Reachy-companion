"""Contract tests for the prompt-hardening block (unclear-audio + language pin)."""

from reachy_companion import prompts


def test_hardening_block_appended_to_instructions(monkeypatch, tmp_path):
    """The hardening block is present by default, on top of the base instructions."""
    monkeypatch.delenv("REALTIME_PROMPT_HARDENING", raising=False)
    text = prompts.get_session_instructions(tmp_path)
    assert "wait_for_user" in text
    assert "聽不清楚" in text  # unclear-audio clarifier
    assert "台灣中文" in text or "台灣國語" in text  # language pin


def test_hardening_survives_persona_override(monkeypatch, tmp_path):
    """persona.md replaces profile instructions wholesale; the block still applies.

    It must still be present because it is composed in get_session_instructions,
    not baked into the profile body.
    """
    (tmp_path / "persona.md").write_text("你是一隻測試機器人。", encoding="utf-8")
    monkeypatch.setenv("PERSONA_FILE", str(tmp_path / "persona.md"))
    from reachy_companion.persona import reset_persona_cache

    reset_persona_cache()
    text = prompts.get_session_instructions(tmp_path)
    assert "你是一隻測試機器人" in text
    assert "wait_for_user" in text


def test_hardening_kill_switch(monkeypatch, tmp_path):
    """REALTIME_PROMPT_HARDENING=0 removes the block entirely."""
    monkeypatch.setenv("REALTIME_PROMPT_HARDENING", "0")
    assert "wait_for_user" not in prompts.get_session_instructions(tmp_path)
