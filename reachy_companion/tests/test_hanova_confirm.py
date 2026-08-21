"""Contract tests for the two-step confirmation gate (D-018, R3).

Also pins review round 1 findings 3 (a confirmation must not survive a session
boundary) and 4 (authorisation is spent on success, not on attempt), and review
round 2 finding 2 (every armed action carries an immutable claim id, and every
mutator must present it together with the current epoch).
"""

import pytest

from reachy_companion.hanova.confirm import (
    GATE,
    ConfirmationGate,
    action_in_flight,
    confirmation_expired,
)


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    """Each test gets a fresh session epoch and the default 90 s TTL."""
    monkeypatch.delenv("HANOVA_CONFIRM_TTL_S", raising=False)
    GATE.reset()
    GATE.begin_session()
    yield
    GATE.reset()


def test_arm_returns_the_exact_needs_confirmation_contract():
    """R3 fixes this shape; the model reads `summary` back to the user."""
    out = GATE.arm("email_send", "send mail to a@example.com, subject: Dinner", {"to": "a@example.com"})
    assert out == {
        "status": "needs_confirmation",
        "summary": "send mail to a@example.com, subject: Dinner",
    }


def test_claim_returns_the_armed_payload_without_consuming_it():
    """Finding 4: the authorisation must survive until the operation succeeds."""
    GATE.arm("calendar_delete", "delete 'Dentist' on 2026-09-02", {"event_id": "abc"})
    pending = GATE.claim("calendar_delete")
    assert pending is not None
    assert pending.tool_name == "calendar_delete"
    assert pending.payload == {"event_id": "abc"}
    assert GATE.complete("calendar_delete", pending.claim_id) is True
    assert GATE.claim("calendar_delete") is None  # spent only after success


def test_release_lets_a_transient_failure_be_retried():
    """Finding 4: a 503 must not force the user through a second read-back."""
    GATE.arm("drive_trash", "move 'notes.txt' to Drive trash", {"file_id": "f1"})
    first = GATE.claim("drive_trash")
    assert first is not None
    assert GATE.release("drive_trash", first.claim_id) is True
    retry = GATE.claim("drive_trash")
    assert retry is not None and retry.payload == {"file_id": "f1"}
    # Round 2, finding 2: a retry re-uses the SAME action, so the id is stable.
    assert retry.claim_id == first.claim_id


def test_a_claim_in_flight_cannot_be_claimed_again():
    """Two concurrent confirm calls must not both execute the same delete."""
    GATE.arm("task_delete", "delete task 'buy milk'", {"task_id": "t1"})
    assert GATE.claim("task_delete") is not None
    assert GATE.claim("task_delete") is None


# --- claim identity (round 2, finding 2) -----------------------------------
def test_every_armed_action_gets_its_own_opaque_claim_id():
    """The id is what makes "this exact authorisation" expressible at all."""
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    first = GATE.claim("drive_trash")
    assert first is not None and first.claim_id
    GATE.complete("drive_trash", first.claim_id)

    GATE.arm("drive_trash", "move 'b.txt' to Drive trash", {"file_id": "f2"})
    second = GATE.claim("drive_trash")
    assert second is not None
    assert second.claim_id != first.claim_id


def test_a_stale_claim_id_cannot_complete_a_newer_action():
    """Round 2, finding 2: the exact loss -- an old call spending a new approval.

    An operation still in flight when the conversation restarted used to be able
    to call `complete("drive_trash")` and silently destroy the authorisation the
    *new* session had just armed, without ever performing it.
    """
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    stale = GATE.claim("drive_trash")
    assert stale is not None

    GATE.begin_session()
    GATE.arm("drive_trash", "move 'b.txt' to Drive trash", {"file_id": "f2"})

    assert GATE.complete("drive_trash", stale.claim_id) is False
    fresh = GATE.claim("drive_trash")
    assert fresh is not None and fresh.payload == {"file_id": "f2"}


def test_a_stale_claim_id_cannot_release_a_newer_action():
    """The same hole in the other direction: re-arming someone else's action."""
    GATE.arm("task_delete", "delete task 'a'", {"task_id": "t1"})
    stale = GATE.claim("task_delete")
    assert stale is not None
    GATE.begin_session()
    GATE.arm("task_delete", "delete task 'b'", {"task_id": "t2"})
    live = GATE.claim("task_delete")
    assert live is not None

    assert GATE.release("task_delete", stale.claim_id) is False
    # The live action is still in flight, exactly as it was.
    assert GATE.claim("task_delete") is None


def test_re_arming_a_claimed_slot_is_refused_with_its_own_status():
    """Round 2, finding 2: an executing destructive action is not re-armable."""
    GATE.arm("calendar_delete", "delete 'Dentist'", {"event_id": "abc"})
    in_flight = GATE.claim("calendar_delete")
    assert in_flight is not None

    refused = GATE.arm("calendar_delete", "delete 'Optician'", {"event_id": "xyz"})
    assert refused["status"] == "action_in_flight"
    assert refused["status"] != "needs_confirmation"

    # And the original claim is untouched: it still completes normally.
    assert GATE.complete("calendar_delete", in_flight.claim_id) is True


def test_a_bare_abort_cannot_yank_an_action_that_is_executing():
    """Round 2, finding 2: abort without an id may only drop an idle action."""
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    pending = GATE.claim("drive_trash")
    assert pending is not None
    assert GATE.abort("drive_trash")["status"] == "action_in_flight"
    # With the id it is the claim-bound abort, and it works.
    assert GATE.abort("drive_trash", pending.claim_id) == {"status": "aborted"}
    assert GATE.claim("drive_trash") is None


def test_a_claim_bound_abort_rejects_the_wrong_id():
    """Same comparison as complete/release, in the same lock."""
    GATE.arm("drive_trash", "move 'a.txt' to Drive trash", {"file_id": "f1"})
    pending = GATE.claim("drive_trash")
    assert pending is not None
    assert GATE.abort("drive_trash", "not-the-id")["status"] == "action_in_flight"
    assert GATE.complete("drive_trash", pending.claim_id) is True


def test_action_in_flight_payload_is_self_describing():
    """The model has to be able to say something sensible about this."""
    out = action_in_flight()
    assert out["status"] == "action_in_flight"
    assert out["error"]


def test_abort_drops_the_action_and_says_so():
    """The user changing their mind is a first-class outcome, not a timeout."""
    GATE.arm("self_destruct", "arm the sequence", {})
    assert GATE.abort("self_destruct") == {"status": "aborted"}
    assert GATE.claim("self_destruct") is None


def test_claim_without_arm_is_none():
    """A confirm:true first call must not execute anything."""
    assert GATE.claim("drive_trash") is None


def test_pending_actions_do_not_cross_tools():
    """Arming one gated tool must never authorise a different one."""
    GATE.arm("task_delete", "delete task 'buy milk'", {"task_id": "t1"})
    assert GATE.claim("drive_trash") is None
    assert GATE.claim("task_delete") is not None


def test_expired_pending_is_dropped(monkeypatch):
    """The 90 s window is enforced in code, not in the prompt."""
    monkeypatch.setenv("HANOVA_CONFIRM_TTL_S", "1")
    GATE.arm("self_destruct", "arm the sequence", {})
    pending = GATE.claim("self_destruct")
    assert pending is not None
    GATE.release("self_destruct", pending.claim_id)
    # Move the deadline into the past rather than sleeping for the TTL.
    GATE.expire_now_for_tests("self_destruct")
    assert GATE.claim("self_destruct") is None


# --- session scoping (finding 3) ------------------------------------------
def test_a_new_session_invalidates_everything_armed_before_it():
    """A backend reconnect must not carry someone's pending delete across."""
    GATE.arm("calendar_delete", "delete 'Dentist'", {"event_id": "abc"})
    GATE.begin_session()
    assert GATE.claim("calendar_delete") is None


def test_shutdown_clears_the_gate():
    """A closing conversation leaves no authorisation behind for the next one."""
    GATE.arm("email_send", "send mail", {"to": "a@example.com"})
    GATE.end_session()
    assert GATE.claim("email_send") is None


def test_an_action_armed_under_an_older_epoch_is_never_claimable():
    """Even if the dict survived, the epoch stamp refuses it (defence in depth)."""
    GATE.arm("drive_trash", "move 'notes.txt' to Drive trash", {"file_id": "f1"})
    stale_epoch = GATE.epoch()
    GATE.begin_session()
    assert GATE.epoch() != stale_epoch
    GATE.arm("drive_trash", "move 'other.txt' to Drive trash", {"file_id": "f2"})
    pending = GATE.claim("drive_trash")
    assert pending is not None and pending.payload == {"file_id": "f2"}


def test_arming_twice_replaces_an_unclaimed_pending_action():
    """A corrected read-back must supersede the first one, not queue behind it.

    Round 2, finding 2 narrowed this: replacement is right for an action nobody
    has started, and refused for one that is mid-execution.
    """
    GATE.arm("task_complete", "complete 'buy milk'", {"task_id": "t1"})
    GATE.arm("task_complete", "complete 'buy bread'", {"task_id": "t2"})
    pending = GATE.claim("task_complete")
    assert pending is not None
    assert pending.payload == {"task_id": "t2"}


def test_clear_drops_a_pending_action():
    """An aborted ritual leaves nothing armed."""
    GATE.arm("self_destruct", "arm the sequence", {})
    GATE.clear("self_destruct")
    assert GATE.claim("self_destruct") is None


def test_payload_is_copied_not_aliased():
    """Mutating the caller's dict afterwards must not change what executes."""
    payload = {"file_id": "f1"}
    GATE.arm("drive_trash", "move 'notes.txt' to Drive trash", payload)
    payload["file_id"] = "f2"
    pending = GATE.claim("drive_trash")
    assert pending is not None
    assert pending.payload == {"file_id": "f1"}


def test_confirmation_expired_payload_is_self_describing():
    """The model needs enough to recover: re-describe and ask again."""
    out = confirmation_expired()
    assert out["status"] == "confirmation_expired"
    assert "confirm" in out["error"].lower()


def test_independent_gates_do_not_share_state():
    """The class is reusable; only GATE is wired into the app."""
    other = ConfirmationGate()
    other.begin_session()
    GATE.arm("email_send", "send mail", {})
    assert other.claim("email_send") is None


def test_gate_logs_no_summary_and_no_payload(caplog):
    """Finding 7: the summary is the user's own data; it is never logged."""
    import logging

    caplog.set_level(logging.DEBUG)
    GATE.arm("email_send", "send mail to SENTINEL_PRIVATE_x7 about SENTINEL_PRIVATE_x7", {"to": "SENTINEL_PRIVATE_x7"})
    pending = GATE.claim("email_send")
    assert pending is not None
    GATE.complete("email_send", pending.claim_id)
    assert "SENTINEL_PRIVATE_x7" not in caplog.text
