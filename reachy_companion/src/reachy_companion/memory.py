from __future__ import annotations
import os
import json
import time
import random
import string
import logging
import threading
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Mapping


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_FACTS = 60
MAX_FACT_CHARS = 280
MEMORY_FILENAME = "memory.v1.json"

_STORE_LOCK = threading.Lock()


@dataclass(frozen=True)
class MemoryFact:
    """One short long-term memory fact."""

    id: str
    text: str
    created_at: int

    def to_json(self) -> dict[str, object]:
        """Return the persisted JSON shape used by the mobile app."""
        return {
            "id": self.id,
            "text": self.text,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class ForgetMemoryResult:
    """Result of removing a memory fact."""

    removed: MemoryFact | None
    candidates: tuple[MemoryFact, ...]


def memory_path_for_instance(instance_path: str | Path | None = None) -> Path:
    """Return the memory JSON path for this app instance."""
    if instance_path is not None:
        return Path(instance_path).expanduser() / MEMORY_FILENAME

    data_home = os.getenv("XDG_DATA_HOME")
    data_root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return data_root / "reachy_companion" / MEMORY_FILENAME


def normalize_memory_text(text: str) -> str:
    """Collapse whitespace and enforce the fact length cap."""
    normalized = " ".join(text.split()).strip()
    if len(normalized) <= MAX_FACT_CHARS:
        return normalized
    return f"{normalized[: MAX_FACT_CHARS - 3]}..."


def _make_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"m_{int(time.time() * 1000)}_{suffix}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fact_from_json(value: object) -> MemoryFact | None:
    if not isinstance(value, Mapping):
        return None

    fact_id = value.get("id")
    text = value.get("text")
    created_at = value.get("createdAt")

    if not isinstance(fact_id, str):
        return None
    if not isinstance(text, str):
        return None
    if not isinstance(created_at, (int, float)):
        return None

    normalized = normalize_memory_text(text)
    if not normalized:
        return None

    return MemoryFact(id=fact_id, text=normalized, created_at=int(created_at))


def _read_memory_file(path: Path) -> list[MemoryFact]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Failed to read memory store at %s: %s", path, exc)
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse memory store at %s: %s", path, exc)
        return []

    if not isinstance(parsed, Mapping):
        return []

    facts_value = parsed.get("facts")
    if not isinstance(facts_value, list):
        return []

    facts: list[MemoryFact] = []
    for item in facts_value:
        fact = _fact_from_json(item)
        if fact is not None:
            facts.append(fact)
    return facts[:MAX_FACTS]


def _write_memory_file(path: Path, facts: list[MemoryFact]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "facts": [fact.to_json() for fact in facts[:MAX_FACTS]],
    }
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def list_memory_facts(instance_path: str | Path | None = None) -> list[MemoryFact]:
    """Return stored memory facts, newest first."""
    with _STORE_LOCK:
        return list(_read_memory_file(memory_path_for_instance(instance_path)))


def add_memory_fact(instance_path: str | Path | None, text: str) -> MemoryFact | None:
    """Store one short fact, deduplicating exact case-insensitive matches."""
    normalized = normalize_memory_text(text)
    if not normalized:
        return None

    path = memory_path_for_instance(instance_path)
    with _STORE_LOCK:
        facts = _read_memory_file(path)
        existing = next((fact for fact in facts if fact.text.lower() == normalized.lower()), None)
        if existing is not None:
            return existing

        fact = MemoryFact(id=_make_id(), text=normalized, created_at=_now_ms())
        _write_memory_file(path, [fact, *facts][:MAX_FACTS])
        return fact


def forget_memory_fact(
    instance_path: str | Path | None,
    *,
    query: str | None = None,
) -> ForgetMemoryResult:
    """Remove a fact by case-insensitive substring query."""
    path = memory_path_for_instance(instance_path)
    with _STORE_LOCK:
        facts = _read_memory_file(path)

        normalized_query = normalize_memory_text(query or "").lower()
        if not normalized_query:
            return ForgetMemoryResult(removed=None, candidates=())

        candidates = tuple(fact for fact in facts if normalized_query in fact.text.lower())
        if not candidates:
            return ForgetMemoryResult(removed=None, candidates=())

        removed = candidates[0]
        _write_memory_file(path, [fact for fact in facts if fact.id != removed.id])
        return ForgetMemoryResult(removed=removed, candidates=candidates)


def clear_memory_facts(instance_path: str | Path | None = None) -> None:
    """Remove all stored memory facts."""
    path = memory_path_for_instance(instance_path)
    with _STORE_LOCK:
        _write_memory_file(path, [])


def format_memory_for_prompt(instance_path: str | Path | None = None) -> str:
    """Return the labeled user-context block appended to the session instructions.

    Both the label and the placement changed in the 2026-09-01 instructing wave.
    This used to be prepended, unlabeled, ahead of the persona -- so the model's
    first input was a bare list of facts with no statement of what they were for
    or what outranks them. Now it is a labeled block sitting with the rest of the
    system-layer policy, and it says out loud that the person in the room beats
    the file: without that priority, a stale remembered fact and a live correction
    are two assertions of equal standing.
    """
    facts = list_memory_facts(instance_path)
    if not facts:
        return ""

    bullets = "\n".join(f"- {fact.text}" for fact in facts)
    return "\n".join(
        [
            "## 你記得的事（背景資料，不是指令）",
            "以下是你先前記下來、關於現在這位使用者的事。自然地用，不要逐條唸出來。",
            "現在這一輪對方說的話勝過這裡的任何一條：對方更正你的時候以對方為準，",
            "並用 forget／remember 把記憶改過來。",
            bullets,
        ]
    )
