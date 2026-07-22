"""Condition evaluator for pipeline step gating.

Evaluates ``condition`` expressions defined on pipeline steps against
upstream step manifests to decide whether a step should run or be skipped.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: Compiled regex for condition expressions like ``step.field == value``.
_CONDITION_PATTERN = re.compile(
    r"^\s*(?P<step>[a-zA-Z_]\w*)\s*\.\s*(?P<field>[a-zA-Z_][\w.]*)\s*"
    r"(?P<op>>=|<=|==|!=|>|<)\s*"
    r"(?P<value>.+)\s*$"
)


def evaluate_condition(
    condition: str,
    manifests: dict[str, dict[str, Any]],
) -> bool:
    """Evaluate a single condition expression against available manifests.

    Supported forms:

    - ``"always"`` → always ``True`` (run unconditionally)
    - ``"never"`` → always ``False`` (skip; debug use)
    - ``"step.field == value"`` → lookup *field* in ``manifests[step]``,
      compare with *value* using the given operator.

    Args:
        condition: The condition expression string.
        manifests: Dict mapping step id → manifest dict (typically from
            :meth:`~deerflow.pipeline.manifest.Manifest.model_dump`).

    Returns:
        ``True`` when the condition passes (step should run).

    Raises:
        ValueError: when the condition syntax is unrecognised.
    """
    if not condition or not condition.strip():
        return True

    stripped = condition.strip()

    if stripped == "always":
        return True
    if stripped == "never":
        return False

    m = _CONDITION_PATTERN.match(stripped)
    if not m:
        logger.warning("unrecognised condition syntax: %r", condition)
        return False

    step_id = m.group("step")
    field_path = m.group("field")
    op = m.group("op")
    raw_value = m.group("value").strip()

    # Resolve the manifest for the referenced step.
    manifest = manifests.get(step_id)
    if manifest is None:
        logger.warning("condition %r: manifest for step %r not found", condition, step_id)
        return False

    # Navigate nested field path (e.g. "outputs.has_issues").
    actual = _resolve_field(manifest, field_path)

    # Parse the literal value from the expression.
    expected = _parse_literal(raw_value)

    return _compare(actual, expected, op)


def detect_self_reference(
    condition: str,
    step_id: str,
) -> bool:
    """Return ``True`` when *condition* references its own *step_id*.

    This is a cycle guard: a step cannot gate itself.
    """
    m = _CONDITION_PATTERN.match(condition.strip() if condition else "")
    if m and m.group("step") == step_id:
        return True
    return False


# ── Internal helpers ─────────────────────────────────────────────────


def _resolve_field(manifest: dict[str, Any], field_path: str) -> Any:
    """Resolve a dotted field path through a manifest dict.

    If the top-level path doesn't match, falls back to looking inside
    ``outputs``, ``decisions``, or ``next_steps_suggestion`` sub-dicts
    so that conditions like ``review.has_issues`` can find a value
    nested at ``manifest.outputs.has_issues`` or
    ``manifest.decisions[0].type``.
    """
    parts = field_path.split(".")
    current: Any = _try_resolve(manifest, parts)
    if current is not None:
        return current
    # Fallback: look inside known sub-sections, treating each section's
    # value as the resolution root.
    for _section_key, section_val in manifest.items():
        if isinstance(section_val, dict):
            current = _try_resolve(section_val, parts)
            if current is not None:
                return current
        elif isinstance(section_val, list):
            current = _try_resolve(section_val, parts)
            if current is not None:
                return current
    return None


def _try_resolve(root: Any, parts: list[str]) -> Any:
    """Walk *parts* through *root*, returning None at the first miss."""
    current: Any = root
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _parse_literal(raw: str) -> Any:
    """Parse a string literal into a Python value."""
    lower = raw.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower == "none" or lower == "null":
        return None
    # Try number.
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass
    # Strip surrounding quotes for string values.
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw


def _compare(actual: Any, expected: Any, op: str) -> bool:
    """Apply a comparison operator."""
    try:
        if op == "==":
            return actual == expected
        if op == "!=":
            return actual != expected
        if op == ">":
            return (actual is not None) and (actual > expected)
        if op == "<":
            return (actual is not None) and (actual < expected)
        if op == ">=":
            return (actual is not None) and (actual >= expected)
        if op == "<=":
            return (actual is not None) and (actual <= expected)
        logger.warning("unknown operator %r, treating as False", op)
        return False
    except TypeError:
        logger.warning("comparison %r %s %r failed (type mismatch)", actual, op, expected)
        return False
