"""Standalone executor server for the Living Agent system.

Runs as a separate process, receiving task execution requests from the
scheduler (``AgentWorker``) via HTTP.

Start with::

    python -m deerflow.runtime.executor --port 8003

Or configure in ``config.yaml`` under ``living_agent.executor``::

    living_agent:
      executor:
        type: http
        url: http://localhost:8003
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

app = FastAPI(title="Living Agent Executor")


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    task_id: str = Field(description="Task identifier.")
    capability: str = Field(default="", description="Task capability.")
    description: str = Field(default="", description="Task description.")
    skill: str = Field(default="", description="Resolved skill name.")
    channel: str = Field(default="", description="Resolved IM channel name.")


# ---------------------------------------------------------------------------
# Pluggable skill runner
# ---------------------------------------------------------------------------

SkillRunner = Any  # Callable[[ExecuteRequest], dict[str, Any]]

_runner: SkillRunner | None = None


def set_runner(runner: SkillRunner) -> None:
    """Set the skill runner callable.

    The runner receives an ``ExecuteRequest`` and must return a dict with at
    least ``"status"`` (``"completed"`` / ``"ok"`` on success).
    """
    global _runner
    _runner = runner


def _default_runner(request: ExecuteRequest) -> dict[str, Any]:
    """Default runner: log and return a no-op result."""
    logger.info("Default executor: task=%s skill=%s channel=%s", request.task_id, request.skill, request.channel)
    return {
        "status": "completed",
        "output": f"default_executor:{request.skill}/{request.channel}",
        "skill": request.skill,
        "channel": request.channel,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/execute")
async def handle_execute(req: ExecuteRequest) -> dict[str, Any]:
    runner = _runner or _default_runner
    return runner(req)


@app.get("/health")
async def handle_health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Living Agent Executor Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8003, help="Bind port")
    parser.add_argument("--log-level", default="info", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
