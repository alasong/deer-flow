"""Route Engine — loads a YAML route table and matches RoutingContext against it.

Pure functions except for ``RouterEngine.load_routes()`` which performs file IO
(wrapped for testability with ``load_routes_text()`` for in-memory YAML).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .classifier import RoutingContext


@dataclass
class RouteResult:
    """Result of route table matching.

    Attributes:
        skill: Matched skill name, or None if no direct match.
        channel: Matched channel name, or None.
        action: Direct action (e.g. "direct") for routes without a skill, or None.
        mode: Route-level mode override (e.g. "auto_activate"), or None.
        candidates: Fallback candidate entries when no exact match is found.
        match_index: Index of the matching route, or -1 if no match.
    """

    skill: Optional[str] = None
    channel: Optional[str] = None
    action: Optional[str] = None
    mode: Optional[str] = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    match_index: int = -1


def _route_matches(rule: dict[str, Any], ctx: RoutingContext) -> bool:
    """Check if a route rule matches the given RoutingContext.

    A rule's ``match`` dict specifies required attribute values.
    All specified attributes must match for the rule to apply.
    Unspecified attributes are treated as wildcards (match anything).
    """
    match = rule.get("match", {})
    if not match:
        return True  # empty match = catch-all

    for key, expected_value in match.items():
        actual_value = getattr(ctx, key, None)
        if actual_value != expected_value:
            return False

    return True


def _compute_candidates(
    routes: list[dict[str, Any]],
    ctx: RoutingContext,
    max_candidates: int = 5,
) -> list[dict[str, Any]]:
    """Compute fallback candidates by scoring route proximity.

    Scores each route by how many match fields align with the context.
    Returns the top N candidates ordered by match score descending.

    A candidate entry preserves the route's skill/channel/action metadata
    plus a ``match_score`` field (number of matching fields).
    """
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, route in enumerate(routes):
        match = route.get("match", {})
        score = 0
        for key, expected_value in match.items():
            actual_value = getattr(ctx, key, None)
            if actual_value == expected_value:
                score += 1
        scored.append((score, idx, route))

    # Sort by score descending, then by original index (stable tiebreak)
    scored.sort(key=lambda x: (-x[0], x[1]))

    candidates = []
    for score, _idx, route in scored[:max_candidates]:
        entry = dict(route)
        entry["match_score"] = score
        candidates.append(entry)

    return candidates


class RouterEngine:
    """Loads a route table and matches RoutingContext against it.

    Usage::

        engine = RouterEngine()
        engine.load_routes("path/to/routes.yaml")
        result = engine.route(ctx)
        if result.skill:
            print(f"Route to {result.skill}/{result.channel}")
        else:
            print(f"Candidates: {result.candidates}")
    """

    def __init__(self) -> None:
        self.routes: list[dict[str, Any]] = []

    def load_routes(self, path: str | Path) -> None:
        """Load route table from a YAML file.

        Args:
            path: Filesystem path to the YAML routes file.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the YAML content is invalid or has no ``routes`` key.
            RuntimeError: If PyYAML is not installed.
        """
        if yaml is None:
            raise RuntimeError("PyYAML is required to load route files. Install with: pip install pyyaml")

        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(f"Route file not found: {path}")

        raw = path_obj.read_text(encoding="utf-8")
        self.load_routes_text(raw)

    def load_routes_text(self, yaml_text: str) -> None:
        """Load route table from a YAML string (in-memory, for testing).

        Args:
            yaml_text: YAML content as a string.

        Raises:
            ValueError: If the YAML content is invalid.
        """
        if yaml is None:
            raise RuntimeError("PyYAML is required.")

        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict) or "routes" not in data:
            raise ValueError("YAML content must have a top-level 'routes' key")

        routes = data["routes"]
        if not isinstance(routes, list):
            raise ValueError("'routes' must be a list")

        self.routes = routes

    def route(self, ctx: RoutingContext) -> RouteResult:
        """Match a RoutingContext against the loaded route table.

        Iterates routes in declaration order; the first matching route wins.
        If no route matches, returns a fallback with candidates.

        Args:
            ctx: The classification context to match.

        Returns:
            A RouteResult with the matched skill/channel/action, or fallback candidates.
        """
        for i, rule in enumerate(self.routes):
            if _route_matches(rule, ctx):
                return RouteResult(
                    skill=rule.get("skill"),
                    channel=rule.get("channel"),
                    action=rule.get("action"),
                    mode=rule.get("mode"),
                    candidates=[],
                    match_index=i,
                )

        # No match — return fallback with candidates
        return RouteResult(
            skill=None,
            channel=None,
            action=None,
            candidates=_compute_candidates(self.routes, ctx),
            match_index=-1,
        )
