from __future__ import annotations
from types import ModuleType
from typing import Any
from pathlib import Path
from importlib import import_module
from collections.abc import Iterator

import pytest

from reachy_companion import mcp_servers
from reachy_companion.tool_spaces import PREINSTALLED_TOOL_SPACE_SPECS
from reachy_companion.tools.music import Music


SEARCH_SLUG = "pollen-robotics/reachy-mini-search-tool"
MCP_PREAMBLE_POLICY = (
    "Remote calls take a moment, so give a brief lead-in first in the same language as the "
    "conversation, then call this tool."
)
PROFILE_PREAMBLE_LINE = (
    "- 回答時開口就講重點，沒有資訊量的前導語就略過；慢工作開始前先給自然前導，再直接做事。"
)


def _core_tools() -> ModuleType:
    return import_module("reachy_companion.tools.core_tools")


@pytest.fixture(autouse=True)
def _clean_mcp_seam(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("NOTION_MCP_URL", "NOTION_MCP_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    _core_tools().EXTRA_TOOLS.clear()
    yield
    module = _core_tools()
    module.EXTRA_TOOLS.clear()
    module.initialize_tools(force=True)


def test_bundled_search_description_carries_slow_preamble_and_music_contrast() -> None:
    """Plan rev 3 B2/B3: search starts with an audible lead-in but never handles playback.

    2026-10-30 (context diet): the use-when side is a condition (the answer
    depends on current facts, even unasked) rather than a list of ways to say
    "search", and the playback contrast names all three media tools.
    """
    (search_spec,) = PREINSTALLED_TOOL_SPACE_SPECS[SEARCH_SLUG]
    description = search_spec.description

    assert "Use when:" in description
    assert "the answer depends on current facts" in description
    assert "even if the user did not say search" in description
    assert "answer those from the results, not from memory" in description
    assert "Preamble sample for slow web lookup" in description
    assert "「我查一下」" in description
    assert "示範語氣，不是觸發條件" in description
    assert "conversation's language" in description
    assert "the lead-in is not a substitute for the call" in description
    assert "Do NOT use when: the user wants media played — that is music, youtube or nas" in description
    assert "Do not just say you'll look it up" not in description


def test_music_description_stays_on_the_speaker_and_has_a_play_only_preamble() -> None:
    """Plan rev 3 B2/B3: speaker audio routes to music; stopping stays immediate.

    2026-10-30 (context diet): `music` no longer claims "YouTube playback" --
    that phrase blurred the line with the `youtube` tool, which plays on the TV.
    `music` is the speaker; a video on the TV is youtube or nas. The conversation
    language of the lead-in now lives once, in the system rules' cross-channel
    language clause, so the description no longer repeats it.

    2026-10-31: `stop` also ends a video on the TV, because mini reached for
    `music stop` while a NAS clip played; *playing* a video still belongs to
    youtube or nas. The same day `pause` and `resume` joined, and all three act
    on the speaker and the TV together (plan W1, W2).
    """
    description = Music.description

    assert "Use when:" in description
    assert "Do NOT use when:" in description
    assert "youtube" not in description.lower().replace("that is youtube or nas", "")
    assert "your own speaker" in description
    assert "a video played on the TV — that is youtube or nas" in description
    assert "`pause`, `resume` or `stop`, which act on everything playing" in description
    assert "the music on your speaker and the video on the TV alike" in description
    assert "`play` with `query`" in description
    assert "Before `play`, say a short lead-in, because finding a track takes a moment" in description
    assert "「我找一下來播」" in description
    assert description.count("示範語氣，不是觸發條件") == 1
    assert description.count("lead-in") == 1
    assert "`pause`, `resume` and `stop` need none, because the person wants them done now" in description
    from reachy_companion.prompts import hardening_block

    assert "同一輪的旁白、工具訊息和回答都用同一種語言" in hardening_block()


class _FakeSpec:
    server_alias = "notion"
    remote_name = "search_pages"
    namespaced_name = "notion__search_pages"
    description = "search notion"
    parameters_schema = {"type": "object", "properties": {}}


class _FakeClient:
    instances: list[Any] = []

    def __init__(self, server: Any, known_tools: Any = ()) -> None:
        self.server = server
        self.known_tools = list(known_tools)
        _FakeClient.instances.append(self)

    async def list_tool_specs(self) -> list[_FakeSpec]:
        return [_FakeSpec()]


@pytest.mark.asyncio
async def test_remote_mcp_descriptions_end_with_the_preamble_policy_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan rev 3 B2: every MCP tool gets the slow-remote-call policy at registration."""
    monkeypatch.setenv("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "secret")
    monkeypatch.setattr(_FakeClient, "instances", [])
    monkeypatch.setattr(mcp_servers, "RemoteMcpToolClient", _FakeClient)

    assert await mcp_servers.register_mcp_tools() == ["notion__search_pages"]

    description = _core_tools().EXTRA_TOOLS["notion__search_pages"].description
    assert description.endswith(MCP_PREAMBLE_POLICY)
    assert description.count(MCP_PREAMBLE_POLICY) == 1
    assert description == f"search notion {MCP_PREAMBLE_POLICY}"


def test_persona_and_locked_profile_allow_slow_work_preambles() -> None:
    """Plan rev 3 B5: point-first answers no longer forbid slow-work lead-ins.

    2026-10-30 (context diet): the slow-work lead-in rule lives once, in the
    system rules (`### 頻道與開場白`), and the operator's `persona.md` no longer
    restates it -- nor anything that could forbid it. The locked profile keeps
    its own copy of the line.
    """
    from reachy_companion.prompts import hardening_block

    repo_root = Path(__file__).resolve().parents[2]
    persona = repo_root / "persona.md"
    profile = repo_root / "reachy_companion" / "profiles" / "_reachy_companion_locked_profile" / "profile.md"

    persona_text = persona.read_text(encoding="utf-8")
    profile_text = profile.read_text(encoding="utf-8")
    block = hardening_block()
    assert "要花時間的工作：先說一句自然的前導語，接著直接呼叫工具" in block
    assert "前導" not in persona_text
    assert PROFILE_PREAMBLE_LINE in profile_text
    assert "開口就講重點，前導語如果沒有資訊量就略過" not in persona_text
    assert "開口就講重點，不要用前導語開場" not in profile_text
