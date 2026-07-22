"""Tests for deerflow.runtime.board.CoordinationBoard — thread-safe shared coordination blackboard."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def board():
    """Create a fresh CoordinationBoard for each test."""
    from deerflow.runtime.board import CoordinationBoard
    return CoordinationBoard()


@pytest.fixture
def sample_entry():
    from deerflow.runtime.board import BoardEntry
    return BoardEntry(
        key="test_key",
        value={"hello": "world"},
        updated_at="2026-07-21T12:00:00+00:00",
        updated_by="test_user",
    )


# ---------------------------------------------------------------------------
# BoardEntry dataclass
# ---------------------------------------------------------------------------

class TestBoardEntry:
    """BoardEntry is a simple data container."""

    def test_fields(self):
        from deerflow.runtime.board import BoardEntry

        entry = BoardEntry(
            key="k",
            value=42,
            updated_at="2026-01-01T00:00:00Z",
            updated_by="alice",
        )
        assert entry.key == "k"
        assert entry.value == 42
        assert entry.updated_at == "2026-01-01T00:00:00Z"
        assert entry.updated_by == "alice"

    def test_repr(self):
        from deerflow.runtime.board import BoardEntry

        entry = BoardEntry(key="k", value="v", updated_at="ts", updated_by="bob")
        r = repr(entry)
        assert "BoardEntry" in r
        assert "k" in r
        assert "v" in r


# ---------------------------------------------------------------------------
# post
# ---------------------------------------------------------------------------

class TestPost:
    def test_post_new_key(self, board):
        """Posting to a new key returns a BoardEntry with the given data."""
        entry = board.post(key="alpha", value=1, updated_by="alice")
        assert entry.key == "alpha"
        assert entry.value == 1
        assert entry.updated_by == "alice"
        assert isinstance(entry.updated_at, str)
        assert entry.updated_at != ""

    def test_post_overwrite_existing(self, board):
        """Posting to an existing key overwrites value and updated_by."""
        board.post(key="alpha", value=1, updated_by="alice")
        entry = board.post(key="alpha", value=2, updated_by="bob")
        assert entry.key == "alpha"
        assert entry.value == 2
        assert entry.updated_by == "bob"

    def test_post_updates_timestamp(self, board):
        """Each post updates the timestamp."""
        e1 = board.post(key="ts_test", value="a", updated_by="alice")
        e2 = board.post(key="ts_test", value="b", updated_by="alice")
        assert e2.updated_at >= e1.updated_at

    def test_post_returns_entry(self, board):
        """post returns the BoardEntry that was stored."""
        entry = board.post(key="xyz", value=[1, 2, 3], updated_by="carol")
        assert isinstance(entry, object)
        # Verify it is actually retrievable
        same = board.read("xyz")
        assert same is entry  # same object reference


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

class TestRead:
    def test_read_existing_key(self, board):
        """read returns the BoardEntry for an existing key."""
        board.post(key="foo", value="bar", updated_by="alice")
        entry = board.read("foo")
        assert entry is not None
        assert entry.key == "foo"
        assert entry.value == "bar"
        assert entry.updated_by == "alice"

    def test_read_nonexistent_returns_none(self, board):
        """read returns None when the key does not exist."""
        assert board.read("nonexistent") is None

    def test_read_empty_string_key(self, board):
        """read handles edge cases like an empty-string key."""
        board.post(key="", value="empty", updated_by="alice")
        entry = board.read("")
        assert entry is not None
        assert entry.value == "empty"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:
    def test_list_empty(self, board):
        """list on an empty board returns an empty list."""
        assert board.list() == []

    def test_list_all(self, board):
        """list without prefix returns all entries."""
        board.post(key="a", value=1, updated_by="alice")
        board.post(key="b", value=2, updated_by="bob")
        board.post(key="c", value=3, updated_by="carol")
        entries = board.list()
        assert len(entries) == 3
        keys = {e.key for e in entries}
        assert keys == {"a", "b", "c"}

    def test_list_prefix_filter(self, board):
        """list with prefix filters to keys starting with that prefix."""
        board.post(key="task.alpha", value=10, updated_by="alice")
        board.post(key="task.beta", value=20, updated_by="bob")
        board.post(key="note.gamma", value=30, updated_by="carol")

        task_entries = board.list(prefix="task.")
        assert len(task_entries) == 2
        assert {e.key for e in task_entries} == {"task.alpha", "task.beta"}

        note_entries = board.list(prefix="note.")
        assert len(note_entries) == 1
        assert note_entries[0].key == "note.gamma"

    def test_list_prefix_no_match(self, board):
        """list with a prefix that matches nothing returns empty list."""
        board.post(key="user.a", value=1, updated_by="alice")
        assert board.list(prefix="zzz.") == []

    def test_list_prefix_empty_returns_all(self, board):
        """list with empty prefix is equivalent to list() and returns all."""
        board.post(key="x", value=1, updated_by="alice")
        board.post(key="y", value=2, updated_by="bob")
        assert len(board.list(prefix="")) == 2

    def test_list_preserves_values(self, board):
        """list entries carry correct values."""
        board.post(key="k1", value={"nested": True}, updated_by="alice")
        board.post(key="k2", value=99, updated_by="bob")
        entries = board.list()
        values = {e.key: e.value for e in entries}
        assert values["k1"] == {"nested": True}
        assert values["k2"] == 99


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_existing_key(self, board):
        """delete returns True for an existing key and removes it."""
        board.post(key="temp", value="gone", updated_by="alice")
        assert board.read("temp") is not None
        result = board.delete("temp")
        assert result is True
        assert board.read("temp") is None

    def test_delete_nonexistent_returns_false(self, board):
        """delete returns False when the key does not exist."""
        result = board.delete("never_existed")
        assert result is False

    def test_delete_reduces_list_count(self, board):
        """After deleting a key, list no longer includes it."""
        board.post(key="a", value=1, updated_by="alice")
        board.post(key="b", value=2, updated_by="bob")
        board.delete("a")
        keys = {e.key for e in board.list()}
        assert keys == {"b"}


# ---------------------------------------------------------------------------
# watch  (blocking read — uses threading.Event internally)
# ---------------------------------------------------------------------------

class TestWatch:
    def test_watch_returns_when_key_posted(self, board):
        """watch blocks until another thread posts to the watched key."""

        results = []

        def poster():
            import time
            time.sleep(0.05)
            board.post(key="watch_test", value="done", updated_by="alice")

        t = threading.Thread(target=poster, daemon=True)
        t.start()

        entry = board.watch(key="watch_test", timeout=5.0)
        assert entry is not None
        assert entry.key == "watch_test"
        assert entry.value == "done"
        assert entry.updated_by == "alice"

    def test_watch_times_out(self, board):
        """watch returns None when timeout elapses without the key being posted."""
        entry = board.watch(key="ghost", timeout=0.1)
        assert entry is None

    def test_watch_zero_timeout(self, board):
        """watch with zero timeout returns None immediately if key absent."""
        entry = board.watch(key="noway", timeout=0)
        assert entry is None

    def test_watch_existing_key_returns_immediately(self, board):
        """watch returns immediately if the key already exists."""
        board.post(key="existing", value="present", updated_by="alice")
        entry = board.watch(key="existing", timeout=5.0)
        assert entry is not None
        assert entry.value == "present"

    def test_watch_multiple_waiters_all_notified(self, board):
        """Multiple threads watching the same key are all unblocked when it is posted."""

        barrier = threading.Barrier(3)  # 2 watchers + poster
        results = []

        def watcher(wid):
            barrier.wait(timeout=5)
            e = board.watch(key="multi_watch", timeout=5.0)
            results.append((wid, e.value if e else None))

        def poster():
            barrier.wait(timeout=5)
            board.post(key="multi_watch", value="all_good", updated_by="alice")

        threads = [
            threading.Thread(target=watcher, args=(1,), daemon=True),
            threading.Thread(target=watcher, args=(2,), daemon=True),
            threading.Thread(target=poster, daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=6)

        assert len(results) == 2
        for wid, val in results:
            assert val == "all_good", f"watcher {wid} got {val}"

    def test_watch_respects_short_timeout(self, board):
        """watch returns None after a very short timeout when no post occurs."""
        import time

        start = time.monotonic()
        entry = board.watch(key="quick_timeout", timeout=0.01)
        elapsed = time.monotonic() - start
        assert entry is None
        # Should resolve quickly; allow some margin for scheduling
        assert elapsed < 2.0, f"watch took {elapsed}s for a 0.01s timeout"


# ---------------------------------------------------------------------------
# Thread safety — concurrent post
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_post(self, board):
        """Multiple threads posting to different keys is safe under race conditions."""

        n_threads = 20
        barrier = threading.Barrier(n_threads)
        posted = []

        def worker(i):
            barrier.wait(timeout=5)
            board.post(key=f"concurrent_{i}", value=i, updated_by=f"worker_{i}")
            posted.append(i)

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(posted) == n_threads
        entries = board.list()
        assert len(entries) == n_threads
        keys = {e.key for e in entries}
        for i in range(n_threads):
            assert f"concurrent_{i}" in keys

    def test_concurrent_post_same_key(self, board):
        """Multiple threads posting to the same key is safe (last write wins)."""

        n_threads = 10
        barrier = threading.Barrier(n_threads)

        def worker(i):
            barrier.wait(timeout=5)
            board.post(key="shared", value=i, updated_by=f"worker_{i}")

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        entry = board.read("shared")
        assert entry is not None
        assert entry.key == "shared"
        # The last writer wins; we just verify it's one of the workers
        assert entry.updated_by.startswith("worker_")

    def test_concurrent_read_write(self, board):
        """Reads do not interfere with concurrent writes."""

        board.post(key="rw", value=0, updated_by="alice")
        lock = threading.Lock()
        reads = []

        def writer():
            for i in range(50):
                board.post(key="rw", value=i, updated_by=f"writer_{i}")

        def reader():
            for _ in range(50):
                e = board.read("rw")
                with lock:
                    reads.append(e.value if e else None)

        threads = [
            threading.Thread(target=writer, daemon=True),
            threading.Thread(target=reader, daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(reads) == 50
        # Every read should have observed some value (no corruption)
        assert all(r is not None for r in reads)

    def test_concurrent_list(self, board):
        """Concurrent list operations don't race with posts."""

        def writer():
            for i in range(30):
                board.post(key=f"list_w_{i}", value=i, updated_by="w")

        def lister(results):
            for _ in range(20):
                entries = board.list(prefix="list_w_")
                results.append(len(entries))

        results = []
        t1 = threading.Thread(target=writer, daemon=True)
        t2 = threading.Thread(target=lister, args=(results,), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # list never raises — all calls safe
        assert len(results) > 0
