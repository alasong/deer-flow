from pydantic import BaseModel, Field


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
