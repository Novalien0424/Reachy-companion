"""Contract tests for the prompt-hardening block (unclear-audio + language pin)."""

from reachy_companion import prompts


def test_hardening_block_appended_to_instructions(monkeypatch, tmp_path):
    """The hardening block is present by default, on top of the base instructions."""
    monkeypatch.delenv("REALTIME_PROMPT_HARDENING", raising=False)
    text = prompts.get_session_instructions(tmp_path)
    assert "wait_for_user" in text
    assert "聽不清楚" in text  # unclear-audio clarifier
    assert "台灣中文" in text or "台灣國語" in text  # language pin


def test_hardening_block_teaches_length_calibration(monkeypatch, tmp_path):
    """Brevity rules: length follows content, no filler, no preambles."""
    monkeypatch.delenv("REALTIME_PROMPT_HARDENING", raising=False)
    text = prompts.get_session_instructions(tmp_path)
    assert "回答長度" in text  # the section heading
    assert "長度跟著內容走" in text  # calibration, not a flat sentence cap
    assert "前導語" in text  # no "let me think" openers


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


def test_mode_rules_block_covers_every_mode() -> None:
    """Party mode never told the model it was in party mode; modes must."""
    from reachy_companion.prompts import mode_rules_block
    from reachy_companion.conversation_mode import ConversationMode

    one = mode_rules_block(ConversationMode.ONE_ON_ONE)
    group = mode_rules_block(ConversationMode.GROUP)
    record = mode_rules_block(ConversationMode.RECORD)
    assert "一對一聊天模式" in one
    assert "多人聊天模式" in group
    assert "紀錄模式" in record
    assert "summarize_conversation" in record
    assert "set_conversation_mode" in record
    # Each block names its own mode and no other, so a live update cannot leave
    # two postures in the instructions at once.
    assert "紀錄模式" not in one and "紀錄模式" not in group


def test_the_record_block_matches_the_surface_the_mode_actually_ships() -> None:
    """The prompt must not deny tools 紀錄模式 keeps (final review, C5).

    `toolboxes.session_tool_exclusions` never hides `EXTRA_TOOLS` — an MCP tool
    belongs to no box, so hiding it would strand it for the whole meeting — and
    the hardening block tells the model to use the tools it is given. A block
    that closed the list at "只有四件" contradicted both, so a model that saw a
    Notion tool in its own session had two rules pointing opposite ways.
    """
    from reachy_companion.prompts import mode_rules_block
    from reachy_companion.conversation_mode import ConversationMode

    record = mode_rules_block(ConversationMode.RECORD)
    assert "其他外掛工具如果還在也可以用" in record
    assert "只有四件" not in record
    # The redirect for everything else survives the rewrite.
    assert "切模式" in record


def test_hardening_block_carries_the_verbatim_and_example_rules() -> None:
    """One rule must name both envelope shapes at once (who_is_this and summarize).

    Two envelopes now travel to the model — `require_repeat_verbatim`/`response_text`
    from `who_is_this` and `speak_verbatim`/`summary_text` from
    `summarize_conversation`. A rule that named only one would leave the other
    looking like ordinary tool output, which is exactly the paraphrasing the
    envelope exists to stop.
    """
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for phrase in (
        "回答長度範例",
        "require_repeat_verbatim",
        "speak_verbatim",
        "response_text",
        "summary_text",
        "只講真的做過的事",
    ):
        assert phrase in block


def test_hardening_block_states_no_numeric_length_cap() -> None:
    """Operator ruling: calibration, never a number (D-028, and the user memory).

    The brevity fix is few-shot examples of the *tone*, not a sentence budget:
    a numeric cap truncates the explanations and stories that the calibration
    rule above deliberately allows.
    """
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for banned in ("一到兩句", "不超過兩句", "最多三句", "1-2 sentences"):
        assert banned not in block


def test_length_examples_are_labelled_as_style_not_as_triggers() -> None:
    """Research §D3: 2.1-mini matches prompt example phrases too literally.

    Community reports have the mini tier treating an illustrative exchange as a
    trigger condition — waiting to hear "現在幾點？" before answering briefly.
    The heading and the closing line are what keep the block a demonstration.
    """
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    assert "示範語氣，不是觸發條件" in block
    assert "不是要你等到聽見這些句子才這樣講" in block
