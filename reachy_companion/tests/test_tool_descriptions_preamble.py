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
PERSONA_PREAMBLE_LINE = (
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
    """Plan rev 3 B2/B3: search starts with an audible lead-in but never handles playback."""
    (search_spec,) = PREINSTALLED_TOOL_SPACE_SPECS[SEARCH_SLUG]
    description = search_spec.description

    assert "Use when:" in description
    assert "search, check the web, look something up, find today's events" in description
    assert "Preamble sample for slow web lookup" in description
    assert "「我查一下」" in description
    assert "示範語氣，不是觸發條件" in description
    assert "conversation's language" in description
    assert "the lead-in is not a substitute for the call" in description
    assert "Do NOT use when: the user wants media played" in description
    assert "`music`, not search" in description
    assert "Do not just say you'll look it up" not in description


def test_music_description_carries_youtube_playback_and_play_only_preamble() -> None:
    """Plan rev 3 B2/B3: media playback routes to music; stopping stays immediate."""
    description = Music.description

    assert "Use when:" in description
    assert "Do NOT use when:" in description
    assert "YouTube playback" in description
    assert "choose `action=play` with `query`" in description
    assert "Preamble sample for `play`" in description
    assert "「我找一下來播」" in description
    assert "示範語氣，不是觸發條件" in description
    assert "conversation's language" in description
    assert description.count("Preamble sample") == 1
    assert "`stop` is instant and should have no preamble because the person wants silence now" in description


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
    """Plan rev 3 B5: point-first answers no longer forbid slow-work lead-ins."""
    repo_root = Path(__file__).resolve().parents[2]
    persona = repo_root / "persona.md"
    profile = repo_root / "reachy_companion" / "profiles" / "_reachy_companion_locked_profile" / "profile.md"

    persona_text = persona.read_text(encoding="utf-8")
    profile_text = profile.read_text(encoding="utf-8")
    assert PERSONA_PREAMBLE_LINE in persona_text
    assert PERSONA_PREAMBLE_LINE in profile_text
    assert "開口就講重點，前導語如果沒有資訊量就略過" not in persona_text
    assert "開口就講重點，不要用前導語開場" not in profile_text
