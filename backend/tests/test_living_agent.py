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

    def test_router_setup_integration(self):
        """LivingAgentService stores should be usable by the agent_tasks router."""
        from app.gateway.routers import agent_tasks as at_module
        from app.gateway.living_agent import LivingAgentService

        # Reset globals
        at_module._registry = None
        at_module._task_store = None
        at_module._checkpointer = None
        at_module._gate_store = None

        service = LivingAgentService()
        at_module.setup(
            registry=service.registry,
            task_store=service.task_store,
            checkpointer=service.checkpointer,
            gate_store=service.gate_store,
        )

        # Stores should be accessible via router
        assert at_module._get_registry() is service.registry
        assert at_module._get_task_store() is service.task_store
        assert at_module._get_gate_store() is service.gate_store
