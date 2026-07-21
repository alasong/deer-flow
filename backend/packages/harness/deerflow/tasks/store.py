from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from deerflow.tasks.model import Task, TaskStatus, transition_cancel, transition_claim, transition_complete, transition_execute, transition_fail

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure data-shaping
# ---------------------------------------------------------------------------


def serialize_tasks(tasks: list[Task]) -> dict[str, Any]:
    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks),
    }


def deserialize_tasks(data: dict[str, Any]) -> list[Task]:
    return [Task.from_dict(t) for t in data.get("tasks", [])]


def apply_claim(task: Task, agent_id: str) -> Task:
    """Pure: return a new Task in claimed status, or raise."""
    return transition_claim(task, agent_id)


def apply_execute(task: Task) -> Task:
    """Pure: return a new Task in executing status, or raise."""
    return transition_execute(task)


def apply_complete(task: Task, result: dict[str, Any]) -> Task:
    """Pure: return a new Task in completed status, or raise."""
    return transition_complete(task, result)


def apply_fail(task: Task, error: str) -> Task:
    """Pure: return a new Task in failed status, or raise."""
    return transition_fail(task, error)


def apply_cancel(task: Task) -> Task:
    """Pure: return a new Task in cancelled status, or raise."""
    return transition_cancel(task)


# ---------------------------------------------------------------------------
# Task Store
# ---------------------------------------------------------------------------


class TaskStore:
    """Thread-safe in-memory task store with optional file persistence."""

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = Lock()
        self._file_path = Path(file_path) if file_path else None
        if self._file_path:
            self._load()

    def put(self, task: Task) -> Task:
        with self._lock:
            self._tasks[task.task_id] = task
            self._save()
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, status: TaskStatus | None = None, agent_id: str | None = None) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if agent_id is not None:
            tasks = [t for t in tasks if t.agent_id == agent_id]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def claim(self, task_id: str, agent_id: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task = apply_claim(task, agent_id)
            self._tasks[task_id] = task
            self._save()
            return task

    def start_executing(self, task_id: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task = apply_execute(task)
            self._tasks[task_id] = task
            self._save()
            return task

    def complete(self, task_id: str, result: dict[str, Any]) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task = apply_complete(task, result)
            self._tasks[task_id] = task
            self._save()
            return task

    def fail(self, task_id: str, error: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task = apply_fail(task, error)
            self._tasks[task_id] = task
            self._save()
            return task

    def cancel(self, task_id: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task = apply_cancel(task)
            self._tasks[task_id] = task
            self._save()
            return task

    def find_pending(self, capability: str | None = None) -> list[Task]:
        """Find claimable tasks, optionally filtered by capability prefix."""
        tasks = self.list(status=TaskStatus.pending)
        if capability:
            tasks = [t for t in tasks if t.capability.startswith(capability)]
        return tasks

    def _load(self) -> None:
        if not self._file_path or not self._file_path.exists():
            return
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            tasks = deserialize_tasks(data)
            self._tasks = {t.task_id: t for t in tasks}
            logger.info("Loaded %d tasks from %s", len(tasks), self._file_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load tasks: %s", exc)

    def _save(self) -> None:
        if not self._file_path:
            return
        data = serialize_tasks(list(self._tasks.values()))
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
