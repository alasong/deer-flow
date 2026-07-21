"""Living Agent setup — wires AgentWorker into the Gateway lifespan.

Creates and manages the shared stores (TaskStore, AgentRegistry, HumanGateStore,
TaskCheckpointer) and the AgentWorker background loop.
"""

from __future__ import annotations

import logging
from typing import Any

from deerflow.agents.registry import AgentRegistry
from deerflow.config.living_agent_config import LivingAgentConfig, LivingAgentExecutorConfig
from deerflow.runtime.agent_worker import AgentWorker
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.runtime.executor_client import ExecutorClient, HttpExecutorClient, NoopExecutorClient
from deerflow.tasks.gate_store import HumanGateStore
from deerflow.tasks.store import TaskStore

logger = logging.getLogger(__name__)


class _DictCheckpointerStore:
    """Minimal dict-based store satisfying TaskCheckpointer's internal put/get protocol.

    TaskCheckpointer.save() calls put(config, data) where config contains
    the thread_id and data is the checkpoint payload. This store simply keeps
    an in-memory dict keyed by thread_id.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def put(self, config: dict[str, Any], data: dict[str, Any]) -> None:
        self._data[config["configurable"]["thread_id"]] = data

    def get(self, config: dict[str, Any]) -> dict[str, Any] | None:
        return self._data.get(config["configurable"]["thread_id"])


class LivingAgentService:
    """Manages the living agent lifecycle within the Gateway process."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        task_store: TaskStore | None = None,
        checkpointer_parent: TaskCheckpointer | None = None,
        gate_store: HumanGateStore | None = None,
        poll_interval: float = 5.0,
        config: LivingAgentConfig | None = None,
    ) -> None:
        self.registry = registry or AgentRegistry()
        self.task_store = task_store or TaskStore()
        self.gate_store = gate_store or HumanGateStore()
        self.poll_interval = poll_interval
        self._worker: AgentWorker | None = None
        self._config = config or LivingAgentConfig()

        # Default checkpointer uses a simple in-memory dict store
        # (not langgraph's MemorySaver, which has a different put() signature).
        self.checkpointer = checkpointer_parent or TaskCheckpointer(_DictCheckpointerStore())

        # Build the executor client based on config
        self._executor = self._build_executor(self._config.executor)

    @staticmethod
    def _build_executor(executor_cfg: LivingAgentExecutorConfig) -> ExecutorClient:
        """Create an ExecutorClient based on the executor config."""
        if executor_cfg.type == "http" and executor_cfg.url:
            logger.info("Using HttpExecutorClient: url=%s", executor_cfg.url)
            return HttpExecutorClient(
                url=executor_cfg.url,
                api_key=executor_cfg.api_key,
                timeout_seconds=executor_cfg.timeout_seconds,
            )
        logger.info("Using NoopExecutorClient (no executor configured)")
        return NoopExecutorClient()

    async def start(self) -> None:
        """Start the AgentWorker background loop."""
        self._worker = AgentWorker(
            task_store=self.task_store,
            agent_registry=self.registry,
            checkpointer=self.checkpointer,
            gate_store=self.gate_store,
            poll_interval=self.poll_interval,
            executor_client=self._executor,
        )

        await self._worker.start()
        logger.info(
            "Living agent worker started (poll_interval=%ss, executor=%s, agents=%d, tasks=%d, gates=%d)",
            self.poll_interval,
            type(self._executor).__name__,
            len(self.registry.list()),
            len(self.task_store.list()),
            len(self.gate_store.list()),
        )

    async def stop(self) -> None:
        """Stop the AgentWorker gracefully."""
        if self._worker is not None:
            await self._worker.stop()
            self._worker = None
            logger.info("Living agent worker stopped")
