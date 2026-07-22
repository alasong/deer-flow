"""Blueprint DAG — topology loading, node readiness, dependency resolution.

Loads blueprint.yaml → builds in-memory DAG → provides readiness checks.
Blueprint = fixed PDCA topology with retry policy and checkpoint nodes.
"""
import json, os, sys, hashlib
from pathlib import Path

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import yaml
except ImportError:
    yaml = None


def _yaml_load(path):
    """Load YAML with graceful fallback."""
    if yaml is None:
        return None
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


# ── Blueprint loading ──────────────────────────────────────────────

def _blueprints_dir():
    return os.path.join(SKILL_DIR, "docs", "topology")


def _blueprint_path(name="default"):
    index = _yaml_load(os.path.join(_blueprints_dir(), "index.yaml"))
    if index and "blueprints" in index:
        bp_info = index["blueprints"].get(name)
        if bp_info:
            return os.path.join(_blueprints_dir(), bp_info["file"])
    return os.path.join(_blueprints_dir(), "blueprint.yaml")


def load_blueprint(name="default"):
    """Load blueprint YAML. Returns dict or None if not found."""
    path = _blueprint_path(name)
    if not os.path.exists(path):
        return None
    data = _yaml_load(path)
    return data


def list_blueprints():
    """List available blueprints from index.yaml."""
    index = _yaml_load(os.path.join(_blueprints_dir(), "index.yaml"))
    if not index or "blueprints" not in index:
        return {"default": {"file": "blueprint.yaml", "description": "Default blueprint"}}
    return {k: v for k, v in index["blueprints"].items()}


def validate_blueprint(data):
    """Validate blueprint structure. Returns list of errors (empty = valid)."""
    errors = []
    if not data:
        return ["blueprint is empty"]
    stages = data.get("stages", {})
    if not stages:
        return ["blueprint has no stages"]

    for sname, sdef in stages.items():
        nodes = sdef.get("nodes", [])
        if not nodes:
            errors.append(f"stage '{sname}' has no nodes")
        node_refs = set()
        for n in nodes:
            ref = n.get("ref")
            if not ref:
                errors.append(f"stage '{sname}': node without ref")
                continue
            if ref in node_refs:
                errors.append(f"stage '{sname}': duplicate node ref '{ref}'")
            node_refs.add(ref)
            ntype = n.get("type")
            if ntype not in ("engine_exec", "llm_spawn", "llm_merge", "llm_converge",
                             "engine_diff", "repair_gate", "manual_checkpoint",
                             "engine_diff + llm_converge"):
                errors.append(f"stage '{sname}' node '{ref}': unknown type '{ntype}'")
            if n.get("retry") is not None and (not isinstance(n["retry"], int) or n["retry"] < 0):
                errors.append(f"stage '{sname}' node '{ref}': invalid retry={n['retry']}")
        # Check deps refer to existing nodes
        for n in nodes:
            ref = n.get("ref", "?")
            for dep in n.get("deps", []):
                if dep not in node_refs:
                    errors.append(f"stage '{sname}' node '{ref}': dep '{dep}' not found")

    return errors


# ── DAG node state ─────────────────────────────────────────────────

def _dag_key(stage, ref):
    return f"{stage}.{ref}"


def _init_dag_progress(state, blueprint):
    """Initialize or ensure dag_progress in state from blueprint."""
    progress = state.setdefault("dag_progress", {})
    stages = (blueprint or {}).get("stages", {})
    for sname, sdef in stages.items():
        sp = progress.setdefault(sname, {})
        for n in sdef.get("nodes", []):
            ref = n["ref"]
            if ref not in sp:
                sp[ref] = "pending"
    return progress


# ── Node readiness engine ──────────────────────────────────────────

def _node_status(state, stage, ref):
    """Get status of a single node. Returns one of:
    pending | running | checkpoint_blocked | done | skipped | dirty | failed
    """
    progress = state.get("dag_progress", {}).get(stage, {})
    return progress.get(ref, "pending")


def _is_node_done(state, stage, ref):
    return _node_status(state, stage, ref) in ("done", "skipped")


def _all_nodes_done(state, stage, nodes_list):
    """Check if all named nodes are done/skipped."""
    for ref in nodes_list:
        if not _is_node_done(state, stage, ref):
            return False
    return True


def _compute_ready_nodes(state, blueprint, stage):
    """Compute all nodes in a stage that are ready to execute.
    Make-style: finds ALL nodes whose deps are met and conditions pass.
    Returns list of (ref, node_def) tuples.
    """
    stages = (blueprint or {}).get("stages", {})
    sdef = stages.get(stage, {})
    nodes = sdef.get("nodes", [])
    progress = state.get("dag_progress", {}).get(stage, {})

    # Resume safety: downgrade stale "running" to "pending"
    # (process crashed mid-node; node-start was called but node-done never fired)
    if stage == state.get("stage"):
        for ref, _status in list(progress.items()):
            if _status == "running":
                progress[ref] = "pending"

    ready = []
    for ndef in nodes:
        ref = ndef["ref"]
        status = progress.get(ref, "pending")

        # Skip already done/skipped/running
        if status in ("done", "skipped", "running", "checkpoint_blocked"):
            continue

        # Failed → only ready if retry remaining
        if status == "failed":
            retry_count = state.get("retry_counts", {}).get(_dag_key(stage, ref), 0)
            max_retry = ndef.get("retry", 3)
            if retry_count >= max_retry:
                continue  # max retries exhausted, won't auto-retry
            # else: ready for retry

        # Check deps
        deps_met = True
        for dep in ndef.get("deps", []):
            if not _is_node_done(state, stage, dep):
                deps_met = False
                break
        if not deps_met:
            continue

        # Check conditions
        if not _eval_node_conditions(ndef, state):
            continue

        # Check checkpoint blocking
        if _checkpoint_blocking(state, stage, ref, ndef):
            continue

        ready.append((ref, ndef))

    return ready


def _eval_node_conditions(node_def, state):
    """Evaluate run_when / domain_filter / min_depth conditions."""
    run_when = node_def.get("run_when")
    if run_when and isinstance(run_when, dict):
        for key, val in run_when.items():
            actual = state.get(key)
            if actual != val:
                return False

    domain_filter = node_def.get("domain_filter")
    if domain_filter:
        domain = state.get("domain", "")
        if domain not in domain_filter:
            return False

    min_depth = node_def.get("min_depth")
    if min_depth is not None:
        depth = state.get("depth", 0)
        if depth < min_depth:
            return False

    return True


# ── Checkpoint barriers ────────────────────────────────────────────

def _checkpoint_blocking(state, stage, ref, node_def):
    """Check if any upstream checkpoint is blocking this node.
    Returns blocking checkpoint ref, or None.
    """
    deps = node_def.get("deps", [])
    if not deps:
        return None
    progress = state.get("dag_progress", {}).get(stage, {})
    for dep in deps:
        if progress.get(dep) == "checkpoint_blocked":
            return dep
    return None


def mark_checkpoint_done(state, stage, ref):
    """Mark a checkpoint node as checkpoint_blocked (done but blocking downstream)."""
    progress = state.setdefault("dag_progress", {}).setdefault(stage, {})
    progress[ref] = "checkpoint_blocked"
    return {"action": "checkpoint_blocked", "node": ref,
            "desc": f"Checkpoint '{ref}' executed — blocking downstream. "
                    f"Use 'pipeline pass-checkpoint {ref}' or 'pipeline reject-checkpoint {ref}'."}


def pass_checkpoint(state, stage, ref):
    """Accept a checkpoint — mark done, unblock downstream."""
    progress = state.get("dag_progress", {}).get(stage, {})
    if progress.get(ref) == "checkpoint_blocked":
        progress[ref] = "done"
        return {"action": "checkpoint_passed", "node": ref}
    return {"action": "error", "reason": f"Checkpoint '{ref}' not found or not blocked"}


def reject_checkpoint(state, stage, ref, blueprint):
    """Reject a checkpoint — reset upstream nodes for redo."""
    progress = state.get("dag_progress", {}).get(stage, {})
    if progress.get(ref) != "checkpoint_blocked":
        return {"action": "error", "reason": f"Checkpoint '{ref}' not found or not blocked"}

    stages = (blueprint or {}).get("stages", {})
    sdef = stages.get(stage, {})
    upstream = _get_upstream_of(sdef, stage, ref)

    for up_ref in upstream:
        progress[up_ref] = "pending"
    progress[ref] = "pending"

    return {"action": "checkpoint_rejected", "node": ref,
            "reset": list(upstream),
            "desc": f"Checkpoint '{ref}' rejected. Reset {len(upstream)} upstream node(s)."}


def _get_upstream_of(sdef, stage, ref):
    """Walk deps backward from a node to find all upstream nodes.
    Returns set of node names (ordered by dep chain).
    """
    nodes = {n["ref"]: n for n in sdef.get("nodes", [])}
    visited = set()
    to_visit = [ref]

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)
        ndef = nodes.get(current, {})
        for dep in ndef.get("deps", []):
            if dep not in visited:
                to_visit.append(dep)

    visited.discard(ref)
    return visited


# ── Retry tracking ─────────────────────────────────────────────────

def record_retry(state, stage, ref):
    """Increment retry count for a node. Returns new count."""
    key = _dag_key(stage, ref)
    counts = state.setdefault("retry_counts", {})
    counts[key] = counts.get(key, 0) + 1
    return counts[key]


def retry_count(state, stage, ref):
    """Get current retry count for a node."""
    return state.get("retry_counts", {}).get(_dag_key(stage, ref), 0)


def reset_node(state, stage, ref):
    """Reset a single node to pending. Safe for already-pending nodes."""
    progress = state.get("dag_progress", {}).get(stage, {})
    if ref in progress:
        progress[ref] = "pending"


def mark_fingerprint(state, stage, ref):
    """Store a fingerprint for a completed node's outputs."""
    fps = state.setdefault("fingerprints", {})
    fps[_dag_key(stage, ref)] = _quick_hash(str(state.get("dag_progress", {})))


def _quick_hash(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]


# ── Stage-level helpers ────────────────────────────────────────────

def get_stage_nodes(blueprint, stage):
    """Get all node definitions for a stage."""
    stages = (blueprint or {}).get("stages", {})
    sdef = stages.get(stage, {})
    return sdef.get("nodes", [])


def get_stage_transitions(blueprint, stage):
    """Get transition definitions for a stage."""
    stages = (blueprint or {}).get("stages", {})
    sdef = stages.get(stage, {})
    return sdef.get("transitions", [])


def all_stage_nodes_done(state, stage, blueprint):
    """Check if every node in a stage is done or skipped."""
    nodes = get_stage_nodes(blueprint, stage)
    refs = [n["ref"] for n in nodes]
    return _all_nodes_done(state, stage, refs)


def stage_summary(state, stage):
    """Return summary dict for a stage's node statuses."""
    progress = state.get("dag_progress", {}).get(stage, {})
    counts = {"pending": 0, "running": 0, "done": 0, "skipped": 0,
              "failed": 0, "dirty": 0, "checkpoint_blocked": 0}
    for status in progress.values():
        if status in counts:
            counts[status] += 1
    return counts


def pipeline_status(state):
    """Full pipeline status across all stages."""
    result = {}
    for stage, progress in state.get("dag_progress", {}).items():
        result[stage] = stage_summary(state, stage)
    return result
