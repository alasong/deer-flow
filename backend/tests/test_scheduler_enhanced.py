"""Tests for M1 Scheduler Enhancement.

Tests the enhanced scheduler features:
- Thread routing (resolve_execution_thread_id / dispatch)
- Context filtering (check_context_filter)
- Max runs per day (apply_max_runs_per_day)
- Config model loading (ContextFilter / ScheduledTask)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from deerflow.config.scheduler_config import ContextFilter, ScheduledTask
from deerflow.scheduler.enhanced import (
    EnhancedSchedulerService,
    apply_max_runs_per_day,
    check_context_filter,
    resolve_execution_thread_id,
)


# ---------------------------------------------------------------------------
# Test 5: Config model loading
# ---------------------------------------------------------------------------

def test_scheduled_task_full_config_loads():
    """Verify ScheduledTask model loads all fields correctly."""
    task = ScheduledTask(
        id="daily-summary",
        trigger="0 9 * * *",
        thread_id="thread-456",
        prompt="Generate daily summary",
        max_runs_per_day=1,
        context_filter=ContextFilter(require_recent_activity=True, max_idle_days=3),
    )
    assert task.id == "daily-summary"
    assert task.trigger == "0 9 * * *"
    assert task.thread_id == "thread-456"
    assert task.prompt == "Generate daily summary"
    assert task.max_runs_per_day == 1
    assert task.context_filter is not None
    assert task.context_filter.require_recent_activity is True
    assert task.context_filter.max_idle_days == 3


def test_scheduled_task_defaults():
    """Verify optional fields get correct defaults."""
    task = ScheduledTask(id="minimal", trigger="0 0 * * *", prompt="test")
    assert task.thread_id is None
    assert task.max_runs_per_day == 1
    assert task.context_filter is None


def test_scheduled_task_from_dict():
    """Verify ScheduledTask can be constructed from a dict (config-parsing path)."""
    data = {
        "id": "from-dict",
        "trigger": "*/5 * * * *",
        "thread_id": "tid-99",
        "prompt": "Check system health",
        "max_runs_per_day": 3,
        "context_filter": {"require_recent_activity": True, "max_idle_days": 7},
    }
    task = ScheduledTask(**data)
    assert task.id == "from-dict"
    assert task.max_runs_per_day == 3
    assert task.context_filter.max_idle_days == 7


# ---------------------------------------------------------------------------
# Tests 2 & 3: Context filter logic
# ---------------------------------------------------------------------------

def test_context_filter_passes_with_recent_activity():
    """check_context_filter returns True when last_activity is within max_idle_days."""
    f = ContextFilter(require_recent_activity=True, max_idle_days=3)
    recent = datetime.now(UTC) - timedelta(hours=1)
    assert check_context_filter(f, last_activity_at=recent) is True


def test_context_filter_fails_when_idle_too_long():
    """check_context_filter returns False when idle past max_idle_days."""
    f = ContextFilter(require_recent_activity=True, max_idle_days=3)
    idle = datetime.now(UTC) - timedelta(days=10)
    assert check_context_filter(f, last_activity_at=idle) is False


def test_context_filter_passes_when_not_required():
    """check_context_filter returns True when require_recent_activity is False."""
    f = ContextFilter(require_recent_activity=False)
    assert (
        check_context_filter(f, last_activity_at=datetime.now(UTC) - timedelta(days=365))
        is True
    )


def test_context_filter_fails_on_no_activity():
    """check_context_filter returns False when last_activity_at is None and required."""
    f = ContextFilter(require_recent_activity=True, max_idle_days=3)
    assert check_context_filter(f, last_activity_at=None) is False


def test_context_filter_boundary_just_under():
    """check_context_filter passes just under max_idle_days boundary."""
    f = ContextFilter(require_recent_activity=True, max_idle_days=3)
    recent = datetime.now(UTC) - timedelta(days=2, hours=23, minutes=59)
    assert check_context_filter(f, last_activity_at=recent) is True


# ---------------------------------------------------------------------------
# Test 4: max_runs_per_day
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_runs_per_day_blocks_exceeded():
    """apply_max_runs_per_day returns False when daily limit is reached."""
    calls: list[str] = []

    async def get_count(task_id: str) -> int:
        calls.append(task_id)
        return 2

    task = ScheduledTask(id="limited", trigger="cron", prompt="x", max_runs_per_day=2)
    assert await apply_max_runs_per_day(task, get_count) is False
    assert calls == ["limited"]


@pytest.mark.asyncio
async def test_max_runs_per_day_allows_under_limit():
    """apply_max_runs_per_day returns True when under daily limit."""

    async def get_count(task_id: str) -> int:
        return 1

    task = ScheduledTask(id="limited", trigger="cron", prompt="x", max_runs_per_day=2)
    assert await apply_max_runs_per_day(task, get_count) is True


@pytest.mark.asyncio
async def test_max_runs_per_day_zero_is_unlimited():
    """apply_max_runs_per_day returns True when max_runs_per_day is 0."""

    async def get_count(task_id: str) -> int:
        return 9999

    task = ScheduledTask(id="unlimited", trigger="cron", prompt="x", max_runs_per_day=0)
    assert await apply_max_runs_per_day(task, get_count) is True


# ---------------------------------------------------------------------------
# Test 1: Thread routing + full dispatch
# ---------------------------------------------------------------------------

def test_resolve_execution_thread_id_uses_task_thread():
    """resolve_execution_thread_id returns task.thread_id when set."""
    task = ScheduledTask(id="t1", trigger="* * * * *", prompt="x", thread_id="fixed-thread")
    assert resolve_execution_thread_id(task) == "fixed-thread"


def test_resolve_execution_thread_id_generates_new_when_not_set():
    """resolve_execution_thread_id generates a unique id when task.thread_id is None."""
    task = ScheduledTask(id="t2", trigger="* * * * *", prompt="x")
    result = resolve_execution_thread_id(task)
    assert isinstance(result, str) and len(result) > 0
    UUID(result)


@pytest.mark.asyncio
async def test_scheduler_task_with_thread_route():
    """Enhanced dispatch uses task.thread_id for the execution thread."""
    launched: list[dict] = []

    async def fake_launch(thread_id: str, prompt: str) -> dict:
        launched.append({"thread_id": thread_id, "prompt": prompt})
        return {"run_id": "run-1", "thread_id": thread_id}

    async def get_last_activity(thread_id: str) -> datetime | None:
        return datetime.now(UTC)

    async def get_count(task_id: str) -> int:
        return 0

    task = ScheduledTask(
        id="route-task",
        trigger="cron",
        thread_id="target-thread-42",
        prompt="Run on specific thread",
    )

    svc = EnhancedSchedulerService(launch_run=fake_launch)
    result = await svc.dispatch(
        task,
        get_last_activity=get_last_activity,
        get_run_count_today=get_count,
    )

    assert result["outcome"] == "launched"
    assert len(launched) == 1
    assert launched[0]["thread_id"] == "target-thread-42"
    assert launched[0]["prompt"] == "Run on specific thread"


@pytest.mark.asyncio
async def test_scheduler_task_without_thread_generates_new():
    """Enhanced dispatch generates a new thread_id when task has none."""
    launched: list[dict] = []

    async def fake_launch(thread_id: str, prompt: str) -> dict:
        launched.append({"thread_id": thread_id, "prompt": prompt})
        return {"run_id": "run-1", "thread_id": thread_id}

    async def get_count(task_id: str) -> int:
        return 0

    task = ScheduledTask(id="no-thread-task", trigger="cron", prompt="Run on new thread")

    svc = EnhancedSchedulerService(launch_run=fake_launch)
    result = await svc.dispatch(
        task,
        get_last_activity=lambda tid: None,
        get_run_count_today=get_count,
    )

    assert result["outcome"] == "launched"
    assert len(launched) == 1
    UUID(launched[0]["thread_id"])
    assert launched[0]["prompt"] == "Run on new thread"


@pytest.mark.asyncio
async def test_dispatch_filters_by_context():
    """Enhanced dispatch returns filtered when thread is idle."""
    async def fake_launch(thread_id: str, prompt: str) -> dict:
        raise RuntimeError("Should not be called")

    async def get_last_activity(thread_id: str) -> datetime | None:
        return datetime.now(UTC) - timedelta(days=10)

    async def get_count(task_id: str) -> int:
        return 0

    task = ScheduledTask(
        id="filter-task",
        trigger="cron",
        thread_id="idle-thread",
        prompt="Should be filtered",
        context_filter=ContextFilter(require_recent_activity=True, max_idle_days=3),
    )

    svc = EnhancedSchedulerService(launch_run=fake_launch)
    result = await svc.dispatch(
        task,
        get_last_activity=get_last_activity,
        get_run_count_today=get_count,
    )

    assert result["outcome"] == "filtered"
    assert result.get("reason") == "thread_idle"


@pytest.mark.asyncio
async def test_dispatch_filters_by_max_runs():
    """Enhanced dispatch returns filtered when max_runs_per_day is exceeded."""
    async def fake_launch(thread_id: str, prompt: str) -> dict:
        raise RuntimeError("Should not be called")

    async def get_count(task_id: str) -> int:
        return 5

    task = ScheduledTask(
        id="max-run-task",
        trigger="cron",
        prompt="Should be filtered by max runs",
        max_runs_per_day=3,
    )

    svc = EnhancedSchedulerService(launch_run=fake_launch)
    result = await svc.dispatch(
        task,
        get_last_activity=lambda tid: None,
        get_run_count_today=get_count,
    )

    assert result["outcome"] == "filtered"
    assert result.get("reason") == "max_runs_exceeded"
