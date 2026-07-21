"""Tests for task model, lifecycle transitions, store, and classifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from deerflow.tasks.classifier import classify_task
from deerflow.tasks.model import (
    Task,
    TaskPriority,
    TaskStatus,
    transition_cancel,
    transition_claim,
    transition_complete,
    transition_execute,
    transition_fail,
)
from deerflow.tasks.store import TaskStore


class TestTaskModel:
    def test_create_defaults(self):
        t = Task(task_id="task_001", capability="dev.code", description="Fix bug")
        assert t.status == TaskStatus.pending
        assert t.priority == TaskPriority.normal
        assert not t.is_terminal
        assert t.is_claimable
        assert t.created_at
        assert t.updated_at

    def test_terminal_states(self):
        t = Task(task_id="t1", capability="x", description="x", status=TaskStatus.completed)
        assert t.is_terminal
        assert not t.is_claimable

    def test_roundtrip_dict(self):
        t = Task(
            task_id="t1",
            capability="dev.code",
            description="Fix bug",
            priority=TaskPriority.high,
            agent_id="agent.dev",
            payload={"key": "value"},
            result={"output": "done"},
            checkpoint_id="cp_001",
        )
        d = t.to_dict()
        restored = Task.from_dict(d)
        assert restored.task_id == t.task_id
        assert restored.agent_id == t.agent_id
        assert restored.priority == t.priority
        assert restored.payload == t.payload
        assert restored.checkpoint_id == t.checkpoint_id

    def test_from_dict_minimal(self):
        t = Task.from_dict({"task_id": "t1", "capability": "x", "description": "x"})
        assert t.status == TaskStatus.pending

    def test_auto_timestamps(self):
        t = Task(task_id="t1", capability="x", description="x")
        assert t.created_at
        assert t.updated_at


class TestLifecycleTransitions:
    def test_full_lifecycle(self):
        t = Task(task_id="t1", capability="dev.code", description="Fix bug")
        transition_claim(t, "agent.dev")
        assert t.status == TaskStatus.claimed
        assert t.agent_id == "agent.dev"

        transition_execute(t)
        assert t.status == TaskStatus.executing

        transition_complete(t, {"output": "fixed"})
        assert t.status == TaskStatus.completed
        assert t.result == {"output": "fixed"}

    def test_claim_invalid_state(self):
        t = Task(task_id="t1", capability="x", description="x", status=TaskStatus.completed)
        with pytest.raises(ValueError, match="Cannot claim"):
            transition_claim(t, "agent.x")

    def test_complete_invalid_state(self):
        t = Task(task_id="t1", capability="x", description="x", status=TaskStatus.pending)
        with pytest.raises(ValueError, match="Cannot complete"):
            transition_complete(t, {})

    def test_fail_from_claimed(self):
        t = Task(task_id="t1", capability="x", description="x")
        transition_claim(t, "agent.x")
        transition_fail(t, "something went wrong")
        assert t.status == TaskStatus.failed
        assert t.error == "something went wrong"

    def test_fail_from_executing(self):
        t = Task(task_id="t1", capability="x", description="x")
        transition_claim(t, "agent.x")
        transition_execute(t)
        transition_fail(t, "runtime error")
        assert t.status == TaskStatus.failed

    def test_cancel_pending(self):
        t = Task(task_id="t1", capability="x", description="x")
        transition_cancel(t)
        assert t.status == TaskStatus.cancelled

    def test_cancel_completed_raises(self):
        t = Task(task_id="t1", capability="x", description="x", status=TaskStatus.completed)
        with pytest.raises(ValueError, match="Cannot cancel"):
            transition_cancel(t)


class TestTaskStore:
    def test_put_and_get(self):
        store = TaskStore()
        t = Task(task_id="t1", capability="dev.code", description="Fix bug")
        store.put(t)
        assert store.get("t1") is t
        assert store.get("nonexistent") is None

    def test_full_store_lifecycle(self):
        store = TaskStore()
        t = Task(task_id="t1", capability="dev.code", description="Fix bug")
        store.put(t)

        store.claim("t1", "agent.dev")
        assert store.get("t1").status == TaskStatus.claimed

        store.start_executing("t1")
        assert store.get("t1").status == TaskStatus.executing

        store.complete("t1", {"output": "done"})
        assert store.get("t1").status == TaskStatus.completed

    def test_fail_store(self):
        store = TaskStore()
        store.put(Task(task_id="t1", capability="x", description="x"))
        store.claim("t1", "agent.x")
        store.fail("t1", "error")
        assert store.get("t1").status == TaskStatus.failed
        assert store.get("t1").error == "error"

    def test_cancel_store(self):
        store = TaskStore()
        store.put(Task(task_id="t1", capability="x", description="x"))
        store.cancel("t1")
        assert store.get("t1").status == TaskStatus.cancelled

    def test_list_filter(self):
        store = TaskStore()
        store.put(Task(task_id="t1", capability="a", description="1", status=TaskStatus.pending))
        store.put(Task(task_id="t2", capability="a", description="2", status=TaskStatus.completed))
        store.put(Task(task_id="t3", capability="b", description="3", status=TaskStatus.pending))
        assert len(store.list()) == 3
        assert len(store.list(status=TaskStatus.pending)) == 2
        assert len(store.list(status=TaskStatus.completed)) == 1

    def test_find_pending(self):
        store = TaskStore()
        store.put(Task(task_id="t1", capability="dev.code", description="1"))
        store.put(Task(task_id="t2", capability="review.code", description="2"))
        assert len(store.find_pending()) == 2
        assert len(store.find_pending(capability="dev")) == 1

    def test_file_persistence(self, tmp_path: Path):
        file_path = tmp_path / "tasks.json"
        store = TaskStore(file_path=file_path)
        store.put(Task(task_id="t1", capability="x", description="x"))
        store.put(Task(task_id="t2", capability="y", description="y"))

        store2 = TaskStore(file_path=file_path)
        assert store2.get("t1") is not None
        assert store2.get("t2") is not None
        assert len(store2.list()) == 2

    def test_load_missing_file(self, tmp_path: Path):
        store = TaskStore(file_path=tmp_path / "nonexistent.json")
        assert store.list() == []

    def test_nonexistent_operations(self):
        store = TaskStore()
        with pytest.raises(KeyError):
            store.claim("nope", "agent.x")
        with pytest.raises(KeyError):
            store.complete("nope", {})
        with pytest.raises(KeyError):
            store.fail("nope", "err")
        with pytest.raises(KeyError):
            store.cancel("nope")


class TestClassifier:
    def test_analysis_keyword(self):
        c = classify_task("调研RAG系统的现状")
        assert c.confidence > 0.5
        assert c.skill == "pdf"

    def test_implement_keyword(self):
        c = classify_task("实现用户认证功能")
        assert c.skill == "pdf"
        assert c.channel in ("full", "standard")

    def test_default_on_no_match(self):
        c = classify_task("xyzzy_nonexistent_12345")
        assert c.skill == "pdf"
        assert c.channel == "standard"
        assert c.confidence < 0.5

    def test_custom_keyword_map(self):
        custom_map = [
            ("my_skill", "fast", "custom", ["urgent", "quick"]),
        ]
        c = classify_task("this is urgent", keyword_map=custom_map)
        assert c.skill == "my_skill"
        assert c.channel == "fast"
