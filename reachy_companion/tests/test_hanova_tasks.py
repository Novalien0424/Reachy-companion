"""Contract tests for the four Google Tasks tools (D-018, R1/R3/R5)."""

import types
import importlib

import pytest

from reachy_companion.hanova import gtasks
from reachy_companion.hanova.confirm import GATE
from reachy_companion.tools.task_add import TaskAdd
from reachy_companion.tools.task_list import TaskList
from reachy_companion.tools.task_delete import TaskDelete
from reachy_companion.tools.task_complete import TaskComplete


LIST_ID = "list-under-test"


def _deps():
    return types.SimpleNamespace(reachy_mini=None, instance_path=None)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """Configure the google-workspace family and empty the confirmation gate.

    Every patch in this file targets an attribute of a `hanova.*` module object,
    never a `reachy_companion.tools.*` one, because `test_external_loading.py`
    and `test_tool_space_runtime.py` pop every `reachy_companion.tools.*` entry
    out of `sys.modules`. Both copies of a tool module hold a reference to the
    *same* `hanova.gtasks`, which is never popped, so patching through it reaches
    whichever copy is under test.
    """
    creds_dir = tmp_path / "google-workspace-mcp"
    creds_dir.mkdir()
    (creds_dir / "someone@example.com.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDS_DIR", str(creds_dir))
    monkeypatch.setenv("HANOVA_GOOGLE_ACCOUNT", "someone@example.com")
    monkeypatch.setenv("HANOVA_GTASKS_LIST_ID", LIST_ID)
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_tool_names_match_their_filenames():
    """The loader resolves tools by filename == Tool.name."""
    assert TaskAdd.name == "task_add"
    assert TaskList.name == "task_list"
    assert TaskComplete.name == "task_complete"
    assert TaskDelete.name == "task_delete"


def test_descriptions_carry_no_personal_identifier():
    """R10: upstream leaked the owner's name and list names into these."""
    for text in (TaskAdd().description, TaskList().description, TaskComplete().description, TaskDelete().description):
        assert "@" not in text
        assert LIST_ID not in text
        assert len(text) <= 120


def test_create_task_posts_the_expected_body(monkeypatch):
    """Title is required, notes and due are optional and omitted when absent."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(method=method, url=url, body=body)
        return {"id": "t1", "title": "buy milk", "status": "needsAction"}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)
    gtasks.create_task(LIST_ID, "buy milk")
    assert seen["method"] == "POST"
    assert seen["url"] == f"{gtasks.T_BASE}/lists/{LIST_ID}/tasks"
    assert seen["body"] == {"title": "buy milk"}


def test_complete_task_patches_the_status(monkeypatch):
    """Completion is a PATCH to status=completed, not a delete."""
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(method=method, url=url, body=body)
        return {"id": "t1", "status": "completed"}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)
    gtasks.complete_task(LIST_ID, "t1")
    assert seen["method"] == "PATCH"
    assert seen["url"] == f"{gtasks.T_BASE}/lists/{LIST_ID}/tasks/t1"
    assert seen["body"] == {"status": "completed"}


def test_list_tasks_always_says_whether_completed_items_are_wanted(monkeypatch):
    """Review finding 1: Google's tasks.list defaults showCompleted to *true*.

    Omitting the flag therefore asked for completed tasks. `complete_task` PATCHes
    `status` and cannot set the read-only `hidden` flag, so anything ticked off
    through this app came straight back in the next outstanding-only listing.
    """
    seen = {}

    def fake_api_call(method, url, body=None, query=None, account=None):
        seen.update(query=query)
        return {"items": []}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)
    gtasks.list_tasks(LIST_ID)
    assert seen["query"]["showCompleted"] == "false"
    assert "showHidden" not in seen["query"]

    gtasks.list_tasks(LIST_ID, show_completed=True)
    assert seen["query"]["showCompleted"] == "true"
    assert seen["query"]["showHidden"] == "true"


def test_list_task_lists_follows_the_next_page(monkeypatch):
    """Review finding 2: an account's lists can arrive over more than one page."""
    pages = {
        None: {"items": [{"id": "a", "title": "Work"}], "nextPageToken": "p2"},
        "p2": {"items": [{"id": "b", "title": "Home"}]},
    }
    monkeypatch.setattr(
        gtasks.gauth,
        "api_call",
        lambda method, url, body=None, query=None, account=None: pages[(query or {}).get("pageToken")],
    )
    assert [entry["id"] for entry in gtasks.list_task_lists()] == ["a", "b"]


def test_find_task_follows_the_next_page(monkeypatch):
    """Review finding 2: the 101st task is still a task; one page is not a list."""

    def fake_api_call(method, url, body=None, query=None, account=None):
        if url.endswith("/users/@me/lists"):
            return {"items": [{"id": "a", "title": "Work"}]}
        if (query or {}).get("pageToken") == "p2":
            return {"items": [{"id": "t2", "title": "Buy milk"}]}
        return {"items": [{"id": "t1", "title": "Gym"}], "nextPageToken": "p2"}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)
    task, candidates, error = gtasks.find_task("milk")
    assert error is None and candidates == []
    assert task is not None and task["id"] == "t2"


def test_find_task_refuses_to_call_a_capped_walk_unambiguous(monkeypatch):
    """Review finding 2: a truncated search proves neither uniqueness nor absence.

    Every page comes back full with another `nextPageToken`, so the walk stops on
    its own page cap with more still out there. One match was seen -- but a
    second could be sitting on the page nobody read, and a gated tool must not
    arm on that. The match is handed back as a candidate, not as *the* answer.
    """

    def fake_api_call(method, url, body=None, query=None, account=None):
        if url.endswith("/users/@me/lists"):
            return {"items": [{"id": "a", "title": "Work"}]}
        first = (query or {}).get("pageToken") is None
        return {
            "items": [{"id": "t-first" if first else "t-filler", "title": "Buy milk" if first else "Gym"}],
            "nextPageToken": "more",
        }

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)
    task, candidates, error = gtasks.find_task("milk")
    assert task is None and error == "truncated"
    assert [item["id"] for item in candidates] == ["t-first"]


def _across_lists(monkeypatch, tasks_by_list: dict[str, list[dict]]) -> None:
    """Stub gauth.api_call so find_task walks two lists without a network."""

    def fake_api_call(method, url, body=None, query=None, account=None):
        if url.endswith("/users/@me/lists"):
            return {
                "items": [{"id": name, "title": f"List {name}"} for name in tasks_by_list],
            }
        for name, tasks in tasks_by_list.items():
            if f"/lists/{name}/tasks" in url:
                return {"items": tasks}
        return {"items": []}

    monkeypatch.setattr(gtasks.gauth, "api_call", fake_api_call)


def test_find_task_searches_every_list(monkeypatch):
    """A task the user names may live in any list, not just the default one."""
    _across_lists(monkeypatch, {"a": [{"id": "t1", "title": "Gym"}], "b": [{"id": "t2", "title": "Buy milk"}]})
    task, candidates, error = gtasks.find_task("milk")
    assert error is None and candidates == []
    assert task is not None and task["id"] == "t2" and task["list_id"] == "b"


def test_find_task_reports_ambiguity(monkeypatch):
    """Two matches must never be resolved by guessing."""
    _across_lists(
        monkeypatch, {"a": [{"id": "t1", "title": "Buy milk"}], "b": [{"id": "t2", "title": "buy MILK again"}]}
    )
    task, candidates, error = gtasks.find_task("milk")
    assert task is None and error == "ambiguous" and len(candidates) == 2


def test_find_task_reports_no_match(monkeypatch):
    """Zero matches is a clean answer, not an exception."""
    _across_lists(monkeypatch, {"a": [{"id": "t1", "title": "Gym"}]})
    task, candidates, error = gtasks.find_task("milk")
    assert task is None and error == "not_found"


@pytest.mark.asyncio
async def test_task_add_is_unavailable_without_credentials(monkeypatch):
    """R5: no credentials directory means the tool is off, and it names the key."""
    monkeypatch.delenv("GOOGLE_CREDS_DIR")
    out = await TaskAdd()(deps=_deps(), title="buy milk")
    assert out == {"status": "unavailable", "reason": "GOOGLE_CREDS_DIR"}


@pytest.mark.asyncio
async def test_task_add_requires_a_configured_list(monkeypatch):
    """Finding 10: a per-tool prerequisite, because task_list does not need it."""
    monkeypatch.delenv("HANOVA_GTASKS_LIST_ID")
    out = await TaskAdd()(deps=_deps(), title="buy milk")
    assert out == {"status": "unavailable", "reason": "HANOVA_GTASKS_LIST_ID"}


@pytest.mark.asyncio
async def test_the_other_task_tools_do_not_need_a_list_id(monkeypatch):
    """Finding 10: list/complete/delete walk every list, so the id is irrelevant."""
    import reachy_companion.tools.task_list as task_list_module

    monkeypatch.delenv("HANOVA_GTASKS_LIST_ID")
    monkeypatch.setattr(task_list_module.gtasks, "list_task_lists", lambda: [])
    out = await TaskList()(deps=_deps())
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_gated_task_tools_confirm_without_resupplying_match(monkeypatch):
    """Finding 4: the confirming call carries only `confirm`."""
    import reachy_companion.tools.task_delete as task_delete_module

    assert task_delete_module.TaskDelete.parameters_schema["required"] == []
    monkeypatch.setattr(
        task_delete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t9", "title": "Old chore", "list_id": "b", "list_title": "Home"},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        task_delete_module.gtasks,
        "delete_task",
        lambda list_id, task_id: deleted.update(task_id=task_id),
    )
    await TaskDelete()(deps=_deps(), match="chore")
    out = await TaskDelete()(deps=_deps(), confirm=True)
    assert out["ok"] is True and deleted == {"task_id": "t9"}


@pytest.mark.asyncio
async def test_a_transient_task_failure_keeps_the_authorisation(monkeypatch):
    """Finding 4: a 503 must not cost the user their confirmation."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
            [],
            None,
        ),
    )
    attempts = {"n": 0}

    def flaky(list_id, task_id):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise task_complete_module.GoogleApiError(503, {}, url="https://x.invalid/t", method="PATCH")
        return {"id": task_id}

    monkeypatch.setattr(task_complete_module.gtasks, "complete_task", flaky)
    await TaskComplete()(deps=_deps(), match="milk")
    first = await TaskComplete()(deps=_deps(), confirm=True)
    assert first["ok"] is False and first.get("retryable") is True
    second = await TaskComplete()(deps=_deps(), confirm=True)
    assert second["ok"] is True and second["status"] == "completed"


@pytest.mark.asyncio
async def test_an_unexpected_task_failure_never_strands_the_claim(monkeypatch):
    """Task 2's review ruling: a gated tool settles its claim on *every* path.

    The `except` list is a closed set of expected error families. A holder that
    dies on anything outside it -- a bug, a cancellation, a driver raising its
    own class -- would otherwise leave the slot claimed, and `claim()` refuses an
    in-flight action forever while `arm()` refuses to replace it. Task 7's
    `calendar_delete` settles in a `finally`; these two must do the same or the
    tool is dead for the rest of the session after one unexpected fault.
    """
    import reachy_companion.tools.task_delete as task_delete_module

    monkeypatch.setattr(
        task_delete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t9", "title": "Old chore", "list_id": "b", "list_title": "Home"},
            [],
            None,
        ),
    )

    def unexpected(list_id, task_id):
        raise ZeroDivisionError("not in the tool's except list")

    monkeypatch.setattr(task_delete_module.gtasks, "delete_task", unexpected)
    await TaskDelete()(deps=_deps(), match="chore")
    with pytest.raises(ZeroDivisionError):
        await TaskDelete()(deps=_deps(), confirm=True)

    # The slot is settled, so a fresh read-back arms normally again.
    monkeypatch.setattr(task_delete_module.gtasks, "delete_task", lambda list_id, task_id: None)
    assert (await TaskDelete()(deps=_deps(), match="chore"))["status"] == "needs_confirmation"
    assert (await TaskDelete()(deps=_deps(), confirm=True))["ok"] is True


@pytest.mark.asyncio
async def test_task_logs_never_carry_the_task_title(monkeypatch, caplog):
    """Finding 7: the to-do list is the user's own data."""
    import logging

    import reachy_companion.tools.task_add as task_add_module

    sentinel = "SENTINEL_PRIVATE_x7"
    monkeypatch.setattr(
        task_add_module.gtasks,
        "create_task",
        lambda **kwargs: {"id": "t1", "title": sentinel, "status": "needsAction", "due": None},
    )
    caplog.set_level(logging.DEBUG)
    await TaskAdd()(deps=_deps(), title=sentinel)
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_task_add_creates_and_reports_the_task(monkeypatch):
    """A real artifact grounds what Reachy says next."""
    import reachy_companion.tools.task_add as task_add_module

    monkeypatch.setattr(
        task_add_module.gtasks,
        "create_task",
        lambda **kwargs: {"id": "t1", "title": "buy milk", "status": "needsAction", "due": None},
    )
    out = await TaskAdd()(deps=_deps(), title="buy milk")
    assert out["ok"] is True and out["task_id"] == "t1"


@pytest.mark.asyncio
async def test_task_list_groups_by_list(monkeypatch):
    """The model needs to know which list an item is in to talk about it."""
    import reachy_companion.tools.task_list as task_list_module

    monkeypatch.setattr(task_list_module.gtasks, "list_task_lists", lambda: [{"id": "a", "title": "Work"}])
    monkeypatch.setattr(
        task_list_module.gtasks,
        "list_tasks",
        lambda list_id, limit=50, show_completed=False: [{"id": "t1", "title": "Gym", "due": None}],
    )
    out = await TaskList()(deps=_deps())
    assert out["ok"] is True
    assert out["lists"][0]["title"] == "Work"
    assert out["lists"][0]["tasks"][0]["title"] == "Gym"


@pytest.mark.asyncio
async def test_task_complete_arms_before_it_completes(monkeypatch):
    """R3: the first call reads the exact task back and changes nothing."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
            [],
            None,
        ),
    )

    def fail_complete(list_id, task_id):
        raise AssertionError("task_complete must not write before confirmation")

    monkeypatch.setattr(task_complete_module.gtasks, "complete_task", fail_complete)
    out = await TaskComplete()(deps=_deps(), match="milk")
    assert out["status"] == "needs_confirmation" and "Buy milk" in out["summary"]


@pytest.mark.asyncio
async def test_task_complete_executes_the_armed_payload(monkeypatch):
    """The confirmed write uses what was read back."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
            [],
            None,
        ),
    )
    done = {}
    monkeypatch.setattr(
        task_complete_module.gtasks,
        "complete_task",
        lambda list_id, task_id: done.update(list_id=list_id, task_id=task_id) or {"id": task_id},
    )
    await TaskComplete()(deps=_deps(), match="milk")
    out = await TaskComplete()(deps=_deps(), match="totally different", confirm=True)
    assert out["ok"] is True and out["status"] == "completed"
    assert done == {"list_id": "a", "task_id": "t1"}


@pytest.mark.asyncio
async def test_task_delete_arms_and_then_deletes(monkeypatch):
    """R3 again, for the irreversible one."""
    import reachy_companion.tools.task_delete as task_delete_module

    monkeypatch.setattr(
        task_delete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            {"id": "t9", "title": "Old chore", "list_id": "b", "list_title": "Home"},
            [],
            None,
        ),
    )
    deleted = {}
    monkeypatch.setattr(
        task_delete_module.gtasks,
        "delete_task",
        lambda list_id, task_id: deleted.update(list_id=list_id, task_id=task_id),
    )
    armed = await TaskDelete()(deps=_deps(), match="chore")
    assert armed["status"] == "needs_confirmation" and "Old chore" in armed["summary"]
    out = await TaskDelete()(deps=_deps(), match="chore", confirm=True)
    assert out["ok"] is True and out["status"] == "deleted"
    assert deleted == {"list_id": "b", "task_id": "t9"}


@pytest.mark.asyncio
async def test_task_delete_confirm_without_arm_is_refused(monkeypatch):
    """A confirm:true first call must delete nothing."""
    import reachy_companion.tools.task_delete as task_delete_module

    def fail_delete(list_id, task_id):
        raise AssertionError("task_delete must not delete without a pending action")

    monkeypatch.setattr(task_delete_module.gtasks, "delete_task", fail_delete)
    out = await TaskDelete()(deps=_deps(), match="chore", confirm=True)
    assert out["status"] == "confirmation_expired"


@pytest.mark.asyncio
async def test_gated_task_tools_refuse_a_too_short_match():
    """A two-character floor is the minimum defence over a noisy STT channel."""
    assert (await TaskComplete()(deps=_deps(), match="a"))["ok"] is False
    assert (await TaskDelete()(deps=_deps(), match="a"))["ok"] is False


@pytest.mark.asyncio
async def test_task_complete_refuses_an_ambiguous_match(monkeypatch):
    """Two candidates arm nothing and are handed back for the user to pick."""
    import reachy_companion.tools.task_complete as task_complete_module

    monkeypatch.setattr(
        task_complete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            None,
            [
                {"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"},
                {"id": "t2", "title": "Buy milk again", "list_id": "b", "list_title": "Home"},
            ],
            "ambiguous",
        ),
    )
    out = await TaskComplete()(deps=_deps(), match="milk")
    assert out["ok"] is False and out["error"] == "ambiguous" and len(out["candidates"]) == 2
    assert GATE.claim("task_complete") is None


@pytest.mark.asyncio
async def test_task_delete_refuses_a_truncated_search(monkeypatch):
    """Review finding 2: a search that could not finish must arm nothing.

    The tool has to say so out loud rather than deleting the one item it happened
    to see, so the refusal is machine-readable *and* carries the partial result
    for the model to name back.
    """
    import reachy_companion.tools.task_delete as task_delete_module

    monkeypatch.setattr(
        task_delete_module.gtasks,
        "find_task",
        lambda match, include_completed=False: (
            None,
            [{"id": "t1", "title": "Buy milk", "list_id": "a", "list_title": "Work"}],
            "truncated",
        ),
    )

    def fail_delete(list_id, task_id):
        raise AssertionError("task_delete must not act on a search it could not finish")

    monkeypatch.setattr(task_delete_module.gtasks, "delete_task", fail_delete)
    out = await TaskDelete()(deps=_deps(), match="milk")
    assert out["ok"] is False and out["error"] == "search_truncated"
    assert out["candidates"] == [{"title": "Buy milk", "list": "Work"}]
    assert GATE.claim("task_delete") is None


def test_all_four_tools_reach_the_model_session():
    """The locked profile must list them, or the model never sees them."""
    core_tools = importlib.import_module("reachy_companion.tools.core_tools")
    core_tools.initialize_tools(force=True)
    try:
        names = {spec["name"] for spec in core_tools.get_tool_specs()}
        assert {"task_add", "task_list", "task_complete", "task_delete"} <= names
    finally:
        core_tools._TOOLS_SIGNATURE = None
