"""Thread-safe shared coordination blackboard for agent-to-agent state exchange.

Provides :class:`BoardEntry` (a data container) and :class:`CoordinationBoard`
(a thread-safe dict-like board with blocking ``watch`` semantics).

**Usage**::

    from deerflow.runtime.board import CoordinationBoard

    board = CoordinationBoard()
    board.post("status", "ready", updated_by="agent_a")
    entry = board.read("status")           # BoardEntry
    all_entries = board.list()             # list[BoardEntry]
    filtered = board.list(prefix="task.")  # list[BoardEntry]
    board.delete("status")                 # True/False
    entry = board.watch("event", timeout=10.0)  # blocks until posted
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class BoardEntry:
    """A single entry stored on the coordination board.

    Attributes:
        key: Unique identifier on the board.
        value: Arbitrary payload.
        updated_at: ISO-8601 timestamp of the most recent ``post``.
        updated_by: Identifier of the actor that last posted.
    """

    key: str
    value: Any
    updated_at: str
    updated_by: str


class CoordinationBoard:
    """Thread-safe shared coordination blackboard.

    Allows concurrent readers and writers via a single :class:`threading.Lock`.
    The ``watch`` method uses :class:`threading.Event` per key so that a thread
    can block until another thread posts to a specific key.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._board: dict[str, BoardEntry] = {}
        self._events: dict[str, threading.Event] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post(self, key: str, value: Any, updated_by: str) -> BoardEntry:
        """Create or overwrite the entry for *key* and return it.

        Args:
            key: Board key to write to.
            value: Any value to store.
            updated_by: Actor identifier (e.g. agent name).

        Returns:
            The newly stored :class:`BoardEntry`.
        """
        entry = BoardEntry(
            key=key,
            value=value,
            updated_at=_now_iso(),
            updated_by=updated_by,
        )
        with self._lock:
            self._board[key] = entry
            event = self._events.get(key)
        if event is not None:
            event.set()
        return entry

    def read(self, key: str) -> BoardEntry | None:
        """Return the entry for *key*, or ``None`` if it does not exist."""
        with self._lock:
            return self._board.get(key)

    def list(self, prefix: str = "") -> list[BoardEntry]:
        """Return all entries whose key starts with *prefix*.

        When *prefix* is empty (the default), every entry is returned.
        """
        with self._lock:
            if not prefix:
                return list(self._board.values())
            return [e for k, e in self._board.items() if k.startswith(prefix)]

    def delete(self, key: str) -> bool:
        """Delete the entry for *key*.

        Returns:
            ``True`` if the key existed and was removed, ``False`` otherwise.
        """
        with self._lock:
            if key in self._board:
                del self._board[key]
                return True
            return False

    def watch(self, key: str, timeout: float = 30.0) -> BoardEntry | None:
        """Block until *key* is posted, up to *timeout* seconds.

        Returns the :class:`BoardEntry` if the key was posted (or already
        existed), or ``None`` on timeout.

        If *key* already exists when ``watch`` is called, the method returns
        immediately without waiting.
        """
        with self._lock:
            existing = self._board.get(key)
            if existing is not None:
                return existing
            event = self._events.get(key)
            if event is None:
                event = threading.Event()
                self._events[key] = event
            event.clear()

        event_was_set = event.wait(timeout=timeout)

        with self._lock:
            return self._board.get(key) if event_was_set else None


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
