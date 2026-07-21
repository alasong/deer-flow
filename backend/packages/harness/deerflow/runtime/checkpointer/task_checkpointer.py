from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from langgraph.types import Checkpointer

from deerflow.tasks.model import Task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure data-shaping
# ---------------------------------------------------------------------------


def build_task_checkpoint_meta(
    task_id: str,
    agent_id: str,
    phase: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build checkpoint metadata for a task execution.

    Returns a dict suitable for storing as checkpoint metadata.

    Args:
        task_id: The task identifier.
        agent_id: The executing agent identifier.
        phase: Current execution phase (e.g. ``"plan"``, ``"execute"``, ``"review"``).
        context: Optional execution context snapshot.
    """
    return {
        "task_id": task_id,
        "agent_id": agent_id,
        "phase": phase,
        "context": context or {},
    }


def format_checkpoint_id(task_id: str, phase: str, sequence: int = 1) -> str:
    """Generate a deterministic checkpoint ID for a task phase."""
    return f"cp_{task_id}_{phase}_{sequence:04d}"


# ---------------------------------------------------------------------------
# Task Checkpointer
# ---------------------------------------------------------------------------


class TaskCheckpointer:
    """Wrapper around the existing LangGraph checkpointer for task-level state.

    Maps task IDs to checkpoint IDs so that interrupted tasks can be resumed
    from their last checkpoint. Thread-safe in-memory mapping with optional
    file persistence.

    Usage::

        cp = get_checkpointer()
        tcp = TaskCheckpointer(cp)
        tcp.save(task, phase="execute", context={...})
        # ... later, on resume:
        state = tcp.restore(task)
    """

    def __init__(
        self,
        checkpointer: Checkpointer,
        mapping_path: str | Path | None = None,
    ) -> None:
        self._checkpointer = checkpointer
        self._mapping: dict[str, str] = {}  # task_id → checkpoint_id
        self._lock = Lock()
        self._mapping_path = Path(mapping_path) if mapping_path else None
        self._sequence: dict[str, int] = {}
        if self._mapping_path:
            self._load_mapping()

    # -- Save / Restore --

    def save(self, task: Task, phase: str, context: dict[str, Any] | None = None) -> str:
        """Save a checkpoint for a task's current execution state.

        Args:
            task: The task being executed.
            phase: Current execution phase name.
            context: Optional execution context snapshot to persist.

        Returns:
            The checkpoint ID.
        """
        seq = self._sequence.get(task.task_id, 0) + 1
        self._sequence[task.task_id] = seq
        cp_id = format_checkpoint_id(task.task_id, phase, seq)

        meta = build_task_checkpoint_meta(task.task_id, task.agent_id or "", phase, context)
        config = {"configurable": {"thread_id": cp_id}}

        # Persist via the underlying checkpointer
        self._checkpointer.put(config, {
            "task_meta": meta,
            "task_snapshot": task.to_dict(),
        })

        with self._lock:
            self._mapping[task.task_id] = cp_id
            task.checkpoint_id = cp_id
            self._save_mapping()

        logger.info("Checkpoint saved: %s (task=%s, phase=%s)", cp_id, task.task_id, phase)
        return cp_id

    def restore(self, task: Task) -> dict[str, Any] | None:
        """Restore a task's last checkpoint.

        Returns the checkpointed state dict, or ``None`` if no checkpoint exists.
        """
        with self._lock:
            cp_id = self._mapping.get(task.task_id)

        if cp_id is None:
            return None

        try:
            config = {"configurable": {"thread_id": cp_id}}
            state = self._checkpointer.get(config)
            if state is None:
                logger.warning("Checkpoint %s not found for task %s", cp_id, task.task_id)
                return None
            return state.get("task_meta")
        except Exception as exc:
            logger.error("Failed to restore checkpoint %s: %s", cp_id, exc)
            return None

    def get_checkpoint_id(self, task_id: str) -> str | None:
        with self._lock:
            return self._mapping.get(task_id)

    def has_checkpoint(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._mapping

    def clear(self, task_id: str) -> None:
        """Remove checkpoint mapping for a completed/failed task."""
        with self._lock:
            self._mapping.pop(task_id, None)
            self._sequence.pop(task_id, None)
            self._save_mapping()

    # -- Mapping persistence --

    def _load_mapping(self) -> None:
        if not self._mapping_path or not self._mapping_path.exists():
            return
        try:
            data = json.loads(self._mapping_path.read_text(encoding="utf-8"))
            self._mapping = data.get("mapping", {})
            seq_raw = data.get("sequence", {})
            self._sequence = {k: int(v) for k, v in seq_raw.items()}
            logger.info("Loaded %d checkpoint mappings", len(self._mapping))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load checkpoint mapping: %s", exc)

    def _save_mapping(self) -> None:
        if not self._mapping_path:
            return
        data = {
            "mapping": self._mapping,
            "sequence": self._sequence,
        }
        self._mapping_path.parent.mkdir(parents=True, exist_ok=True)
        self._mapping_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
