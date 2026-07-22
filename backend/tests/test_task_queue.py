"""Tests for TaskQueue thread-safe in-memory task queue."""

import threading

import pytest

from deerflow.tasks.model import Task, TaskStatus
from deerflow.tasks.queue import TaskQueue


@pytest.fixture
def queue():
    return TaskQueue()


class TestTaskQueue:
    """Test suite for TaskQueue."""

    def test_enqueue_returns_task_id(self, queue):
        """Enqueue a task and verify it returns the task_id."""
        task = Task(task_id="t1", capability="test", description="enqueue test")
        task_id = queue.enqueue(task)
        assert task_id == "t1"
        assert queue.get("t1") is not None
        assert queue.get("t1").status == TaskStatus.pending

    def test_claim_changes_status_to_claimed(self, queue):
        """Claim a pending task and verify its status changes to claimed."""
        task = Task(task_id="t2", capability="test", description="claim test")
        queue.enqueue(task)
        claimed = queue.claim(agent_id="agent-a")
        assert claimed is not None
        assert claimed.task_id == "t2"
        assert claimed.status == TaskStatus.claimed
        assert claimed.agent_id == "agent-a"

    def test_claim_returns_none_when_no_pending(self, queue):
        """Claiming from an empty queue should return None."""
        assert queue.claim(agent_id="agent-a") is None

    def test_claim_skips_cancelled_tasks_in_deque(self, queue):
        """Claim should skip cancelled tasks that remain in the pending deque."""
        queue.enqueue(Task(task_id="c1", capability="test", description="will be cancelled"))
        queue.enqueue(Task(task_id="c2", capability="test", description="will be claimed"))
        queue.cancel("c1")
        claimed = queue.claim(agent_id="agent-a")
        assert claimed is not None
        assert claimed.task_id == "c2"

    def test_complete_task(self, queue):
        """Complete a claimed task transitions to completed with result."""
        task = Task(task_id="t5", capability="test", description="complete test")
        queue.enqueue(task)
        queue.claim(agent_id="agent-a")
        completed = queue.complete("t5", {"output": "done"})
        assert completed.status == TaskStatus.completed
        assert completed.result == {"output": "done"}
        assert queue.get("t5").status == TaskStatus.completed

    def test_fail_claimed_task(self, queue):
        """Fail a claimed task transitions to failed with error."""
        task = Task(task_id="t6", capability="test", description="fail test")
        queue.enqueue(task)
        queue.claim(agent_id="agent-a")
        failed = queue.fail("t6", "error occurred")
        assert failed.status == TaskStatus.failed
        assert failed.error == "error occurred"

    def test_cancel_pending_task(self, queue):
        """Cancel a pending task transitions to cancelled."""
        task = Task(task_id="t7", capability="test", description="cancel pending")
        queue.enqueue(task)
        cancelled = queue.cancel("t7")
        assert cancelled.status == TaskStatus.cancelled

    def test_cancel_claimed_task(self, queue):
        """Cancel a claimed task transitions to cancelled."""
        task = Task(task_id="t8", capability="test", description="cancel claimed")
        queue.enqueue(task)
        queue.claim(agent_id="agent-a")
        cancelled = queue.cancel("t8")
        assert cancelled.status == TaskStatus.cancelled

    def test_list_pending_returns_only_pending(self, queue):
        """list_pending returns only tasks with pending status."""
        for i in range(4):
            queue.enqueue(Task(task_id=f"lp-{i}", capability="test", description=str(i)))
        queue.claim(agent_id="agent-a")  # claims lp-0
        queue.claim(agent_id="agent-a")  # claims lp-1
        pending = queue.list_pending()
        assert len(pending) == 2
        assert all(t.status == TaskStatus.pending for t in pending)

    def test_list_active_returns_claimed_tasks(self, queue):
        """list_active returns tasks that are claimed or executing."""
        queue.enqueue(Task(task_id="la-0", capability="test", description="active"))
        queue.enqueue(Task(task_id="la-1", capability="test", description="pending"))
        queue.claim(agent_id="agent-a")  # claims la-0
        active = queue.list_active()
        assert len(active) == 1
        assert active[0].task_id == "la-0"
        assert active[0].status == TaskStatus.claimed

    def test_get_existing_task(self, queue):
        """get returns the task for a valid task_id."""
        queue.enqueue(Task(task_id="g1", capability="test", description="get test"))
        result = queue.get("g1")
        assert result is not None
        assert result.task_id == "g1"

    def test_get_non_existent_task_returns_none(self, queue):
        """get returns None for a non-existent task_id."""
        assert queue.get("does-not-exist") is None

    def test_concurrent_claim_safety(self, queue):
        """Multiple threads claiming simultaneously must not duplicate tasks."""
        num_tasks = 20
        for i in range(num_tasks):
            queue.enqueue(Task(task_id=f"c-{i}", capability="test", description=str(i)))

        claimed_ids = []
        lock = threading.Lock()

        def claim_worker():
            while True:
                claimed = queue.claim(agent_id="worker")
                if claimed is None:
                    break
                with lock:
                    claimed_ids.append(claimed.task_id)

        threads = [threading.Thread(target=claim_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(claimed_ids) == num_tasks
        assert len(set(claimed_ids)) == num_tasks  # no duplicates

    def test_complete_fails_on_pending_task(self, queue):
        """Complete on a pending (unclaimed) task should raise ValueError."""
        queue.enqueue(Task(task_id="e1", capability="test", description="error test"))
        with pytest.raises(ValueError):
            queue.complete("e1", {})

    def test_fail_on_terminal_task_raises(self, queue):
        """Fail on a completed task should raise ValueError."""
        queue.enqueue(Task(task_id="e2", capability="test", description="fail already done"))
        queue.claim(agent_id="agent-a")
        queue.complete("e2", {"ok": True})
        with pytest.raises(ValueError):
            queue.fail("e2", "too late")
