"""Thread-safe in-memory task queue.

Reuses Task model and lifecycle transitions from deerflow.tasks.model.
"""

from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any

from deerflow.tasks.model import (
    Task,
    TaskStatus,
    transition_cancel,
    transition_claim,
    transition_complete,
    transition_execute,
    transition_fail,
)


class TaskQueue:
    """A thread-safe FIFO queue for Task objects.

    Internal structure:
      - _tasks: dict[str, Task] — all tasks indexed by task_id
      - _pending: deque[str] — FIFO queue of task_ids in pending status
      - _lock: Lock — protects all concurrent access
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._pending: deque[str] = deque()
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, task: Task) -> str:
        """Add a task to the queue and return its task_id."""
        with self._lock:
            self._tasks[task.task_id] = task
            if task.status == TaskStatus.pending:
                self._pending.append(task.task_id)
            return task.task_id

    def claim(self, agent_id: str) -> Task | None:
        """Claim the next pending task (FIFO order). Returns None if none available."""
        with self._lock:
            while self._pending:
                task_id = self._pending.popleft()
                task = self._tasks.get(task_id)
                if task is not None and task.status == TaskStatus.pending:
                    transition_claim(task, agent_id)
                    return task
            return None

    def complete(self, task_id: str, result: dict[str, Any]) -> Task:
        """Transition a claimed task to completed.

        Internally runs claimed -> executing -> completed.
        """
        with self._lock:
            task = self._tasks[task_id]
            transition_execute(task)
            transition_complete(task, result)
            return task

    def fail(self, task_id: str, error: str) -> Task:
        """Transition a claimed or executing task to failed."""
        with self._lock:
            task = self._tasks[task_id]
            transition_fail(task, error)
            return task

    def cancel(self, task_id: str) -> Task:
        """Cancel a pending or claimed task.

        The task_id may remain in the internal pending deque after cancellation;
        it will be skipped on the next claim() call.
        """
        with self._lock:
            task = self._tasks[task_id]
            transition_cancel(task)
            return task

    def list_pending(self) -> list[Task]:
        """Return all tasks with pending status."""
        with self._lock:
            return [
                t for t in self._tasks.values() if t.status == TaskStatus.pending
            ]

    def list_active(self) -> list[Task]:
        """Return all tasks with claimed or executing status."""
        with self._lock:
            return [
                t
                for t in self._tasks.values()
                if t.status in (TaskStatus.claimed, TaskStatus.executing)
            ]

    def get(self, task_id: str) -> Task | None:
        """Look up a task by task_id. Returns None if not found."""
        with self._lock:
            return self._tasks.get(task_id)
