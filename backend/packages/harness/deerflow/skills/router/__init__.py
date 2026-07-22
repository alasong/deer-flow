"""Skill Routing Engine — classifier + route table matcher.

Provides:
- SkillClassifier: extracts domain/complexity/task_type/keywords from free text.
- RouterEngine: matches a RoutingContext against a YAML route table and returns
  the best-matching skill/channel or a fallback candidate list.

Extracted from the PDF engine's ``_auto_classify_output_kind`` channel selection
logic so the same classification can be reused by other skills and workflows.
See ``skills/public/pdf/tools/pdf_engine_channel.py`` for the original.
"""

from __future__ import annotations

from .classifier import RoutingContext, SkillClassifier
from .engine import RouteResult, RouterEngine
from .middleware import RoutingMiddleware

__all__ = [
    "RoutingContext",
    "SkillClassifier",
    "RouteResult",
    "RouterEngine",
    "RoutingMiddleware",
]
