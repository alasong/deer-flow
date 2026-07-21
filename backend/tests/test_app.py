"""Tests for the main FastAPI gateway app."""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_agent_tasks_router_registered():
    """Verify agent_tasks router is included in create_app().

    The agent_tasks router provides the Living Agent REST API
    (agents CRUD, task lifecycle, human gates). If this test fails,
    the router was likely omitted from create_app()'s include_router calls.
    """
    with (
        mock.patch("app.gateway.app.get_gateway_config") as mock_gc,
        mock.patch("app.gateway.app._resolve_trace_enabled_for_app_construction", return_value=False),
    ):
        mock_cfg = mock.MagicMock()
        mock_cfg.host = "0.0.0.0"
        mock_cfg.port = 8001
        mock_cfg.enable_docs = False
        mock_gc.return_value = mock_cfg

        from app.gateway.app import create_app

        app = create_app()

    route_paths = {r.path for r in app.routes}
    assert "/api/agents/tasks" in route_paths, "agent_tasks task routes not registered"
    assert "/api/agents/gates" in route_paths, "agent_tasks gate routes not registered"
    assert "/api/agents/agents" in route_paths, "agent_tasks agent routes not registered"


@pytest.mark.asyncio
async def test_full_flow_integration():
    """Integration test: API → TaskStore → Worker → completion via default executor."""
    from app.gateway.living_agent import LivingAgentService
    from app.gateway.routers import agent_tasks

    service = LivingAgentService(poll_interval=0.1)
    await service.start()

    # Wire stores into the router (same as lifespan does)
    agent_tasks.setup(
        registry=service.registry,
        task_store=service.task_store,
        checkpointer=service.checkpointer,
        gate_store=service.gate_store,
    )

    # Build a minimal FastAPI app with the agent_tasks router
    app = FastAPI()
    app.include_router(agent_tasks.router)

    with TestClient(app) as client:
        # Register an agent
        client.post("/api/agents/agents", json={
            "agent_id": "agent.dev",
            "name": "Dev Agent",
            "capabilities": ["dev.code"],
        })

        # Submit a task
        resp = client.post("/api/agents/tasks", json={
            "capability": "dev",
            "description": "Fix the login bug",
        })
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        assert task_id.startswith("task_")

        # Wait for the worker to poll and process the task
        await asyncio.sleep(0.3)

        # Check the task was completed by the default executor
        resp = client.get(f"/api/agents/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed", f"Expected completed, got {resp.json()['status']}"

    await service.stop()
