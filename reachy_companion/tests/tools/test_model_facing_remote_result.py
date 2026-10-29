"""The model receives each piece of a remote MCP result once (plan 2026-10-29 M2)."""

import json

import pytest


mcp_types = pytest.importorskip("mcp_types")

from reachy_companion.mcp_client import RemoteToolCallResponse  # noqa: E402
from reachy_companion.tools.core_tools import model_facing_remote_result  # noqa: E402


# Shape captured live from pollen-robotics/reachy-mini-search-tool on 2026-10-29: one text
# block holding the Space's repr, no structured content.
SEARCH_TEXT = (
    "{'query': '台北 今天 天氣', 'results': [{'title': '縣市預報- 臺北市', "
    "'snippet': '今日白天 多雲 21 - 27', 'url': 'https://www.cwa.gov.tw/V8/C/W/County/County.html?CID=63'}]}"
)


def _envelope(**result_fields: object) -> dict[str, object]:
    return RemoteToolCallResponse.from_call_tool_result(
        server_alias="pollen_robotics_reachy_mini_search_tool",
        remote_tool_name="reachy_mini_search_tool_search_web",
        result=mcp_types.CallToolResult(**result_fields),
    ).to_tool_result()


def test_search_result_reaches_the_model_once_without_the_envelope_identity() -> None:
    """The live search shape: one copy of the text, no restated tool names."""
    envelope = _envelope(content=[mcp_types.TextContent(type="text", text=SEARCH_TEXT)])
    assert envelope["content_blocks"][0]["text"] == envelope["text"] == SEARCH_TEXT

    assert model_facing_remote_result(envelope) == {"status": "ok", "text": SEARCH_TEXT}


def test_text_that_is_the_json_of_structured_content_is_dropped() -> None:
    """MCP back-compat text that serializes the structured content is not sent twice."""
    structured = {"rows": [{"city": "台北", "high_c": 27}]}
    envelope = _envelope(
        content=[mcp_types.TextContent(type="text", text=json.dumps(structured))],
        structured_content=structured,
    )

    assert model_facing_remote_result(envelope) == {"status": "ok", "structured_content": structured}


def test_text_distinct_from_structured_content_is_kept() -> None:
    """Text that says something beyond the structured content stays."""
    structured = {"rows": 2}
    envelope = _envelope(
        content=[mcp_types.TextContent(type="text", text="Found two rows.")],
        structured_content=structured,
    )

    assert model_facing_remote_result(envelope) == {
        "status": "ok",
        "text": "Found two rows.",
        "structured_content": structured,
    }


def test_error_text_appears_once_and_the_failure_stays_visible() -> None:
    """A failure keeps status and error; the identical text copy goes."""
    envelope = _envelope(
        content=[mcp_types.TextContent(type="text", text="Search backend unavailable")],
        is_error=True,
    )

    assert model_facing_remote_result(envelope) == {"status": "error", "error": "Search backend unavailable"}


def test_non_text_blocks_are_kept() -> None:
    """Only text blocks are covered by `text`; images and other blocks stay."""
    image = mcp_types.ImageContent(type="image", data="aGk=", mime_type="image/png")
    envelope = _envelope(content=[mcp_types.TextContent(type="text", text="a chart"), image])

    trimmed = model_facing_remote_result(envelope)

    assert trimmed["text"] == "a chart"
    assert [block["type"] for block in trimmed["content_blocks"]] == ["image"]


def test_unknown_keys_pass_through() -> None:
    """Keys the trim does not know about are left alone."""
    assert model_facing_remote_result({"status": "ok", "text": "x", "note": 1}) == {
        "status": "ok",
        "text": "x",
        "note": 1,
    }
