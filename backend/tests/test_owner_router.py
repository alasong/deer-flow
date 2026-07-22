"""Tests for the Owner API REST router.

Uses FastAPI ``TestClient`` against a minimal app with the owner router to verify
that every endpoint returns the expected shape and reacts correctly to valid and
invalid inputs.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.gateway.routers import owner as owner_router
from deerflow.agents import owner as owner_singletons
from deerflow.tasks.model import Task


@pytest.fixture(autouse=True)
def _reset_owner_state():
    """Clear in-memory state in all owner singletons before each test."""
    reg = owner_singletons.get_registry()
    reg._agents.clear()
    q = owner_singletons.get_queue()
    q._tasks.clear()
    q._pending.clear()
    b = owner_singletons.get_board()
    b._board.clear()
    b._events.clear()
    # Replace the router's approval gate with a fresh one
    owner_router._approval_gate = owner_router.ApprovalGate()
    yield


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(owner_router.router)
    return app


class TestGetAgents:
    """GET /api/owner/agents"""

    def test_empty(self):
        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/agents")
        assert resp.status_code == 200
        assert resp.json() == {"agents": []}

    def test_with_registered_agents(self):
        registry = owner_singletons.get_registry()
        registry.register("agent-1", "Agent One", capabilities=["search", "code"])
        registry.register("agent-2", "Agent Two", capabilities=["web"])

        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["agents"]) == 2

        agents = {a["agent_id"]: a for a in data["agents"]}
        assert agents["agent-1"]["name"] == "Agent One"
        assert agents["agent-1"]["capabilities"] == ["search", "code"]
        assert agents["agent-1"]["status"] == "active"
        assert "registered_at" in agents["agent-1"]

        assert agents["agent-2"]["name"] == "Agent Two"
        assert agents["agent-2"]["capabilities"] == ["web"]

    def test_only_active_returned(self):
        registry = owner_singletons.get_registry()
        registry.register("agent.a", "A")
        registry.update_status("agent.a", "paused")
        registry.register("agent.b", "B")

        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/agents")
        assert resp.status_code == 200
        assert len(resp.json()["agents"]) == 1
        assert resp.json()["agents"][0]["agent_id"] == "agent.b"


class TestGetQueue:
    """GET /api/owner/queue"""

    def test_empty(self):
        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/queue")
        assert resp.status_code == 200
        assert resp.json() == {"pending": [], "active": []}

    def test_with_pending_and_active(self):
        queue = owner_singletons.get_queue()
        queue.enqueue(Task(task_id="t1", capability="search", description="Search"))
        queue.enqueue(Task(task_id="t2", capability="code", description="Write code"))
        queue.claim("agent-1")

        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["pending"]) == 1
        assert data["pending"][0]["task_id"] == "t2"
        assert len(data["active"]) == 1
        assert data["active"][0]["task_id"] == "t1"


class TestGetBoard:
    """GET /api/owner/board"""

    def test_empty(self):
        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/board")
        assert resp.status_code == 200
        assert resp.json() == {"entries": []}

    def test_all_entries(self):
        board = owner_singletons.get_board()
        board.post("status.agent-1", "ready", updated_by="agent-1")
        board.post("task.1", {"progress": 0.5}, updated_by="agent-1")

        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/board")
        assert resp.status_code == 200
        assert len(resp.json()["entries"]) == 2

    def test_filtered_by_prefix(self):
        board = owner_singletons.get_board()
        board.post("task.1", "v1", updated_by="a1")
        board.post("task.2", "v2", updated_by="a1")
        board.post("status.x", "ready", updated_by="a1")

        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/board?prefix=task.")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 2
        assert {e["key"] for e in data["entries"]} == {"task.1", "task.2"}


class TestApprovals:
    """Approval endpoints"""

    def test_list_empty(self):
        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/approvals")
        assert resp.status_code == 200
        assert resp.json() == {"approvals": []}

    def test_create(self):
        with TestClient(_make_app()) as client:
            resp = client.post(
                "/api/owner/approvals/new",
                json={
                    "task_id": "task-1",
                    "reason": "Need review",
                    "requested_by": "agent-1",
                },
            )
        assert resp.status_code == 200
        a = resp.json()["approval"]
        assert a["task_id"] == "task-1"
        assert a["reason"] == "Need review"
        assert a["requested_by"] == "agent-1"
        assert a["status"] == "pending"
        assert a["approved_by"] is None
        assert a["rejected_by"] is None
        assert a["rejection_reason"] is None
        assert "created_at" in a
        assert "updated_at" in a

    def test_create_duplicate_returns_409(self):
        with TestClient(_make_app()) as client:
            client.post(
                "/api/owner/approvals/new",
                json={"task_id": "t1", "reason": "First", "requested_by": "a1"},
            )
            resp = client.post(
                "/api/owner/approvals/new",
                json={"task_id": "t1", "reason": "Again", "requested_by": "a2"},
            )
        assert resp.status_code == 409

    def test_approve(self):
        owner_router._approval_gate.request_approval("t1", "Review", "agent-1")
        with TestClient(_make_app()) as client:
            resp = client.post(
                "/api/owner/approvals/t1/approve",
                json={"approved_by": "admin"},
            )
        assert resp.status_code == 200
        assert resp.json()["approval"]["status"] == "approved"
        assert resp.json()["approval"]["approved_by"] == "admin"

    def test_approve_nonexistent_returns_409(self):
        with TestClient(_make_app()) as client:
            resp = client.post(
                "/api/owner/approvals/no-such-task/approve",
                json={"approved_by": "admin"},
            )
        assert resp.status_code == 409

    def test_reject(self):
        owner_router._approval_gate.request_approval("t2", "Review", "agent-1")
        with TestClient(_make_app()) as client:
            resp = client.post(
                "/api/owner/approvals/t2/reject",
                json={"rejected_by": "admin", "reason": "Not needed"},
            )
        assert resp.status_code == 200
        a = resp.json()["approval"]
        assert a["status"] == "rejected"
        assert a["rejected_by"] == "admin"
        assert a["rejection_reason"] == "Not needed"

    def test_reject_already_approved_returns_409(self):
        gate = owner_router._approval_gate
        gate.request_approval("t3", "Review", "agent-1")
        gate.approve("t3", "admin")
        with TestClient(_make_app()) as client:
            resp = client.post(
                "/api/owner/approvals/t3/reject",
                json={"rejected_by": "admin", "reason": "No"},
            )
        assert resp.status_code == 409

    def test_approved_not_in_pending_list(self):
        owner_router._approval_gate.request_approval("t1", "Review", "agent-1")
        owner_router._approval_gate.approve("t1", "admin")
        with TestClient(_make_app()) as client:
            resp = client.get("/api/owner/approvals")
        assert resp.json()["approvals"] == []
