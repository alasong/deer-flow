"""Tests for LivingAgentService — Gateway lifespan integration."""

from __future__ import annotations

import pytest

from deerflow.agents.model import Agent
from deerflow.tasks.model import Task


class TestLivingAgentService:
    def test_create_and_start_stop(self):
        from app.gateway.living_agent import LivingAgentService

        service = LivingAgentService(poll_interval=0.5)
        assert service.registry is not None
        assert service.task_store is not None
        assert service.checkpointer is not None
        assert service.gate_store is not None

    async def test_lifecycle(self):
        from app.gateway.living_agent import LivingAgentService

        service = LivingAgentService(poll_interval=0.5)
        assert service._worker is None

        await service.start()
        assert service._worker is not None

        await service.stop()
        assert service._worker is None

    async def test_with_data(self):
        from app.gateway.living_agent import LivingAgentService

        service = LivingAgentService(poll_interval=0.5)

        # Add some data before start
        service.registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        service.task_store.put(Task(task_id="t1", capability="dev", description="Test task"))

        await service.start()
        # Worker should be running
        assert service._worker is not None

        await service.stop()
        # Data should persist after stop
        assert service.registry.get("agent.dev") is not None
        assert service.task_store.get("t1") is not None

    async def test_default_executor_processes_task(self):
        """Default executor should process a task to completion via worker poll."""
        import asyncio

        from app.gateway.living_agent import LivingAgentService
        from deerflow.agents.model import Agent
        from deerflow.tasks.model import Task

        service = LivingAgentService(poll_interval=0.1)

        service.registry.register(Agent(
            agent_id="agent.test",
            name="Test Agent",
            capabilities=["test.run"],
        ))
        service.task_store.put(Task(
            task_id="t1",
            capability="test",
            description="Run a test task",
        ))

        await service.start()
        await asyncio.sleep(0.3)  # Allow worker to poll
        await service.stop()

        task = service.task_store.get("t1")
        assert task is not None
        assert task.status.value == "completed", f"Expected completed, got {task.status.value}"

    def test_router_app_state_integration(self):
        """LivingAgentService stores should be settable on app.state for DI."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.gateway.routers import agent_tasks as at_module
        from app.gateway.living_agent import LivingAgentService

        service = LivingAgentService()
        app = FastAPI()
        app.include_router(at_module.router)
        app.state.agent_registry = service.registry
        app.state.task_store = service.task_store
        app.state.gate_store = service.gate_store

        client = TestClient(app)

        # Verify endpoints work via app.state DI
        resp = client.post("/api/agents/tasks", json={
            "capability": "dev.code",
            "description": "Test task",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

        # Verify stores are the same instances
        assert app.state.agent_registry is service.registry
        assert app.state.task_store is service.task_store
        assert app.state.gate_store is service.gate_store
