"""Tests for TaskCheckpointer wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deerflow.runtime.checkpointer.task_checkpointer import (
    TaskCheckpointer,
    build_task_checkpoint_meta,
    format_checkpoint_id,
)
from deerflow.tasks.model import Task

# -- Mock checkpointer --


class _MockCheckpointer:
    """Minimal mock: stores state dicts keyed by thread_id."""

    def __init__(self):
        self._data: dict[str, Any] = {}

    def put(self, config: dict, data: dict) -> None:
        tid = config["configurable"]["thread_id"]
        self._data[tid] = data

    def get(self, config: dict) -> dict | None:
        tid = config["configurable"]["thread_id"]
        return self._data.get(tid)


class TestPureFunctions:
    def test_build_meta(self):
        meta = build_task_checkpoint_meta("t1", "agent.x", "plan", {"key": "val"})
        assert meta["task_id"] == "t1"
        assert meta["agent_id"] == "agent.x"
        assert meta["phase"] == "plan"
        assert meta["context"] == {"key": "val"}

    def test_build_meta_minimal(self):
        meta = build_task_checkpoint_meta("t1", "agent.x", "plan")
        assert meta["context"] == {}

    def test_format_checkpoint_id(self):
        assert format_checkpoint_id("t1", "plan", 1) == "cp_t1_plan_0001"
        assert format_checkpoint_id("task_001", "execute", 42) == "cp_task_001_execute_0042"


class TestTaskCheckpointer:
    def test_save_and_get_checkpoint_id(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        task = Task(task_id="t1", capability="x", description="x")
        cp_id = tcp.save(task, phase="plan", context={"step": 1})
        assert cp_id.startswith("cp_t1_plan_")
        assert task.checkpoint_id == cp_id
        assert tcp.get_checkpoint_id("t1") == cp_id

    def test_has_checkpoint(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        task = Task(task_id="t1", capability="x", description="x")
        assert not tcp.has_checkpoint("t1")
        tcp.save(task, phase="plan")
        assert tcp.has_checkpoint("t1")

    def test_restore(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        task = Task(task_id="t1", capability="x", description="x")
        tcp.save(task, phase="plan", context={"step": 1})

        meta = tcp.restore(task)
        assert meta is not None
        assert meta["task_id"] == "t1"
        assert meta["phase"] == "plan"
        assert meta["context"]["step"] == 1

    def test_restore_no_checkpoint(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        task = Task(task_id="t1", capability="x", description="x")
        assert tcp.restore(task) is None

    def test_multiple_phases(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        task = Task(task_id="t1", capability="x", description="x")
        cp1 = tcp.save(task, phase="plan")
        cp2 = tcp.save(task, phase="execute", context={"progress": 50})
        # Last checkpoint takes precedence
        assert tcp.get_checkpoint_id("t1") == cp2
        assert cp1 != cp2

    def test_clear(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        task = Task(task_id="t1", capability="x", description="x")
        tcp.save(task, phase="plan")
        assert tcp.has_checkpoint("t1")
        tcp.clear("t1")
        assert not tcp.has_checkpoint("t1")

    def test_mapping_persistence(self, tmp_path: Path):
        map_path = tmp_path / "checkpoint_map.json"
        tcp = TaskCheckpointer(_MockCheckpointer(), mapping_path=map_path)
        task = Task(task_id="t1", capability="x", description="x")
        tcp.save(task, phase="plan")

        # New instance loads from file
        tcp2 = TaskCheckpointer(_MockCheckpointer(), mapping_path=map_path)
        assert tcp2.has_checkpoint("t1")
        assert tcp2.get_checkpoint_id("t1") == task.checkpoint_id

    def test_multiple_tasks(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        t1 = Task(task_id="t1", capability="x", description="x")
        t2 = Task(task_id="t2", capability="y", description="y")
        cp1 = tcp.save(t1, phase="plan")
        cp2 = tcp.save(t2, phase="execute")
        assert tcp.get_checkpoint_id("t1") == cp1
        assert tcp.get_checkpoint_id("t2") == cp2
        assert cp1 != cp2

    def test_clear_unrelated(self):
        tcp = TaskCheckpointer(_MockCheckpointer())
        t1 = Task(task_id="t1", capability="x", description="x")
        t2 = Task(task_id="t2", capability="y", description="y")
        tcp.save(t1, phase="plan")
        tcp.save(t2, phase="execute")
        tcp.clear("t1")
        assert not tcp.has_checkpoint("t1")
        assert tcp.has_checkpoint("t2")
