"""M1 Scheduler Enhancement — thread routing, context filters, max-runs tracking.

This module extends the existing scheduler framework with:

1. **Thread routing** — a ``ScheduledTask`` with ``thread_id`` set dispatches
   to that specific thread (``run_agent``-style execution). Without it, a
   fresh thread UUID is generated per run.

2. **Context filter** — when ``context_filter.require_recent_activity`` is
   true, the dispatch is gated on the target thread's last activity time.
   A thread idle beyond ``max_idle_days`` is skipped.

3. **Max runs per day** — enforces ``max_runs_per_day`` via a caller-provided
   counter callback. Set to 0 for unlimited.

All public functions are designed to be testable with minimal dependencies:
pure functions or async functions that take their dependencies as callbacks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from deerflow.config.scheduler_config import ContextFilter, ScheduledTask

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def check_context_filter(
    filter_config: ContextFilter,
    *,
    last_activity_at: datetime | None,
) -> bool:
    """Check whether a thread passes the context filter.

    Args:
        filter_config: The ``ContextFilter`` settings.
        last_activity_at: The thread's last activity timestamp, or ``None``
            if no activity has been recorded.

    Returns:
        ``True`` if the thread passes the filter (or no filter is active),
        ``False`` if the thread should be skipped.
    """
    if not filter_config.require_recent_activity:
        return True

    if last_activity_at is None:
        # No recorded activity → no evidence of recent use → skip.
        return False

    now = datetime.now(UTC)
    if last_activity_at.tzinfo is None:
        last_activity_at = last_activity_at.replace(tzinfo=UTC)
    idle_days = (now - last_activity_at).total_seconds() / 86400.0
    return idle_days <= filter_config.max_idle_days


def resolve_execution_thread_id(task: ScheduledTask) -> str:
    """Determine the thread ID to use for a task execution.

    When ``task.thread_id`` is set, return it verbatim. Otherwise generate
    a new random UUID.
    """
    if task.thread_id is not None:
        return task.thread_id
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Async helpers (callback-based so tests can inject mocks)
# ---------------------------------------------------------------------------


async def apply_max_runs_per_day(
    task: ScheduledTask,
    get_run_count_today: Callable[[str], Awaitable[int]],
) -> bool:
    """Check whether the task is below its daily run limit.

    Args:
        task: The scheduled task definition.
        get_run_count_today: Async callback that returns today's run count
            for a given task id.

    Returns:
        ``True`` if the task may proceed, ``False`` if the daily limit
        has been reached (or exceeded).
    """
    if task.max_runs_per_day == 0:
        return True  # unlimited
    count = await get_run_count_today(task.id)
    return count < task.max_runs_per_day


# ---------------------------------------------------------------------------
# Enhanced dispatch service
# ---------------------------------------------------------------------------


class EnhancedSchedulerService:
    """Wraps the existing scheduler dispatch with enhanced pre-flight checks.

    Usage::

        svc = EnhancedSchedulerService(launch_run=original_launch_run)
        result = await svc.dispatch(
            task,
            get_last_activity=thread_store.get_last_activity,
            get_run_count_today=run_repo.count_today_by_task,
        )
    """

    def __init__(
        self,
        launch_run: Callable[..., Awaitable[dict]],
    ) -> None:
        self._launch_run = launch_run

    async def dispatch(
        self,
        task: ScheduledTask,
        *,
        get_last_activity: Callable[[str], Awaitable[datetime | None]],
        get_run_count_today: Callable[[str], Awaitable[int]],
    ) -> dict[str, Any]:
        """Dispatch a scheduled task with enhanced checks.

        The dispatch order is:
        1. Resolve the execution thread ID.
        2. Check context filter (only when ``task.thread_id`` is set).
        3. Check max runs per day.
        4. Launch the run via the configured ``launch_run`` callback.

        Returns a dict with at least ``outcome`` (``"launched"`` or
        ``"filtered"``) and a ``reason`` when filtered.
        """
        execution_thread_id = resolve_execution_thread_id(task)

        # --- Context filter ---
        if (
            task.context_filter is not None
            and task.context_filter.require_recent_activity
            and task.thread_id is not None
        ):
            last_activity = await get_last_activity(task.thread_id)
            if not check_context_filter(task.context_filter, last_activity_at=last_activity):
                logger.info(
                    "Task %s filtered: thread %s idle (last activity: %s, max_idle_days=%d)",
                    task.id,
                    task.thread_id,
                    last_activity,
                    task.context_filter.max_idle_days,
                )
                return {
                    "outcome": "filtered",
                    "reason": "thread_idle",
                    "thread_id": execution_thread_id,
                }

        # --- Max runs per day ---
        if not await apply_max_runs_per_day(task, get_run_count_today):
            logger.info(
                "Task %s filtered: daily run limit reached (%d/day)",
                task.id,
                task.max_runs_per_day,
            )
            return {
                "outcome": "filtered",
                "reason": "max_runs_exceeded",
                "thread_id": execution_thread_id,
            }

        # --- Launch ---
        result = await self._launch_run(
            thread_id=execution_thread_id,
            prompt=task.prompt,
        )
        return {
            "outcome": "launched",
            "thread_id": execution_thread_id,
            "run_id": result.get("run_id"),
        }
