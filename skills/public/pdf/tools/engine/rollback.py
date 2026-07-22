"""Rollback engine — precise upstream rollback and stage-level retry.

Core operations:
- get_upstream_of: recursive dependency walk to find affected nodes
- rollback_to_stage: phase-level rollback via HSM transitions
- mark_dirty: tag nodes needing re-execution
- rollback_decision: mark decisions for re-evaluation

All operations are "safe" — they don't delete state, just reset status to pending.
"""
from . import dag as _dag
from . import hsm as _hsm


# ── Upstream resolution ────────────────────────────────────────────

def get_upstream_of(state, stage, ref, blueprint=None):
    """Walk deps backward to find ALL upstream nodes recursively.
    Returns set of node refs that need resetting.
    """
    bp = blueprint or _dag.load_blueprint()
    if not bp:
        return set()

    nodes = _dag.get_stage_nodes(bp, stage)
    sdef = bp.get("stages", {}).get(stage, {})
    nodes_by_ref = {n["ref"]: n for n in nodes}
    visited = set()
    to_visit = [ref]

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)
        ndef = nodes_by_ref.get(current, {})
        for dep in ndef.get("deps", []):
            if dep not in visited:
                to_visit.append(dep)

    visited.discard(ref)
    # Only return nodes that actually exist in progress
    progress = state.get("dag_progress", {}).get(stage, {})
    return {n for n in visited if n in progress}


# ── Dirty node tracking ────────────────────────────────────────────

def mark_dirty(state, stage, ref, reason=""):
    """Mark a node as dirty — needs re-execution before stage can complete.

    Dirty differs from pending: pending means "not started yet",
    dirty means "was done but invalidated and needs redo".
    """
    progress = state.get("dag_progress", {}).setdefault(stage, {})
    progress[ref] = "dirty"
    dirty_log = state.setdefault("dirty_log", [])
    dirty_log.append({
        "stage": stage,
        "ref": ref,
        "reason": reason,
        "ts": __import__("time").time(),
    })
    return {"dirty": True, "node": ref, "reason": reason}


def mark_clean(state, stage, ref):
    """Clear dirty status (when node is about to retry)."""
    progress = state.get("dag_progress", {}).get(stage, {})
    if progress.get(ref) == "dirty":
        progress[ref] = "pending"


def list_dirty(state):
    """List all dirty nodes across all stages."""
    dirty = []
    for stage, progress in state.get("dag_progress", {}).items():
        for ref, status in progress.items():
            if status == "dirty":
                dirty.append({"stage": stage, "ref": ref})
    return dirty


# ── Stage-level rollback ───────────────────────────────────────────

def rollback_to_stage(state, target_stage, event, blueprint=None):
    """Rollback to a target stage via HSM transition.

    This is the primary rollback entry point. It:
    1. Fires an HSM transition event (e.g., 'design_flaw' → plan)
    2. The HSM applies the transition's reset scope automatically
    3. Logs the rollback for audit

    Returns result from hsm.fire_event.
    """
    bp = blueprint or _dag.load_blueprint()

    # Log rollback
    rollback_log = state.setdefault("rollback_history", [])
    rollback_log.append({
        "event": event,
        "from_stage": state.get("stage"),
        "to_stage": target_stage,
        "ts": __import__("time").time(),
    })

    result = _hsm.fire_event(state, event)
    return result


def check_rollback_readiness(state, target_stage):
    """Check if rollback to target stage is safe.
    Returns (ok, reason).
    """
    order = ["plan", "do", "check", "act", "done"]
    current_stage = state.get("stage", "plan")

    if current_stage == target_stage:
        return False, "already in target stage"

    try:
        current_idx = order.index(current_stage)
        target_idx = order.index(target_stage)
    except ValueError:
        return False, f"unknown stage: {current_stage} or {target_stage}"

    if target_idx >= current_idx:
        return False, f"can only rollback backward (current={current_stage}, target={target_stage})"

    return True, f"ready to rollback from '{current_stage}' to '{target_stage}'"


# ── Decision rollback ──────────────────────────────────────────────

def rollback_decisions(state, stages=None):
    """Mark decisions in specified stages for re-evaluation.
    If stages is None, marks all decisions as stale.
    """
    stale = state.setdefault("stale_decisions", [])
    if stages:
        for s in stages:
            stale.append({"stage": s, "ts": __import__("time").time()})
    else:
        stale.append({"stage": "*", "ts": __import__("time").time()})
    return {"stale_stages": stages or ["*"]}


# ── Retry orchestration ────────────────────────────────────────────

def retry_node(state, stage, ref, blueprint=None):
    """Reset a single node for retry + reset all downstream nodes.

    When a node fails and hasn't exhausted retries:
    1. Reset the node itself to pending
    2. Reset all downstream nodes that depend on it
    3. Update retry count
    """
    # Reset the node itself
    _dag.reset_node(state, stage, ref)
    _dag.record_retry(state, stage, ref)

    # Find and reset downstream nodes
    bp = blueprint or _dag.load_blueprint()
    downstream = _find_downstream(state, stage, ref, bp)
    for dref in downstream:
        _dag.reset_node(state, stage, dref)

    return {
        "action": "retry",
        "node": ref,
        "stage": stage,
        "retry_count": _dag.retry_count(state, stage, ref),
        "downstream_reset": list(downstream),
    }


def _find_downstream(state, stage, ref, blueprint):
    """Find all nodes that directly or indirectly depend on ref."""
    bp = blueprint or _dag.load_blueprint()
    nodes = _dag.get_stage_nodes(bp, stage)
    dep_map = {}
    for n in nodes:
        for dep in n.get("deps", []):
            dep_map.setdefault(dep, set()).add(n["ref"])

    downstream = set()
    to_visit = {ref}
    visited = set()

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)
        for child in dep_map.get(current, set()):
            if child not in visited:
                downstream.add(child)
                to_visit.add(child)

    progress = state.get("dag_progress", {}).get(stage, {})
    return {d for d in downstream if d in progress}
