"""Tests for the agent tasks REST API router."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.gateway.routers.agent_tasks import (
    router,
    setup as router_setup,
)
from deerflow.agents.model import Agent
from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.tasks.model import Task
from deerflow.tasks.store import TaskStore


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def stores() -> dict[str, Any]:
    registry = AgentRegistry()
    task_store = TaskStore()
    checkpointer = TaskCheckpointer(MagicMock())
    router_setup(registry, task_store, checkpointer)
    return {
        "registry": registry,
        "task_store": task_store,
        "checkpointer": checkpointer,
    }


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
        """Without setup(), the router should return 503."""
        from app.gateway.routers import agent_tasks as at_module

        # Reset globals
        at_module._registry = None
        at_module._task_store = None
        at_module._checkpointer = None

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(at_module.router)
        client = TestClient(app)

        resp = client.post("/api/agents/tasks", json={"capability": "x", "description": "x"})
        assert resp.status_code == 503
