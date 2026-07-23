"""RPD (Recursive PDCA) LangChain tool for DeerFlow."""

import json
import logging
from typing import Any

from langchain.tools import tool

from deerflow.rpd import engine
from deerflow.rpd.shared import require_state

logger = logging.getLogger(__name__)

# Action routing table: "tree.tick" → (engine.cmd_tree_tick, {})
# Second element is default param keys for positional args.
_ACTION_ROUTES: dict[str, tuple[Any, list[str]]] = {
    "init": (engine.cmd_init, ["slug", "goal", "resume", "force"]),
    "init-and-expand": (engine.cmd_init_and_expand, ["slug", "goal", "children", "resume", "force"]),
    "tree.tick": (engine.cmd_tree_tick, []),
    "tree.status": (engine.cmd_tree_status, []),
    "tree.node-start": (engine.cmd_tree_node_start, ["node_id"]),
    "tree.node-done": (engine.cmd_tree_node_done, ["node_id"]),
    "tree.node-fail": (engine.cmd_tree_node_fail, ["node_id"]),
    "tree.node-skip": (engine.cmd_tree_node_skip, ["node_id"]),
    "tree.node-advance": (engine.cmd_tree_node_advance, ["node_id", "action", "result_summary"]),
    "tree.batch-done": (engine.cmd_tree_batch_done, ["node_ids", "summaries"]),
    "tree.check-async": (engine.cmd_tree_check_async, ["node_id", "result"]),
    "tree.expand": (engine.cmd_tree_expand, ["node_id", "children_spec"]),
    "tree.prune": (engine.cmd_tree_prune, ["node_id"]),
    "phase.transition": (engine.cmd_phase_transition, ["node_id", "new_phase"]),
    "phase.set-mode": (engine.cmd_phase_set_mode, ["node_id", "mode"]),
    "phase.set-style": (engine.cmd_phase_set_style, ["node_id", "style"]),
    "phase.set-async": (engine.cmd_phase_set_async, ["node_id", "reason"]),
    "methodology.list": (engine.cmd_methodology_list, ["phase", "mode"]),
    "methodology.get": (engine.cmd_methodology_get, ["name"]),
    "state": (engine.cmd_state_show, ["key"]),
    "state.set": (engine.cmd_state_set, ["key", "value"]),
    "save": (engine.cmd_save, []),
}


@tool("rpd", parse_docstring=True)
def rpd_tool(
    action: str,
    params: str = "{}",
) -> str:
    """Execute an RPD (Recursive PDCA) tree state machine operation.

    Actions: init | init-and-expand | tree.tick | tree.status | tree.expand
             tree.node-start | tree.node-done | tree.node-fail | tree.node-skip
             tree.node-advance | tree.batch-done | tree.check-async | tree.prune
             phase.transition | phase.set-mode | phase.set-style | phase.set-async
             methodology.list | methodology.get | state | state.set | save

    Key optimizations (use these to reduce token cost):
      init-and-expand slug=<s> goal=<g> children=[...]
            — init + expand in one call (saves 1 round-trip)
      tree.node-advance node_id=<id> action=done|fail|skip [result_summary=<s>]
            — start + complete a node in one call (saves 1-2 round-trips)
      tree.batch-done node_ids=["id1","id2"] [summaries={}]
            — mark multiple nodes done at once (saves N-1 round-trips)

    Params are passed as JSON in the 'params' argument. See SKILL.md for full docs.

    Args:
        action: The RPD action to execute (e.g. "init", "tree.tick", "tree.expand").
        params: JSON string with action-specific parameters.
    """
    try:
        return _execute(action, params)
    except Exception as e:
        logger.exception("RPD action failed: %s", action)
        return json.dumps({"action": action, "error": str(e)}, ensure_ascii=False)


def _execute(action: str, params: str) -> str:
    route = _ACTION_ROUTES.get(action)
    if route is None:
        # Try reverse mapping: action may contain dots that map to our keys
        available = sorted(_ACTION_ROUTES.keys())
        # Also allow "tree tick" (space) → "tree.tick" (dot)
        normalized = action.replace(" ", ".")
        route = _ACTION_ROUTES.get(normalized)
        if route is None:
            return json.dumps({
                "action": action,
                "error": f"Unknown action. Available: {', '.join(available)}",
            }, ensure_ascii=False)

    func, param_keys = route

    # Parse params
    parsed: dict[str, Any] = {}
    if params and params.strip():
        try:
            parsed = json.loads(params)
        except json.JSONDecodeError as e:
            return json.dumps({
                "action": action,
                "error": f"Invalid params JSON: {e}",
            }, ensure_ascii=False)

    # Convert "phase" param key to "new_phase" for phase.transition
    if action in ("phase.transition", "phase transition") and "phase" in parsed:
        parsed["new_phase"] = parsed.pop("phase")

    # Convert "children"/"children_spec" param (JSON string → list) for tree.expand
    if action in ("tree.expand", "tree expand"):
        children_val = parsed.get("children") or parsed.get("children_spec")
        if isinstance(children_val, str):
            try:
                parsed["children_spec"] = json.loads(children_val)
            except json.JSONDecodeError as e:
                return json.dumps({
                    "action": action,
                    "error": f"Invalid children JSON: {e}",
                }, ensure_ascii=False)
        elif isinstance(children_val, list):
            parsed["children_spec"] = children_val
        # Also accept "children" param from LLM as alias
        if "children" in parsed and "children_spec" not in parsed:
            parsed["children_spec"] = parsed["children"]

    # Build kwargs from param_keys
    kwargs: dict[str, Any] = {}
    for key in param_keys:
        if key in parsed:
            kwargs[key] = parsed[key]

    result = func(**kwargs)
    return json.dumps(result, indent=2, ensure_ascii=False)
