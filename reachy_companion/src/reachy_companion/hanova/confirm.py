"""Two-step confirmation gate for destructive tools (D-018, R3).

A gated tool called without `confirm: true` does *no work*: it computes the
exact human-readable action, parks the resolved payload here, and returns
`{"status": "needs_confirmation", "summary": ...}`. Only a second call with
`confirm: true` executes -- and it executes the *parked* payload, not whatever
arguments the second call carried, so a mis-heard correction between the two
turns cannot silently retarget a delete.

The window is `HANOVA_CONFIRM_TTL_S` (90 s, matching upstream `self_destruct`).
This is enforced here, in code. A prompt instruction is not a gate.

**Two things review round 1 changed.**

*Finding 3 -- the gate is session-scoped.* A `ConfirmationGate` keyed only by
tool name and living for the life of the process would let a confirmation armed
in one conversation be consumed by the next one after a backend reconnect. Every
pending action is stamped with the current **epoch**; `begin_session()` mints a
new epoch (wired into the realtime session start in Task 5) and `end_session()`
drops everything (wired into shutdown). An action from an older epoch is never
claimable, even if it is somehow still in the dict.

*Finding 4 -- authorisation is spent on success, not on attempt.* The old
`take()` removed the pending action *before* the destructive call ran, so a 503
from Google turned a confirmed delete into "please confirm again" and lost the
user's authorisation to a transient fault. Now: `claim()` marks it in flight and
hands it back, `complete()` spends it, `release()` puts it back for a retry, and
`abort()` throws it away because the user said no.

**Two things review round 2 changed.**

*Finding 2 -- every armed action carries an immutable claim id.* An epoch scopes
a *session*; it does not identify an *action*. `complete("drive_trash")` took
only a tool name, so whoever called it spent whatever happened to be in that
slot -- including an action a newer session had just armed, which dropped the
user's authorisation without performing anything. And `arm()` overwrote a slot
whose action was mid-execution, which made a destructive operation claimable
again while it was still running. Now `arm()` mints an opaque `claim_id`,
`claim()` hands it to the caller, and `complete()` / `release()` / the
claim-bound `abort()` all require it. Every one of them compares **epoch and
claim id inside the same `with self._lock:` block that performs the mutation**,
so there is no window between checking and acting. Re-arming a claimed slot is
refused with `action_in_flight()`.

*Finding 9 -- `release()` is for transient failures only.* Re-arming an action
after an authentication failure, a refused recipient or a validation error keeps
an approval alive for something that can never succeed as approved. Terminal
failures call `complete()` instead: the authorisation is spent, and the model
has to read a corrected action back. The classification itself lives with each
error family (`gauth.is_transient`, `gdrive.is_transient`,
`gmail_smtp.is_transient`), not here -- this module only enforces that whichever
one is chosen presents the right claim id.

Nothing here logs `summary` or `payload`: both are the user's own words about
their own data (finding 7). The `claim_id` is a random token, not derived from
either, so it is safe to log.
"""

from __future__ import annotations
import time
import uuid
import logging
import threading
from typing import Any, Dict
from dataclasses import replace, dataclass

from reachy_companion.hanova import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingAction:
    """One resolved, read-back-to-the-user action awaiting confirmation."""

    tool_name: str
    summary: str
    payload: Dict[str, Any]
    expires_at: float
    epoch: str
    # Round 2, finding 2: minted at arm time, immutable for the life of the
    # action, and required by every mutator. It identifies *this* authorisation,
    # which the epoch alone cannot do.
    claim_id: str
    claimed_at: float | None = None


class ConfirmationGate:
    """Holds at most one pending action per tool name, per session, with a TTL."""

    def __init__(self) -> None:
        """Create an empty gate with no session yet."""
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingAction] = {}
        self._epoch = ""

    # --- session lifecycle (finding 3) ------------------------------------
    def begin_session(self) -> str:
        """Start a new confirmation epoch, invalidating everything armed before."""
        with self._lock:
            self._pending.clear()
            self._epoch = uuid.uuid4().hex
            epoch = self._epoch
        logger.info("Confirmation gate: new session epoch")
        return epoch

    def end_session(self) -> None:
        """Drop every pending action; the conversation is over."""
        with self._lock:
            self._pending.clear()
            self._epoch = ""
        logger.info("Confirmation gate: session ended, nothing left armed")

    def epoch(self) -> str:
        """Return the current session epoch id."""
        with self._lock:
            return self._epoch

    # --- the two-step contract --------------------------------------------
    def _live(self, tool_name: str, claim_id: str) -> PendingAction | None:
        """Return the pending action iff it matches epoch **and** claim id.

        Round 2, finding 2: callers must invoke this **inside** their own
        `with self._lock:` block, immediately before mutating, so the comparison
        and the mutation cannot be separated by another thread.
        """
        pending = self._pending.get(tool_name)
        if pending is None:
            return None
        if not self._epoch or pending.epoch != self._epoch:
            return None
        if pending.claim_id != claim_id:
            return None
        return pending

    def arm(self, tool_name: str, summary: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Park a resolved action and return the needs-confirmation contract.

        Round 2, finding 2: refuses to overwrite an action that is already in
        flight. Replacing a claimed slot would make a destructive operation that
        is *currently executing* claimable a second time.
        """
        ttl_s = settings.confirm_ttl_s()
        with self._lock:
            if not self._epoch:
                # Arming outside a session would be immediately unclaimable;
                # open one rather than silently parking a dead action.
                self._epoch = uuid.uuid4().hex
            existing = self._pending.get(tool_name)
            # Review finding 2: no TTL term here. The window bounds how long an
            # *unused* authorisation may wait for the user; it says nothing
            # about an operation that is already executing. A delete that runs
            # longer than the window is still running, and letting its slot be
            # re-armed would make it claimable a second time -- precisely the
            # hazard this refusal exists to prevent.
            if existing is not None and existing.claimed_at is not None and existing.epoch == self._epoch:
                logger.info("Confirmation for %s is in flight; refused to re-arm", tool_name)
                return action_in_flight()
            self._pending[tool_name] = PendingAction(
                tool_name=tool_name,
                summary=summary,
                payload=dict(payload),
                expires_at=time.monotonic() + ttl_s,
                epoch=self._epoch,
                claim_id=uuid.uuid4().hex,
            )
        logger.info("Confirmation armed for %s (ttl %.0fs)", tool_name, ttl_s)
        return {"status": "needs_confirmation", "summary": summary}

    def claim(self, tool_name: str) -> PendingAction | None:
        """Take the pending action in flight without spending it (finding 4).

        Returns None when there is nothing armed, when it has expired, when it
        belongs to an earlier session epoch, or when another call already has it
        in flight. The returned object carries the `claim_id` that every
        subsequent `complete` / `release` / `abort` must present (round 2,
        finding 2).
        """
        now = time.monotonic()
        with self._lock:
            pending = self._pending.get(tool_name)
            if pending is None:
                return None
            if pending.epoch != self._epoch or not self._epoch:
                logger.info("Confirmation for %s belonged to an earlier session; refused", tool_name)
                self._pending.pop(tool_name, None)
                return None
            # Review finding 2: the in-flight test comes **before** the expiry
            # test, and only the expiry test evicts. Otherwise an operation
            # still executing past its TTL would be deleted from the slot by any
            # passing `claim()`, and the very next `arm()` would succeed while
            # it was still running -- re-opening through a second door the hole
            # the re-arm refusal closes.
            if pending.claimed_at is not None:
                logger.info("Confirmation for %s is already in flight; refused", tool_name)
                return None
            if pending.expires_at <= now:
                logger.info("Confirmation for %s expired before it was used", tool_name)
                self._pending.pop(tool_name, None)
                return None
            in_flight = replace(pending, claimed_at=now)
            self._pending[tool_name] = in_flight
            return in_flight

    def complete(self, tool_name: str, claim_id: str) -> bool:
        """Spend *this* authorisation. Returns whether it was still the live one.

        Called on success, and on a **terminal** failure (round 2, finding 9):
        an authentication error, a refused recipient or a validation error means
        the resolved action itself is wrong, so keeping the approval alive would
        keep an approval for something unachievable.
        """
        with self._lock:
            if self._live(tool_name, claim_id) is None:
                logger.info("Confirmation for %s: stale claim refused at complete", tool_name)
                return False
            self._pending.pop(tool_name, None)
        logger.info("Confirmation for %s completed", tool_name)
        return True

    def release(self, tool_name: str, claim_id: str) -> bool:
        """Handle a **transient** failure; re-arm so the user can just say "try again".

        Returns whether this claim was still the live one. Round 2, finding 9:
        only transient faults may take this path.
        """
        with self._lock:
            pending = self._live(tool_name, claim_id)
            if pending is None:
                logger.info("Confirmation for %s: stale claim refused at release", tool_name)
                return False
            self._pending[tool_name] = replace(pending, claimed_at=None)
        logger.info("Confirmation for %s released for retry", tool_name)
        return True

    def abort(self, tool_name: str, claim_id: str | None = None) -> Dict[str, Any]:
        """Drop the action and say so, because the user said no.

        With a *claim_id* this is the claim-bound abort and the id must match.
        Without one it may only drop an action that is **not** in flight: a bare
        abort must never yank an operation that is already executing (round 2,
        finding 2).
        """
        with self._lock:
            pending = self._pending.get(tool_name)
            if pending is None:
                logger.info("Confirmation for %s aborted (nothing was armed)", tool_name)
                return {"status": "aborted"}
            if claim_id is None:
                if pending.claimed_at is not None and pending.epoch == self._epoch:
                    logger.info("Confirmation for %s is in flight; bare abort refused", tool_name)
                    return action_in_flight()
            elif self._live(tool_name, claim_id) is None:
                logger.info("Confirmation for %s: stale claim refused at abort", tool_name)
                return action_in_flight()
            self._pending.pop(tool_name, None)
        logger.info("Confirmation for %s aborted", tool_name)
        return {"status": "aborted"}

    def clear(self, tool_name: str) -> None:
        """Drop any pending action for *tool_name*, in flight or not.

        Unlike `abort`, this is a lifecycle operation (session start/shutdown),
        not a user decision, so it is unconditional by design.
        """
        with self._lock:
            self._pending.pop(tool_name, None)

    def reset(self) -> None:
        """Drop every pending action and the epoch. Used by tests."""
        with self._lock:
            self._pending.clear()
            self._epoch = ""

    def expire_now_for_tests(self, tool_name: str) -> None:
        """Move a pending deadline into the past so a test need not sleep."""
        with self._lock:
            pending = self._pending.get(tool_name)
            if pending is not None:
                self._pending[tool_name] = replace(pending, expires_at=time.monotonic() - 1.0)


GATE = ConfirmationGate()


def confirmation_expired() -> Dict[str, Any]:
    """Return the payload for a `confirm: true` call with nothing armed to confirm."""
    return {
        "status": "confirmation_expired",
        "error": (
            "Nothing is pending confirmation. Describe the action again and ask "
            "the user to confirm before calling with confirm true."
        ),
    }


def action_in_flight() -> Dict[str, Any]:
    """Return the payload for trying to re-arm or bare-abort an executing action.

    Round 2, finding 2. This is a distinct status on purpose: the model must not
    read it as "expired" and start a fresh read-back while the first operation is
    still running, and it must not read it as success either.
    """
    return {
        "status": "action_in_flight",
        "error": (
            "That action is already running. Wait for it to finish and report "
            "its result before arming or cancelling anything for this tool."
        ),
    }
