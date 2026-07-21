"""Tests for the agent tasks REST API router."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import agent_tasks as at_module
from deerflow.agents.model import Agent
from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.tasks.gate import HumanGate
from deerflow.tasks.gate_store import HumanGateStore
from deerflow.tasks.model import Task
from deerflow.tasks.store import TaskStore


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(at_module.router)
    return app


@pytest.fixture
def stores(app: FastAPI) -> dict[str, Any]:
    """Set up stores on app.state."""
    registry = AgentRegistry()
    task_store = TaskStore()
    checkpointer = TaskCheckpointer(MagicMock())
    gate_store = HumanGateStore()

    app.state.agent_registry = registry
    app.state.task_store = task_store
    app.state.checkpointer = checkpointer
    app.state.gate_store = gate_store

    return {
        "registry": registry,
        "task_store": task_store,
        "checkpointer": checkpointer,
        "gate_store": gate_store,
    }


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestAgentEndpoints:
    def test_create_agent(self, client: TestClient, stores):
        resp = client.post("/api/agents/agents", json={
            "agent_id": "agent.dev",
            "name": "Dev Agent",
            "capabilities": ["dev.code", "dev.ops"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "agent.dev"
        assert data["status"] == "idle"
        assert data["capabilities"] == ["dev.code", "dev.ops"]

    def test_create_duplicate(self, client: TestClient, stores):
        client.post("/api/agents/agents", json={
            "agent_id": "agent.dev",
            "name": "Dev Agent",
        })
        resp = client.post("/api/agents/agents", json={
            "agent_id": "agent.dev",
            "name": "Dev Agent 2",
        })
        assert resp.status_code == 409

    def test_list_agents(self, client: TestClient, stores):
        client.post("/api/agents/agents", json={"agent_id": "a.1", "name": "A1"})
        client.post("/api/agents/agents", json={"agent_id": "a.2", "name": "A2"})
        resp = client.get("/api/agents/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["agents"]) == 2

    def test_get_agent(self, client: TestClient, stores):
        client.post("/api/agents/agents", json={
            "agent_id": "agent.dev",
            "name": "Dev",
            "capabilities": ["dev"],
        })
        resp = client.get("/api/agents/agents/agent.dev")
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "agent.dev"

    def test_get_agent_not_found(self, client: TestClient, stores):
        resp = client.get("/api/agents/agents/nonexistent")
        assert resp.status_code == 404

    def test_delete_agent(self, client: TestClient, stores):
        client.post("/api/agents/agents", json={"agent_id": "agent.x", "name": "X"})
        resp = client.delete("/api/agents/agents/agent.x")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_not_found(self, client: TestClient, stores):
        resp = client.delete("/api/agents/agents/nonexistent")
        assert resp.status_code == 404


class TestTaskEndpoints:
    def test_submit_task(self, client: TestClient, stores):
        resp = client.post("/api/agents/tasks", json={
            "capability": "dev.code",
            "description": "Fix bug",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"].startswith("task_")
        assert data["status"] == "pending"

    def test_submit_with_payload(self, client: TestClient, stores):
        resp = client.post("/api/agents/tasks", json={
            "capability": "dev.code",
            "description": "Fix bug",
            "payload": {"file": "main.py", "priority": 1},
        })
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        task = stores["task_store"].get(task_id)
        assert task is not None
        assert task.payload == {"file": "main.py", "priority": 1}

    def test_list_tasks(self, client: TestClient, stores):
        client.post("/api/agents/tasks", json={"capability": "x", "description": "1"})
        client.post("/api/agents/tasks", json={"capability": "x", "description": "2"})
        resp = client.get("/api/agents/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_tasks_filter_status(self, client: TestClient, stores):
        task_store = stores["task_store"]
        t1 = Task(task_id="t1", capability="x", description="1")
        t2 = Task(task_id="t2", capability="x", description="2", status="completed")
        task_store.put(t1)
        task_store.put(t2)

        resp = client.get("/api/agents/tasks?status=pending")
        assert len(resp.json()) == 1

        resp = client.get("/api/agents/tasks?status=completed")
        assert len(resp.json()) == 1

    def test_get_task(self, client: TestClient, stores):
        resp = client.post("/api/agents/tasks", json={
            "capability": "dev.code",
            "description": "Fix bug",
        })
        task_id = resp.json()["task_id"]
        resp = client.get(f"/api/agents/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_get_task_not_found(self, client: TestClient, stores):
        resp = client.get("/api/agents/tasks/nonexistent")
        assert resp.status_code == 404

    def test_claim_task(self, client: TestClient, stores):
        resp = client.post("/api/agents/tasks", json={
            "capability": "dev.code",
            "description": "Fix bug",
        })
        task_id = resp.json()["task_id"]

        resp = client.post(f"/api/agents/tasks/{task_id}/claim", json={
            "agent_id": "agent.dev",
        })
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "agent.dev"
        assert resp.json()["status"] == "claimed"

    def test_claim_already_claimed(self, client: TestClient, stores):
        task_store = stores["task_store"]
        task_store.put(Task(task_id="t1", capability="x", description="x"))
        task_store.claim("t1", "agent.dev")

        resp = client.post("/api/agents/tasks/t1/claim", json={"agent_id": "agent.x"})
        assert resp.status_code == 409

    def test_cancel_task(self, client: TestClient, stores):
        resp = client.post("/api/agents/tasks", json={
            "capability": "dev.code",
            "description": "Fix bug",
        })
        task_id = resp.json()["task_id"]

        resp = client.post(f"/api/agents/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_nonexistent(self, client: TestClient, stores):
        resp = client.post("/api/agents/tasks/nonexistent/cancel")
        assert resp.status_code == 404

    def test_not_configured(self):
        """Without app.state stores, the router should return 503."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(at_module.router)
        client = TestClient(app)

        resp = client.post("/api/agents/tasks", json={"capability": "x", "description": "x"})
        assert resp.status_code == 503


class TestGateEndpoints:
    def test_list_gates_empty(self, client, stores):
        resp = client.get("/api/agents/gates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["gates"] == []

    def test_list_gates(self, client, stores):
        gate_store = stores["gate_store"]
        gate_store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        gate_store.create(HumanGate(gate_id="g2", task_id="t2", step_index=0))

        resp = client.get("/api/agents/gates")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_list_gates_filter_status(self, client, stores):
        gate_store = stores["gate_store"]
        gate_store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        gate_store.create(HumanGate(gate_id="g2", task_id="t1", step_index=0))
        gate_store.approve("g2")

        resp = client.get("/api/agents/gates?status=pending")
        assert resp.json()["count"] == 1

        resp = client.get("/api/agents/gates?status=approved")
        assert resp.json()["count"] == 1

    def test_list_gates_filter_task(self, client, stores):
        gate_store = stores["gate_store"]
        gate_store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        gate_store.create(HumanGate(gate_id="g2", task_id="t2", step_index=0))

        resp = client.get("/api/agents/gates?task_id=t1")
        assert resp.json()["count"] == 1
        assert resp.json()["gates"][0]["gate_id"] == "g1"

    def test_get_gate(self, client, stores):
        gate_store = stores["gate_store"]
        gate_store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))

        resp = client.get("/api/agents/gates/g1")
        assert resp.status_code == 200
        assert resp.json()["gate_id"] == "g1"

    def test_get_gate_not_found(self, client, stores):
        resp = client.get("/api/agents/gates/nonexistent")
        assert resp.status_code == 404

    def test_approve_gate(self, client, stores):
        gate_store = stores["gate_store"]
        gate_store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))

        resp = client.post("/api/agents/gates/g1/approve", json={
            "approved_by": "admin",
            "human_input": "Looks good",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["approved_by"] == "admin"
        assert data["human_input"] == "Looks good"

    def test_approve_not_found(self, client, stores):
        resp = client.post("/api/agents/gates/nonexistent/approve", json={})
        assert resp.status_code == 404

    def test_approve_already_approved(self, client, stores):
        gate_store = stores["gate_store"]
        gate_store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        gate_store.approve("g1")

        resp = client.post("/api/agents/gates/g1/approve", json={})
        assert resp.status_code == 409

    def test_reject_gate(self, client, stores):
        gate_store = stores["gate_store"]
        gate_store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))

        resp = client.post("/api/agents/gates/g1/reject", json={
            "approved_by": "admin",
            "human_input": "Not ready",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert resp.json()["human_input"] == "Not ready"

    def test_reject_not_found(self, client, stores):
        resp = client.post("/api/agents/gates/nonexistent/reject", json={})
        assert resp.status_code == 404

    def test_gate_not_configured(self):
        """Without gate_store on app.state, gate endpoints should return 503."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(at_module.router)
        client = TestClient(app)

        resp = client.get("/api/agents/gates")
        assert resp.status_code == 503
