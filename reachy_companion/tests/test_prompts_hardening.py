"""Contract tests for the prompt-hardening block (unclear-audio + language pin)."""

from reachy_companion import prompts
from reachy_companion.conversation_mode import ConversationMode


def _assembled_session_instructions(tmp_path, mode: ConversationMode = ConversationMode.RECORD) -> str:
    return f"{prompts.get_session_instructions(tmp_path)}\n\n{prompts.mode_rules_block(mode)}"


def test_hardening_block_appended_to_instructions(monkeypatch, tmp_path):
    """The hardening block is present by default, on top of the base instructions."""
    monkeypatch.delenv("REALTIME_PROMPT_HARDENING", raising=False)
    text = _assembled_session_instructions(tmp_path)
    assert "wait_for_user" in text
    assert "聽不清楚" in text  # unclear-audio clarifier
    assert "台灣中文" in text or "台灣國語" in text  # language pin


def test_hardening_block_teaches_length_calibration(monkeypatch, tmp_path):
    """Brevity rules: length follows content, no filler, selective preambles."""
    monkeypatch.delenv("REALTIME_PROMPT_HARDENING", raising=False)
    text = prompts.get_session_instructions(tmp_path)
    assert "回答長度" in text  # the section heading
    assert "長度跟著內容走" in text  # calibration, not a flat sentence cap
    assert "前導語" in text  # selective slow-work lead-ins


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
    assert "聽不清楚時" not in prompts.mode_rules_block(ConversationMode.RECORD)


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


def test_the_block_carries_the_2x_structure_the_models_expect() -> None:
    """§C6 of the realtime research: 2.x models read these blocks by name."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for heading in ("訊息頻道", "開場白", "思考", "Tool Availability"):
        assert heading in block


def test_the_preamble_block_promises_slow_work_leadins_and_fast_actions_go_direct() -> None:
    """Plan rev 3 B1: commentary is audible, but only slow work gets lead-ins."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    assert "commentary" in block
    assert "final_answer" in block
    assert "會把兩個頻道的聲音都播出來" in block
    assert "讓對方知道慢工作" in block
    assert "查網路" in block
    assert "找出能播放的音樂" in block
    assert "MCP" in block
    assert "接著直接呼叫工具" in block
    assert "拿到結果再說結果" in block
    assert "快速的機器人動作" in block
    assert "直接做" in block
    assert "只會讓對方多等" in block
    assert "不會發出聲音" not in block
    assert "被丟掉" not in block


def test_unclear_audio_rule_is_once_and_last_system_layer_section(monkeypatch, tmp_path) -> None:
    """Plan rev 3 A3: fragment recovery is the last system-layer rule the model reads."""
    monkeypatch.delenv("REALTIME_PROMPT_HARDENING", raising=False)
    text = _assembled_session_instructions(tmp_path, ConversationMode.RECORD)
    heading = "### 聽不清楚時"

    assert text.count(heading) == 1
    assert text.rstrip().endswith(
        "同樣的澄清說法不要連續重複，因為重複會像卡住；換一個自然問法就好。"
    )
    assert text.index("### 目前模式：紀錄模式") < text.index(heading)
    assert "片段、半句" in text
    assert "因為猜測會回答到沒人說過的事" in text
    assert "這一輪不要呼叫其他工具，因為工具會把猜測變成動作或查詢" in text


def test_the_block_states_no_numeric_length_cap_anywhere() -> None:
    """Extends the existing pin to the caps this wave removed (operator rule)."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    for banned in ("一到兩句", "不超過兩句", "最多三句", "1-2 sentences", "一句話答完", "1～3 句"):
        assert banned not in block


def test_default_greeting_is_taiwan_chinese_without_numeric_cap() -> None:
    """The profile fallback must match the prompt language and brevity policy."""
    from reachy_companion.prompts import DEFAULT_GREETING_PROMPT

    assert DEFAULT_GREETING_PROMPT == (
        "現在用簡短自然的台灣中文主動問候使用者，順口介紹一下你自己是 Reachy。"
        "語氣自然、有角色感，每次換一種順口的說法。"
    )
    assert "一句" not in DEFAULT_GREETING_PROMPT
    assert "1" not in DEFAULT_GREETING_PROMPT


def test_every_negative_rule_carries_its_reason_or_an_alternative() -> None:
    """Bare negation costs 23-32% accuracy; the alternative to a ban is a TOOL."""
    from reachy_companion.prompts import hardening_block

    block = hardening_block()
    # The one enumerated banlist this block used to carry is gone, replaced by
    # the tool that is the affirmative action for the same situation.
    assert "「我在這裡」" not in block
    assert "wait_for_user" in block
    assert "比忍住不說話可靠" in block


def test_the_prompt_names_no_boxed_tool_outside_tool_availability() -> None:
    """Skill: a prompt naming an absent tool invites the model to SIMULATE it."""
    from itertools import chain

    from reachy_companion.prompts import hardening_block
    from reachy_companion.toolboxes import TOOLBOXES

    block = hardening_block()
    availability_at = block.index("## Tool Availability")
    for name in sorted(set(chain.from_iterable(TOOLBOXES.values()))):
        position = block.find(name)
        if position == -1:
            continue
        assert position > availability_at, f"{name} is named before the Tool Availability block"
    assert "open_toolbox" in block[availability_at:]
