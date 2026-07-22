"""Routing middleware — classifies user input and injects routing guidance.

Before the agent runs, this middleware classifies the latest user message
via SkillClassifier, matches it against the route table via RouterEngine,
and injects a hidden SystemMessage with routing guidance so the LLM is
aware of which skill/channel was suggested.

The injected guidance is advisory — the LLM may override it by calling
``task(skill="...")`` with a different skill or by choosing a general
workflow instead.
"""

from __future__ import annotations

import logging
import time
from typing import Literal, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from deerflow.skills.router.classifier import SkillClassifier
from deerflow.skills.router.engine import RouterEngine

logger = logging.getLogger(__name__)

_ROUTES_YAML = """\
# Default Skill Route Table
routes:
  - match: { domain: "tech", complexity: "high", task_type: "analysis" }
    skill: "pdf"
    channel: "analysis"
    mode: auto_activate
  - match: { domain: "tech", complexity: "high", task_type: "research" }
    skill: "deep-research"
  - match: { domain: "tech", complexity: "high", task_type: "implementation" }
    skill: "pdf"
    channel: "full"
  - match: { domain: "tech", complexity: "medium", task_type: "implementation" }
    skill: "pdf"
    channel: "standard"
  - match: { domain: "tech", complexity: "high", task_type: "planning" }
    skill: "pdf"
    channel: "planning"
  - match: { domain: "science", complexity: "high", task_type: "research" }
    skill: "deep-research"
  - match: { domain: "science", complexity: "high", task_type: "analysis" }
    skill: "pdf"
    channel: "analysis"
  - match: { task_type: "query", complexity: "low" }
    action: "direct"
  - match: {}
    skill: "pdf"
    channel: "standard"
"""


class RoutingMiddleware(AgentMiddleware):
    """Injects routing guidance into the agent's context.

    On each ``before_agent`` call, the middleware:
    1. Extracts the latest human message from state.
    2. Classifies it via ``SkillClassifier``.
    3. Matches against ``RouterEngine``.
    4. Injects a ``<skill_routing>`` SystemMessage before the user message.

    The injected message is marked with ``hide_from_ui`` so it never appears
    in the chat UI.

    The route table is copied from ``routes.yaml`` and embedded as a module
    constant so no file I/O is needed at runtime.
    """

    def __init__(
        self,
        *,
        classifier: SkillClassifier | None = None,
        engine: RouterEngine | None = None,
        routing_mode: Literal["advisory", "auto_activate", "enforce"] = "advisory",
    ) -> None:
        super().__init__()
        self._classifier = classifier or SkillClassifier()
        self._engine = engine or self._build_default_engine()
        self.routing_mode = routing_mode

    @staticmethod
    def _build_default_engine() -> RouterEngine:
        engine = RouterEngine()
        engine.load_routes_text(_ROUTES_YAML)
        return engine

    def _latest_user_message(self, state) -> str | None:
        messages = list(state.get("messages", []))
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "human":
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return None

    def _build_guidance(self, ctx, result, effective_mode=None) -> str:
        parts = ["<skill_routing>"]
        parts.append("  <classification>")
        parts.append(f"    <domain>{ctx.domain}</domain>")
        parts.append(f"    <complexity>{ctx.complexity}</complexity>")
        parts.append(f"    <task_type>{ctx.task_type}</task_type>")
        parts.append("  </classification>")

        if result.skill:
            parts.append("  <route>")
            parts.append(f"    <skill>{result.skill}</skill>")
            parts.append(f"    <channel>{result.channel or 'standard'}</channel>")
            parts.append(f"    <match_index>{result.match_index}</match_index>")
            if result.mode:
                parts.append(f"    <mode>{result.mode}</mode>")
            parts.append("  </route>")
            if effective_mode in ("auto_activate", "enforce"):
                mode_labels = {"auto_activate": "automatically activated", "enforce": "enforced"}
                label = mode_labels.get(effective_mode, "activated")
                parts.append(
                    f"  <guidance>Your input was classified for skill "
                    f"'{result.skill}' (channel: {result.channel or 'standard'}). "
                    f"This route is {label}. Use this skill's workflow.</guidance>"
                )
            else:
                parts.append(
                    "  <guidance>Your input was classified for skill "
                    f"'{result.skill}' (channel: {result.channel or 'standard'}). "
                    "Use this skill's workflow if applicable, or call "
                    'task(skill="...") to override.</guidance>'
                )
        elif result.action:
            parts.append("  <route>")
            parts.append(f"    <action>{result.action}</action>")
            parts.append(f"    <match_index>{result.match_index}</match_index>")
            parts.append("  </route>")
            parts.append(
                "  <guidance>Your input matches a direct-action route "
                f"('{result.action}'). No skill invocation needed.</guidance>"
            )
        elif result.candidates:
            parts.append("  <candidates>")
            for c in result.candidates:
                name = c.get("skill") or c.get("action", "unknown")
                score = c.get("match_score", 0)
                parts.append(f'    <candidate skill="{name}" score="{score}" />')
            parts.append("  </candidates>")
            parts.append(
                "  <guidance>No exact skill match. Consider the candidates "
                "above or use a general approach.</guidance>"
            )
        else:
            parts.append(
                "  <guidance>No routing match found. Use general "
                "capabilities.</guidance>"
            )

        parts.append("</skill_routing>")
        return "\n".join(parts)

    @override
    def before_agent(self, state, runtime: Runtime) -> dict | None:
        user_text = self._latest_user_message(state)
        if not user_text:
            return None

        ctx = self._classifier.classify(user_text)
        result = self._engine.route(ctx)

        # Effective mode: route-level override takes priority over middleware default
        effective_mode = result.mode or self.routing_mode

        guidance = self._build_guidance(ctx, result, effective_mode=effective_mode)

        logger.debug(
            "RoutingMiddleware: domain=%s complexity=%s task_type=%s skill=%s mode=%s",
            ctx.domain,
            ctx.complexity,
            ctx.task_type,
            result.skill,
            effective_mode,
        )

        update: dict = {
            "messages": [
                SystemMessage(
                    content=guidance,
                    additional_kwargs={"hide_from_ui": True},
                )
            ],
        }

        # auto_activate: inject skill_context when a skill is matched
        if effective_mode == "auto_activate" and result.skill:
            update["skill_context"] = [
                {
                    "name": result.skill,
                    "path": f"/mnt/skills/public/{result.skill}",
                    "description": (
                        f"Auto-activated by routing ({result.skill}/"
                        f"{result.channel or 'standard'})"
                    ),
                    "loaded_at": int(time.time()),
                }
            ]

        return update

    @override
    async def abefore_agent(self, state, runtime: Runtime) -> dict | None:
        return self.before_agent(state, runtime)
