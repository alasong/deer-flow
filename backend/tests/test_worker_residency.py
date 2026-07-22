"""Tests for run_agent residency decision."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from deerflow.runtime.runs.worker import _decide_residency


class TestDecideResidency:
    """Tests for _decide_residency."""

    async def test_no_checkpointer_returns_false(self):
        """No checkpointer → False."""
        result = await _decide_residency(
            checkpointer=None,
            thread_id="test-tid",
            scheduler=AsyncMock(),
        )
        assert result is False

    async def test_no_scheduler_returns_false(self):
        """No scheduler → False."""
        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=None,
        )
        assert result is False

    async def test_no_active_goal_returns_false(self, monkeypatch):
        """Goal with status != active → False."""

        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "completed", "created_at": 100}

        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)
        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=AsyncMock(),
        )
        assert result is False

    async def test_no_goal_at_all_returns_false(self, monkeypatch):
        """None goal → False."""

        async def fake_read_goal(*args, **kwargs):
            return None

        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)
        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=AsyncMock(),
        )
        assert result is False

    async def test_read_goal_raises_returns_false(self, monkeypatch):
        """Exception in read_thread_goal → False."""

        async def fake_read_goal(*args, **kwargs):
            raise RuntimeError("DB error")

        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)
        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=AsyncMock(),
        )
        assert result is False

    async def test_active_goal_dispatches_scheduler(self, monkeypatch):
        """Active goal dispatches scheduler and returns True."""

        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test objective", "status": "active", "created_at": 100}

        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        scheduler = AsyncMock()
        scheduler.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "run-1"})

        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=scheduler,
        )
        assert result is True
        scheduler.dispatch.assert_awaited_once()
        call_args = scheduler.dispatch.call_args
        assert call_args is not None
        task = call_args[0][0]
        assert task.thread_id == "test-tid"
        assert "test objective" in task.prompt
        assert task.max_runs_per_day == 0

    async def test_scheduler_launch_failed_returns_false(self, monkeypatch):
        """Scheduler returns non-launched outcome → False."""

        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}

        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        scheduler = AsyncMock()
        scheduler.dispatch = AsyncMock(return_value={"outcome": "filtered", "reason": "max_runs_exceeded"})

        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=scheduler,
        )
        assert result is False

    async def test_scheduler_raises_returns_false(self, monkeypatch):
        """Scheduler.dispatch raises → False."""

        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}

        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        scheduler = AsyncMock()
        scheduler.dispatch = AsyncMock(side_effect=RuntimeError("dispatch failed"))

        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=scheduler,
        )
        assert result is False
