"""Contract tests for the home_control Skill via Home Assistant REST (D-005)."""

import importlib

import pytest

from reachy_companion.tools.home_control import HomeControl


ENTITIES = '{"客厅的灯": "light.living_room", "书房的灯": "light.study"}'


class _FakeResponse:
    """Minimal stand-in for the httpx response Home Assistant returns."""

    status_code = 200

    def raise_for_status(self) -> None:
        """Behave like a 2xx response."""
        return None

    def json(self) -> list[str]:
        """Return HA's changed-states list (empty when nothing changed)."""
        return []


@pytest.fixture(autouse=True)
def ha_env(monkeypatch):
    """Provide the Home Assistant env the Skill reads at construction time."""
    monkeypatch.setenv("HA_URL", "http://homeassistant.local:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HA_ENTITIES", ENTITIES)


@pytest.fixture
def rebuilt_registry():
    """Rebuild the real tool registry, then invalidate it for later test modules.

    core_tools is resolved through importlib because other test modules pop it
    from sys.modules; a module-level import here could hold a stale copy with a
    different ALL_TOOLS. Teardown only clears the signature, so the next reader
    rebuilds lazily once monkeypatch has restored the environment.
    """
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    yield core_tools
    core_tools._TOOLS_SIGNATURE = None


def test_locked_profile_registers_the_skill_by_filename(rebuilt_registry):
    """US-09: dropping tools/home_control.py in is the whole integration."""
    specs = {spec["name"]: spec for spec in rebuilt_registry.get_tool_specs()}
    assert "home_control" in specs, "tool discovery is by filename == Tool.name"

    # Resolved after the rebuild: modules popped from sys.modules by other test
    # modules are re-imported by the registry, giving a fresh class object.
    registered_class = importlib.import_module("reachy_companion.tools.home_control").HomeControl
    assert isinstance(rebuilt_registry.get_tools()["home_control"], registered_class)

    # The session spec must carry the per-instance schema, not the class default.
    assert registered_class.parameters_schema == {}
    assert set(specs["home_control"]["parameters"]["properties"]["target"]["enum"]) == {"客厅的灯", "书房的灯"}
    assert "客厅的灯" in specs["home_control"]["description"]


def test_tool_contract_enumerates_configured_devices():
    """Schema and description are built from HA_ENTITIES at construction."""
    tool = HomeControl()  # schema/description computed at construction from HA_ENTITIES
    props = tool.parameters_schema["properties"]
    assert HomeControl.name == "home_control"
    assert set(tool.parameters_schema["required"]) == {"action", "target"}
    assert props["action"]["enum"] == ["turn_on", "turn_off", "toggle"]
    assert set(props["target"]["enum"]) == {"客厅的灯", "书房的灯"}
    assert "客厅的灯" in tool.description  # model sees the real device names


def test_spec_exposes_the_instance_schema_not_the_class_default():
    """Tool.spec() -- the only path to the realtime session -- reads the instance."""
    spec = HomeControl().spec()
    assert spec["name"] == "home_control"
    assert set(spec["parameters"]["properties"]["target"]["enum"]) == {"客厅的灯", "书房的灯"}
    assert "客厅的灯" in spec["description"]


@pytest.mark.asyncio
async def test_friendly_name_resolves_to_entity_and_calls_ha(monkeypatch):
    """A spoken device name maps to its entity id and hits the HA service URL."""
    calls = {}

    async def fake_post(self, url, json=None, headers=None, **kw):
        calls["url"], calls["json"], calls["headers"] = url, json, headers
        return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")
    assert calls["url"] == "http://homeassistant.local:8123/api/services/light/turn_on"
    assert calls["json"] == {"entity_id": "light.living_room"}
    assert calls["headers"]["Authorization"] == "Bearer tok"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_unknown_target_reports_known_devices():
    """An unmapped device is refused locally and the allowlist is echoed back."""
    out = await HomeControl()(deps=None, action="turn_on", target="车库门")
    assert out["ok"] is False
    assert "客厅的灯" in out["known_devices"]


@pytest.mark.asyncio
async def test_ha_error_is_reported_not_raised(monkeypatch):
    """A transport failure becomes a tool result, never an exception."""

    async def fake_post(self, url, **kw):
        import httpx

        raise httpx.ConnectError("no route")

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await HomeControl()(deps=None, action="turn_off", target="客厅的灯")
    assert out["ok"] is False and "no route" in out["error"]


@pytest.mark.asyncio
async def test_unsupported_action_is_rejected_before_any_request(monkeypatch):
    """An action outside the enum never reaches the network."""

    async def fail_post(self, *args, **kwargs):
        raise AssertionError("home_control must not call HA for an unsupported action")

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)
    out = await HomeControl()(deps=None, action="rm -rf", target="客厅的灯")
    assert out["ok"] is False
    assert "rm -rf" in out["error"]


@pytest.mark.asyncio
async def test_missing_ha_config_is_reported_not_raised(monkeypatch):
    """HA_URL/HA_TOKEN absent is a reported misconfiguration, not a KeyError."""
    monkeypatch.delenv("HA_URL")
    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")
    assert out["ok"] is False
    assert "HA_URL" in out["error"]


def test_malformed_entities_do_not_break_construction(monkeypatch):
    """Tools are built inside initialize_tools(); a bad env must not brick startup."""
    monkeypatch.setenv("HA_ENTITIES", "{not json")
    tool = HomeControl()
    assert tool.parameters_schema["properties"]["target"]["enum"] == []
    assert "none configured" in tool.description

    monkeypatch.setenv("HA_ENTITIES", '["light.living_room"]')
    assert HomeControl().parameters_schema["properties"]["target"]["enum"] == []


def test_unset_entities_yields_an_empty_allowlist(monkeypatch):
    """No HA_ENTITIES means no controllable devices, not a crash."""
    monkeypatch.delenv("HA_ENTITIES")
    assert HomeControl().parameters_schema["properties"]["target"]["enum"] == []
