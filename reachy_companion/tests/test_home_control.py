"""Contract tests for the home_control Skill via Home Assistant REST (D-005)."""

import importlib

import pytest

from reachy_companion.tools.home_control import HomeControl


ENTITIES = '{"客厅的灯": "light.living_room", "书房的灯": "light.study"}'


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


def test_the_description_marks_turning_on_as_slow_work():
    """The TV takes 4-9 s to come on; without this, mini went silent for that long (3 of 4 runs)."""
    description = HomeControl().description
    assert "up to ten seconds" in description and "slow work" in description


def test_spec_exposes_the_instance_schema_not_the_class_default():
    """Tool.spec() -- the only path to the realtime session -- reads the instance."""
    spec = HomeControl().spec()
    assert spec["name"] == "home_control"
    assert set(spec["parameters"]["properties"]["target"]["enum"]) == {"客厅的灯", "书房的灯"}
    assert "客厅的灯" in spec["description"]


class _Reply:
    """Minimal stand-in for an httpx response."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        """Return the canned JSON body."""
        return self._payload


class _FakeHA:
    """A stand-in Home Assistant: entity states, and what a service call does to them.

    *obeys* False models a device HA accepts the command for but that never
    changes (the 2026-10-30 gap: the tool said 「開好了」 on HA's 200 alone).
    *settle_reads* is how many state reads a commanded change takes to show,
    and *on_state* is what a device reads once it is on.
    """

    def __init__(self, states, *, obeys=True, settle_reads=0, on_state="on", service_status=200, service_timeout=False):
        self.states = dict(states)
        self.obeys = obeys
        self.service_status = service_status
        self.service_timeout = service_timeout
        self.settle_reads = settle_reads
        self.on_state = on_state
        self.calls = []
        self._pending = None

    async def request(self, method, url, json=None, headers=None, **kwargs):
        """Serve GET /api/states/<id> and POST /api/services/<domain>/<service>."""
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        path = url.split(":8123", 1)[1]
        if method == "GET" and path.startswith("/api/states/"):
            entity = path.rsplit("/", 1)[1]
            if self._pending and self._pending[0] == entity:
                if self._pending[2] <= 0:
                    self.states[entity] = self._pending[1]
                    self._pending = None
                else:
                    self._pending[2] -= 1
            if entity not in self.states:
                return _Reply(404, {"message": "Entity not found."})
            return _Reply(200, {"entity_id": entity, "state": self.states[entity]})
        if method == "POST" and path.startswith("/api/services/"):
            service = path.rsplit("/", 1)[1]
            entity = json["entity_id"]
            if self.service_status != 200:
                return _Reply(self.service_status, {"message": "refused"})
            if self.obeys:
                after = {"turn_on": self.on_state, "turn_off": "off"}[service]
                self._pending = [entity, after, self.settle_reads]
            if self.service_timeout:
                import httpx

                raise httpx.ReadTimeout("timed out")
            return _Reply(200, [])
        raise AssertionError(f"unexpected request {method} {path}")

    def service_calls(self):
        """Return the POSTs, as (url, payload)."""
        return [(c["url"], c["json"]) for c in self.calls if c["method"] == "POST"]


@pytest.fixture
def fake_ha(monkeypatch):
    """Install a fake HA behind httpx and make the read-back polls instant."""
    import httpx

    from reachy_companion.tools import home_control

    monkeypatch.setattr(home_control, "_CONFIRM_POLL_S", 0.0)
    monkeypatch.setattr(home_control, "_CONFIRM_TIMEOUT_S", 0.05)

    def install(states, **kwargs):
        fake = _FakeHA(states, **kwargs)
        monkeypatch.setattr(httpx.AsyncClient, "request", fake.request)
        return fake

    return install


@pytest.mark.asyncio
async def test_friendly_name_resolves_to_entity_and_calls_ha(fake_ha):
    """A spoken device name maps to its entity id and hits the HA service URL."""
    ha = fake_ha({"light.living_room": "off"})

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert ha.service_calls() == [
        ("http://homeassistant.local:8123/api/services/light/turn_on", {"entity_id": "light.living_room"})
    ]
    assert ha.calls[-1]["headers"]["Authorization"] == "Bearer tok"
    assert out == {"ok": True, "action": "turn_on", "target": "客厅的灯", "state": "on"}


@pytest.mark.asyncio
async def test_a_device_that_ignores_the_command_is_reported_as_not_switched(fake_ha):
    """HA answers 200 but the light stays off: the result says so, and why."""
    fake_ha({"light.living_room": "off"}, obeys=False)

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert out["ok"] is False
    assert out["state"] == "off"
    assert "did not turn on" in out["error"]
    assert "do not say it worked" in out["error"]


@pytest.mark.asyncio
async def test_a_slow_device_is_confirmed_once_its_state_changes(fake_ha, monkeypatch):
    """A TV takes a few reads to show the new state; the poll waits for it."""
    from reachy_companion.tools import home_control

    monkeypatch.setattr(home_control, "_CONFIRM_TIMEOUT_S", 5.0)
    fake_ha({"light.living_room": "on"}, settle_reads=3)

    out = await HomeControl()(deps=None, action="turn_off", target="客厅的灯")

    assert out["ok"] is True and out["state"] == "off"


@pytest.mark.asyncio
async def test_any_awake_media_state_counts_as_on(fake_ha):
    """The cast TV reads idle, then paused, after turn_on (live probe, 2026-10-30)."""
    fake_ha({"light.living_room": "off"}, on_state="paused")

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert out["ok"] is True and out["state"] == "paused"


@pytest.mark.asyncio
async def test_toggle_is_judged_against_the_state_before_the_call(fake_ha):
    """Toggle on a light that is on becomes a turn_off, confirmed as off."""
    ha = fake_ha({"light.living_room": "on"})

    out = await HomeControl()(deps=None, action="toggle", target="客厅的灯")

    assert [url.rsplit("/", 1)[1] for url, _ in ha.service_calls()] == ["turn_off"]
    assert out["ok"] is True and out["state"] == "off" and out["action"] == "toggle"


@pytest.mark.asyncio
async def test_a_device_already_in_the_asked_state_says_so(fake_ha):
    """「把燈關掉」 when it is already off: still sent, and the result names the state it was in.

    Live 1.31.1, a bare `already: true` came out as 「電視已經自動關掉了」.
    """
    fake_ha({"light.living_room": "off"})

    out = await HomeControl()(deps=None, action="turn_off", target="客厅的灯")

    assert out["ok"] is True and out["state"] == "off" and out["was_already"] == "off"


@pytest.mark.asyncio
async def test_an_offline_device_is_reported_without_sending_a_command(fake_ha):
    """HA reads the device as unavailable: nothing is sent, and the person hears it is offline."""
    ha = fake_ha({"light.living_room": "unavailable"})

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert ha.service_calls() == []
    assert out["ok"] is False and out["state"] == "unavailable"
    assert "offline" in out["error"]


@pytest.mark.asyncio
async def test_a_device_ha_does_not_know_is_reported_as_a_setup_problem(fake_ha):
    """A mapped entity id HA has no state for (a typo in HA_ENTITIES) is a setup fix."""
    ha = fake_ha({})

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert ha.service_calls() == []
    assert out["ok"] is False
    assert "setup" in out["error"]


@pytest.mark.asyncio
async def test_ha_error_is_reported_not_raised_and_carries_no_address(monkeypatch):
    """A transport failure becomes a tool result, never an exception, and the LAN address stays out."""
    import httpx

    async def fail(self, method, url, **kwargs):
        raise httpx.ConnectError(f"All connection attempts failed for {url}")

    monkeypatch.setattr(httpx.AsyncClient, "request", fail)
    out = await HomeControl()(deps=None, action="turn_off", target="客厅的灯")

    assert out["ok"] is False
    assert "not responding" in out["error"]
    assert "homeassistant.local" not in str(out)


@pytest.mark.asyncio
async def test_a_timed_out_command_that_landed_is_still_confirmed(fake_ha):
    """HA does not answer in time, but the TV comes on anyway: the read-back decides, not the timeout."""
    fake_ha({"light.living_room": "off"}, service_timeout=True)

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert out["ok"] is True and out["state"] == "on"


@pytest.mark.asyncio
async def test_a_timed_out_command_that_did_not_land_says_so(fake_ha):
    """No answer and no change: not switched, and never 'it worked'."""
    fake_ha({"light.living_room": "off"}, obeys=False, service_timeout=True)

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert out["ok"] is False and out["state"] == "off"
    assert "did not turn on" in out["error"] and "do not say it worked" in out["error"]


@pytest.mark.asyncio
async def test_a_refused_command_is_not_called_not_responding(fake_ha):
    """HA answers 400 (the device has no such service): it refused, it did answer."""
    fake_ha({"light.living_room": "off"}, service_status=400)

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert out["ok"] is False
    assert "refused" in out["error"] and "HTTP 400" in out["error"]
    assert "not responding" not in out["error"]


@pytest.mark.asyncio
async def test_the_result_never_carries_the_entity_id(fake_ha):
    """The model needs the spoken name and the state, not HA's internal id."""
    fake_ha({"light.living_room": "off"})

    out = await HomeControl()(deps=None, action="turn_on", target="客厅的灯")

    assert "light.living_room" not in str(out)


@pytest.mark.asyncio
async def test_unsupported_action_is_rejected_before_any_request(monkeypatch):
    """An action outside the enum never reaches the network."""
    import httpx

    async def fail_request(self, *args, **kwargs):
        raise AssertionError("home_control must not call HA for an unsupported action")

    monkeypatch.setattr(httpx.AsyncClient, "request", fail_request)
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
