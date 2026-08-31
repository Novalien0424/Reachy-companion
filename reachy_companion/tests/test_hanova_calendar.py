"""Contract tests for the three calendar tools (D-018, R1/R3/R5/R11)."""

import types
import importlib

import pytest

from reachy_companion.hanova import gcal
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.calendar_add import CalendarAdd
from reachy_companion.tools.calendar_list import CalendarList
from reachy_companion.tools.calendar_delete import CalendarDelete


CALENDAR_ID = "calendar-under-test@example.invalid"


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """Configure the google-workspace family and empty the confirmation gate.

    Every patch below targets an attribute of a `hanova.*` module object, never a
    `reachy_companion.tools.*` one. That matters: `test_external_loading.py` and
    `test_tool_space_runtime.py` pop every `reachy_companion.tools.*` entry out
    of `sys.modules`, so the tool classes imported at collection can be a
    different copy from the one a later in-test import returns. Both copies hold
    a reference to the *same* `hanova.gcal` module, which is never popped, so
    patching through it reaches whichever copy is under test. `test_hanova_cast.py`
    documents the other half of this hazard, where the patches name their target
    by string and the re-import is mandatory.
    """
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    monkeypatch.setenv("HANOVA_GCAL_CALENDAR_ID", CALENDAR_ID)
    monkeypatch.delenv("HANOVA_TZ", raising=False)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert CalendarAdd.name == "calendar_add"
    assert CalendarList.name == "calendar_list"
    assert CalendarDelete.name == "calendar_delete"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream leaked the real calendar address into this description."""
    for text in (CalendarAdd().description, CalendarList().description, CalendarDelete().description):
        assert "@" not in text
        assert CALENDAR_ID not in text
        assert len(text) <= 120


def test_create_event_uses_the_configured_timezone(monkeypatch):
    """R11: Asia/Taipei comes from config, never from a literal in the code."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(method=method, url=url, body=body)
        return {"id": "evt1", "summary": "Dinner", "htmlLink": "https://example.invalid/e"}

    monkeypatch.setattr(gcal.gauth, "api_call", fake_api_call)
    monkeypatch.setenv("HANOVA_TZ", "Europe/Paris")
    gcal.create_event(CALENDAR_ID, "Dinner", "2026-09-02T19:00:00+02:00", "2026-09-02T20:30:00+02:00", "Europe/Paris")
    assert seen["method"] == "POST"
    assert seen["url"] == f"{gcal.CAL_BASE}/calendars/{CALENDAR_ID}/events"
    assert seen["body"]["start"] == {"dateTime": "2026-09-02T19:00:00+02:00", "timeZone": "Europe/Paris"}


def test_list_events_builds_the_expected_query(monkeypatch):
    """The singleEvents + orderBy pair is what makes recurring events readable."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(url=url, query=query)
        return {"items": []}

    monkeypatch.setattr(gcal.gauth, "api_call", fake_api_call)
    gcal.list_events(CALENDAR_ID, "2026-09-01T00:00:00Z", "2026-09-08T00:00:00Z", limit=10, search="dentist")
    assert seen["query"]["singleEvents"] == "true"
    assert seen["query"]["orderBy"] == "startTime"
    assert seen["query"]["maxResults"] == 10
    assert seen["query"]["q"] == "dentist"


def _events(*summaries: str) -> dict:
    return {
        "items": [
            {"id": f"evt{index}", "summary": summary, "start": {"dateTime": "2026-09-02T19:00:00+08:00"}}
            for index, summary in enumerate(summaries)
        ]
    }


def test_find_event_matches_case_insensitively(monkeypatch):
    """Voice input has no case; a substring match must not care either."""
    monkeypatch.setattr(gcal.gauth, "api_call", lambda *a, **k: _events("Dentist Appointment", "Gym"))
    event, candidates, error = gcal.find_event(CALENDAR_ID, "dentist", 14)
    assert error is None and candidates == []
    assert event is not None and event["id"] == "evt0"


def test_find_event_reports_ambiguity(monkeypatch):
    """Two matches must never be resolved by guessing."""
    monkeypatch.setattr(gcal.gauth, "api_call", lambda *a, **k: _events("Dentist A", "Dentist B"))
    event, candidates, error = gcal.find_event(CALENDAR_ID, "dentist", 14)
    assert event is None and error == "ambiguous" and len(candidates) == 2


def test_find_event_reports_no_match(monkeypatch):
    """Zero matches is a clean answer, not an exception."""
    monkeypatch.setattr(gcal.gauth, "api_call", lambda *a, **k: _events("Gym"))
    event, candidates, error = gcal.find_event(CALENDAR_ID, "dentist", 14)
    assert event is None and error == "not_found"


@pytest.mark.asyncio
async def test_calendar_add_is_unavailable_without_credentials(monkeypatch):
    """R5: no credentials directory means the tool is off, and it says which key."""
    monkeypatch.delenv("GOOGLE_CREDS_DIR")
    out = await CalendarAdd()(
        deps=_deps(), summary="Dinner", start="2026-09-02T19:00:00+08:00", end="2026-09-02T20:30:00+08:00"
    )
    assert out == {"status": "unavailable", "reason": "GOOGLE_CREDS_DIR"}


@pytest.mark.asyncio
async def test_calendar_tools_are_unavailable_without_a_calendar_id(monkeypatch):
    """Finding 10: the old family gate ignored the calendar id entirely."""
    monkeypatch.delenv("HANOVA_GCAL_CALENDAR_ID")
    out = await CalendarList()(deps=_deps())
    assert out == {"status": "unavailable", "reason": "HANOVA_GCAL_CALENDAR_ID"}


@pytest.mark.asyncio
async def test_calendar_add_creates_and_reports_the_event(monkeypatch):
    """A real artifact grounds what Reachy says next."""
    import reachy_companion.tools.calendar_add as calendar_add_module

    monkeypatch.setattr(
        calendar_add_module.gcal,
        "create_event",
        lambda **kwargs: {"id": "evt1", "summary": "Dinner", "htmlLink": "https://example.invalid/e"},
    )
    out = await CalendarAdd()(
        deps=_deps(), summary="Dinner", start="2026-09-02T19:00:00+08:00", end="2026-09-02T20:30:00+08:00"
    )
    assert out["ok"] is True and out["event_id"] == "evt1"


@pytest.mark.asyncio
async def test_calendar_add_reports_an_api_error_without_echoing_it(monkeypatch, caplog):
    """A Google failure is tool output -- but finding 7 says not *that* output.

    Google's error bodies quote the request back, so the summary the user
    dictated would otherwise reach the log and the model's mouth.
    """
    import logging

    import reachy_companion.tools.calendar_add as calendar_add_module

    sentinel = "SENTINEL_PRIVATE_x7"

    def boom(**kwargs):
        raise calendar_add_module.GoogleApiError(
            403,
            {"error": {"message": f"forbidden for {sentinel}"}},
            url=f"https://www.googleapis.com/calendar/v3/calendars/{sentinel}/events",
            method="POST",
        )

    monkeypatch.setattr(calendar_add_module.gcal, "create_event", boom)
    caplog.set_level(logging.DEBUG)
    out = await CalendarAdd()(
        deps=_deps(),
        summary=f"Dinner with {sentinel}",
        start="2026-09-02T19:00:00+08:00",
        end="2026-09-02T20:30:00+08:00",
    )
    assert out["ok"] is False and out["error"]
    assert sentinel not in out["error"]
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_calendar_list_returns_compact_events(monkeypatch):
    """The model gets titles and times, not raw Google payloads."""
    import reachy_companion.tools.calendar_list as calendar_list_module

    monkeypatch.setattr(
        calendar_list_module.gcal,
        "list_events",
        lambda **kwargs: [
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}, "end": {}}
        ],
    )
    out = await CalendarList()(deps=_deps(), days=7)
    assert out["ok"] is True and out["count"] == 1
    assert out["events"][0]["summary"] == "Dentist"


@pytest.mark.asyncio
async def test_calendar_delete_arms_before_it_deletes(monkeypatch):
    """R3: the first call reads the exact event back and deletes nothing."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )

    def fail_delete(calendar_id, event_id):
        raise AssertionError("calendar_delete must not delete before confirmation")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", fail_delete)
    out = await CalendarDelete()(deps=_deps(), match="dentist")
    assert out["status"] == "needs_confirmation"
    assert "Dentist" in out["summary"] and "2026-09-02" in out["summary"]


@pytest.mark.asyncio
async def test_calendar_delete_executes_the_armed_payload(monkeypatch):
    """The confirmed delete uses what was read back, not the second call's args."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "delete_event",
        lambda calendar_id, event_id: deleted.update(calendar_id=calendar_id, event_id=event_id),
    )
    await CalendarDelete()(deps=_deps(), match="dentist")
    out = await CalendarDelete()(deps=_deps(), match="something else entirely", confirm=True)
    assert out["ok"] is True and out["status"] == "deleted"
    assert deleted == {"calendar_id": CALENDAR_ID, "event_id": "evt1"}


@pytest.mark.asyncio
async def test_confirming_needs_no_match_at_all(monkeypatch):
    """Finding 4: the schema must not force the model to resupply the frozen field."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    assert calendar_delete_module.CalendarDelete.parameters_schema["required"] == []

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "delete_event",
        lambda calendar_id, event_id: deleted.update(event_id=event_id),
    )
    await CalendarDelete()(deps=_deps(), match="dentist")
    out = await CalendarDelete()(deps=_deps(), confirm=True)  # no `match` at all
    assert out["ok"] is True and deleted == {"event_id": "evt1"}


@pytest.mark.asyncio
async def test_a_transient_failure_keeps_the_authorisation(monkeypatch):
    """Finding 4: a 503 must not cost the user their confirmation."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )
    attempts = {"n": 0}

    def flaky(calendar_id, event_id):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise calendar_delete_module.GoogleApiError(503, {}, url="https://x.invalid/e", method="DELETE")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", flaky)
    await CalendarDelete()(deps=_deps(), match="dentist")
    first = await CalendarDelete()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await CalendarDelete()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "deleted"


@pytest.mark.asyncio
async def test_a_permanent_failure_spends_the_authorisation(monkeypatch):
    """A 404 means the resolved action is wrong; re-confirm from scratch."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )

    def gone(calendar_id, event_id):
        raise calendar_delete_module.GoogleApiError(404, {}, url="https://x.invalid/e", method="DELETE")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", gone)
    await CalendarDelete()(deps=_deps(), match="dentist")
    assert (await CalendarDelete()(deps=_deps(), confirm=True))["ok"] is False
    assert (await CalendarDelete()(deps=_deps(), confirm=True))["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_an_unexpected_failure_never_strands_the_claim(monkeypatch):
    """Task 2's review ruling: a claim must be settled on *every* path.

    The `except` list is a closed set of expected error families. A holder that
    dies on anything outside it -- a bug, a cancellation, a driver raising its
    own class -- would otherwise leave the slot claimed, and `claim()` refuses an
    in-flight action forever while `arm()` refuses to replace it. The tool would
    then be dead for the rest of the session with no way back short of a
    reconnect. Settling in a `finally` is what keeps the next read-back possible.
    """
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )

    def unexpected(calendar_id, event_id):
        raise ZeroDivisionError("not in the tool's except list")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", unexpected)
    await CalendarDelete()(deps=_deps(), match="dentist")
    with pytest.raises(ZeroDivisionError):
        await CalendarDelete()(deps=_deps(), confirm=True)

    # The slot is settled, so a fresh read-back arms normally again.
    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", lambda calendar_id, event_id: None)
    assert (await CalendarDelete()(deps=_deps(), match="dentist"))["status"] == "needs_confirmation"
    assert (await CalendarDelete()(deps=_deps(), confirm=True))["ok"] is True


@pytest.mark.asyncio
async def test_a_confirmation_does_not_survive_a_new_session(monkeypatch):
    """Finding 3: a backend reconnect must invalidate everything armed before it."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            {"id": "evt1", "summary": "Dentist", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
            [],
            None,
        ),
    )

    def fail_delete(calendar_id, event_id):
        raise AssertionError("a confirmation from a previous session must not execute")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", fail_delete)
    await CalendarDelete()(deps=_deps(), match="dentist")
    GATE.begin_session()
    out = await CalendarDelete()(deps=_deps(), confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_calendar_delete_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must delete nothing."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    def fail_delete(calendar_id, event_id):
        raise AssertionError("calendar_delete must not delete without a pending action")

    monkeypatch.setattr(calendar_delete_module.gcal, "delete_event", fail_delete)
    out = await CalendarDelete()(deps=_deps(), match="dentist", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_calendar_delete_refuses_an_ambiguous_match(monkeypatch):
    """Two candidates arm nothing and are handed back for the user to pick."""
    import reachy_companion.tools.calendar_delete as calendar_delete_module

    monkeypatch.setattr(
        calendar_delete_module.gcal,
        "find_event",
        lambda calendar_id, match, window_days: (
            None,
            [
                {"id": "evt1", "summary": "Dentist A", "start": {"dateTime": "2026-09-02T19:00:00+08:00"}},
                {"id": "evt2", "summary": "Dentist B", "start": {"dateTime": "2026-09-03T19:00:00+08:00"}},
            ],
            "ambiguous",
        ),
    )
    out = await CalendarDelete()(deps=_deps(), match="dentist")
    assert out["ok"] is False and out["error"] == "ambiguous"
    assert len(out["candidates"]) == 2
    assert GATE.claim("calendar_delete") is None


@pytest.mark.asyncio
async def test_calendar_delete_rejects_a_too_short_match():
    """A two-character floor is the minimum defence over a noisy STT channel."""
    out = await CalendarDelete()(deps=_deps(), match="d")
    assert out["ok"] is False


def test_all_three_tools_reach_the_model_session():
    """The locked profile must list the family, or the model never sees them.

    2026-08-31 tool diet: these are no longer registered under their own
    names -- they are the actions of the `calendar` façade, which is what the
    profile lists now. Their modules, names and prerequisite rows are
    unchanged; only the surface the model reaches them through is.
    """
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        registry = core_tools.get_tools()
        assert "calendar" in registry, "the locked profile no longer lists the family"
        reachable = {tool.name for tool in type(registry["calendar"]).ACTIONS.values()}
        assert {"calendar_add", "calendar_list", "calendar_delete"} <= reachable
    finally:
        core_tools._TOOLS_SIGNATURE = None
