from __future__ import annotations

from pydantic import BaseModel, Field


class ContextFilter(BaseModel):
    """Filter that checks thread context before dispatching a scheduled task.

    When ``require_recent_activity`` is true, the scheduler will only dispatch
    the task if the target thread has had activity within ``max_idle_days``.
    """

    require_recent_activity: bool = Field(
        default=False,
        description="If true, skip the task when the thread has been idle "
        "longer than max_idle_days.",
    )
    max_idle_days: int = Field(
        default=3,
        ge=1,
        description="Maximum number of days since the thread's last activity. "
        "Only meaningful when require_recent_activity is true.",
    )


class ScheduledTask(BaseModel):
    """Config-level representation of a scheduled task with enhanced features.

    This model lives at the config/control-plane layer and is the source of
    truth for defining what should run, when, and under what constraints.
    """

    id: str = Field(description="Unique identifier for the task.")
    trigger: str = Field(
        description="Cron expression or trigger spec (e.g. '0 9 * * *')."
    )
    thread_id: str | None = Field(
        default=None,
        description="Target thread ID. When set, the task runs on this "
        "specific thread. When None, a new thread is created per run.",
    )
    prompt: str = Field(description="The prompt / instruction for the agent run.")
    max_runs_per_day: int = Field(
        default=1,
        ge=0,
        description="Maximum number of runs per day for this task. "
        "Set to 0 for unlimited.",
    )
    context_filter: ContextFilter | None = Field(
        default=None,
        description="Optional context filter that gates dispatch based on "
        "thread state (e.g. last activity time).",
    )
    delay_seconds: int = Field(
        default=0,
        ge=0,
        description="Delay in seconds before the task should execute. "
        "0 means immediate/default scheduling.",
    )


class SchedulerConfig(BaseModel):
    enabled: bool = Field(default=False)
    poll_interval_seconds: int = Field(default=5, ge=1, le=300)
    lease_seconds: int = Field(default=120, ge=5, le=3600)
    max_concurrent_runs: int = Field(default=3, ge=1, le=32)
    min_once_delay_seconds: int = Field(default=60, ge=1, le=86400)
    default_recursion_limit: int = Field(
        default=250,
        ge=50,
        le=2000,
        description="Recursion limit for scheduled (autonomous) task runs. "
        "Scheduled tasks are non-interactive and may need more LangGraph super-steps "
        "than interactive chat runs (default 100). Clamped server-side by ``AppConfig.max_recursion_limit``.",
    )
