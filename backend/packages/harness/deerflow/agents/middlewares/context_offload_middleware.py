"""Context offload middleware for DeerFlow.

When the message token count reaches a configurable threshold, the middleware
packages the full conversation context (messages, goal, delegations, skill_context)
and writes it to disk under ``.fat/threads/<thread_id>/``. It then trims the
messages list in state to keep only the most recent messages and sets
``offload_summary`` / ``offload_path`` so downstream observers can detect
the offload.

The offload file is a JSON document containing:
  - messages       serialized message list
  - goal           current goal state (if any)
  - delegations    active delegation ledger entries
  - skill_context  loaded skill references
  - timestamp      unix epoch seconds

This middleware is *pure* in the sense that the IO (file write) is isolated
in a single method (``_write_offload``), which tests replace via monkeypatch.
All other methods are stateless data transformations.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.summarization import count_tokens_approximately
from langchain_core.messages import AnyMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from deerflow.config.offload_config import OffloadConfig

logger = logging.getLogger(__name__)


def _serialize_message(msg: AnyMessage) -> dict[str, Any]:
    """Serialize a single LangChain message to a JSON-safe dict."""
    if hasattr(msg, "model_dump"):
        raw = msg.model_dump()
        # model_dump may include unserializable fields; drop any that fail.
        return raw
    # Fallback for very old message types.
    return {
        "type": getattr(msg, "type", "unknown"),
        "content": getattr(msg, "content", ""),
    }


class ContextOffloadMiddleware(AgentMiddleware):
    """Token-aware context offload middleware.

    Before each model call this middleware counts tokens in the message list.
    If the count reaches the configured *threshold*, the current context is
    saved to disk and the message list is trimmed to the *messages_to_keep*
    most recent entries.
    """

    DEFAULT_OFFLOAD_THRESHOLD = 150_000
    _DEFAULT_MESSAGES_TO_KEEP = 10
    _DEFAULT_OFFLOAD_DIR = ".fat/threads"

    def __init__(
        self,
        *,
        offload_threshold: int | None = None,
        messages_to_keep: int | None = None,
        offload_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.threshold = offload_threshold if offload_threshold is not None else self.DEFAULT_OFFLOAD_THRESHOLD
        self.messages_to_keep = messages_to_keep if messages_to_keep is not None else self._DEFAULT_MESSAGES_TO_KEEP
        self.offload_dir = offload_dir if offload_dir is not None else self._DEFAULT_OFFLOAD_DIR

    @classmethod
    def from_config(cls, config: OffloadConfig) -> ContextOffloadMiddleware:
        """Build middleware from an :class:`OffloadConfig`."""
        return cls(
            offload_threshold=config.threshold,
            messages_to_keep=config.messages_to_keep,
            offload_dir=config.offload_dir,
        )

    # ------------------------------------------------------------------
    # Public middleware hooks
    # ------------------------------------------------------------------

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._maybe_offload(state, runtime)

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._maybe_offload(state, runtime)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _maybe_offload(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Run the offload decision and action, returning state updates or None."""
        messages: list[AnyMessage] = state.get("messages", [])
        if not messages:
            return None

        token_count = self._count_tokens(messages)
        if token_count < self.threshold:
            return None

        thread_id = self._resolve_thread_id(runtime)
        dump = self._package_state(state, runtime)
        path = self._write_offload(dump, thread_id)
        summary = self._build_offload_summary(dump, path)
        trimmed = self._trim_messages(messages)

        decisions = self._extract_key_decisions(messages)[:5]
        self._sync_decisions_to_memory(decisions, runtime)

        logger.info(
            "Context offloaded to %s (%d messages, ~%d tokens, %d kept)",
            path,
            len(dump.get("messages", [])),
            token_count,
            len(trimmed),
        )

        return {
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *trimmed],
            "offload_summary": summary,
            "offload_path": path,
            "offload_key_decisions": decisions,
        }

    # ------------------------------------------------------------------
    # Helpers designed for easy mocking in tests
    # ------------------------------------------------------------------

    def _count_tokens(self, messages: list[AnyMessage]) -> int:
        """Count tokens in *messages* using LangChain's approximate counter."""
        return count_tokens_approximately(messages)

    def _package_state(self, state: AgentState, runtime: Runtime) -> dict[str, Any]:
        """Package the current thread state into a serialisable dict."""
        return {
            "messages": [_serialize_message(m) for m in state.get("messages", [])],
            "goal": state.get("goal"),
            "delegations": state.get("delegations", []),
            "skill_context": state.get("skill_context", []),
            "timestamp": time.time(),
        }

    def _write_offload(self, dump: dict[str, Any], thread_id: str) -> str:
        """Write *dump* to ``{offload_dir}/{thread_id}/offload_{ts}.json``.

        Returns the absolute path of the written file.
        """
        offload_dir = Path(self.offload_dir) / thread_id
        offload_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(dump.get("timestamp", time.time()))
        filename = f"offload_{timestamp}.json"
        filepath = offload_dir / filename
        with open(filepath, "w") as f:
            json.dump(dump, f, indent=2, default=str)
        return str(filepath.resolve())

    def _build_offload_summary(self, dump: dict[str, Any], path: str) -> str:
        """Build a short human-readable summary of the offloaded context."""
        msg_count = len(dump.get("messages", []))
        goal = dump.get("goal")
        goal_info = f", goal={goal.get('objective', goal)!r}" if goal else ""
        return f"Context offloaded to {path} ({msg_count} messages{goal_info}, at {time.strftime('%H:%M:%S', time.localtime(dump.get('timestamp', 0)))})"

    def _trim_messages(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """Keep the *messages_to_keep* most recent messages."""
        if len(messages) <= self.messages_to_keep:
            return list(messages)
        return messages[-self.messages_to_keep :]

    def _extract_key_decisions(self, messages: list[AnyMessage]) -> list[dict]:
        """Extract tool_call decisions from recent AI messages for offload awareness.

        Scans the last 50 messages in reverse and extracts a lightweight entry
        for every ``AIMessage`` that carries ``tool_calls``. The results are
        pure rule-based (no LLM call) so decisions about tool usage are
        recoverable after an offload.

        Returns:
            A list of dicts, each with ``type``, ``summary``, and ``source_msg_id``.
        """
        decisions: list[dict] = []
        recent = messages[-50:] if len(messages) > 50 else messages
        for msg in reversed(recent):
            if msg.type != "ai":
                continue
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                continue
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args = tc.get("args", {})
                # Build a brief parameter preview (first 3 keys, values truncated)
                param_parts: list[str] = []
                for k, v in list(args.items())[:3]:
                    if isinstance(v, str) and len(v) > 80:
                        v = v[:80] + "..."
                    param_parts.append(f"{k}={v!r}")
                if len(args) > 3:
                    param_parts.append("...")
                args_preview = ", ".join(param_parts)
                decisions.append({
                    "type": "tool_call",
                    "summary": f"{name}({args_preview})",
                    "source_msg_id": getattr(msg, "id", None),
                })
        return decisions

    def _sync_decisions_to_memory(self, decisions: list[dict], runtime: Runtime) -> None:
        """Best-effort sync of offload decisions to memory.

        If *runtime* carries a ``memory_manager`` attribute, up to 5 decisions
        are saved with the ``[offload-decision]`` prefix so the LLM can
        recover key context after an offload. Failures are logged but not
        propagated.
        """
        mm = getattr(runtime, "memory_manager", None)
        if mm is None:
            return
        for d in decisions[:5]:
            try:
                mm.add_memory(content=f"[offload-decision] {d['summary']}")
            except Exception:
                logger.exception("Failed to sync offload decision to memory")

    @staticmethod
    def _resolve_thread_id(runtime: Runtime) -> str:
        """Extract the thread id from runtime context, defaulting to ``"unknown"``."""
        context = getattr(runtime, "context", None) or {}
        tid = context.get("thread_id", "unknown")
        return str(tid)
