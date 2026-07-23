"""RPD Engine — pure Python functions for the Recursive PDCA tree state machine.

Port of the CLI rpd-engine.py to callable functions.
Each public cmd_* function manages state persistence (load/save).
Private _* functions accept a state dict for unit testing.
"""

import json
import os
import shutil

from .shared import (
    STATE_DIR, STATE_FILE, SCHEMA_VERSION,
    NODE_STATUSES, PHASES, PHASE_ORDER,
    PHASE_MODES, STYLES, METHODOLOGY_REGISTRY,
    timestamp, generate_id, make_node, make_default_state,
    load_state, save_state, require_state,
    list_available_methodologies, get_methodology, get_methodology_names_for,
    _collect_leaves, _all_deps_done, _resolve_waves,
    _count_tree_nodes, _find_node_chain, _find_parent_child_index,
    _aggregate_produced,
)


# ── Init ────────────────────────────────────────────────────────────────

def cmd_init_and_expand(slug: str = "rpd-task", goal: str = "",
                        children: list[dict] | None = None,
                        resume: bool = False, force: bool = False) -> dict:
    """Initialize a task and expand children in one call.

    Equivalent to cmd_init() + cmd_tree_expand() but saves one LLM round-trip.
    """
    init_result = cmd_init(slug=slug, goal=goal, resume=resume, force=force)
    action = init_result.get("action")

    # If init returned a non-expandable action, pass through
    if action in ("conflict", "resumed"):
        return init_result

    root_id = init_result.get("root_id")
    if not root_id or not children:
        return init_result

    expand_result = cmd_tree_expand(root_id, children)
    return {
        "action": "initialized_and_expanded",
        "slug": slug,
        "goal": goal,
        "root_id": root_id,
        "children": expand_result.get("children", []),
        "count": expand_result.get("count", 0),
        "tick": expand_result.get("tick"),
    }

def cmd_init(slug: str = "rpd-task", goal: str = "", resume: bool = False, force: bool = False) -> dict:
    """Initialize a new RPD task or resume an existing one.

    Returns a result dict with action and metadata.
    """
    existing = load_state()

    if existing and not force:
        if resume:
            return _do_resume(existing)
        else:
            return {
                "action": "conflict",
                "slug": existing["slug"],
                "goal": existing.get("goal", ""),
                "created_at": existing.get("created_at", ""),
                "terminal": _count_terminal_nodes(existing["root"]),
                "total": _count_tree_nodes(existing["root"]),
                "message": "Active RPD task exists. Use resume=True or force=True.",
            }

    state = make_default_state(slug, goal)
    save_state(state)
    return {
        "action": "initialized",
        "slug": slug,
        "goal": goal,
        "root_id": state["root"]["id"],
    }


def _do_resume(state: dict) -> dict:
    """Resume an existing task."""
    async_nodes = _collect_async_nodes(state["root"])
    state["status"] = "running"
    save_state(state)

    result: dict = {
        "action": "resumed",
        "slug": state["slug"],
        "goal": state.get("goal", ""),
        "terminal": _count_terminal_nodes(state["root"]),
        "total": _count_tree_nodes(state["root"]),
    }

    if async_nodes:
        result["async_pending"] = [
            {"id": n["id"], "title": n.get("title", ""), "reason": n.get("async_reason", "")}
            for n in async_nodes
        ]

    result["tree_status"] = _format_tree_lines(state["root"])
    tick_result = _do_tree_tick(state)
    result["tick"] = tick_result
    save_state(state)
    return result


# ── Tree Commands ───────────────────────────────────────────────────────

def cmd_tree_tick() -> dict:
    """Return all ready nodes across the entire tree, grouped by wave."""
    state = require_state()
    result = _do_tree_tick(state)
    save_state(state)
    return result


def _do_tree_tick(state: dict) -> dict:
    """Internal tick — takes state, returns result, does NOT save."""
    root = state["root"]
    ready_nodes: list[dict] = []

    def _walk(node):
        nonlocal ready_nodes
        children = node.get("children", [])

        if node["status"] == "waiting_for_children":
            all_children_terminal = all(
                c["status"] in ("done", "failed", "skipped") for c in children
            )
            if all_children_terminal:
                node["status"] = "running"
                ready_nodes.append({
                    "id": node["id"],
                    "phase": node["phase"],
                    "mode": node["mode"],
                    "style": node.get("style", "balanced"),
                    "title": node.get("title", ""),
                    "action": "children_done",
                    "summary": _children_status_summary(children),
                    "aggregated": _aggregate_produced(children),
                })
            return

        if node["status"] != "pending":
            return

        deps = node.get("dependencies", [])
        if deps:
            all_dep_done = True
            for dep_id in deps:
                dep_chain = _find_node_chain(root, dep_id)
                if dep_chain is None:
                    all_dep_done = False
                    break
                dep_node = dep_chain[-1]
                if dep_node["status"] != "done":
                    all_dep_done = False
                    break
            if not all_dep_done:
                return

        if not children:
            ready_nodes.append({
                "id": node["id"],
                "phase": node["phase"],
                "mode": node["mode"],
                "style": node.get("style", "balanced"),
                "title": node.get("title", ""),
                "action": "execute",
            })
            return

    _walk(root)

    def _collect_ready_children(parent):
        nonlocal ready_nodes
        for child in parent.get("children", []):
            if child["status"] == "pending":
                deps = child.get("dependencies", [])
                all_dep_done = True
                for dep_id in deps:
                    dep_chain = _find_node_chain(root, dep_id)
                    if dep_chain is None:
                        all_dep_done = False
                        break
                    dep_node = dep_chain[-1]
                    if dep_node["status"] != "done":
                        all_dep_done = False
                        break
                if all_dep_done:
                    if not child.get("children"):
                        ready_nodes.append({
                            "id": child["id"],
                            "phase": child["phase"],
                            "mode": child["mode"],
                            "style": child.get("style", "balanced"),
                            "title": child.get("title", ""),
                            "action": "execute",
                        })
                    else:
                        _collect_ready_children(child)
            elif child["status"] in ("done", "failed", "skipped"):
                pass
            elif child["status"] == "running" and child.get("children"):
                _collect_ready_children(child)
            elif child["status"] == "waiting_for_children":
                _walk(child)

    _collect_ready_children(root)

    if not ready_nodes:
        if root["status"] in ("done", "failed"):
            return {"action": "task_done", "status": root["status"]}
        total = _count_tree_nodes(root)
        terminals = _count_terminal_nodes(root)
        if terminals >= total > 0:
            return {"action": "no_ready_nodes", "total": total, "terminal": terminals}
        return {"action": "no_ready_nodes", "reason": "all pending nodes have unsatisfied dependencies"}

    nid_to_status = _build_status_map(root)
    wave = _resolve_waves_raw(ready_nodes, nid_to_status)

    return {
        "action": "nodes_ready",
        "count": len(ready_nodes),
        "waves": _group_by_wave(ready_nodes, wave),
        "nodes": ready_nodes,
    }


def cmd_tree_node_start(node_id: str) -> dict:
    state = require_state()
    node = _find_node(state["root"], node_id)
    node["status"] = "running"
    save_state(state)
    is_async = node.get("async", False)
    return {
        "action": "node_started",
        "node_id": node_id,
        "status": "running",
        "async": is_async,
    }


def cmd_tree_node_done(node_id: str, produced: dict | None = None) -> dict:
    state = require_state()
    node = _find_node(state["root"], node_id)
    node["status"] = "done"
    if produced:
        node["produced"] = produced
    if node_id == state["root"]["id"]:
        state["status"] = "done"
    save_state(state)
    total = _count_tree_nodes(state["root"])
    terminals = _count_terminal_nodes(state["root"])
    return {
        "action": "node_done",
        "node_id": node_id,
        "progress": {"terminal": terminals, "total": total},
    }


def cmd_tree_node_fail(node_id: str) -> dict:
    state = require_state()
    node = _find_node(state["root"], node_id)
    node["status"] = "failed"
    save_state(state)
    total = _count_tree_nodes(state["root"])
    terminals = _count_terminal_nodes(state["root"])
    return {
        "action": "node_failed",
        "node_id": node_id,
        "progress": {"terminal": terminals, "total": total},
    }


def cmd_tree_node_skip(node_id: str) -> dict:
    state = require_state()
    node = _find_node(state["root"], node_id)
    node["status"] = "skipped"
    save_state(state)
    total = _count_tree_nodes(state["root"])
    terminals = _count_terminal_nodes(state["root"])
    return {
        "action": "node_skipped",
        "node_id": node_id,
        "progress": {"terminal": terminals, "total": total},
    }


def cmd_tree_node_advance(node_id: str, action: str = "done",
                          result_summary: str = "") -> dict:
    """Start and complete a pending leaf node in one call.

    Combines node-start + node-done into one round-trip.
    action must be 'done', 'fail', or 'skip'.

    Optionally attach a result_summary to the node's decision_log.
    """
    _ADVANCE_STATUS = {"done": "done", "fail": "failed", "skip": "skipped"}
    status = _ADVANCE_STATUS.get(action)
    if status is None:
        raise ValueError(f"Invalid advance action '{action}'. Use done, fail, or skip.")

    state = require_state()
    node = _find_node(state["root"], node_id)
    if node["status"] != "pending":
        raise ValueError(
            f"Node {node_id} is not pending (status={node['status']}). Cannot advance."
        )

    node["status"] = status
    if result_summary:
        node.setdefault("decision_log", []).append({
            "timestamp": timestamp(),
            "event": "advanced",
            "action": action,
            "summary": result_summary,
        })

    save_state(state)
    total = _count_tree_nodes(state["root"])
    terminals = _count_terminal_nodes(state["root"])
    return {
        "action": "node_advanced",
        "node_id": node_id,
        "result": action,
        "progress": {"terminal": terminals, "total": total},
    }


def cmd_tree_batch_done(node_ids: list[str],
                        summaries: dict[str, str] | None = None,
                        produced_map: dict[str, dict] | None = None) -> dict:
    """Mark multiple pending leaf nodes as done in one call.

    Skips nodes that are not pending (returns per-node errors).
    Optionally attach summaries to each node's decision_log
    and produced payload per node.
    """
    state = require_state()
    results: list[dict] = []

    for node_id in node_ids:
        node = _find_node(state["root"], node_id)
        if node["status"] != "pending":
            results.append({
                "id": node_id,
                "error": f"not pending (status={node['status']})",
            })
            continue
        node["status"] = "done"
        if summaries and node_id in summaries:
            node.setdefault("decision_log", []).append({
                "timestamp": timestamp(),
                "event": "batch_done",
                "summary": summaries[node_id],
            })
        if produced_map and node_id in produced_map:
            node["produced"] = produced_map[node_id]
        results.append({"id": node_id, "status": "done"})

    save_state(state)
    total = _count_tree_nodes(state["root"])
    terminals = _count_terminal_nodes(state["root"])
    return {
        "action": "batch_done",
        "results": results,
        "progress": {"terminal": terminals, "total": total},
    }


def cmd_tree_check_async(node_id: str, result: str = "done") -> dict:
    """Resolve an async A-phase node."""
    if result not in ("done", "fail"):
        raise ValueError("result must be 'done' or 'fail'")

    state = require_state()
    node = _find_node(state["root"], node_id)

    if not node.get("async"):
        raise ValueError(f"Node {node_id} is not async. Use node-done instead.")

    node["status"] = "done" if result == "done" else "failed"
    node["async_resolved_at"] = timestamp()
    save_state(state)

    total = _count_tree_nodes(state["root"])
    terminals = _count_terminal_nodes(state["root"])
    return {
        "action": "async_resolved",
        "node_id": node_id,
        "result": result,
        "progress": {"terminal": terminals, "total": total},
    }


def cmd_tree_expand(node_id: str, children_spec: list[dict]) -> dict:
    """Expand a node into children."""
    state = require_state()
    parent = _find_node(state["root"], node_id)

    if parent.get("children"):
        raise ValueError(f"Node {node_id} already has children. Prune first or expand a leaf node.")

    # Pass 1: create all nodes
    created_nodes = []
    for spec in children_spec:
        child = make_node(
            phase=spec.get("phase", "D"),
            mode=spec.get("mode"),
            title=spec.get("title", ""),
            description=spec.get("description", ""),
            methodology=spec.get("methodology"),
            style=spec.get("style", "balanced"),
            dependencies=spec.get("dependencies", []),
        )
        parent.setdefault("children", []).append(child)
        created_nodes.append(child)

    # Pass 2: resolve dependency references (support both integer index and string id)
    for i, spec in enumerate(children_spec):
        raw_deps = spec.get("dependencies", [])
        resolved = []
        for d in raw_deps:
            if isinstance(d, int):
                if 0 <= d < len(created_nodes):
                    resolved.append(created_nodes[d]["id"])
                else:
                    raise ValueError(f"Child {i}: dependency index {d} out of range (0-{len(created_nodes)-1})")
            else:
                resolved.append(str(d))
        created_nodes[i]["dependencies"] = resolved

    created = [{
        "id": n["id"],
        "phase": n["phase"],
        "mode": n["mode"],
        "style": n.get("style"),
        "title": n.get("title", ""),
    } for n in created_nodes]

    parent["status"] = "waiting_for_children"
    save_state(state)

    tick_result = _do_tree_tick(state)
    save_state(state)

    return {
        "action": "expanded",
        "parent_id": node_id,
        "children": created,
        "count": len(created),
        "tick": tick_result,
    }


def cmd_tree_prune(node_id: str) -> dict:
    """Remove a node and all its children. Reverts expand."""
    state = require_state()

    if state["root"]["id"] == node_id:
        raise ValueError("Cannot prune root node")

    parent, idx = _find_parent_child_index(state["root"], node_id)
    if parent is None:
        raise ValueError(f"Node {node_id} not found")

    removed = parent["children"].pop(idx)
    parent["status"] = "pending"
    save_state(state)

    return {
        "action": "pruned",
        "node_id": node_id,
        "title": removed.get("title", ""),
        "descendants": len(removed.get("children", [])),
        "parent_id": parent["id"],
    }


def cmd_tree_status() -> dict:
    """Return full tree status."""
    state = require_state()
    root = state["root"]
    total = _count_tree_nodes(root)
    terminals = _count_terminal_nodes(root)

    return {
        "slug": state["slug"],
        "goal": state.get("goal", ""),
        "status": state["status"],
        "total_nodes": total,
        "terminal_nodes": terminals,
        "tree": _format_tree_lines(root),
    }


# ── Phase Commands ──────────────────────────────────────────────────────

def cmd_phase_set_mode(node_id: str, mode: str) -> dict:
    state = require_state()
    node = _find_node(state["root"], node_id)
    phase = node["phase"]
    valid_modes = PHASE_MODES.get(phase, [])

    if mode not in valid_modes:
        raise ValueError(f"Mode '{mode}' not valid for phase {phase}. Valid: {valid_modes}")

    node["mode"] = mode
    save_state(state)
    return {"action": "mode_set", "node_id": node_id, "phase": phase, "mode": mode}


def cmd_phase_transition(node_id: str, new_phase: str) -> dict:
    new_phase = new_phase.upper()
    if new_phase == "DONE":
        state = require_state()
        node = _find_node(state["root"], node_id)
        old_phase = node["phase"]
        node["phase"] = "DONE"
        node["status"] = "done"
        if node_id == state["root"]["id"]:
            state["status"] = "done"
        save_state(state)
        return {
            "action": "phase_transition",
            "node_id": node_id,
            "from": old_phase,
            "to": "DONE",
            "tick": None,
        }
    if new_phase not in PHASES:
        raise ValueError(f"Invalid phase '{new_phase}'. Use P, D, C, A, or DONE.")

    state = require_state()
    node = _find_node(state["root"], node_id)
    old_phase = node["phase"]
    node["phase"] = new_phase
    node["mode"] = None
    node["status"] = "pending"
    save_state(state)

    tick_result = _do_tree_tick(state)
    save_state(state)

    return {
        "action": "phase_transition",
        "node_id": node_id,
        "from": old_phase,
        "to": new_phase,
        "tick": tick_result,
    }


def cmd_phase_set_async(node_id: str, reason: str = "") -> dict:
    """Mark an A-phase node as async (non-blocking)."""
    state = require_state()
    node = _find_node(state["root"], node_id)

    if node["phase"] != "A":
        raise ValueError(
            f"Only A-phase nodes can be async. Node {node_id} is in phase {node['phase']}."
        )

    node["async"] = True
    node["async_reason"] = reason
    if node["status"] == "pending":
        node["status"] = "running"
    save_state(state)
    return {
        "action": "async_set",
        "node_id": node_id,
        "reason": reason,
    }


def cmd_phase_set_style(node_id: str, style: str) -> dict:
    """Set execution style for a node."""
    if style not in STYLES:
        raise ValueError(f"Invalid style '{style}'. Valid: {sorted(STYLES)}")

    state = require_state()
    node = _find_node(state["root"], node_id)
    node["style"] = style
    save_state(state)
    return {"action": "style_set", "node_id": node_id, "style": style}


# ── Methodology Commands ────────────────────────────────────────────────

def cmd_methodology_list(phase: str | None = None, mode: str | None = None) -> dict:
    available = list_available_methodologies()
    entries = []
    for name in available:
        applicable = []
        for (p, m), ms in METHODOLOGY_REGISTRY.items():
            if name in ms:
                applicable.append(f"{p}+{m}")
        entries.append({"name": name, "applicable": applicable})
    suggested = []
    if phase or mode:
        suggested = get_methodology_names_for(phase or "?", mode or "?")
    return {
        "methodologies": entries,
        "suggested": suggested if (phase or mode) else None,
    }


def cmd_methodology_get(name: str) -> dict:
    content = get_methodology(name)
    if content is None:
        available = list_available_methodologies()
        raise ValueError(f"Methodology '{name}' not found. Available: {available}")
    return {"name": name, "content": content}


# ── State Commands ──────────────────────────────────────────────────────

def cmd_state_show(key: str | None = None) -> dict:
    state = require_state()
    if key:
        parts = key.split(".")
        val = state
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                val = None
                break
        if val is None:
            raise ValueError(f"Key '{key}' not found")
        return {"key": key, "value": val}
    return {"state": state}


def cmd_state_set(key: str, value: str) -> dict:
    state = require_state()
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        parsed = value

    parts = key.split(".")
    target = state
    for p in parts[:-1]:
        target = target.setdefault(p, {})
    target[parts[-1]] = parsed

    save_state(state)
    return {"action": "state_set", "key": key, "value": parsed}


def cmd_save() -> dict:
    require_state()
    save_state(require_state())
    return {"action": "saved"}


# ── Helpers ─────────────────────────────────────────────────────────────

def _find_node(root: dict, node_id: str) -> dict:
    chain = _find_node_chain(root, node_id)
    if chain is None:
        raise ValueError(f"Node {node_id} not found")
    return chain[-1]


def _children_status_summary(children: list[dict]) -> str:
    counts = {"done": 0, "failed": 0, "skipped": 0, "pending": 0, "running": 0}
    for c in children:
        s = c.get("status", "pending")
        if s in counts:
            counts[s] += 1
        else:
            counts["pending"] += 1
    parts = [f"{k}={v}" for k, v in counts.items() if v > 0]
    return ", ".join(parts)


def _count_terminal_nodes(node: dict) -> int:
    count = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if n["status"] in ("done", "failed", "skipped"):
            count += 1
        for c in n.get("children", []):
            stack.append(c)
    return count


def _collect_async_nodes(node: dict) -> list[dict]:
    """Collect all async-running nodes from the tree."""
    result = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.get("async") and n["status"] == "running":
            result.append(n)
        for c in n.get("children", []):
            stack.append(c)
    return result


def _build_status_map(node: dict) -> dict[str, str]:
    """Build a flat map of node_id → status for the entire tree."""
    result = {}
    stack = [node]
    while stack:
        n = stack.pop()
        result[n["id"]] = n["status"]
        for c in n.get("children", []):
            stack.append(c)
    return result


def _resolve_waves_raw(nodes: list[dict], status_map: dict[str, str]) -> dict[str, int]:
    """Wavelength calculation for a list of nodes using the full tree status map."""
    node_map = {n["id"]: n for n in nodes}
    wave: dict[str, int] = {}

    changed = True
    while changed:
        changed = False
        for n in nodes:
            nid = n["id"]
            if nid in wave:
                continue
            deps = n.get("dependencies", [])
            if not deps:
                wave[nid] = 0
                changed = True
            else:
                dep_waves = [wave.get(d) for d in deps]
                if dep_waves and all(w is not None for w in dep_waves):
                    wave[nid] = max(dep_waves) + 1
                    changed = True

    for n in nodes:
        wave.setdefault(n["id"], 0)
    return wave


def _group_by_wave(nodes: list[dict], wave: dict[str, int]) -> dict[int, list[str]]:
    groups: dict[int, list[str]] = {}
    for n in nodes:
        w = wave.get(n["id"], 0)
        groups.setdefault(w, []).append(n["id"])
    return dict(sorted(groups.items()))


def _format_tree_lines(node: dict, depth: int = 0) -> list[str]:
    """Format the node and its children into indented status lines."""
    prefix = "  " * depth
    phase_mode = node["phase"]
    if node.get("mode"):
        phase_mode += f"/{node['mode']}"
    style = node.get("style", "balanced")
    style_tag = f":{style}" if style != "balanced" else ""
    title = node.get("title", "")
    status_icon = {
        "pending": "○",
        "running": "▶",
        "done": "✓",
        "failed": "✗",
        "skipped": "–",
        "waiting_for_children": "◉",
    }.get(node["status"], "?")

    if node.get("async") and node["status"] == "running":
        status_icon = "↻"

    deps = node.get("dependencies", [])
    dep_str = f" dep:[{','.join(deps)}]" if deps else ""

    line = f"{prefix}{status_icon} {node['id']} [{phase_mode}{style_tag}] {title}{dep_str}"
    lines = [line]

    for child in node.get("children", []):
        lines.extend(_format_tree_lines(child, depth + 1))

    return lines
