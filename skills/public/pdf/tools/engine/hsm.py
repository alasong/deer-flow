"""HSM (Hierarchical State Machine) — stage transition engine for PDF strong engine.

Drives PDCA stage progression with:
- Hierarchical state paths: plan → plan.develop → plan.review → do → check → act
- Event-driven transitions with target, reset scope, and max_loops
- Loop counting per transition — auto-pause when exhausted
- Rollback events (design_flaw → plan, bug → do) with precise reset scope

References blueprint.yaml for stage definitions and transition rules.
"""
import time, os

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import yaml
except ImportError:
    yaml = None

from . import constraints as _constraints


def _yaml_load(path):
    if yaml is None:
        return None
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _blueprint_path(blueprint_name="default"):
    """Resolve blueprint file path by name. Falls back to 'default' → 'blueprint.yaml'."""
    index_path = os.path.join(_SKILL_DIR, "docs", "topology", "index.yaml")
    index = _yaml_load(index_path)
    if index and "blueprints" in index:
        bp_info = index["blueprints"].get(blueprint_name)
        if bp_info:
            return os.path.join(_SKILL_DIR, "docs", "topology", bp_info["file"])
        # fallback to default
        default_info = index["blueprints"].get("default")
        if default_info:
            return os.path.join(_SKILL_DIR, "docs", "topology", default_info["file"])
    return os.path.join(_SKILL_DIR, "docs", "topology", "blueprint.yaml")


def _load_blueprint(blueprint_name=None):
    """Load blueprint by name. If None, read from state.blueprint or fallback to 'default'."""
    if blueprint_name is None:
        # Try to read from state
        state_path = os.path.join(_SKILL_DIR, "..", "..", ".fat", "pdf", ".pdf_state.json")
        if os.path.exists(state_path):
            try:
                state_data = _yaml_load(state_path)
                if state_data and "blueprint" in state_data:
                    blueprint_name = state_data["blueprint"]
                elif state_data and "channel" in state_data:
                    blueprint_name = state_data["channel"]
            except Exception:
                pass
    if not blueprint_name:
        blueprint_name = "default"
    path = _blueprint_path(blueprint_name)
    if not os.path.exists(path):
        return None
    return _yaml_load(path)


# ── HSM State Management ───────────────────────────────────────────

def _init_hsm_state(state):
    """Ensure HSM state fields exist in state dict. Returns state (mutated)."""
    if "hsm_path" not in state:
        state["hsm_path"] = ["plan"]
    if "hsm_loop_counts" not in state:
        state["hsm_loop_counts"] = {}
    if "hsm_paused" not in state:
        state["hsm_paused"] = False
    if "hsm_pause_reason" not in state:
        state["hsm_pause_reason"] = None
    return state


def hsm_path(state):
    """Get current HSM path as list of strings."""
    return state.get("hsm_path", ["plan"])


def hsm_path_str(state):
    """Get current HSM path as dot-separated string."""
    return ".".join(hsm_path(state))


def hsm_status(state):
    """Full HSM status dict."""
    bp = _load_blueprint()
    current = hsm_path_str(state)
    available_events = _available_transitions(state, bp)

    return {
        "path": current,
        "paused": state.get("hsm_paused", False),
        "pause_reason": state.get("hsm_pause_reason"),
        "loop_counts": dict(state.get("hsm_loop_counts", {})),
        "available_events": available_events,
        "stage": state.get("stage", "plan"),
        "round": state.get("round", 1),
    }


# ── Transition Engine ──────────────────────────────────────────────

def _available_transitions(state, blueprint):
    """Return list of events that can be fired from current HSM state."""
    bp = blueprint or _load_blueprint()
    if not bp:
        return []

    stages = bp.get("stages", {})
    current_stage = state.get("stage", "plan")
    sdef = stages.get(current_stage, {})
    transitions = sdef.get("transitions", [])

    available = []
    for t in transitions:
        event = t["event"]
        loop_key = _hsm_loop_key(current_stage, event)
        max_loops = t.get("max_loops", 3)
        current_loops = state.get("hsm_loop_counts", {}).get(loop_key, 0)

        available.append({
            "event": event,
            "target": t.get("target"),
            "desc": t.get("desc", ""),
            "max_loops": max_loops,
            "current_loops": current_loops,
            "exhausted": current_loops >= max_loops,
        })
    return available


def _hsm_loop_key(stage, event):
    return f"{stage}:{event}"


def fire_event(state, event):
    """Fire an HSM transition event. Returns result dict.

    Returns:
      - {"action": "transition", "from": ..., "to": ..., "reset": [...]} on success
      - {"action": "error", "reason": ...} on failure
      - {"action": "paused", "reason": ..., "event": ...} when max_loops exhausted
    """
    bp = _load_blueprint()
    if not bp:
        return {"action": "error", "reason": "blueprint not found"}

    stages = bp.get("stages", {})
    current_stage = state.get("stage", "plan")
    sdef = stages.get(current_stage, {})
    transitions = sdef.get("transitions", [])

    # Find matching transition
    matched = None
    for t in transitions:
        if t["event"] == event:
            matched = t
            break

    if not matched:
        return {"action": "error", "reason": f"event '{event}' not available for stage '{current_stage}'"}

    # Check max_loops
    loop_key = _hsm_loop_key(current_stage, event)
    loops = state.setdefault("hsm_loop_counts", {})
    current = loops.get(loop_key, 0)
    max_loops = matched.get("max_loops", 3)

    if current >= max_loops:
        state["hsm_paused"] = True
        state["hsm_pause_reason"] = f"max_loops ({max_loops}) exhausted for event '{event}'"
        return {"action": "paused", "reason": state["hsm_pause_reason"],
                "event": event, "max_loops": max_loops}

    # ── Constraint verification ───────────────────────────────────────
    # Auto-verify constraints before executing transition.
    # Blocking violations prevent the transition.
    all_pass, violations, blocking = _constraints.verify_constraints(
        stage=current_stage, state=state)
    if blocking:
        return {
            "action": "constraints_blocked",
            "blocking": blocking,
            "violations": violations,
            "reason": "Constraints not satisfied",
        }

    # Execute transition: increment loop count
    loops[loop_key] = current + 1

    # Resolve target and reset scope
    target = matched.get("target")
    reset_scope = matched.get("reset", [])

    # Apply resets
    dag_progress = state.setdefault("dag_progress", {})
    if reset_scope:
        for reset_path in reset_scope:
            _apply_reset(state, reset_path, bp)

    # Update HSM path and stage
    new_stage = _target_to_stage(target, current_stage)
    old_stage = state.get("stage", current_stage)
    state["stage"] = new_stage

    # Build hsm_path
    if target == "__exit__":
        state["hsm_path"] = _next_stage_hsm_path(current_stage)
    elif target == "__final__":
        state["hsm_path"] = ["__final__"]
        state["stage"] = "done"
    elif target and target.startswith("plan."):
        state["hsm_path"] = ["plan"] + target.split(".")[1:]
    elif target and target.startswith("do."):
        state["hsm_path"] = ["do"] + target.split(".")[1:]
    elif target == "plan":
        state["hsm_path"] = ["plan"]
    elif target == "do":
        state["hsm_path"] = ["do"]
    elif target == "check":
        state["hsm_path"] = ["check"]
    elif target == "act":
        state["hsm_path"] = ["act"]
    else:
        # Default: advance to next stage
        state["hsm_path"] = [new_stage]

    return {
        "action": "transition",
        "from": old_stage,
        "to": new_stage,
        "event": event,
        "desc": matched.get("desc", ""),
        "reset": list(reset_scope),
        "loop_count": loops.get(loop_key, 0),
    }


def _target_to_stage(target, current_stage):
    """Resolve a transition target to a stage name."""
    if target == "__exit__":
        return _next_stage_name(current_stage)
    if target == "__final__":
        return "done"
    if target and target.startswith("plan"):
        return "plan"
    if target and target.startswith("do"):
        return "do"
    if target == "check":
        return "check"
    if target == "act":
        return "act"
    return current_stage  # stay


def _next_stage_name(current):
    order = ["plan", "do", "check", "act", "done"]
    try:
        idx = order.index(current)
        return order[idx + 1] if idx + 1 < len(order) else "done"
    except ValueError:
        return "done"


def _next_stage_hsm_path(current):
    order = ["plan", "do", "check", "act", "done"]
    try:
        idx = order.index(current)
        next_s = order[idx + 1] if idx + 1 < len(order) else "done"
        return [next_s]
    except ValueError:
        return ["done"]


def _apply_reset(state, reset_path, blueprint):
    """Apply a reset scope path — reset matching DAG nodes to pending."""
    # reset_path examples: "plan.*", "do.D1_doer", "plan.P0_design"
    dag_progress = state.setdefault("dag_progress", {})

    if reset_path.endswith(".*"):
        # Reset all nodes in the stage
        stage_name = reset_path[:-2]
        if stage_name in dag_progress:
            for ref in dag_progress[stage_name]:
                dag_progress[stage_name][ref] = "pending"
        return

    # Specific stage ref patterns: "do.D1_doer"
    parts = reset_path.split(".", 1)
    if len(parts) == 2:
        stage_name, ref_pattern = parts
        if stage_name in dag_progress:
            if ref_pattern in dag_progress[stage_name]:
                dag_progress[stage_name][ref_pattern] = "pending"
            else:
                # Pattern might be wildcard within nodes
                for ref in dag_progress[stage_name]:
                    if ref.startswith(ref_pattern.rstrip("*")):
                        dag_progress[stage_name][ref] = "pending"


# ── Retry management ───────────────────────────────────────────────

def mark_node_done(state, stage, ref):
    """Mark a DAG node as done."""
    progress = state.setdefault("dag_progress", {}).setdefault(stage, {})
    progress[ref] = "done"


def mark_node_failed(state, stage, ref):
    """Mark a DAG node as failed."""
    progress = state.setdefault("dag_progress", {}).setdefault(stage, {})
    progress[ref] = "failed"


def mark_node_running(state, stage, ref):
    """Mark a DAG node as running."""
    progress = state.setdefault("dag_progress", {}).setdefault(stage, {})
    progress[ref] = "running"


def mark_node_skipped(state, stage, ref):
    """Mark a DAG node as skipped."""
    progress = state.setdefault("dag_progress", {}).setdefault(stage, {})
    progress[ref] = "skipped"


# ── Force state overrides (for debugging / manual intervention) ────

def hsm_goto(state, path_str):
    """Force-set HSM path (debug/manual override)."""
    path = path_str.split(".") if path_str else []
    if not path:
        return {"action": "error", "reason": "empty path"}
    state["hsm_path"] = path
    state["stage"] = path[0]
    state["hsm_paused"] = False
    state["hsm_pause_reason"] = None
    return {"action": "goto", "path": path_str}


def hsm_unpause(state):
    """Clear paused state — allows transitions to continue."""
    state["hsm_paused"] = False
    state["hsm_pause_reason"] = None
    return {"action": "unpaused"}


def hsm_reset_loops(state, event=None):
    """Reset loop counters. If event given, resets only that event."""
    loops = state.setdefault("hsm_loop_counts", {})
    if event:
        for key in list(loops.keys()):
            if key.endswith(f":{event}"):
                loops[key] = 0
    else:
        for key in list(loops.keys()):
            loops[key] = 0
    state["hsm_paused"] = False
    state["hsm_pause_reason"] = None
    return {"action": "loops_reset", "event": event or "all"}
