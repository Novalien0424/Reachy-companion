"""Contract tests for the backchannel / minimum-content classifier.

Mandarin backchannels (嗯/對/好/...) must never be treated as addressable
turns; short-but-substantive utterances must still pass. These tests pin
that contract for Tasks 6 and 7, which gate turns on it.
"""

import pytest

from reachy_companion.audio.backchannel import is_backchannel, is_substantive


@pytest.mark.parametrize("text", [
    "嗯", "嗯嗯", "嗯嗯嗯", "對", "對對", "好", "好的", "是", "是喔", "喔",
    "欸", "哦", "唔", "呵", "哈哈", "哈哈哈", "yeah", "ok", "okay",
    "uh-huh", "mm", "hmm", "嗯 嗯", "哈哈！", "好~",
    "嗯哼", "好喔", "這樣喔",  # unspaced multi-syllable fillers (ASR rarely spaces these)
])
def test_backchannels_detected(text):
    """Acknowledgement/laughter tokens in EN and ZH must classify as backchannel."""
    assert is_backchannel(text)


@pytest.mark.parametrize("text", [
    "幫我開燈", "瑞奇你可以播歌嗎", "好，那幫我關冷氣",  # content beats a leading 好
    "stop", "四十二是答案嗎",
])
def test_substantive_not_backchannel(text):
    """Real content, even a leading 好 or a single EN word, must not be gated out."""
    assert not is_backchannel(text)
    assert is_substantive(text)


def test_empty_and_whitespace_are_not_substantive():
    """Empty/whitespace-only text is never substantive, and always backchannel."""
    assert not is_substantive("")
    assert not is_substantive("  ")
    assert is_backchannel("")  # nothing said = nothing addressed


def test_min_chars_env(monkeypatch):
    """REALTIME_MIN_TURN_CHARS raises the length floor for is_substantive."""
    monkeypatch.setenv("REALTIME_MIN_TURN_CHARS", "4")
    assert not is_substantive("開燈")   # 2 chars < 4
    monkeypatch.delenv("REALTIME_MIN_TURN_CHARS")
