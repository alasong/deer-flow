"""Tests for ApprovalGate thread-safe approval gate module."""

import threading

import pytest

from deerflow.tasks.approval import ApprovalGate, ApprovalRequest


@pytest.fixture
def gate():
    return ApprovalGate()


class TestApprovalGate:
    """Test suite for ApprovalGate."""

    # ------------------------------------------------------------------
    # Normal path: request -> approve
    # ------------------------------------------------------------------

    def test_request_approval_creates_pending(self, gate):
        """request_approval should create a pending ApprovalRequest."""
        req = gate.request_approval(
            task_id="task-1",
            reason="Need human approval for deployment",
            requested_by="agent-alpha",
        )
        assert isinstance(req, ApprovalRequest)
        assert req.task_id == "task-1"
        assert req.reason == "Need human approval for deployment"
        assert req.requested_by == "agent-alpha"
        assert req.status == "pending"
        assert req.approved_by is None
        assert req.rejected_by is None
        assert req.rejection_reason is None
        assert req.created_at != ""
        assert req.updated_at != ""

    def test_approve_changes_status(self, gate):
        """Approve a pending request should set status to approved."""
        gate.request_approval("task-1", "reason", "agent-alpha")
        req = gate.approve("task-1", "user-admin")
        assert req.status == "approved"
        assert req.approved_by == "user-admin"
        assert req.task_id == "task-1"
        # updated_at should have changed
        assert req.updated_at >= req.created_at

    def test_approve_returns_request_object(self, gate):
        """approve should return the updated ApprovalRequest."""
        gate.request_approval("task-1", "reason", "agent-alpha")
        req = gate.approve("task-1", "user-admin")
        assert req.task_id == "task-1"
        assert req.status == "approved"

    # ------------------------------------------------------------------
    # Normal path: request -> reject
    # ------------------------------------------------------------------

    def test_reject_with_reason(self, gate):
        """Reject a pending request with a reason should set status to rejected."""
        gate.request_approval("task-2", "Need approval", "agent-alpha")
        req = gate.reject("task-2", "user-admin", reason="Not sufficient data")
        assert req.status == "rejected"
        assert req.rejected_by == "user-admin"
        assert req.rejection_reason == "Not sufficient data"
        assert req.approved_by is None

    def test_reject_without_reason(self, gate):
        """Reject a pending request without a reason should set status to rejected."""
        gate.request_approval("task-3", "Need approval", "agent-alpha")
        req = gate.reject("task-3", "user-admin")
        assert req.status == "rejected"
        assert req.rejected_by == "user-admin"
        assert req.rejection_reason == ""

    # ------------------------------------------------------------------
    # Normal path: list_pending
    # ------------------------------------------------------------------

    def test_list_pending_returns_only_pending(self, gate):
        """list_pending should return only pending requests."""
        gate.request_approval("t1", "reason1", "agent-a")
        gate.request_approval("t2", "reason2", "agent-b")
        gate.request_approval("t3", "reason3", "agent-a")
        gate.approve("t1", "admin")
        gate.reject("t3", "admin", reason="nope")

        pending = gate.list_pending()
        assert len(pending) == 1
        assert pending[0].task_id == "t2"
        assert pending[0].status == "pending"

    def test_list_pending_empty_when_all_resolved(self, gate):
        """list_pending should be empty when all requests are approved or rejected."""
        gate.request_approval("t1", "reason", "agent-a")
        gate.approve("t1", "admin")
        assert gate.list_pending() == []

    def test_list_pending_empty_when_no_requests(self, gate):
        """list_pending should be empty when no requests exist."""
        assert gate.list_pending() == []

    # ------------------------------------------------------------------
    # Get
    # ------------------------------------------------------------------

    def test_get_existing_request(self, gate):
        """get should return the request for a valid task_id."""
        gate.request_approval("t1", "reason", "agent-a")
        req = gate.get("t1")
        assert req is not None
        assert req.task_id == "t1"

    def test_get_non_existent_returns_none(self, gate):
        """get should return None for a task_id that was never requested."""
        assert gate.get("nonexistent") is None

    def test_get_after_approve(self, gate):
        """get should return the updated request after approval."""
        gate.request_approval("t1", "reason", "agent-a")
        gate.approve("t1", "admin")
        req = gate.get("t1")
        assert req is not None
        assert req.status == "approved"

    # ------------------------------------------------------------------
    # Boundary: duplicate requests and invalid state transitions
    # ------------------------------------------------------------------

    def test_duplicate_request_raises(self, gate):
        """request_approval with an existing task_id should raise ValueError."""
        gate.request_approval("dup-task", "reason", "agent-a")
        with pytest.raises(ValueError, match="dup-task"):
            gate.request_approval("dup-task", "another reason", "agent-b")

    def test_approve_already_approved_raises(self, gate):
        """approve on an already approved request should raise ValueError."""
        gate.request_approval("t1", "reason", "agent-a")
        gate.approve("t1", "admin")
        with pytest.raises(ValueError, match="t1"):
            gate.approve("t1", "other-admin")

    def test_approve_rejected_raises(self, gate):
        """approve on a rejected request should raise ValueError."""
        gate.request_approval("t1", "reason", "agent-a")
        gate.reject("t1", "admin", reason="nope")
        with pytest.raises(ValueError, match="t1"):
            gate.approve("t1", "other-admin")

    def test_reject_already_rejected_raises(self, gate):
        """reject on an already rejected request should raise ValueError."""
        gate.request_approval("t1", "reason", "agent-a")
        gate.reject("t1", "admin", reason="nope")
        with pytest.raises(ValueError, match="t1"):
            gate.reject("t1", "other-admin", reason="still nope")

    def test_reject_approved_raises(self, gate):
        """reject on an approved request should raise ValueError."""
        gate.request_approval("t1", "reason", "agent-a")
        gate.approve("t1", "admin")
        with pytest.raises(ValueError, match="t1"):
            gate.reject("t1", "other-admin", reason="too late")

    # ------------------------------------------------------------------
    # Concurrency safety
    # ------------------------------------------------------------------

    def test_concurrent_approve_same_request(self, gate):
        """Multiple threads approving the same request must be safe; only one succeeds."""
        gate.request_approval("concurrent", "reason", "agent-a")

        results = []
        lock = threading.Lock()

        def approve_worker():
            try:
                gate.approve("concurrent", "worker")
                with lock:
                    results.append("ok")
            except ValueError:
                with lock:
                    results.append("err")

        threads = [threading.Thread(target=approve_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread succeeded
        assert results.count("ok") == 1
        assert results.count("err") == 7

        # Final state is approved
        req = gate.get("concurrent")
        assert req is not None
        assert req.status == "approved"

    def test_concurrent_request_different_tasks(self, gate):
        """Multiple threads requesting approval for different tasks must all succeed."""
        results = []
        lock = threading.Lock()

        def request_worker(i: int):
            try:
                gate.request_approval(f"ct-{i}", f"reason {i}", f"agent-{i}")
                with lock:
                    results.append("ok")
            except ValueError:
                with lock:
                    results.append("err")

        threads = [threading.Thread(target=request_worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        assert results.count("ok") == 20
        assert results.count("err") == 0

        pending = gate.list_pending()
        assert len(pending) == 20
