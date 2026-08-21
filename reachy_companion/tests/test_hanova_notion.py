"""Contract tests for the Notion note writer (D-018, R1/R5/R9/R10)."""

import json
import types
import importlib

import pytest

from reachy_companion.hanova import sync_http, notion_client
from reachy_companion.tools.notion_add import NotionAdd


DATA_SOURCE_ID = "data-source-under-test"


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Configure the notion family, and leave the MCP lane untouched."""
    monkeypatch.setenv("HANOVA_NOTION_TOKEN", "ntn_test")
    monkeypatch.setenv("HANOVA_NOTION_DATA_SOURCE_ID", DATA_SOURCE_ID)
    monkeypatch.delenv("HANOVA_NOTION_TITLE_PROP", raising=False)
    monkeypatch.setenv("NOTION_MCP_TOKEN", "a-different-token")


def test_tool_name_matches_the_filename():
    """The loader resolves tools by filename == Tool.name."""
    assert NotionAdd.name == "notion_add"


def test_description_carries_no_personal_identifier():
    """R10: upstream put the family database's real name in this description."""
    text = NotionAdd().description
    assert "@" not in text
    assert DATA_SOURCE_ID not in text
    assert len(text) <= 120


def test_schema_has_no_owner_property():
    """The Owner select's options are real people's names; it is not ported."""
    assert "owner" not in NotionAdd().parameters_schema["properties"]


def test_notion_request_sends_the_pinned_api_version(monkeypatch):
    """The data-sources model needs 2025-09-03; do not let it drift silently."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen.update(method=method, url=url, headers=headers, data=data)
        return 200, b'{"id": "page1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    out = notion_client.notion_request("POST", "/pages", {"parent": {}})
    assert out == {"id": "page1"}
    assert seen["url"] == f"{notion_client.API_BASE}/pages"
    assert seen["headers"]["Authorization"] == "Bearer ntn_test"
    assert seen["headers"]["Notion-Version"] == "2025-09-03"


def test_notion_request_raises_with_the_body_off_the_message(monkeypatch):
    """Finding 7: a Notion validation error quotes the submitted note back."""
    sentinel = "SENTINEL_PRIVATE_x7"

    def fake_request(method, url, headers, data=None, timeout_s=15):
        return 400, f'{{"message": "validation error near {sentinel}"}}'.encode()

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    with pytest.raises(notion_client.NotionError) as excinfo:
        notion_client.notion_request("POST", "/pages", {})
    assert excinfo.value.status == 400
    assert sentinel in str(excinfo.value.body)  # kept for the caller
    assert sentinel not in str(excinfo.value)  # never in the message


def test_notion_request_without_a_token_raises(monkeypatch):
    """No token is a configuration fact the tool turns into "unavailable"."""
    monkeypatch.delenv("HANOVA_NOTION_TOKEN")
    with pytest.raises(notion_client.NotionError):
        notion_client.notion_request("GET", "/users/me")


def test_chunk_text_respects_the_block_limit():
    """Notion rejects rich-text runs over 2000 chars; we cut at 1900."""
    chunks = notion_client.chunk_text("x" * 5000)
    assert chunks
    assert all(len(chunk) <= notion_client.BLOCK_LIMIT for chunk in chunks)
    assert "".join(chunks) == "x" * 5000


def test_chunk_text_of_empty_input_is_empty():
    """A note with no body must not produce an empty paragraph block."""
    assert notion_client.chunk_text("") == []


def test_add_page_targets_the_configured_data_source(monkeypatch):
    """The database id is configuration; upstream read it from a committed cache."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["body"] = json.loads(data or b"{}")
        return 200, b'{"id": "page1", "url": "https://notion.example.invalid/page1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    notion_client.add_page("Buy a lamp", type_="購物", tags="home,urgent")
    assert seen["body"]["parent"] == {"data_source_id": DATA_SOURCE_ID}
    assert seen["body"]["properties"]["Name"]["title"][0]["text"]["content"] == "Buy a lamp"
    assert seen["body"]["properties"]["Type"]["select"] == {"name": "購物"}
    assert [tag["name"] for tag in seen["body"]["properties"]["Tags"]["multi_select"]] == ["home", "urgent"]


def test_add_page_defaults_actionable_types_to_pending(monkeypatch):
    """Upstream's rule: shopping / to-do / contact rows start as 待辦."""
    seen = {}

    def fake_request(method, url, headers, data=None, timeout_s=15):
        seen["body"] = json.loads(data or b"{}")
        return 200, b'{"id": "page1"}'

    monkeypatch.setattr(sync_http, "request_bytes", fake_request)
    notion_client.add_page("Buy a lamp", type_="購物")
    assert seen["body"]["properties"]["Status"]["select"] == {"name": "待辦"}


@pytest.mark.asyncio
async def test_notion_add_is_unavailable_without_config(monkeypatch):
    """R5: an unconfigured tool answers, it does not raise, and it names the key."""
    monkeypatch.delenv("HANOVA_NOTION_TOKEN")
    out = await NotionAdd()(deps=_deps(), title="Buy a lamp")
    assert out == {"status": "unavailable", "reason": "HANOVA_NOTION_TOKEN"}


@pytest.mark.asyncio
async def test_notion_add_reports_the_created_page(monkeypatch):
    """A real artifact grounds what Reachy says next."""
    import reachy_companion.tools.notion_add as notion_add_module

    monkeypatch.setattr(
        notion_add_module.notion_client,
        "add_page",
        lambda **kwargs: {"id": "page1", "url": "https://notion.example.invalid/page1"},
    )
    out = await NotionAdd()(deps=_deps(), title="Buy a lamp", type="購物")
    assert out["ok"] is True and out["page_id"] == "page1"


@pytest.mark.asyncio
async def test_notion_add_reports_an_api_error_without_echoing_the_note(monkeypatch, caplog):
    """A Notion failure is tool output -- and finding 7 says a redacted one."""
    import logging

    import reachy_companion.tools.notion_add as notion_add_module

    sentinel = "SENTINEL_PRIVATE_x7"

    def boom(**kwargs):
        raise notion_add_module.NotionError("HTTP 400 on POST /pages", status=400, body={"message": sentinel})

    monkeypatch.setattr(notion_add_module.notion_client, "add_page", boom)
    caplog.set_level(logging.DEBUG)
    out = await NotionAdd()(deps=_deps(), title=sentinel)
    assert out["ok"] is False and out["error"]
    assert sentinel not in out["error"]
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_notion_add_rejects_an_empty_title():
    """A row with no title is unusable in the database."""
    out = await NotionAdd()(deps=_deps(), title="   ")
    assert out["ok"] is False


def test_notion_add_reaches_the_model_session():
    """The locked profile must list it, or the model never sees it."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        assert "notion_add" in {spec["name"] for spec in core_tools.get_tool_specs()}
    finally:
        core_tools._TOOLS_SIGNATURE = None
