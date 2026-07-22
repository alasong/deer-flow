"""Thread-safe in-memory approval gate module.

Provides ApprovalRequest data class and ApprovalGate class for lightweight
approval workflow management. Independent of the Task model and queue —
uses only the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Self


@dataclass
class ApprovalRequest:
    """An approval request associated with a task.

    Lifecycle: pending -> approved | rejected
    """

    task_id: str
    reason: str
    requested_by: str
    status: str = "pending"
    approved_by: str | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


class ApprovalGate:
    """A thread-safe in-memory approval gate.

    Internal structure:
      - _requests: dict[str, ApprovalRequest] — all requests indexed by task_id
      - _lock: Lock — protects all concurrent access
    """

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_approval(self, task_id: str, reason: str, requested_by: str) -> ApprovalRequest:
        """Create a new approval request.

        Raises ValueError if a request for the given task_id already exists.
        """
        with self._lock:
            if task_id in self._requests:
                raise ValueError(f"Approval request already exists for task {task_id!r}")
            req = ApprovalRequest(
                task_id=task_id,
                reason=reason,
                requested_by=requested_by,
            )
            self._requests[task_id] = req
            return req

    def approve(self, task_id: str, approved_by: str) -> ApprovalRequest:
        """Approve a pending approval request.

        Raises ValueError if the request does not exist or is not in pending status.
        """
        with self._lock:
            req = self._get_request(task_id)
            if req.status != "pending":
                raise ValueError(
                    f"Cannot approve request for task {task_id!r}: "
                    f"current status is {req.status!r}"
                )
            req.status = "approved"
            req.approved_by = approved_by
            req.updated_at = datetime.now(timezone.utc).isoformat()
            return req

    def reject(
        self,
        task_id: str,
        rejected_by: str,
        reason: str = "",
    ) -> ApprovalRequest:
        """Reject a pending approval request.

        Raises ValueError if the request does not exist or is not in pending status.
        """
        with self._lock:
            req = self._get_request(task_id)
            if req.status != "pending":
                raise ValueError(
                    f"Cannot reject request for task {task_id!r}: "
                    f"current status is {req.status!r}"
                )
            req.status = "rejected"
            req.rejected_by = rejected_by
            req.rejection_reason = reason
            req.updated_at = datetime.now(timezone.utc).isoformat()
            return req

    def get(self, task_id: str) -> ApprovalRequest | None:
        """Look up an approval request by task_id. Returns None if not found."""
        with self._lock:
            return self._requests.get(task_id)

    def list_pending(self) -> list[ApprovalRequest]:
        """Return all approval requests with pending status."""
        with self._lock:
            return [r for r in self._requests.values() if r.status == "pending"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_request(self, task_id: str) -> ApprovalRequest:
        """Get a request by task_id. Raises ValueError if not found.

        Must be called with _lock held.
        """
        req = self._requests.get(task_id)
        if req is None:
            raise ValueError(f"No approval request found for task {task_id!r}")
        return req
