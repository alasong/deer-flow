"""Context offload middleware for DeerFlow.

When the message token count reaches a configurable threshold, the middleware
packages the full conversation context (messages, goal, delegations, skill_context)
and writes it to disk under ``.fat/threads/<thread_id>/``. It then trims the
messages list in state to keep only the most recent messages and sets
``offload_summary`` / ``offload_path`` so downstream observers can detect
the offload.

If a *model* is provided, the middleware also extracts structured compartments
(decisions, specs, task progress, findings) from the offloaded messages and
stores them in ``offload_compartments`` in state. These compartments survive
across successive offloads and are injected into every model call by
DurableContextMiddleware, so the LLM retains awareness of past decisions and
current progress even after context is trimmed.

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
from langchain_core.messages import AnyMessage, RemoveMessage, SystemMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from deerflow.config.offload_config import OffloadConfig

logger = logging.getLogger(__name__)

# System prompt section injected dynamically by this middleware when context
# offload triggers. Kept out of the base system prompt to save tokens per call.
_CONTEXT_OFFLOAD_SYSTEM_PROMPT = """\
<context_offload_system>
**Context Offload (Automatic for Long Conversations)**

When your conversation becomes very long, the system automatically saves the full
context to disk and trims the message list to keep only the most recent messages.
After this happens, you will see these fields in your state:

- ``offload_summary`` — A short summary of the offloaded context (message count,
  goal info, timestamp). Read this first to understand what was offloaded.
- ``offload_path`` — The filesystem path to the full offloaded JSON document.
- ``offload_key_decisions`` — A list of key tool-call decisions automatically
  extracted from the offloaded messages. Each entry describes a tool and its
  parameters (``type``, ``summary``, ``source_msg_id``). These decisions are
  also synced to memory with the ``[offload-decision]`` prefix so you can
  quickly review what actions were taken before the offload.

If you need details from the offloaded context:
1. Read ``offload_summary`` in your state to see what was saved.
2. If you need more detail, use ``read_file`` to read the file at ``offload_path``.
   The file contains the full message history, goal state, and delegation ledger.
3. If you need to search for specific information across offloaded files, use
   ``bash grep`` or ``web_search`` on the path indicated by ``offload_path``.

This mechanism is fully automatic. You do not need to manage it yourself.

**Offload Compartments**
When context is offloaded, the system also extracts structured compartments
(past decisions, active specs, task progress, key findings) and stores them
in ``offload_compartments`` in state. These compartments survive across
successive offloads and are injected into every model call as part of the
durable context. Read them to quickly understand what was decided, what is
being built, and what remains to be done — without needing to read the full
offloaded file.
</context_offload_system>"""

# ── Compartment extraction constants ────────────────────────────────────
_COMPARTMENT_MESSAGE_SUMMARY_LENGTH = 4000
_COMPARTMENT_NUM_DECISIONS = 8
_COMPARTMENT_NUM_SPECS = 6
_COMPARTMENT_NUM_FINDINGS = 6


def _serialize_message(msg: AnyMessage) -> dict[str, Any]:
    """Serialize a single LangChain message to a JSON-safe dict."""
    if hasattr(msg, "model_dump"):
        raw = msg.model_dump()
        return raw
    return {
        "type": getattr(msg, "type", "unknown"),
        "content": getattr(msg, "content", ""),
    }


def _format_messages_for_compartment_prompt(messages: list[AnyMessage], max_chars: int = _COMPARTMENT_MESSAGE_SUMMARY_LENGTH) -> str:
    """Format messages into a compact summary for the compartment extraction prompt."""
    parts: list[str] = []
    total = 0
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            content = str(content)
        tool_calls = getattr(msg, "tool_calls", None) or []
        tc_info = ""
        if tool_calls:
            names = [tc.get("name", "?") for tc in tool_calls]
            tc_info = f" [tools: {', '.join(names)}]"
        line = f"[{role}]{tc_info} {content[:200]}"
        if total + len(line) > max_chars:
            remaining = max_chars - total
            if remaining > 40:
                parts.append(line[:remaining] + "...")
            break
        parts.append(line)
        total += len(line)
    return "\n".join(parts)


_CONTEXT_OFFLOAD_COMPARTMENT_PROMPT = """\
Extract structured information from the conversation below to UPDATE "offload compartments" — a durable summary of what the LLM needs to know about past decisions and current work.

Return a VALID JSON object (and ONLY the JSON object, no markdown, no explanation) with exactly these keys:
{{
  "decisions": ["key decisions with rationale, max {num_decisions} items, each <200 chars"],
  "specs": ["active specs/requirements, max {num_specs} items, each <200 chars"],
  "task_progress": {{"goal": "current goal", "phase": "P/D/C/A", "completed": [...], "pending": [...]}},
  "findings": ["important discoveries, max {num_findings} items, each <200 chars"]
}}

Rules:
- Each item must be a single line, under 200 characters.
- For decisions: include the decision AND the rationale ("选择 ES: 已有集群" not just "选择 ES").
- For task_progress.phase: use P/D/C/A notation.
- If existing compartments are provided, MERGE: add new info, keep old info still relevant, remove stale items.
- IMPORTANT: Return ONLY the raw JSON object. No markdown fences, no explanation.

Existing compartments:
{existing}

Messages to analyze:
{messages}"""


class ContextOffloadMiddleware(AgentMiddleware):
    """Token-aware context offload middleware.

    Before each model call this middleware counts tokens in the message list.
    If the count reaches the configured *threshold*, the current context is
    saved to disk and the message list is trimmed to the *messages_to_keep*
    most recent entries.

    If a *model* is provided and ``compartment_enabled`` is true, the middleware
    also extracts structured compartments from the offloaded messages and
    stores them in ``offload_compartments`` in state.
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
        model: Any | None = None,
        compartment_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.threshold = offload_threshold if offload_threshold is not None else self.DEFAULT_OFFLOAD_THRESHOLD
        self.messages_to_keep = messages_to_keep if messages_to_keep is not None else self._DEFAULT_MESSAGES_TO_KEEP
        self.offload_dir = offload_dir if offload_dir is not None else self._DEFAULT_OFFLOAD_DIR
        self._model = model
        self._compartment_enabled = compartment_enabled

    @classmethod
    def from_config(
        cls,
        config: OffloadConfig,
        *,
        model: Any | None = None,
    ) -> ContextOffloadMiddleware:
        """Build middleware from an :class:`OffloadConfig`."""
        return cls(
            offload_threshold=config.threshold,
            messages_to_keep=config.messages_to_keep,
            offload_dir=config.offload_dir,
            model=model,
            compartment_enabled=config.compartment_enabled,
        )

    # ------------------------------------------------------------------
    # Public middleware hooks
    # ------------------------------------------------------------------

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._maybe_offload(state, runtime)

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return await self._amaybe_offload(state, runtime)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _maybe_offload(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Run the offload decision and action (sync path)."""
        messages: list[AnyMessage] = state.get("messages", [])
        if not messages:
            return None

        token_count = self._count_tokens(messages)
        if token_count < self.threshold:
            return None

        return self._execute_offload(state, runtime, messages, token_count)

    async def _amaybe_offload(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Run the offload decision and action (async path)."""
        messages: list[AnyMessage] = state.get("messages", [])
        if not messages:
            return None

        token_count = self._count_tokens(messages)
        if token_count < self.threshold:
            return None

        return await self._aexecute_offload(state, runtime, messages, token_count)

    def _build_offload_updates(self, state: AgentState, runtime: Runtime, messages: list[AnyMessage], token_count: int) -> dict[str, Any]:
        """Common offload logic shared by sync and async paths."""
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
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                SystemMessage(content=_CONTEXT_OFFLOAD_SYSTEM_PROMPT),
                *trimmed,
            ],
            "offload_summary": summary,
            "offload_path": path,
            "offload_key_decisions": decisions,
        }

    def _execute_offload(self, state: AgentState, runtime: Runtime, messages: list[AnyMessage], token_count: int) -> dict | None:
        """Core offload logic for the sync path."""
        updates = self._build_offload_updates(state, runtime, messages, token_count)
        if self._model is not None and self._compartment_enabled:
            try:
                existing = state.get("offload_compartments") or {}
                compartments = self._extract_compartments(messages, existing)
                if compartments:
                    updates["offload_compartments"] = compartments
            except Exception:
                logger.exception("Compartment extraction failed; continuing without compartments")
        return updates

    async def _aexecute_offload(self, state: AgentState, runtime: Runtime, messages: list[AnyMessage], token_count: int) -> dict | None:
        """Core offload logic for the async path."""
        updates = self._build_offload_updates(state, runtime, messages, token_count)
        if self._model is not None and self._compartment_enabled:
            try:
                existing = state.get("offload_compartments") or {}
                compartments = await self._aextract_compartments(messages, existing)
                if compartments:
                    updates["offload_compartments"] = compartments
            except Exception:
                logger.exception("Compartment extraction failed; continuing without compartments")
        return updates

    # ------------------------------------------------------------------
    # Compartment extraction
    # ------------------------------------------------------------------

    def _build_compartment_prompt(self, messages: list[AnyMessage], existing_compartments: dict) -> str:
        """Build the prompt for compartment extraction."""
        existing_json = json.dumps(existing_compartments, ensure_ascii=False, indent=2)
        msg_summary = _format_messages_for_compartment_prompt(messages)
        return _CONTEXT_OFFLOAD_COMPARTMENT_PROMPT.format(
            num_decisions=_COMPARTMENT_NUM_DECISIONS,
            num_specs=_COMPARTMENT_NUM_SPECS,
            num_findings=_COMPARTMENT_NUM_FINDINGS,
            existing=existing_json,
            messages=msg_summary,
        )

    def _extract_compartments(self, messages: list[AnyMessage], existing_compartments: dict) -> dict | None:
        """Extract structured compartments from messages using the LLM (sync)."""
        if self._model is None:
            return None
        prompt_text = self._build_compartment_prompt(messages, existing_compartments)
        try:
            response = self._model.invoke(prompt_text, config={"metadata": {"lc_source": "offload_compartments"}})
            raw = response.text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                first_nl = raw.find("\n")
                if first_nl != -1:
                    raw = raw[first_nl + 1 :]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
                elif raw.rsplit("\n```", 1):
                    raw = raw.rsplit("\n```", 1)[0].strip()
            compartments = json.loads(raw)
            return self._merge_compartments(existing_compartments, compartments)
        except (json.JSONDecodeError, AttributeError, ValueError):
            logger.warning("Failed to parse compartment extraction response", exc_info=True)
            return None

    async def _aextract_compartments(self, messages: list[AnyMessage], existing_compartments: dict) -> dict | None:
        """Extract structured compartments from messages using the LLM (async)."""
        if self._model is None:
            return None
        prompt_text = self._build_compartment_prompt(messages, existing_compartments)
        try:
            response = await self._model.ainvoke(prompt_text, config={"metadata": {"lc_source": "offload_compartments"}})
            raw = response.text.strip()
            if raw.startswith("```"):
                first_nl = raw.find("\n")
                if first_nl != -1:
                    raw = raw[first_nl + 1 :]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
                elif raw.rsplit("\n```", 1):
                    raw = raw.rsplit("\n```", 1)[0].strip()
            compartments = json.loads(raw)
            return self._merge_compartments(existing_compartments, compartments)
        except (json.JSONDecodeError, AttributeError, ValueError):
            logger.warning("Failed to parse compartment extraction response", exc_info=True)
            return None

    @staticmethod
    def _merge_compartments(existing: dict, new: dict) -> dict:
        """Merge new compartments with existing ones.

        New items are prepended (most recent first), duplicates removed,
        and caps are enforced.
        """
        merged: dict = {}

        # Decisions: prepend new, dedupe, cap at _COMPARTMENT_NUM_DECISIONS
        existing_decisions = list(existing.get("decisions", []))
        new_decisions = list(new.get("decisions", []))
        seen = set(existing_decisions)
        for item in reversed(new_decisions):
            if item not in seen:
                existing_decisions.insert(0, item)
                seen.add(item)
        merged["decisions"] = existing_decisions[:_COMPARTMENT_NUM_DECISIONS]

        # Specs: same approach
        existing_specs = list(existing.get("specs", []))
        new_specs = list(new.get("specs", []))
        seen = set(existing_specs)
        for item in reversed(new_specs):
            if item not in seen:
                existing_specs.insert(0, item)
                seen.add(item)
        merged["specs"] = existing_specs[:_COMPARTMENT_NUM_SPECS]

        # Task progress: new values replace existing
        existing_progress = existing.get("task_progress", {})
        new_progress = new.get("task_progress", {})
        merged_progress = dict(existing_progress)
        merged_progress.update({k: v for k, v in new_progress.items() if v})
        # completed/pending: combine and dedupe
        for key in ("completed", "pending"):
            old_list = list(existing_progress.get(key, []))
            new_list = list(new_progress.get(key, []))
            seen = set(old_list)
            for item in new_list:
                if item not in seen:
                    old_list.append(item)
                    seen.add(item)
            merged_progress[key] = old_list
        merged["task_progress"] = merged_progress

        # Findings: prepend new, dedupe, cap
        existing_findings = list(existing.get("findings", []))
        new_findings = list(new.get("findings", []))
        seen = set(existing_findings)
        for item in reversed(new_findings):
            if item not in seen:
                existing_findings.insert(0, item)
                seen.add(item)
        merged["findings"] = existing_findings[:_COMPARTMENT_NUM_FINDINGS]

        return merged

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
