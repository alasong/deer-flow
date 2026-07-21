from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from deerflow.tasks.model import Task

logger = logging.getLogger(__name__)


class ExecutorClient(ABC):
    """Abstract interface for the living-agent executor process.

    The scheduler (``AgentWorker``) uses this client to dispatch tasks to a
    separate executor process instead of calling an in-process callback.
    """

    @abstractmethod
    async def execute(self, task: Task, skill: str, channel: str) -> dict[str, Any]:
        """Dispatch *task* to the executor.

        Args:
            task: The task being executed.
            skill: Resolved skill name for this task.
            channel: Resolved IM channel name.

        Returns:
            A result dict with at least a ``"status"`` key
            (``"completed"`` / ``"ok"`` on success).
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the client."""
        ...


class NoopExecutorClient(ExecutorClient):
    """Fallback executor that marks tasks as completed without doing work."""

    async def execute(self, task: Task, skill: str, channel: str) -> dict[str, Any]:
        return {
            "status": "completed",
            "output": f"noop:{skill}/{channel}",
            "skill": skill,
            "channel": channel,
        }

    async def close(self) -> None:
        pass


class HttpExecutorClient(ExecutorClient):
    """Dispatches tasks to a remote executor process over HTTP.

    Expects the executor to expose ``POST /execute`` returning a JSON body
    with at least a ``"status"`` key.
    """

    def __init__(
        self,
        url: str,
        *,
        api_key: str = "",
        timeout_seconds: int = 300,
    ) -> None:
        self._url = url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def execute(self, task: Task, skill: str, channel: str) -> dict[str, Any]:
        payload = {
            "task_id": task.task_id,
            "capability": task.capability,
            "description": task.description,
            "skill": skill,
            "channel": channel,
        }
        try:
            resp = await self._client.post("/execute", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            logger.error("Executor timeout for task %s (skill=%s)", task.task_id, skill)
            return {"status": "failed", "error": "executor request timed out"}
        except httpx.HTTPStatusError as exc:
            logger.error("Executor HTTP %s for task %s: %s", exc.response.status_code, task.task_id, exc.response.text)
            return {"status": "failed", "error": f"executor returned {exc.response.status_code}"}
        except (httpx.RequestError, json.JSONDecodeError) as exc:
            logger.error("Executor request error for task %s: %s", task.task_id, exc)
            return {"status": "failed", "error": str(exc)}
        if not isinstance(data, dict):
            return {"status": "failed", "error": f"executor returned non-dict response: {data!r}"}
        return data

    async def close(self) -> None:
        await self._client.aclose()
