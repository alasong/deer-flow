"""Pipeline Runner — Make-style scheduler for PDF strong engine.

Core loop:
1. pipeline_tick(): scan DAG → find all ready nodes → batch return
2. LLM executes each ready node
3. LLM reports result back (done/failed/skipped/checkpoint)
4. Repeat from 1 until all nodes done or transition fires

Integrates with HSM for transition events and rollback.
"""
import os, glob

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from . import dag as _dag
from . import hsm as _hsm
from . import constraints as _constraints
from . import rollback as _rollback


# ── Pipeline tick ──────────────────────────────────────────────────

def pipeline_tick(state, blueprint=None):
    """Make-style pipeline scheduler: find ALL ready nodes, batch them.

    Returns dict with:
      - action: "nodes_ready" | "stage_done" | "all_done" | "paused" | "error"
      - nodes: [(ref, node_def), ...]  — ready nodes (when action=nodes_ready)
      - stage: current stage name
      - hsm_path: current HSM path
      - desc: human-readable description
    """
    if blueprint is None:
        bp = _dag.load_blueprint()
    else:
        bp = blueprint

    if not bp:
        return {"action": "error", "reason": "no blueprint loaded"}

    # Check paused
    if state.get("hsm_paused"):
        return {"action": "paused",
                "reason": state.get("hsm_pause_reason", "HSM paused")}

    current_stage = state.get("stage", "plan")

    # "init" is not a real DAG stage — guard against stale init-from-BASE_STATE
    if current_stage == "init":
        return {"action": "error", "stage": "init",
                "reason": "Stage is still 'init'. Run 'state set stage=plan' "
                          "(or trigger channel select which auto-advances) "
                          "before the first pipeline tick."}

    # Check if current stage is done
    if _dag.all_stage_nodes_done(state, current_stage, bp):
        return {"action": "stage_done", "stage": current_stage,
                "desc": f"All nodes in '{current_stage}' complete. "
                        f"Fire HSM transition event to continue."}

    # Find ready nodes
    ready = _dag._compute_ready_nodes(state, bp, current_stage)

    if not ready:
        # Check if any are failed with retry remaining
        pending = _dag.stage_summary(state, current_stage)
        if pending.get("failed", 0) > 0:
            return {"action": "nodes_failed", "stage": current_stage,
                    "failed_count": pending["failed"],
                    "desc": "Some nodes failed — check retry status."}
        if pending.get("checkpoint_blocked", 0) > 0:
            return {"action": "checkpoint_blocked", "stage": current_stage,
                    "desc": "Checkpoint blocking downstream. Use pass/reject-checkpoint."}
        return {"action": "waiting", "stage": current_stage,
                "desc": "No nodes ready yet (deps not met)."}

    return {"action": "nodes_ready",
            "stage": current_stage,
            "nodes": ready,
            "desc": f"{len(ready)} node(s) ready in '{current_stage}'."}


# ── Node execution helpers ─────────────────────────────────────────

def node_start(state, stage, ref, blueprint=None):
    """Mark node as running with admission validation.

    Checks that the node is in the ready set (deps met, conditions pass,
    not already done/running) before allowing execution.

    Returns:
      - On acceptance: {"action": "started", "node": ref, "stage": stage}
      - On rejection:  {"action": "rejected", "reason": ..., "ready_nodes": [...]}
    """
    bp = blueprint or _dag.load_blueprint()
    if not bp:
        return {"action": "rejected", "reason": "no blueprint loaded"}

    ready = _dag._compute_ready_nodes(state, bp, stage)
    ready_refs = [r[0] for r in ready]

    if ref not in ready_refs:
        return {
            "action": "rejected",
            "reason": f"Node '{ref}' is not in the ready set",
            "ready_nodes": ready_refs,
        }

    _hsm.mark_node_running(state, stage, ref)
    return {"action": "started", "node": ref, "stage": stage}


def node_complete(state, stage, ref, blueprint=None):
    """Mark node as done. Validates artifacts before marking.

    If the node declares artifacts and they are missing, returns
    artifact_missing action instead of marking the node done.
    """
    node_def = _find_node_def(state, stage, ref, blueprint)

    if node_def is None:
        return {"action": "unknown_node", "node": ref, "stage": stage,
                "desc": f"Node '{ref}' not found in blueprint for stage '{stage}'."}

    # manual_checkpoint: skip artifact validation (built-in human gate)
    if node_def and node_def.get("type") == "manual_checkpoint":
        # Auto mode: auto-approve if auto_approve=true in blueprint AND auto_mode=true in state
        auto_mode = state.get("auto_mode", False)
        auto_approve = node_def.get("auto_approve", False)
        if auto_mode and auto_approve:
            _dag.pass_checkpoint(state, stage, ref)
            return pipeline_tick(state, blueprint)
        result = _dag.mark_checkpoint_done(state, stage, ref)
        return result

    # optional nodes: skip artifact validation
    if node_def and node_def.get("optional"):
        _hsm.mark_node_done(state, stage, ref)
        return pipeline_tick(state, blueprint)

    # Artifact validation for non-optional, non-checkpoint nodes
    missing = _check_artifacts(node_def, state, stage, ref)
    if missing:
        return missing

    _hsm.mark_node_done(state, stage, ref)
    return pipeline_tick(state, blueprint)


def node_fail(state, stage, ref, blueprint=None):
    """Mark node as failed and handle retry logic.

    After marking, auto-ticks the pipeline so that the engine
    discovers retry-ready nodes without requiring an explicit
    LLM pipeline_tick call.
    """
    node_def = _find_node_def(state, stage, ref, blueprint)
    max_retry = node_def.get("retry", 3) if node_def else 3
    count = _dag.record_retry(state, stage, ref)

    if count >= max_retry:
        _hsm.mark_node_failed(state, stage, ref)

        # Auto-rollback: retries exhausted triggers automatic HSM rollback.
        # The rollback target and event depend on the current stage.
        # act stage has no rollback transition — will fall through to retries_exhausted.
        rollback_map = {
            "plan":   ("plan", "design_flaw"),          # stay in plan, redo design
            "check":  ("do", "bug"),                    # rollback to do
        }

        if stage == "do":
            # Distinguish node types: doer nodes → bug (stay in do),
            # other nodes (review/merge/fix) → design_flaw_detected (back to plan)
            _nref = ref or ""
            if "doer" in _nref:
                target_stage, event = "do", "bug"
            else:
                target_stage, event = "plan", "design_flaw_detected"
        elif stage in rollback_map:
            target_stage, event = rollback_map[stage]
        else:
            # act or unknown stage: no rollback transition available
            tick = pipeline_tick(state, blueprint)
            return {"action": "retries_exhausted", "node": ref,
                    "stage": stage, "retries": count,
                    "max_retry": max_retry,
                    "desc": f"Node '{ref}' exhausted {max_retry} retries. "
                            f"No rollback transition for stage '{stage}'.",
                    "tick": tick}

        # Check rollback readiness (skip when target == current — HSM substate
        # rollback within the same stage, e.g. plan -> plan via design_flaw)
        if target_stage != stage:
            ok, reason = _rollback.check_rollback_readiness(state, target_stage)
            if not ok:
                tick = pipeline_tick(state, blueprint)
                return {"action": "retries_exhausted", "node": ref,
                        "stage": stage, "retries": count,
                        "max_retry": max_retry,
                        "rollback_error": reason,
                        "desc": f"Node '{ref}' exhausted {max_retry} retries. "
                                f"Rollback not possible: {reason}",
                        "tick": tick}

        # Execute rollback via HSM transition
        rollback_result = _rollback.rollback_to_stage(
            state, target_stage, event, blueprint)
        tick = pipeline_tick(state, blueprint)

        return {"action": "auto_rollback", "node": ref,
                "stage": stage, "retries": count,
                "max_retry": max_retry,
                "rollback_result": rollback_result,
                "desc": f"Node '{ref}' exhausted {max_retry} retries. "
                        f"Auto-rollback to '{target_stage}' via event '{event}'.",
                "tick": tick}

    _hsm.mark_node_failed(state, stage, ref)
    tick = pipeline_tick(state, blueprint)
    return {"action": "node_failed", "node": ref,
            "stage": stage, "retries": count,
            "max_retry": max_retry,
            "desc": f"Node '{ref}' failed ({count}/{max_retry} retries). "
                    f"Reset and retry.",
            "tick": tick}


def node_skip(state, stage, ref, blueprint=None):
    """Mark optional node as skipped."""
    _hsm.mark_node_skipped(state, stage, ref)
    return pipeline_tick(state, blueprint)


def _find_node_def(state, stage, ref, blueprint):
    """Find node definition in blueprint by ref."""
    bp = blueprint or _dag.load_blueprint()
    if not bp:
        return None
    nodes = _dag.get_stage_nodes(bp, stage)
    for n in nodes:
        if n["ref"] == ref:
            return n
    return None


# ── Artifact validation ─────────────────────────────────────────────

def _check_artifacts(node_def, state, stage, ref):
    """Validate that all artifacts declared in the node definition exist on disk.

    Checks each artifact pattern against two search directories:
      1. project_root (from state, or cwd)
      2. project_root/.fat/pdf/

    For glob patterns (containing * or ?), uses glob.glob() so that patterns
    like ``plan_review_*.md`` match any file. For literal paths, uses
    os.path.exists().

    Returns:
      - None if all artifacts pass, or if node_def is None / has no artifacts
      - dict with ``action: "artifact_missing"`` if any artifact is missing
    """
    if node_def is None:
        return None

    artifacts = node_def.get("artifacts", [])
    if not artifacts:
        return None

    project_root = state.get("project_root", os.getcwd())
    search_dirs = [
        project_root,
        os.path.join(project_root, ".fat", "pdf"),
    ]

    for pattern in artifacts:
        if not pattern:
            continue

        found = False
        has_glob = "*" in pattern or "?" in pattern

        for search_dir in search_dirs:
            if has_glob:
                full_pattern = os.path.join(search_dir, pattern)
                matches = glob.glob(full_pattern)
                if matches:
                    found = True
                    break
            else:
                full_path = os.path.join(search_dir, pattern)
                if os.path.exists(full_path):
                    found = True
                    break

        if not found:
            location_hint = f"{project_root} or .fat/pdf/"
            return {
                "action": "artifact_missing",
                "node": ref,
                "stage": stage,
                "artifact": pattern,
                "desc": f"Node '{ref}' artifact missing: '{pattern}' not found "
                        f"in {location_hint}. "
                        f"LLM must produce the declared artifact before marking done.",
            }

    return None


# ── Checkpoint handling ────────────────────────────────────────────

def pass_checkpoint(state, stage, ref, blueprint=None):
    """Accept checkpoint — unblock downstream, trigger re-tick."""
    result = _dag.pass_checkpoint(state, stage, ref)
    if result.get("action") == "checkpoint_passed":
        tick = pipeline_tick(state, blueprint)
        return {**result, "tick": tick}
    return result


def reject_checkpoint(state, stage, ref, blueprint=None):
    """Reject checkpoint — reset upstream, trigger re-tick."""
    bp = blueprint or _dag.load_blueprint()
    result = _dag.reject_checkpoint(state, stage, ref, bp)
    if result.get("action") == "checkpoint_rejected":
        tick = pipeline_tick(state, blueprint)
        return {**result, "tick": tick}
    return result


# ── Rollback wrappers ──────────────────────────────────────────────

def rollback_stage(state, target_stage, event):
    """Execute stage-level rollback via HSM transition."""
    return _rollback.rollback_to_stage(state, target_stage, event)


# ── Pipeline status ────────────────────────────────────────────────

def pipeline_status(state):
    """Full pipeline status."""
    bp = _dag.load_blueprint()
    stages = (bp or {}).get("stages", {})
    result = {
        "hsm": _hsm.hsm_status(state),
        "nodes": {},
    }
    for sname in stages:
        result["nodes"][sname] = _dag.stage_summary(state, sname)
    return result


def pipeline_summary(state):
    """One-line pipeline summary."""
    status = pipeline_status(state)
    hsm_info = status["hsm"]
    stage_info = status["nodes"].get(hsm_info.get("stage", ""), {})
    total = sum(stage_info.values())
    done = stage_info.get("done", 0) + stage_info.get("skipped", 0)
    return (f"[{hsm_info['path']}] {hsm_info['stage']} "
            f"{done}/{total} nodes done")
