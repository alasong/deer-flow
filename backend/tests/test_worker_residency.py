"""Tests for run_agent residency decision."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from deerflow.agents.goal_state import GoalEvaluation
from deerflow.agents.thread_state import GoalHealthEntry
from deerflow.runtime.runs.worker import (
    _compute_goal_progress,
    _compute_goal_throttle_delay,
    _decide_residency,
)


class TestDecideResidency:
    """Tests for _decide_residency."""

    async def test_no_checkpointer_returns_false(self):
        """No checkpointer -> False."""
        result = await _decide_residency(
            checkpointer=None,
            thread_id="test-tid",
            scheduler=AsyncMock(),
        )
        assert result is False

    async def test_no_scheduler_returns_false(self):
        """No scheduler -> False."""
        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=None,
        )
        assert result is False

    async def test_no_active_goal_returns_false(self, monkeypatch):
        """Goal with status != active -> False."""

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
        """None goal -> False."""

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
        """Exception in read_thread_goal -> False."""

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
        """Scheduler returns non-launched outcome -> False."""

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
        """Scheduler.dispatch raises -> False."""

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

    # -- Backoff + observability tests --

    async def test_backoff_attempt_count_increments(self, monkeypatch):
        """Attempt count increments in checkpoint metadata on non-launched outcome."""
        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}
        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._read_residency_attempts",
            AsyncMock(return_value=2),
        )

        written = []
        async def fake_write(checkpointer, thread_id, attempts):
            written.append(attempts)
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._write_residency_attempts",
            fake_write,
        )

        reset_called = []
        async def fake_reset(checkpointer, thread_id):
            reset_called.append(True)
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._reset_residency_attempts",
            fake_reset,
        )

        scheduler = AsyncMock()
        scheduler.dispatch = AsyncMock(return_value={"outcome": "filtered"})

        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=scheduler,
        )
        assert result is False
        assert written == [3], f"Expected attempts to increment from 2 to 3, got {written}"
        assert reset_called == [], "Should NOT reset on failure"
        assert scheduler.dispatch.await_count == 1

    async def test_backoff_resets_on_success(self, monkeypatch):
        """Attempt count resets to 0 after successful dispatch."""
        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}
        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._read_residency_attempts",
            AsyncMock(return_value=3),
        )

        written = []
        async def fake_write(checkpointer, thread_id, attempts):
            written.append(attempts)
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._write_residency_attempts",
            fake_write,
        )

        reset_called = []
        async def fake_reset(checkpointer, thread_id):
            reset_called.append(True)
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._reset_residency_attempts",
            fake_reset,
        )

        scheduler = AsyncMock()
        scheduler.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "run-1"})

        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=scheduler,
        )
        assert result is True
        assert reset_called == [True], f"Expected reset on success, got {reset_called}"
        assert written == [], "Should NOT call write on success (uses reset)"

    async def test_backoff_schedules_with_increasing_delay(self, monkeypatch):
        """Different attempt counts produce different delay_seconds on ScheduledTask."""
        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}
        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        read_mock = AsyncMock()
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._read_residency_attempts",
            read_mock,
        )

        # Attempt 0 -> delay 1800
        read_mock.return_value = 0
        s = AsyncMock()
        s.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "r1"})
        await _decide_residency(checkpointer=AsyncMock(), thread_id="tid-0", scheduler=s)
        assert s.dispatch.call_args[0][0].delay_seconds == 1800

        # Attempt 1 -> delay 3600
        read_mock.return_value = 1
        s2 = AsyncMock()
        s2.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "r2"})
        await _decide_residency(checkpointer=AsyncMock(), thread_id="tid-1", scheduler=s2)
        assert s2.dispatch.call_args[0][0].delay_seconds == 3600

        # Attempt 2 -> delay 7200
        read_mock.return_value = 2
        s3 = AsyncMock()
        s3.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "r3"})
        await _decide_residency(checkpointer=AsyncMock(), thread_id="tid-2", scheduler=s3)
        assert s3.dispatch.call_args[0][0].delay_seconds == 7200

    async def test_backoff_caps_at_max_attempts(self, monkeypatch):
        """Beyond max attempts, delay stays at max backoff value."""
        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}
        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        read_mock = AsyncMock()
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._read_residency_attempts",
            read_mock,
        )

        # Attempt 5 (beyond array length 5 -> index 4 = max)
        read_mock.return_value = 5
        s = AsyncMock()
        s.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "r1"})
        await _decide_residency(checkpointer=AsyncMock(), thread_id="tid-5", scheduler=s)
        assert s.dispatch.call_args[0][0].delay_seconds == 28800

        # Attempt 100 (way beyond)
        read_mock.return_value = 100
        s2 = AsyncMock()
        s2.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "r2"})
        await _decide_residency(checkpointer=AsyncMock(), thread_id="tid-100", scheduler=s2)
        assert s2.dispatch.call_args[0][0].delay_seconds == 28800

    async def test_backoff_uses_checkpoint_metadata(self, monkeypatch):
        """Verify read/write of attempt count through checkpoint metadata helpers."""
        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}
        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        read_tracker = {"called": 0, "return_value": 1}
        async def fake_read(checkpointer, thread_id):
            read_tracker["called"] += 1
            return read_tracker["return_value"]
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._read_residency_attempts",
            fake_read,
        )

        written = []
        async def fake_write(checkpointer, thread_id, attempts):
            written.append((thread_id, attempts))
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._write_residency_attempts",
            fake_write,
        )

        reset_called = []
        async def fake_reset(checkpointer, thread_id):
            reset_called.append(thread_id)
        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._reset_residency_attempts",
            fake_reset,
        )

        # Failure path: attempts read as 1, dispatch filtered -> write 2
        scheduler = AsyncMock()
        scheduler.dispatch = AsyncMock(return_value={"outcome": "filtered"})
        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=scheduler,
        )
        assert result is False
        assert read_tracker["called"] == 1
        assert written == [("test-tid", 2)], f"Expected write(2), got {written}"
        assert reset_called == []

        # Reset for success path
        written.clear()
        read_tracker["called"] = 0

        # Success path: attempts read as 1, dispatch launched -> reset
        scheduler2 = AsyncMock()
        scheduler2.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "run-1"})
        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid-2",
            scheduler=scheduler2,
        )
        assert result is True
        assert read_tracker["called"] == 1
        assert written == [], f"Should NOT write on success, got {written}"
        assert reset_called == ["test-tid-2"]

    async def test_residency_publishes_journal_event(self, monkeypatch):
        """Journal events are published for residency decisions (success path)."""
        async def fake_read_goal(*args, **kwargs):
            return {"objective": "test", "status": "active", "created_at": 100}
        monkeypatch.setattr("deerflow.runtime.goal.read_thread_goal", fake_read_goal)

        monkeypatch.setattr(
            "deerflow.runtime.runs.worker._read_residency_attempts",
            AsyncMock(return_value=0),
        )

        scheduler = AsyncMock()
        scheduler.dispatch = AsyncMock(return_value={"outcome": "launched", "run_id": "run-1"})

        journal = MagicMock()

        result = await _decide_residency(
            checkpointer=AsyncMock(),
            thread_id="test-tid",
            scheduler=scheduler,
            journal=journal,
        )
        assert result is True
        assert journal.add_event.call_count >= 2

        event_types = [c[0][0] for c in journal.add_event.call_args_list]
        event_data = [c[0][1] for c in journal.add_event.call_args_list]

        assert "residency.decision" in event_types
        assert "residency.scheduled" in event_types

        decision_idx = event_types.index("residency.decision")
        assert event_data[decision_idx]["decision"] == "scheduled"

        scheduled_idx = event_types.index("residency.scheduled")
        assert event_data[scheduled_idx]["delay"] == 1800
        assert event_data[scheduled_idx]["thread_id"] == "test-tid"


class TestGoalHealth:
    """Tests for _compute_goal_progress and _compute_goal_throttle_delay."""

    def test_satisfied_returns_one(self):
        """satisfied=True yields progress 1.0."""
        evaluation: GoalEvaluation = {
            "satisfied": True,
            "blocker": "none",
            "reason": "goal met",
        }
        result = _compute_goal_progress({}, evaluation)
        assert result == 1.0

    def test_not_met_yet_returns_point_five(self):
        """blocker=goal_not_met_yet yields progress 0.5."""
        evaluation: GoalEvaluation = {
            "satisfied": False,
            "blocker": "goal_not_met_yet",
            "reason": "still working",
        }
        result = _compute_goal_progress({}, evaluation)
        assert result == 0.5

    def test_other_blocker_returns_point_one(self):
        """Other blockers (e.g. missing_evidence) yield progress 0.1."""
        evaluation: GoalEvaluation = {
            "satisfied": False,
            "blocker": "missing_evidence",
            "reason": "no evidence found",
        }
        result = _compute_goal_progress({}, evaluation)
        assert result == 0.1

    def test_empty_health_no_throttle(self):
        """Empty goal_health list returns 0."""
        result = _compute_goal_throttle_delay([])
        assert result == 0

    def test_all_recent_progress_ge_03_no_throttle(self):
        """All last-3 entries with progress >= 0.3 returns 0."""
        health: list[GoalHealthEntry] = [
            {"run_id": "r1", "tick": 1, "progress": 0.5, "decision_count": 1, "token_used": 100},
            {"run_id": "r2", "tick": 2, "progress": 0.7, "decision_count": 2, "token_used": 200},
            {"run_id": "r3", "tick": 3, "progress": 1.0, "decision_count": 3, "token_used": 300},
        ]
        result = _compute_goal_throttle_delay(health)
        assert result == 0

    def test_one_low_progress_entry(self):
        """1 low-progress entry in last 3 -> int(1800 * 1.5) = 2700."""
        health: list[GoalHealthEntry] = [
            {"run_id": "r1", "tick": 1, "progress": 0.1, "decision_count": 1, "token_used": 100},
            {"run_id": "r2", "tick": 2, "progress": 0.5, "decision_count": 2, "token_used": 200},
            {"run_id": "r3", "tick": 3, "progress": 0.7, "decision_count": 3, "token_used": 300},
        ]
        result = _compute_goal_throttle_delay(health)
        assert result == 2700

    def test_two_consecutive_low_progress(self):
        """2 low-progress entries in last 3 -> int(1800 * 3.0) = 5400."""
        health: list[GoalHealthEntry] = [
            {"run_id": "r1", "tick": 1, "progress": 0.1, "decision_count": 1, "token_used": 100},
            {"run_id": "r2", "tick": 2, "progress": 0.2, "decision_count": 2, "token_used": 200},
            {"run_id": "r3", "tick": 3, "progress": 0.7, "decision_count": 3, "token_used": 300},
        ]
        result = _compute_goal_throttle_delay(health)
        assert result == 5400

    def test_three_plus_consecutive_low_progress(self):
        """3 low-progress entries in last 3 -> int(1800 * 6.0) = 10800."""
        health: list[GoalHealthEntry] = [
            {"run_id": "r1", "tick": 1, "progress": 0.0, "decision_count": 1, "token_used": 100},
            {"run_id": "r2", "tick": 2, "progress": 0.1, "decision_count": 2, "token_used": 200},
            {"run_id": "r3", "tick": 3, "progress": 0.2, "decision_count": 3, "token_used": 300},
        ]
        result = _compute_goal_throttle_delay(health)
        assert result == 10800
