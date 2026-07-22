"""Tests for AgentRegistry — thread-safe in-memory agent registry."""

from __future__ import annotations

import threading
from datetime import datetime

import pytest

from deerflow.agents.registry import AgentInfo, AgentRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> AgentRegistry:
    return AgentRegistry()


# ---------------------------------------------------------------------------
# AgentInfo dataclass
# ---------------------------------------------------------------------------


class TestAgentInfo:
    def test_agent_info_fields(self) -> None:
        info = AgentInfo(
            agent_id="test-agent",
            name="Test Agent",
            capabilities=["search", "compute"],
            status="active",
            registered_at="2026-01-01T00:00:00",
        )
        assert info.agent_id == "test-agent"
        assert info.name == "Test Agent"
        assert info.capabilities == ["search", "compute"]
        assert info.status == "active"
        assert info.registered_at == "2026-01-01T00:00:00"

    def test_default_status(self) -> None:
        info = AgentInfo(
            agent_id="a",
            name="A",
            capabilities=["x"],
            registered_at="2026-01-01T00:00:00",
        )
        assert info.status == "active"


# ---------------------------------------------------------------------------
# AgentRegistry — registration
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_new_agent(self, registry: AgentRegistry) -> None:
        info = registry.register("agent-1", "Agent One", ["search"])
        assert info.agent_id == "agent-1"
        assert info.name == "Agent One"
        assert info.capabilities == ["search"]
        assert info.status == "active"
        # registered_at should be an ISO-8601 timestamp
        datetime.fromisoformat(info.registered_at)

    def test_register_without_capabilities(self, registry: AgentRegistry) -> None:
        info = registry.register("agent-2", "Agent Two")
        assert info.agent_id == "agent-2"
        assert info.capabilities == []

    def test_register_duplicate_raises(self, registry: AgentRegistry) -> None:
        registry.register("dup", "First")
        with pytest.raises(ValueError, match="already registered"):
            registry.register("dup", "Second")


# ---------------------------------------------------------------------------
# AgentRegistry — unregister
# ---------------------------------------------------------------------------


class TestUnregister:
    def test_unregister_existing_agent(self, registry: AgentRegistry) -> None:
        registry.register("a1", "Agent A1")
        registry.unregister("a1")
        assert registry.get("a1") is None

    def test_unregister_nonexistent_does_not_raise(self, registry: AgentRegistry) -> None:
        # Unregistering a non-existent agent should be a no-op
        registry.unregister("nonexistent")
        assert registry.get("nonexistent") is None

    def test_unregister_then_register_again(self, registry: AgentRegistry) -> None:
        registry.register("reuse", "Original")
        registry.unregister("reuse")
        info = registry.register("reuse", "Re-registered")
        assert info.name == "Re-registered"


# ---------------------------------------------------------------------------
# AgentRegistry — get
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_existing(self, registry: AgentRegistry) -> None:
        expected = registry.register("g1", "Get Test", ["read"])
        actual = registry.get("g1")
        assert actual is not None
        assert actual == expected

    def test_get_nonexistent(self, registry: AgentRegistry) -> None:
        assert registry.get("no-such-agent") is None

    def test_get_after_unregister(self, registry: AgentRegistry) -> None:
        registry.register("tmp", "Temporary")
        registry.unregister("tmp")
        assert registry.get("tmp") is None


# ---------------------------------------------------------------------------
# AgentRegistry — lookup by capability
# ---------------------------------------------------------------------------


class TestLookup:
    def test_lookup_no_filter_returns_all(self, registry: AgentRegistry) -> None:
        a1 = registry.register("a1", "A1", ["search"])
        a2 = registry.register("a2", "A2", ["compute"])
        all_agents = registry.lookup()
        assert len(all_agents) == 2
        assert a1 in all_agents
        assert a2 in all_agents

    def test_lookup_by_capability(self, registry: AgentRegistry) -> None:
        registry.register("search-a", "Search A", ["search"])
        registry.register("search-b", "Search B", ["search", "compute"])
        registry.register("compute-only", "Compute", ["compute"])
        results = registry.lookup(capability="search")
        assert len(results) == 2
        ids = {a.agent_id for a in results}
        assert ids == {"search-a", "search-b"}

    def test_lookup_no_match(self, registry: AgentRegistry) -> None:
        registry.register("x", "X", ["search"])
        results = registry.lookup(capability="vision")
        assert results == []

    def test_lookup_empty_registry(self, registry: AgentRegistry) -> None:
        assert registry.lookup() == []
        assert registry.lookup(capability="anything") == []


# ---------------------------------------------------------------------------
# AgentRegistry — update_status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    def test_update_status(self, registry: AgentRegistry) -> None:
        registry.register("u1", "Updatable", ["x"])
        registry.update_status("u1", "paused")
        info = registry.get("u1")
        assert info is not None
        assert info.status == "paused"

    def test_update_status_invalid_agent(self, registry: AgentRegistry) -> None:
        with pytest.raises(KeyError, match="not found"):
            registry.update_status("ghost", "active")

    def test_update_status_multiple_times(self, registry: AgentRegistry) -> None:
        registry.register("flip", "Flipper", ["x"])
        for status in ["active", "paused", "disabled", "active"]:
            registry.update_status("flip", status)
            assert registry.get("flip").status == status  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# AgentRegistry — list_active
# ---------------------------------------------------------------------------


class TestListActive:
    def test_list_active_only(self, registry: AgentRegistry) -> None:
        registry.register("a", "Active A", ["x"])
        registry.register("b", "Active B", ["y"])
        registry.update_status("b", "paused")
        registry.register("c", "Active C", ["z"])
        registry.update_status("c", "disabled")
        active = registry.list_active()
        assert len(active) == 1
        assert active[0].agent_id == "a"

    def test_list_active_empty(self, registry: AgentRegistry) -> None:
        assert registry.list_active() == []

    def test_list_active_all_non_active(self, registry: AgentRegistry) -> None:
        registry.register("p1", "Paused", ["x"])
        registry.update_status("p1", "paused")
        assert registry.list_active() == []


# ---------------------------------------------------------------------------
# AgentRegistry — concurrency safety
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_registrations(self) -> None:
        """Multiple threads can register agents without data corruption."""
        registry = AgentRegistry()
        n_threads = 10
        agents_per_thread = 50
        barrier = threading.Barrier(n_threads)
        results: list[Exception | None] = [None] * n_threads

        def _register(thread_idx: int) -> None:
            barrier.wait()  # all threads start at the same time
            try:
                for i in range(agents_per_thread):
                    registry.register(
                        f"agent-t{thread_idx}-{i}",
                        f"Thread {thread_idx} Agent {i}",
                        [f"cap-{i % 5}"],
                    )
            except Exception as e:
                results[thread_idx] = e

        threads = [
            threading.Thread(target=_register, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No thread should have raised
        for i, exc in enumerate(results):
            assert exc is None, f"Thread {i} raised: {exc}"

        all_agents = registry.lookup()
        assert len(all_agents) == n_threads * agents_per_thread

    def test_concurrent_unregister_and_lookup(self) -> None:
        """Simultaneous reads and writes do not cause data races."""
        registry = AgentRegistry()
        for i in range(20):
            registry.register(f"target-{i}", f"Target {i}", ["x"])

        barrier = threading.Barrier(6)
        errors: list[Exception | None] = [None] * 6

        def _unregister(idx: int, agent_id: str) -> None:
            barrier.wait()
            try:
                registry.unregister(agent_id)
            except Exception as e:
                errors[idx] = e

        def _read_all(idx: int) -> None:
            barrier.wait()
            try:
                for _ in range(10):
                    registry.lookup()
                    registry.list_active()
                    registry.lookup(capability="x")
            except Exception as e:
                errors[idx] = e

        threads = []
        # 3 unregister threads
        for i, aid in enumerate(["target-0", "target-1", "target-2"]):
            t = threading.Thread(target=_unregister, args=(i, aid))
            threads.append(t)
        # 3 read threads
        for i in range(3, 6):
            t = threading.Thread(target=_read_all, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i, exc in enumerate(errors):
            assert exc is None, f"Thread {i} raised: {exc}"

        # 17 remaining (20 - 3 unregistered)
        assert len(registry.lookup()) == 17

    def test_concurrent_update_status(self) -> None:
        """Concurrent status updates are serialized safely."""
        registry = AgentRegistry()
        registry.register("shared", "Shared Agent", ["x"])

        n_threads = 20
        barrier = threading.Barrier(n_threads)
        errors: list[Exception | None] = [None] * n_threads

        def _toggle(idx: int) -> None:
            barrier.wait()
            try:
                for _ in range(10):
                    registry.update_status("shared", "active")
                    registry.update_status("shared", "paused")
            except Exception as e:
                errors[idx] = e

        threads = [
            threading.Thread(target=_toggle, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i, exc in enumerate(errors):
            assert exc is None, f"Thread {i} raised: {exc}"

        # Final status should be valid
        info = registry.get("shared")
        assert info is not None
        assert info.status in ("active", "paused")
