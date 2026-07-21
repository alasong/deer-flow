from pydantic import BaseModel, Field


class LivingAgentExecutorConfig(BaseModel):
    """Configuration for the living agent executor — a separate process
    that receives and runs agent tasks dispatched by the scheduler."""

    type: str = Field(
        default="http",
        description="Executor type: 'http' (standalone HTTP service) or 'subprocess' (CLI process managed by the scheduler).",
    )
    url: str = Field(
        default="",
        description="HTTP executor URL (e.g. http://localhost:8003). Required when type='http'.",
    )
    api_key: str = Field(
        default="",
        description="Optional shared secret for scheduler→executor authentication.",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="Request timeout for each executor invocation.",
    )
    command: str = Field(
        default="",
        description="Command to start the executor process. Required when type='subprocess'.",
    )


class LivingAgentConfig(BaseModel):
    """Configuration for the Living Agent system.

    The scheduler (while-loop) runs inside the Gateway process.
    The executor (actual skill execution) runs as a separate process
    configured under the ``executor`` key.
    """

    enabled: bool = Field(default=False, description="Enable the living agent system.")
    poll_interval_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=300.0,
        description="Scheduler poll interval in seconds.",
    )
    executor: LivingAgentExecutorConfig = Field(
        default_factory=LivingAgentExecutorConfig,
        description="Executor process configuration.",
    )
