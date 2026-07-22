#!/usr/bin/env python3
"""PDF Engine — deterministic PDCA state machine (strong engine).

All intelligence lives in the LLM (SKILL.md). This engine handles ONLY
deterministic operations:
  1. Blueprint topology enforcement (docs/topology/blueprint.yaml)
  2. HSM transition engine (stage events, rollback, loop limits)
  3. Pipeline scheduler (Make-style: find ready nodes by dep readiness)
  4. Checkpoint barrier (manual_checkpoint nodes block downstream)
  5. Retry tracking + upstream rollback for failure recovery
  6. Constraint verification (pre-transition integrity gates)
  7. Channel selection (lite/standard/full/analysis/planning) based on task attributes + factors
  8. Artifact lifecycle tracking
  9. State persistence (.fat/pdf/.pdf_state.json)
 10. Decision Engineering: precheck, scope, sanitize, ADR, design check, review
 11. Cycle-History adaptive model selection

Usage:
  pdf-engine.py init <task-slug> [...]
  pdf-engine.py state [key] | state set <k>=<v>
  pdf-engine.py channel select|set-override|clear-override
  pdf-engine.py config list-channels|get-channel|get-model|get-made
  pdf-engine.py plan|dag <subcommand> [...]
  pdf-engine.py session|workspace <subcommand> [...]
  pdf-engine.py artifact|agent <subcommand> [...]
  pdf-engine.py memory <subcommand> [...]
  pdf-engine.py archive <consolidate|list|inject|clean|context-inject>
  pdf-engine.py skip-audit <add|summary|clear>
  pdf-engine.py precheck|scope|sanitize|adr|decisions|design|review <...>
  pdf-engine.py resume-state|resume verify|resume summary
  pdf-engine.py checkpoint update --phase <name>
  pdf-engine.py context bundle build <stage> --role <role>
  pdf-engine.py knowledge|history <subcommand> [...]

  # Strong engine (v2)
  pdf-engine.py blueprint <load|list|validate>
  pdf-engine.py hsm <status|event|goto|unpause|reset-loops>
  pdf-engine.py pipeline <tick|status|summary|node-start|node-done|...>
  pdf-engine.py rollback <check|exec|cancel>
  pdf-engine.py constraint verify [stage] [phase]

  # Engine intelligence commands
  pdf-engine.py intel inject
  pdf-engine.py factor analyze
  pdf-engine.py meta review
  pdf-engine.py density-check
  pdf-engine.py converge
"""
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    yaml = None

from pdf_engine_shared import (
    PROJECT_ROOT, STATE_DIR, STATE_FILE, SKILL_DIR,
    _load_channel_rules,
    BASE_STATE, STAGE_ORDER, FACTOR_TAXONOMY,
    PHASE_ORDER, PHASE_NEXT_ACTION,
    _phase_index,
    _query_cycle_db,
    _load_state, _save_state, _require_state, _agent_status, _timestamp,
    _migrate_v1_to_v2, _load_state_with_migration,
    MemoryManager, MemoryBundle,
    PDFContext, SessionManager, generate_session_id,
    SESSIONS_DIR, WORKSPACE_FILE,
    _load_workspace_registry, _save_workspace_registry,
    _write_active_session,
)
from pdf_engine_deceng import (
    cmd_precheck_run, cmd_scope_classify, cmd_sanitize_run,
    cmd_adr_validate, cmd_decisions_merge, cmd_design_check, cmd_review_precheck,
)
from pdf_engine_knowledge import (
    cmd_knowledge_load_past_adrs, cmd_knowledge_match_historical,
    cmd_knowledge_resolve_precedent, cmd_knowledge_get_ts, cmd_knowledge_append_ts,
    cmd_knowledge_search, cmd_knowledge_reindex, cmd_knowledge_seed,
    cmd_knowledge_persist_factors, cmd_knowledge_scan_promotion,
    cmd_history_query, cmd_history_update,
)
from pdf_v5_project import ProjectStateManager
from pdf_v5_plan_dag import PlanManager, DagManager, StubManager, parse_tasks_from_plan

# Extracted command modules
from pdf_engine_session import (
    cmd_session_list, cmd_session_switch, cmd_session_current,
    cmd_session_delete, cmd_session_create,
    cmd_workspace_add, cmd_workspace_remove, cmd_workspace_list, cmd_workspace_switch,
)
from pdf_engine_state import (
    cmd_state, cmd_state_set,
    cmd_resume_state, cmd_checkpoint_update, cmd_resume_verify, cmd_resume_summary,
)
from pdf_engine_channel import (
    cmd_channel_select, cmd_channel_auto, cmd_channel_set_override, cmd_channel_clear_override,
    cmd_config_list_channels, cmd_config_get_channel, cmd_config_get_made,
    cmd_config_get_model, _compute_n_check, cmd_plan_made_allocate,
)
from pdf_engine_artifact import (
    cmd_artifact_add, cmd_artifact_list, cmd_artifact_get, cmd_artifact_compress,
    cmd_artifact_resolve_path,
    cmd_agent_pending, cmd_agent_status_list,
)
from pdf_engine_archive import (
    cmd_archive_route, cmd_skip_audit_route,
    cmd_archive_consolidate, cmd_archive_list, cmd_archive_inject,
    cmd_archive_clean, cmd_archive_context_inject,
    cmd_skip_audit_add, cmd_skip_audit_summary, cmd_skip_audit_clear,
    _check_skip_audit_warning,
)
from pdf_engine_memory import cmd_memory_update

# Strong engine modules (v2)
from engine import dag as _engine_dag
from engine import hsm as _engine_hsm
from engine import runner as _engine_runner
from engine import constraints as _engine_constraints
from engine import rollback as _engine_rollback


# === Commands ===

def cmd_init(task_slug):
    """Initialize state. Accepts --channel, --domain, --output-kind, --model-tier.
    Accepts --session-slug and --project-root for multi-session/project support."""
    raw_args = sys.argv
    force = "--force" in raw_args

    channel = ""
    blueprint = ""
    domain = ""
    output_kind = ""
    model_tier_overrides = {}
    session_slug = None
    project_root = os.getcwd()
    for i, a in enumerate(raw_args):
        if a == "--channel" and i + 1 < len(raw_args):
            channel = raw_args[i + 1]
        if a == "--blueprint" and i + 1 < len(raw_args):
            blueprint = raw_args[i + 1]
        if a == "--domain" and i + 1 < len(raw_args):
            domain = raw_args[i + 1]
        if a == "--output-kind" and i + 1 < len(raw_args):
            output_kind = raw_args[i + 1]
        if a == "--model-tier" and i + 1 < len(raw_args):
            pairs = raw_args[i + 1].split(",")
            for pair in pairs:
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k = k.strip()
                    if k.endswith("_model"):
                        k = k[:-6]
                    model_tier_overrides[k] = v.strip()
        if a == "--session-slug" and i + 1 < len(raw_args):
            session_slug = raw_args[i + 1]
        if a == "--project-root" and i + 1 < len(raw_args):
            project_root = raw_args[i + 1]

    # Always use session mode — every cycle gets an isolated session directory
    session_id = generate_session_id(task_slug, session_slug or task_slug)
    SessionManager.create_session(session_id, project_root, task_slug)
    ProjectStateManager.add_task(project_root, task_slug, task_slug)

    # Archive orphan root state if it belongs to a different session
    root_state_path = os.path.join(project_root, ".fat", "pdf", ".pdf_state.json")
    if os.path.exists(root_state_path):
        try:
            with open(root_state_path) as f:
                root_state = json.load(f)
            root_session_id = root_state.get("session_id") or root_state.get("multi_session", {}).get("session_id")
            if root_session_id and root_session_id != session_id:
                # Archive the old root state
                archive_name = f".pdf_state.{root_session_id}.json"
                archive_path = os.path.join(project_root, ".fat", "pdf", archive_name)
                os.rename(root_state_path, archive_path)
                print(f"  (archived orphan root state: {archive_name})")
        except (json.JSONDecodeError, IOError, OSError):
            pass  # if the root state is unreadable, leave it alone

    ctx = PDFContext(project_root=project_root, session_id=session_id)
    state_path = ctx.state_file
    state_dir = ctx.state_dir

    os.makedirs(state_dir, exist_ok=True)

    ProjectStateManager.ensure_init(project_root)

    if os.path.exists(state_path) and not force:
        existing = _load_state_with_migration(ctx=ctx)
        if existing and existing.get("stage") not in ("done", None):
            print(f"WARNING: Active session exists (stage={existing.get('stage')}, "
                  f"session={session_id}). Use --force to overwrite.", file=sys.stderr)
            return

    state = BASE_STATE.copy()
    state["task_slug"] = task_slug
    if channel:
        state["channel"] = channel
    if blueprint:
        state["blueprint"] = blueprint
    if domain:
        state["domain"] = domain
    if output_kind:
        state["output_kind"] = output_kind
    if model_tier_overrides:
        state.setdefault("model_tier", {})["overrides"] = model_tier_overrides
        state["model_tier"]["override_reason"] = "CLI --model-tier"
    state["session_id"] = session_id
    if session_slug:
        state["session_slug"] = session_slug
    state["project_root"] = project_root
    state.setdefault("multi_session", {
        "version": 1,
        "session_id": session_id,
        "project_id": os.path.basename(project_root),
        "project_root": project_root,
        "session_created_at": _timestamp(),
        "session_updated_at": _timestamp(),
    })
    for s in ["plan", "do", "check", "act"]:
        state["stages"][s]["agents"] = {}
        state["stages"][s]["reviewers"] = {}

    # Seed HSM fields so hsm_path is persisted (not a read-time default)
    _engine_hsm._init_hsm_state(state)

    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            state["git_head"] = result.stdout.strip()
    except Exception:
        pass

    _save_state(state, ctx=ctx)
    _write_active_session(session_id)
    mt_str = f" model_tier_overrides={model_tier_overrides}" if model_tier_overrides else ""

    try:
        bundle = MemoryManager.load_all(project_root)
        state["user_memory"] = {
            "loaded": bundle.total() > 0,
            "summary": bundle.summary(),
            "entries_count": bundle.total(),
        }
        if bundle.total() > 0:
            print(f"  user_memory: loaded {bundle.summary()}")
        _save_state(state, ctx=ctx)
    except Exception:
        pass

    no_seed = "--no-seed" in raw_args
    if not no_seed:
        try:
            from pdf_engine_shared import _seed_history_data
            n_seeded = _seed_history_data(state, project_root)
            if n_seeded > 0:
                _save_state(state, ctx=ctx)
        except Exception:
            pass

    seed_history_json = ""
    for i, a in enumerate(raw_args):
        if a == "--seed-history" and i + 1 < len(raw_args):
            seed_history_json = raw_args[i + 1]
            break
    if seed_history_json:
        try:
            seed_data = json.loads(seed_history_json)
            if isinstance(seed_data, dict):
                state["history_recommendation"] = seed_data
                state["history_seed_source"] = "cli"
                state["_history_seed_info"] = {"source": "cli", "generated_at": _timestamp(), "n_entries": len(seed_data)}
                print(f"  seed-history: injected {len(seed_data)} entries")
                _save_state(state, ctx=ctx)
        except (json.JSONDecodeError, TypeError) as e:
            print(f"ERROR: --seed-history JSON parse failed: {e}. Expected format: '{{\"do.p1\":\"sonnet\",\"do.p2\":\"opus\"}}'", file=sys.stderr)

    print(f"OK: initialized PDF state for '{task_slug}' "
          f"channel={channel or 'auto'} domain={domain or '?'} output_kind={output_kind or '?'}{mt_str}")
    print(f"  Session: {session_id} at {ctx.state_file}")
    PDFContext.set_default(ctx)


# === Decisions Diff ===

def cmd_decisions_diff(f1, f2):
    """Diff two designer decisions YAML from markdown."""
    result1, msg1 = _extract_decisions(f1)
    result2, msg2 = _extract_decisions(f2)

    for fname, result, msg in [(f1, result1, msg1), (f2, result2, msg2)]:
        if msg != "OK":
            print(f"  {fname}: {msg}")

    if msg1 != "OK" or msg2 != "OK":
        return

    keys1, keys2 = result1, result2
    only1 = keys1 - keys2
    only2 = keys2 - keys1
    common = keys1 & keys2

    print(f"decisions_diff: {f1} vs {f2}")
    print(f"  common={len(common)} unique_1={len(only1)} unique_2={len(only2)}")

    if only1:
        for k in sorted(only1):
            print(f"  only_in_1: {k}")
    if only2:
        for k in sorted(only2):
            print(f"  only_in_2: {k}")
    if not only1 and not only2:
        print("  identical (no decision key differences)")


def _extract_decisions(filepath):
    """Extract decision keys from ## Decisions YAML block in markdown."""
    if not os.path.exists(filepath):
        return set(), "FILE_NOT_FOUND"
    try:
        with open(filepath) as f:
            content = f.read()
    except IOError:
        return set(), "FILE_NOT_READABLE"

    match = re.search(r'##\s+Decisions\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
    if not match:
        return set(), "NO_DECISIONS_BLOCK"

    try:
        import yaml
        decisions_yaml = yaml.safe_load(match.group(1))
    except ImportError:
        keys = set()
        for line in match.group(1).split("\n"):
            line = line.strip()
            if line.startswith("- ") and ":" in line:
                key = line.split(":", 1)[0].lstrip("- ").strip()
                if key:
                    keys.add(key)
        return keys, "OK"
    except yaml.YAMLError:
        return set(), "YAML_PARSE_ERROR"

    if not decisions_yaml or "decisions" not in decisions_yaml:
        return set(), "NO_DECISIONS_KEY"

    keys = set()
    for d in decisions_yaml["decisions"]:
        if isinstance(d, dict) and "key" in d:
            keys.add(d["key"])
    return keys, "OK"


# === Context Bundle ===

def cmd_context_bundle_build(stage, role_name):
    """Build context bundle YAML from current state and artifacts."""
    state = _load_state_with_migration()
    if state is None:
        print("WARN: no state available, context bundle empty")
        return

    ctx = PDFContext.get_default()
    pdf_dir = ctx.state_dir

    bundle = {
        "context_bundle": {
            "version": 1,
            "for_role": role_name,
            "at_stage": stage,
            "task": state.get("task_description", "")[:2000],
            "plan_key_decisions": [],
            "made_findings_summary": "",
            "triggered_factors": state.get("triggered_factors", []),
            "active_domain": state.get("domain", ""),
            "mde_state": {
                "N": state.get("stages", {}).get(stage, {}).get("N", 1),
                "M": state.get("stages", {}).get(stage, {}).get("M", 1),
                "channel": state.get("channel", ""),
                "stage": state.get("stage", ""),
            },
        }
    }

    decisions_path = os.path.join(pdf_dir, "plan_decisions.yaml")
    if os.path.exists(decisions_path):
        try:
            with open(decisions_path) as f:
                decisions_data = yaml.safe_load(f) if yaml else None
            if decisions_data and isinstance(decisions_data, dict):
                decisions = decisions_data.get("decisions", [])
                bundle["context_bundle"]["plan_key_decisions"] = [
                    {"id": d.get("id"), "decision": d.get("decision"),
                     "rationale": (d.get("rationale") or "")[:500]}
                    for d in decisions[:20] if isinstance(d, dict)
                ]
        except Exception:
            pass

    made_path = os.path.join(pdf_dir, "made", "synthesized_findings.md")
    if os.path.exists(made_path):
        try:
            with open(made_path) as f:
                content = f.read()
            bundle["context_bundle"]["made_findings_summary"] = content[:2000]
        except Exception:
            pass

    n_files = state.get("scope_files_count", 1) or 1
    base_max = 12000
    per_file_bonus = 2000
    hard_max = 24000
    dynamic_max = min(base_max + max(0, n_files - 1) * per_file_bonus, hard_max)
    bundle["context_bundle"]["_capacity"] = dynamic_max

    skip_warning = _check_skip_audit_warning()
    if skip_warning:
        bundle["context_bundle"]["_skip_audit_warning"] = skip_warning

    if yaml:
        print(yaml.dump(bundle, default_flow_style=False, allow_unicode=True))
    else:
        print(json.dumps(bundle, ensure_ascii=False))


# === ADR Validate Act ===

def cmd_adr_validate_act(filepath):
    """Validate act_report.md frontmatter schema."""
    from pdf_engine_shared import _parse_frontmatter, _validate_act_frontmatter
    if not os.path.exists(filepath):
        print(json.dumps({"valid": False, "errors": [f"file not found: {filepath}"], "warnings": []}))
        return
    try:
        with open(filepath) as f:
            text = f.read()
    except IOError as e:
        print(json.dumps({"valid": False, "errors": [f"read error: {e}"], "warnings": []}))
        return

    data, msg = _parse_frontmatter(text)
    if data is None:
        body_warnings = [f"no_frontmatter: {msg} — using best-effort body analysis"]
        print(json.dumps({"valid": True, "errors": [], "warnings": body_warnings, "fallback": True}))
        return

    result = _validate_act_frontmatter(data)
    print(json.dumps(result, ensure_ascii=False))


# ── Strong engine command handlers (v2) ────────────────────────────

def cmd_blueprint_load(name="default"):
    """Load and display a blueprint."""
    data = _engine_dag.load_blueprint(name)
    if data is None:
        print(json.dumps({"error": f"blueprint '{name}' not found"}))
        return
    errors = _engine_dag.validate_blueprint(data)
    print(json.dumps({
        "name": name,
        "valid": len(errors) == 0,
        "errors": errors,
        "stages": list(data.get("stages", {}).keys()),
    }, ensure_ascii=False))


def cmd_blueprint_list():
    """List available blueprints."""
    bps = _engine_dag.list_blueprints()
    print(json.dumps(bps, ensure_ascii=False, indent=2))


def cmd_blueprint_validate(name="default"):
    """Validate a blueprint."""
    data = _engine_dag.load_blueprint(name)
    if data is None:
        print(json.dumps({"valid": False, "errors": [f"blueprint '{name}' not found"]}))
        return
    errors = _engine_dag.validate_blueprint(data)
    print(json.dumps({"valid": len(errors) == 0, "errors": errors}, ensure_ascii=False))


def cmd_hsm_status():
    """Display HSM status."""
    state = _load_state_with_migration()
    status = _engine_hsm.hsm_status(state)
    print(json.dumps(status, ensure_ascii=False, indent=2))


def cmd_hsm_event(event):
    """Fire an HSM transition event."""
    state = _load_state_with_migration()
    result = _engine_hsm.fire_event(state, event)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_hsm_goto(path_str):
    """Force-set HSM path (debug only)."""
    state = _load_state_with_migration()
    result = _engine_hsm.hsm_goto(state, path_str)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_hsm_unpause():
    """Clear paused state."""
    state = _load_state_with_migration()
    result = _engine_hsm.hsm_unpause(state)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_hsm_reset_loops(event=None):
    """Reset loop counters."""
    state = _load_state_with_migration()
    result = _engine_hsm.hsm_reset_loops(state, event)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def _resolve_blueprint_name(state, hint="default"):
    """Auto-resolve blueprint name from state.blueprint (set by channel select).
    Falls back to hint, then 'default'."""
    if hint != "default":
        return hint
    bp = state.get("blueprint") if state else None
    if bp:
        return bp
    channel = state.get("channel") if state else None
    if channel:
        return channel  # lite/standard/full → index.yaml maps them
    return "default"


def cmd_pipeline_tick(blueprint_name="default"):
    """Make-style pipeline tick — find ready nodes."""
    state = _load_state_with_migration()
    blueprint_name = _resolve_blueprint_name(state, blueprint_name)
    bp = _engine_dag.load_blueprint(blueprint_name)
    _engine_dag._init_dag_progress(state, bp)
    result = _engine_runner.pipeline_tick(state, bp)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pipeline_status():
    """Full pipeline status."""
    state = _load_state_with_migration()
    status = _engine_runner.pipeline_status(state)
    print(json.dumps(status, ensure_ascii=False, indent=2))


def cmd_pipeline_summary():
    """One-line pipeline summary."""
    state = _load_state_with_migration()
    print(_engine_runner.pipeline_summary(state))


def cmd_pipeline_node_start(stage, ref):
    """Mark a node as running with admission validation."""
    state = _load_state_with_migration()
    result = _engine_runner.node_start(state, stage, ref)
    if result.get("action") == "rejected":
        print(json.dumps(result, ensure_ascii=False))
        return
    # action == "started" — persist the state change
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_pipeline_node_done(stage, ref, blueprint_name="default"):
    """Mark a node as done. Triggers re-tick if stage completes."""
    state = _load_state_with_migration()
    blueprint_name = _resolve_blueprint_name(state, blueprint_name)
    bp = _engine_dag.load_blueprint(blueprint_name)
    result = _engine_runner.node_complete(state, stage, ref, bp)
    if result.get("action") != "artifact_missing":
        _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_pipeline_node_fail(stage, ref, blueprint_name="default"):
    """Mark a node as failed. Handles retry logic."""
    state = _load_state_with_migration()
    blueprint_name = _resolve_blueprint_name(state, blueprint_name)
    bp = _engine_dag.load_blueprint(blueprint_name)
    result = _engine_runner.node_fail(state, stage, ref, bp)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_pipeline_node_skip(stage, ref, blueprint_name="default"):
    """Mark a node as skipped."""
    state = _load_state_with_migration()
    blueprint_name = _resolve_blueprint_name(state, blueprint_name)
    bp = _engine_dag.load_blueprint(blueprint_name)
    result = _engine_runner.node_skip(state, stage, ref, bp)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_pipeline_pass_checkpoint(stage, ref, blueprint_name="default"):
    """Accept a checkpoint — unblock downstream."""
    state = _load_state_with_migration()
    blueprint_name = _resolve_blueprint_name(state, blueprint_name)
    bp = _engine_dag.load_blueprint(blueprint_name)
    result = _engine_runner.pass_checkpoint(state, stage, ref, bp)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_pipeline_reject_checkpoint(stage, ref, blueprint_name="default"):
    """Reject a checkpoint — reset upstream."""
    state = _load_state_with_migration()
    blueprint_name = _resolve_blueprint_name(state, blueprint_name)
    bp = _engine_dag.load_blueprint(blueprint_name)
    result = _engine_runner.reject_checkpoint(state, stage, ref, bp)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_rollback_check():
    """Check if rollback to target stage is safe."""
    state = _load_state_with_migration()
    dirty = _engine_rollback.list_dirty(state)
    print(json.dumps({
        "current_stage": state.get("stage"),
        "dirty_nodes": dirty,
    }, ensure_ascii=False, indent=2))


def cmd_rollback_exec(target_stage, event):
    """Execute stage-level rollback."""
    state = _load_state_with_migration()
    ok, reason = _engine_rollback.check_rollback_readiness(state, target_stage)
    if not ok:
        print(json.dumps({"action": "error", "reason": reason}, ensure_ascii=False))
        return
    result = _engine_rollback.rollback_to_stage(state, target_stage, event)
    _save_state(state)
    print(json.dumps(result, ensure_ascii=False))


def cmd_rollback_cancel():
    """Cancel pending rollback — clear dirty markers."""
    state = _load_state_with_migration()
    dirty = _engine_rollback.list_dirty(state)
    for d in dirty:
        _engine_rollback.mark_clean(state, d["stage"], d["ref"])
    _save_state(state)
    print(json.dumps({"action": "rollback_cancelled", "cleared": len(dirty)}, ensure_ascii=False))


def cmd_constraint_verify(stage="", phase=""):
    """Verify constraints for a stage/phase."""
    state = _load_state_with_migration()
    if state is None:
        state = {}
    all_pass, violations, blocking = _engine_constraints.verify_constraints(
        stage=stage or None, phase=phase or None, state=state)
    escalated = _engine_constraints.escalate_constraints(violations, state)
    _save_state(state)
    print(json.dumps({
        "all_pass": all_pass,
        "violations": violations,
        "blocking": blocking,
        "escalated": escalated,
    }, ensure_ascii=False, indent=2))


# === Intel Injection ===

def cmd_intel_inject():
    """Inject WebSearch intelligence into working context."""
    state = _load_state_with_migration()
    if state is None:
        print("WARNING: no state available, cannot inject intel", file=sys.stderr)
        return

    ctx = PDFContext.get_default()
    pdf_dir = ctx.state_dir
    intel_path = os.path.join(pdf_dir, "intel_injection.md")

    if os.path.exists(intel_path):
        print(f"intel_injection: already present at {intel_path}")
        return

    task_slug = state.get("task_slug", "")
    domain = state.get("domain", "")
    task_desc = state.get("task_description", "")

    topic = task_slug
    if domain:
        topic += f" domain={domain}"
    if task_desc:
        short_desc = task_desc[:200].replace("\n", " ")
        topic += f" -- {short_desc}"

    content = (
        "# Intel Injection\n"
        "\n"
        f"Auto-generated by pdf-engine.py intel inject at {_timestamp()}\n"
        "\n"
        "## Search Topic\n"
        f"{topic}\n"
        "\n"
        "## Status\n"
        "This is a placeholder. The LLM should execute a WebSearch for the topic above\n"
        "and populate findings below.\n"
        "\n"
        "## Findings\n"
        "*No intelligence injected yet.*\n"
        "\n"
        "---\n"
        "*Generated by pdf-engine.py intel inject*\n"
    )
    os.makedirs(pdf_dir, exist_ok=True)
    with open(intel_path, "w") as f:
        f.write(content)

    print(f"intel_injection: wrote placeholder to {intel_path}")
    print(f"  topic: {topic}")


# === Factor Analysis ===

def cmd_factor_analyze():
    """Built-in factor matching against FACTOR_TAXONOMY (6 factors)."""
    state = _load_state_with_migration()
    if state is None:
        print("WARNING: no state available, cannot analyze factors", file=sys.stderr)
        return

    task_text = state.get("task_description", "")
    if not task_text:
        input_artifact = state.get("input_artifact", "")
        if input_artifact and os.path.exists(input_artifact):
            try:
                with open(input_artifact) as f:
                    task_text = f.read()
            except IOError:
                pass

    if not task_text:
        task_text = state.get("task_slug", "")

    task_text_lower = task_text.lower()

    matched = {}
    for factor_key, factor_info in FACTOR_TAXONOMY.items():
        triggers = factor_info.get("triggers", [])
        found = [kw for kw in triggers if kw in task_text_lower]
        if found:
            matched[factor_key] = {
                "matched_keywords": found,
                "add_dimensions": factor_info.get("add_dimensions", []),
                "force_channel": factor_info.get("force_channel", None),
            }

    ctx = PDFContext.get_default()
    pdf_dir = ctx.state_dir
    factor_path = os.path.join(pdf_dir, "factor_analysis.md")

    lines = [
        "# Factor Analysis",
        "",
        f"Generated by pdf-engine.py factor analyze at {_timestamp()}",
        "",
    ]
    if matched:
        lines.append(f"## Matched Factors ({len(matched)})")
        lines.append("")
        for fk, fv in matched.items():
            lines.append(f"### {fk}")
            lines.append(f"  - matched_keywords: {', '.join(fv['matched_keywords'])}")
            lines.append(f"  - add_dimensions: {', '.join(fv['add_dimensions'])}")
            if fv["force_channel"]:
                lines.append(f"  - force_channel: {fv['force_channel']}")
            lines.append("")
    else:
        lines.append("## No factors matched")
        lines.append("")

    lines.append("---\n*Generated by pdf-engine.py factor analyze*")

    os.makedirs(pdf_dir, exist_ok=True)
    with open(factor_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"factor_analyze: wrote {len(matched)} matched factors to {factor_path}")
    if matched:
        for fk, fv in matched.items():
            print(f"  factor={fk} triggers={fv['matched_keywords']} "
                  f"dims={fv['add_dimensions']} force_channel={fv['force_channel'] or '-'}")

    # Persist triggered_factors in state
    state.setdefault("triggered_factors", [])
    for f in matched:
        if f not in state["triggered_factors"]:
            state["triggered_factors"].append(f)
    _save_state(state)


# === Meta Review ===

def cmd_meta_review():
    """Framework bias audit -- scan plan_decisions.yaml for bias indicators."""
    plan_decisions_path = "plan_decisions.yaml"

    if not os.path.exists(plan_decisions_path):
        print(f"meta_review: plan_decisions.yaml not found at {plan_decisions_path}, skipping", file=sys.stderr)
        return

    try:
        with open(plan_decisions_path) as f:
            content = f.read()
    except IOError as e:
        print(f"meta_review: cannot read {plan_decisions_path}: {e}", file=sys.stderr)
        return

    minimizing_words = {
        "obviously": 0, "clearly": 0, "trivially": 0,
        "just": 0, "simply": 0, "easy": 0, "of course": 0,
    }
    content_lower = content.lower()
    for word in minimizing_words:
        minimizing_words[word] = content_lower.count(word)

    total_minimizing = sum(minimizing_words.values())

    has_tradeoffs = bool(re.search(r'trade.?off', content_lower))
    has_alternatives = bool(re.search(r'alternatives?', content_lower))
    has_pros_cons = bool(re.search(r'pros?\s*(and|/|&)\s*cons?', content_lower))
    has_risks = bool(re.search(r'risk', content_lower))

    decisions_count = len(re.findall(r'^\s*-\s+\w+:', content, re.MULTILINE))

    ctx = PDFContext.get_default()
    pdf_dir = ctx.state_dir
    meta_path = os.path.join(pdf_dir, "meta_review.md")

    lines = [
        "# Meta Review -- Framework Bias Audit",
        "",
        f"Generated by pdf-engine.py meta review at {_timestamp()}",
        f"Source: {plan_decisions_path}",
        "",
        "## Minimizing Language Scan",
        "",
        f"Total instances of minimizing/dismissive language: {total_minimizing}",
        "",
    ]
    for word, count in minimizing_words.items():
        if count > 0:
            lines.append(f"  '{word}': {count} occurrences")
    if total_minimizing == 0:
        lines.append("  (none detected)")
    lines.append("")

    lines.extend([
        "## Trade-off Coverage",
        "",
        f"  Trade-off discussion: {'YES' if has_tradeoffs else 'NO'}",
        f"  Alternatives discussed: {'YES' if has_alternatives else 'NO'}",
        f"  Pros/cons analysis: {'YES' if has_pros_cons else 'NO'}",
        f"  Risk awareness: {'YES' if has_risks else 'NO'}",
        "",
    ])

    missing = []
    if not has_tradeoffs:
        missing.append("trade-off discussion")
    if not has_alternatives:
        missing.append("alternatives consideration")
    if not has_pros_cons:
        missing.append("pros/cons analysis")
    if not has_risks:
        missing.append("risk awareness")

    if missing:
        lines.append("### Missing Elements")
        for m in missing:
            lines.append(f"  - {m}")
    else:
        lines.append("### All trade-off elements present")
    lines.append("")

    lines.append(f"## Decisions Count: {decisions_count}")
    lines.append("")

    bias_score = total_minimizing + len(missing)
    if bias_score == 0:
        verdict = "CLEAN -- no bias indicators detected"
    elif bias_score <= 3:
        verdict = f"LOW BIAS (score={bias_score}) -- minor concerns, review recommended"
    elif bias_score <= 6:
        verdict = f"MODERATE BIAS (score={bias_score}) -- significant bias indicators, consider revision"
    else:
        verdict = f"HIGH BIAS (score={bias_score}) -- strong bias indicators, revision strongly recommended"

    lines.append(f"## Verdict: {verdict}")
    lines.append("")
    lines.append("---\n*Generated by pdf-engine.py meta review*")

    os.makedirs(pdf_dir, exist_ok=True)
    with open(meta_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"meta_review: wrote report to {meta_path}")
    print(f"  minimizing_language: {total_minimizing} instances")
    print(f"  trade_offs: {'present' if has_tradeoffs else 'MISSING'}")
    print(f"  decisions: {decisions_count}")
    print(f"  verdict: {verdict}")


# === Density Check ===

def cmd_density_check():
    """Information density check on plan.md."""
    plan_path = "plan.md"

    if not os.path.exists(plan_path):
        print(f"density_check: plan.md not found at {plan_path}, skipping", file=sys.stderr)
        return

    try:
        with open(plan_path) as f:
            content = f.read()
    except IOError as e:
        print(f"density_check: cannot read {plan_path}: {e}", file=sys.stderr)
        return

    total_words = len(content.split())
    sections = re.findall(r'^#{1,3}\s+(.+)$', content, re.MULTILINE)
    num_sections = len(sections)

    action_items = len(re.findall(r'^\s*-\s*\[[ x]\]', content, re.MULTILINE))
    decisions = len(re.findall(r'\bdecision[s]?\b', content, re.I))
    code_blocks = len(re.findall(r'```', content)) // 2

    words_per_section = total_words / max(num_sections, 1)
    action_ratio = action_items / max(total_words, 1) * 100 if total_words > 0 else 0

    ctx = PDFContext.get_default()
    pdf_dir = ctx.state_dir
    density_path = os.path.join(pdf_dir, "density_report.md")

    lines = [
        "# Density Report -- Information Density Check",
        "",
        f"Generated by pdf-engine.py density-check at {_timestamp()}",
        f"Source: {plan_path}",
        "",
        "## Raw Metrics",
        "",
        f"  Total words: {total_words}",
        f"  Total sections: {num_sections}",
        f"  Action items: {action_items}",
        f"  Decision references: {decisions}",
        f"  Code blocks: {code_blocks}",
        "",
        "## Derived Metrics",
        "",
        f"  Words per section: {words_per_section:.1f}",
        f"  Action item ratio: {action_ratio:.1f}%",
        "",
        "## Sections",
        "",
    ]
    for s in sections:
        lines.append(f"  - {s.strip()}")
    lines.append("")

    if words_per_section < 50:
        density_verdict = "LOW DENSITY -- sections are very short, may lack detail"
    elif words_per_section < 150:
        density_verdict = "MODERATE DENSITY -- adequate, consider adding more detail"
    else:
        density_verdict = "GOOD DENSITY -- sections are appropriately detailed"

    if action_ratio < 1:
        action_verdict = "LOW ACTIONABILITY -- few explicit action items"
    else:
        action_verdict = "GOOD ACTIONABILITY -- adequate action item coverage"

    lines.append(f"## Density Verdict: {density_verdict}")
    lines.append(f"## Actionability Verdict: {action_verdict}")
    lines.append("")
    lines.append("---\n*Generated by pdf-engine.py density-check*")

    os.makedirs(pdf_dir, exist_ok=True)
    with open(density_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"density_check: wrote report to {density_path}")
    print(f"  words={total_words} sections={num_sections} action_items={action_items} decisions={decisions}")
    print(f"  wps={words_per_section:.1f} action_ratio={action_ratio:.1f}%")
    print(f"  density: {density_verdict}")
    print(f"  actionability: {action_verdict}")


# === Converge ===

def cmd_converge():
    """Mechanical merge of all do_output_*.md files -> merged.md."""
    files = sorted(glob.glob("do_output_*.md"))

    if not files:
        print("converge: no do_output_*.md files found", file=sys.stderr)
        return

    sections = []
    for fpath in files:
        try:
            with open(fpath) as f:
                sections.append(f.read())
        except IOError as e:
            print(f"converge: warning -- cannot read {fpath}: {e}", file=sys.stderr)
            continue

    merged = "\n\n".join(sections)

    with open("merged.md", "w") as f:
        f.write(merged)

    print(f"converge: converged {len(sections)} sections -> merged.md")
    for fpath in files:
        print(f"  + {fpath}")


# === Main ===

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    session_flag = None
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--session" and i + 1 < len(sys.argv):
            session_flag = sys.argv[i + 1]
            break
    if session_flag:
        state_path = SessionManager.state_file(session_flag)
        if os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    state = json.load(f)
                stored_root = state.get("project_root", os.getcwd())
                ctx = PDFContext(project_root=stored_root, session_id=session_flag)
                PDFContext.set_default(ctx)
            except Exception:
                pass

    cmd = sys.argv[1]

    if cmd == "init":
        if len(sys.argv) < 3:
            print("ERROR: usage: pdf-engine.py init <task-slug> [--blueprint full|standard|lite|analysis|planning] "
                  "[--channel lite|standard|full|analysis|planning] [--domain <d>] [--output-kind <k>] "
                  "[--model-tier k=v,...] [--session-slug <slug>] [--project-root <path>] "
                  "[--seed-history <json>] [--no-seed] [--force]", file=sys.stderr)
            return
        cmd_init(sys.argv[2])

    elif cmd == "state":
        if len(sys.argv) >= 4 and sys.argv[2] == "set":
            cmd_state_set(sys.argv[3])
        else:
            key = sys.argv[2] if len(sys.argv) >= 3 else None
            cmd_state(key)

    elif cmd == "channel":
        if len(sys.argv) >= 3 and sys.argv[2] == "auto":
            cmd_channel_auto()
        elif len(sys.argv) >= 4 and sys.argv[2] == "select":
            cmd_channel_select(sys.argv[3])
        elif len(sys.argv) >= 4 and sys.argv[2] == "set-override":
            channel = sys.argv[3]
            reason = sys.argv[4] if len(sys.argv) >= 5 else ""
            cmd_channel_set_override(channel, reason)
        elif len(sys.argv) >= 3 and sys.argv[2] == "clear-override":
            cmd_channel_clear_override()
        else:
            print("ERROR: usage: pdf-engine.py channel auto|select '<json>'|set-override <channel> [reason]|clear-override", file=sys.stderr)

    elif cmd == "artifact":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "add":
            move = False
            third = sys.argv[3] if len(sys.argv) >= 4 else ""
            if third == "--move":
                move = True
                path = sys.argv[4] if len(sys.argv) >= 5 else ""
                role = sys.argv[5] if len(sys.argv) >= 6 else ""
            else:
                path = third
                role = sys.argv[4] if len(sys.argv) >= 5 else ""
            cmd_artifact_add(path, role, move=move)
        elif sub == "list":
            cmd_artifact_list()
        elif sub == "get":
            cmd_artifact_get(sys.argv[3] if len(sys.argv) >= 4 else "")
        elif sub == "compress":
            cmd_artifact_compress()
        elif sub == "resolve-path":
            filename = sys.argv[3] if len(sys.argv) >= 4 else ""
            cmd_artifact_resolve_path(filename)
        else:
            print("ERROR: usage: pdf-engine.py artifact <add|list|get|compress|resolve-path> [...]", file=sys.stderr)

    elif cmd == "agent":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "pending":
            cmd_agent_pending()
        elif sub == "status":
            cmd_agent_status_list()
        else:
            print("ERROR: usage: pdf-engine.py agent <pending|status> [...]", file=sys.stderr)

    elif cmd == "memory":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        project_root = os.getcwd()
        for i, a in enumerate(sys.argv):
            if a == "--project" and i + 1 < len(sys.argv):
                project_root = sys.argv[i + 1]
        if sub == "inspect":
            print(MemoryManager.inspect(project_root))
        elif sub == "list":
            print(MemoryManager.list(project_root))
        elif sub == "inject":
            print(MemoryManager.inject(project_root))
        elif sub == "reload":
            bundle = MemoryManager.reload(project_root)
            print(f"memory reloaded: {bundle.total()} entries")
        elif sub == "clear-cache":
            MemoryManager.clear_cache()
            print("memory cache cleared")
        elif sub == "ignore" and len(sys.argv) >= 4:
            ignore_sub = sys.argv[3]
            key = sys.argv[4] if len(sys.argv) >= 5 else ""
            if ignore_sub == "add":
                print(MemoryManager.ignore_add(project_root, key))
            elif ignore_sub == "remove":
                print(MemoryManager.ignore_remove(project_root, key))
            elif ignore_sub == "list":
                print(MemoryManager.ignore_list(project_root))
            else:
                print("ERROR: usage: pdf-engine.py memory ignore <add|remove|list> [key]", file=sys.stderr)
        elif sub == "update" and len(sys.argv) >= 5:
            key = sys.argv[3]
            value = sys.argv[4]
            cmd_memory_update(key, value)
        else:
            print("ERROR: usage: pdf-engine.py memory <inspect|list|inject|reload|clear-cache|update <key> <value>|ignore <add|remove|list>> [--project <path>] [--yes] [--dry-run]", file=sys.stderr)

    elif cmd == "decisions-diff":
        f1 = sys.argv[2] if len(sys.argv) >= 3 else ""
        f2 = sys.argv[3] if len(sys.argv) >= 4 else ""
        cmd_decisions_diff(f1, f2)

    elif cmd == "precheck":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "run" and len(sys.argv) >= 4:
            cmd_precheck_run(sys.argv[3])
        else:
            print("ERROR: usage: pdf-engine.py precheck run <task_text> [--domain <d>] [--task-type <t>]", file=sys.stderr)

    elif cmd == "scope":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "classify" and len(sys.argv) >= 4:
            cmd_scope_classify(sys.argv[3])
        else:
            print("ERROR: usage: pdf-engine.py scope classify <task_text>", file=sys.stderr)

    elif cmd == "sanitize":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "run" and len(sys.argv) >= 4:
            cmd_sanitize_run(sys.argv[3])
        else:
            print("ERROR: usage: pdf-engine.py sanitize run <task_text>", file=sys.stderr)

    elif cmd == "adr":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "validate" and len(sys.argv) >= 4:
            cmd_adr_validate(sys.argv[3])
        elif sub == "validate-act" and len(sys.argv) >= 4:
            cmd_adr_validate_act(sys.argv[3])
        else:
            print("ERROR: usage: pdf-engine.py adr <validate <filepath>|validate-act <filepath>>", file=sys.stderr)

    elif cmd == "decisions":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "merge" and len(sys.argv) >= 5:
            cmd_decisions_merge(sys.argv[3], sys.argv[4])
        else:
            print("ERROR: usage: pdf-engine.py decisions merge <file1> <file2>", file=sys.stderr)

    elif cmd == "design":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "check" and len(sys.argv) >= 4:
            cmd_design_check(sys.argv[3])
        else:
            print("ERROR: usage: pdf-engine.py design check <filepath> [--domain <d>]", file=sys.stderr)

    elif cmd == "review":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "precheck" and len(sys.argv) >= 4:
            cmd_review_precheck(sys.argv[3])
        else:
            print("ERROR: usage: pdf-engine.py review precheck <filepath> --dimension <dimension>", file=sys.stderr)

    elif cmd == "plan":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        project_root = os.getcwd()
        mgr = PlanManager()
        if sub == "made-allocate":
            cmd_plan_made_allocate()
        elif sub == "append":
            slug = sys.argv[3] if len(sys.argv) >= 4 else ""
            desc = sys.argv[4] if len(sys.argv) >= 5 else ""
            no_overlap = "--no-detect-overlap" in sys.argv
            print(mgr.plan_append(project_root, slug, desc, no_overlap))
        elif sub == "list" and len(sys.argv) >= 4:
            print(mgr.plan_list(project_root, sys.argv[3]))
        elif sub == "current" and len(sys.argv) >= 4:
            print(mgr.plan_current(project_root, sys.argv[3]))
        elif sub == "diff" and len(sys.argv) >= 6:
            v1 = int(sys.argv[4]) if sys.argv[3] == "--v1" else 0
            v2 = int(sys.argv[5]) if sys.argv[4] == "--v2" else 0
            print(mgr.plan_diff(project_root, sys.argv[2], v1, v2))
        elif sub == "history":
            print(mgr.plan_history(project_root))
        elif sub == "detect-overlap" and len(sys.argv) >= 4:
            print(mgr.detect_overlap(project_root, sys.argv[3], sys.argv[4] if len(sys.argv) >= 5 else ""))
        else:
            print("ERROR: usage: pdf-engine.py plan <made-allocate|append|list <slug>|current <slug>|diff --v1 N --v2 M|history|detect-overlap>", file=sys.stderr)

    elif cmd == "dag":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        dm = DagManager()
        if sub == "build" and len(sys.argv) >= 4:
            with open(sys.argv[3]) as f:
                print(dm.dag_build(f.read()))
        elif sub == "status":
            task_states = json.loads(sys.argv[3]) if len(sys.argv) >= 4 else {}
            stub_states = None
            for i, a in enumerate(sys.argv):
                if a == "--stub-states" and i + 1 < len(sys.argv):
                    try:
                        stub_states = json.loads(sys.argv[i + 1])
                    except (json.JSONDecodeError, IndexError):
                        pass
                    break
            print(dm.dag_status(task_states, stub_states))
        elif sub == "check" and len(sys.argv) >= 4:
            with open(sys.argv[3]) as f:
                plan_content = f.read()
            stub_states = None
            for i, a in enumerate(sys.argv):
                if a == "--stub-states" and i + 1 < len(sys.argv):
                    try:
                        stub_states = json.loads(sys.argv[i + 1])
                    except (json.JSONDecodeError, IndexError):
                        pass
                    break
            print(dm.dag_check(plan_content, stub_states))
        elif sub == "next":
            tasks = json.loads(sys.argv[3]) if len(sys.argv) >= 4 else []
            states = json.loads(sys.argv[4]) if len(sys.argv) >= 5 else {}
            print(dm.dag_next(tasks, states))
        elif sub == "visualize" and len(sys.argv) >= 4:
            with open(sys.argv[3]) as f:
                plan_content = f.read()
            tasks = parse_tasks_from_plan(plan_content)
            task_states = None
            for i, a in enumerate(sys.argv):
                if a == "--states" and i + 1 < len(sys.argv):
                    try:
                        task_states = json.loads(sys.argv[i + 1])
                    except (json.JSONDecodeError, IndexError):
                        pass
                    break
            stub_slugs = None
            for i, a in enumerate(sys.argv):
                if a == "--stub-slugs" and i + 1 < len(sys.argv):
                    try:
                        stub_slugs = set(json.loads(sys.argv[i + 1]))
                    except (json.JSONDecodeError, IndexError, TypeError):
                        pass
                    break
            print(dm.dag_visualize(tasks, task_states, stub_slugs))
        elif sub == "stub":
            stub_sub = sys.argv[3] if len(sys.argv) >= 4 else ""
            project_root = os.getcwd()
            sm = StubManager()
            if stub_sub == "inject":
                force = "--force" in sys.argv
                upstream_filter = None
                for i, a in enumerate(sys.argv):
                    if a == "--upstream" and i + 1 < len(sys.argv):
                        upstream_filter = sys.argv[i + 1]
                        break
                plan_content = ""
                plan_path = None
                for i, a in enumerate(sys.argv):
                    if a == "--plan" and i + 1 < len(sys.argv):
                        plan_path = sys.argv[i + 1]
                        break
                if plan_path:
                    with open(plan_path) as f:
                        plan_content = f.read()
                print(sm.stub_inject(project_root, session_flag or "", plan_content, force, upstream_filter))
            elif stub_sub == "status":
                source_filter = None
                downstream_filter = None
                for i, a in enumerate(sys.argv):
                    if a == "--source" and i + 1 < len(sys.argv):
                        source_filter = sys.argv[i + 1]
                    if a == "--downstream" and i + 1 < len(sys.argv):
                        downstream_filter = sys.argv[i + 1]
                print(sm.stub_status(project_root, session_flag or "", source_filter, downstream_filter))
            elif stub_sub == "show" and len(sys.argv) >= 5:
                print(sm.stub_show(project_root, session_flag or "", sys.argv[4]))
            elif stub_sub == "replace" and len(sys.argv) >= 5:
                notify = "--notify" in sys.argv
                print(sm.stub_replace(project_root, session_flag or "", sys.argv[4], notify))
            elif stub_sub == "notify":
                task_slug = sys.argv[4] if len(sys.argv) >= 5 else None
                ack = "--ack" in sys.argv
                print(sm.stub_notify(project_root, session_flag or "", task_slug, ack))
            elif stub_sub == "mapping":
                source_filter = None
                downstream_filter = None
                rebuild = "--rebuild" in sys.argv
                for i, a in enumerate(sys.argv):
                    if a == "--source" and i + 1 < len(sys.argv):
                        source_filter = sys.argv[i + 1]
                    if a == "--downstream" and i + 1 < len(sys.argv):
                        downstream_filter = sys.argv[i + 1]
                print(sm.stub_mapping(project_root, session_flag or "", source_filter, downstream_filter, rebuild))
            elif stub_sub == "cleanup":
                dry_run = "--dry-run" in sys.argv
                print(sm.stub_cleanup(project_root, session_flag or "", dry_run))
            elif stub_sub == "config" and len(sys.argv) >= 5:
                task_slug = sys.argv[4]
                disable = None
                for i, a in enumerate(sys.argv):
                    if a == "--disable-notifications":
                        disable = True
                    if a == "--enable-notifications":
                        disable = False
                print(sm.stub_config(project_root, session_flag or "", task_slug, disable))
            else:
                print("ERROR: usage: pdf-engine.py dag stub <inject|status|show|replace|notify|mapping|cleanup|config>", file=sys.stderr)
        else:
            print("ERROR: usage: pdf-engine.py dag <build|status|check|next|visualize|stub>", file=sys.stderr)

    elif cmd == "skip-audit":
        cmd_skip_audit_route()

    elif cmd == "config":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "list-channels":
            cmd_config_list_channels()
        elif sub == "get-channel" and len(sys.argv) >= 4:
            cmd_config_get_channel(sys.argv[3])
        elif sub == "get-model" and len(sys.argv) >= 4:
            cmd_config_get_model(sys.argv[3])
        elif sub == "get-made":
            cmd_config_get_made()
        elif sub == "n-check" and len(sys.argv) >= 5:
            channel = sys.argv[3]
            try:
                n_do = int(sys.argv[4])
            except ValueError:
                n_do = 1
            n_check = _compute_n_check(channel, n_do)
            print(f"{n_check}")
        else:
            print("ERROR: usage: pdf-engine.py config <list-channels|get-channel <name>|get-model <stage.role>|get-made|n-check <channel> <n_do>>", file=sys.stderr)

    elif cmd == "knowledge":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "load-past-adrs" and len(sys.argv) >= 4:
            cmd_knowledge_load_past_adrs(sys.argv[3])
        elif sub == "match-historical" and len(sys.argv) >= 4:
            cmd_knowledge_match_historical(sys.argv[3])
        elif sub == "resolve-precedent":
            cmd_knowledge_resolve_precedent()
        elif sub == "get-ts" and len(sys.argv) >= 4:
            cmd_knowledge_get_ts(sys.argv[3])
        elif sub == "append-ts" and len(sys.argv) >= 4:
            content = ""
            for i, a in enumerate(sys.argv):
                if a == "--content" and i + 1 < len(sys.argv):
                    content = sys.argv[i + 1]
                    break
            cmd_knowledge_append_ts(sys.argv[3], content)
        elif sub == "search" and len(sys.argv) >= 4:
            top_k = 3
            source = "all"
            for i, a in enumerate(sys.argv):
                if a == "--top-k" and i + 1 < len(sys.argv):
                    try:
                        top_k = int(sys.argv[i + 1])
                    except ValueError:
                        pass
                if a == "--source" and i + 1 < len(sys.argv):
                    source = sys.argv[i + 1]
            cmd_knowledge_search(sys.argv[3], top_k, source)
        elif sub == "reindex":
            source = "all"
            force = False
            for i, a in enumerate(sys.argv):
                if a == "--source" and i + 1 < len(sys.argv):
                    source = sys.argv[i + 1]
                if a == "--force":
                    force = True
            cmd_knowledge_reindex(source, force)
        elif sub == "seed":
            cmd_knowledge_seed()
        elif sub == "persist-factors":
            cmd_knowledge_persist_factors()
        elif sub == "scan-promotion":
            cmd_knowledge_scan_promotion()
        else:
            print("ERROR: usage: pdf-engine.py knowledge <load-past-adrs|match-historical|resolve-precedent|get-ts|append-ts|search|reindex|seed|persist-factors|scan-promotion>", file=sys.stderr)

    elif cmd == "context":
        if len(sys.argv) >= 5 and sys.argv[2] == "bundle" and sys.argv[3] == "build":
            stage = sys.argv[4] if len(sys.argv) > 4 else ""
            role = ""
            for i, a in enumerate(sys.argv):
                if a == "--role" and i + 1 < len(sys.argv):
                    role = sys.argv[i + 1]
                    break
            cmd_context_bundle_build(stage, role)
        else:
            print("ERROR: usage: pdf-engine.py context bundle build <stage> --role <role>", file=sys.stderr)

    elif cmd == "history":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "query":
            cmd_history_query()
        elif sub == "update":
            cmd_history_update()
        else:
            print("ERROR: usage: pdf-engine.py history query|update ...", file=sys.stderr)

    elif cmd == "resume-state":
        cmd_resume_state()

    elif cmd == "checkpoint":
        if len(sys.argv) >= 4 and sys.argv[2] == "update" and sys.argv[3] == "--phase":
            phase = sys.argv[4] if len(sys.argv) >= 5 else ""
            resumed = "--resumed" in sys.argv
            cmd_checkpoint_update(phase, resumed)
        else:
            print("ERROR: usage: pdf-engine.py checkpoint update --phase <name> [--resumed]", file=sys.stderr)

    elif cmd == "resume":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "verify":
            cmd_resume_verify()
        elif sub == "summary":
            cmd_resume_summary()
        else:
            print("ERROR: usage: pdf-engine.py resume <verify|summary>", file=sys.stderr)

    elif cmd == "session":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "list":
            cmd_session_list()
        elif sub == "switch" and len(sys.argv) >= 4:
            cmd_session_switch(sys.argv[3])
        elif sub == "current":
            cmd_session_current()
        elif sub == "delete" and len(sys.argv) >= 4:
            cmd_session_delete(sys.argv[3])
        elif sub == "create" and len(sys.argv) >= 4:
            cmd_session_create(sys.argv[3])
        elif sub == "list-by-slug" and len(sys.argv) >= 4:
            pm = PlanManager()
            print(pm.session_list_by_slug(os.getcwd(), sys.argv[3]))
        elif sub == "":
            print("ERROR: usage: pdf-engine.py session <list|switch|current|delete|create> [...]", file=sys.stderr)
        else:
            print(f"ERROR: unknown session subcommand '{sub}'", file=sys.stderr)

    elif cmd == "workspace":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "add" and len(sys.argv) >= 4:
            cmd_workspace_add(sys.argv[3])
        elif sub == "remove" and len(sys.argv) >= 4:
            cmd_workspace_remove(sys.argv[3])
        elif sub == "list":
            cmd_workspace_list()
        elif sub == "switch" and len(sys.argv) >= 4:
            cmd_workspace_switch(sys.argv[3])
        elif sub == "":
            print("ERROR: usage: pdf-engine.py workspace <add|remove|list|switch> [...]", file=sys.stderr)
        else:
            print(f"ERROR: unknown workspace subcommand '{sub}'", file=sys.stderr)

    elif cmd == "project":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        project_root = os.getcwd()
        for i, a in enumerate(sys.argv):
            if a == "--project" and i + 1 < len(sys.argv):
                project_root = sys.argv[i + 1]
        if sub == "init":
            name = None
            force = "--force" in sys.argv
            for i, a in enumerate(sys.argv):
                if a == "--name" and i + 1 < len(sys.argv):
                    name = sys.argv[i + 1]
            print(ProjectStateManager.init(project_root, name, force))
        elif sub == "status":
            print(ProjectStateManager.status(project_root))
        elif sub == "add-task" and len(sys.argv) >= 4:
            slug = sys.argv[3]
            desc = ""
            channel = "standard"
            for i, a in enumerate(sys.argv):
                if a == "--desc" and i + 1 < len(sys.argv):
                    desc = sys.argv[i + 1]
                if a == "--channel" and i + 1 < len(sys.argv):
                    channel = sys.argv[i + 1]
            print(ProjectStateManager.add_task(project_root, slug, desc, channel))
        elif sub == "close-task" and len(sys.argv) >= 4:
            print(ProjectStateManager.close_task(project_root, sys.argv[3]))
        elif sub == "archive":
            print(ProjectStateManager.archive(project_root))
        elif sub == "rebuild":
            print(ProjectStateManager.rebuild(project_root))
        elif sub == "verify":
            print(ProjectStateManager.verify(project_root))
        else:
            print("ERROR: usage: pdf-engine.py project <init|status|add-task|close-task|archive|rebuild|verify> [...]", file=sys.stderr)

    # ── Strong engine commands (v2) ─────────────────
    elif cmd == "blueprint":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "load":
            name = sys.argv[3] if len(sys.argv) >= 4 else "default"
            cmd_blueprint_load(name)
        elif sub == "list":
            cmd_blueprint_list()
        elif sub == "validate":
            name = sys.argv[3] if len(sys.argv) >= 4 else "default"
            cmd_blueprint_validate(name)
        else:
            print("ERROR: usage: pdf-engine.py blueprint <load [name]|list|validate [name]>", file=sys.stderr)

    elif cmd == "hsm":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "status":
            cmd_hsm_status()
        elif sub == "event" and len(sys.argv) >= 4:
            cmd_hsm_event(sys.argv[3])
        elif sub == "goto" and len(sys.argv) >= 4:
            cmd_hsm_goto(sys.argv[3])
        elif sub == "unpause":
            cmd_hsm_unpause()
        elif sub == "reset-loops":
            event = sys.argv[3] if len(sys.argv) >= 4 else None
            cmd_hsm_reset_loops(event)
        else:
            print("ERROR: usage: pdf-engine.py hsm <status|event <name>|goto <path>|unpause|reset-loops [event]>", file=sys.stderr)

    elif cmd == "pipeline":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "tick":
            blueprint_name = sys.argv[3] if len(sys.argv) >= 4 else "default"
            cmd_pipeline_tick(blueprint_name)
        elif sub == "status":
            cmd_pipeline_status()
        elif sub == "summary":
            cmd_pipeline_summary()
        elif sub == "node-start" and len(sys.argv) >= 5:
            cmd_pipeline_node_start(sys.argv[3], sys.argv[4])
        elif sub == "node-done" and len(sys.argv) >= 5:
            name = sys.argv[5] if len(sys.argv) >= 6 else "default"
            cmd_pipeline_node_done(sys.argv[3], sys.argv[4], name)
        elif sub == "node-fail" and len(sys.argv) >= 5:
            name = sys.argv[5] if len(sys.argv) >= 6 else "default"
            cmd_pipeline_node_fail(sys.argv[3], sys.argv[4], name)
        elif sub == "node-skip" and len(sys.argv) >= 5:
            name = sys.argv[5] if len(sys.argv) >= 6 else "default"
            cmd_pipeline_node_skip(sys.argv[3], sys.argv[4], name)
        elif sub == "pass-checkpoint" and len(sys.argv) >= 5:
            name = sys.argv[5] if len(sys.argv) >= 6 else "default"
            cmd_pipeline_pass_checkpoint(sys.argv[3], sys.argv[4], name)
        elif sub == "reject-checkpoint" and len(sys.argv) >= 5:
            name = sys.argv[5] if len(sys.argv) >= 6 else "default"
            cmd_pipeline_reject_checkpoint(sys.argv[3], sys.argv[4], name)
        else:
            print("ERROR: usage: pdf-engine.py pipeline <tick [blueprint]|status|summary|node-start <stage> <ref>|node-done <stage> <ref>|...>", file=sys.stderr)

    elif cmd == "rollback":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "check":
            cmd_rollback_check()
        elif sub == "exec" and len(sys.argv) >= 5:
            cmd_rollback_exec(sys.argv[3], sys.argv[4])
        elif sub == "cancel":
            cmd_rollback_cancel()
        else:
            print("ERROR: usage: pdf-engine.py rollback <check|exec <target_stage> <event>|cancel>", file=sys.stderr)

    elif cmd == "constraint":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "verify":
            stage = sys.argv[3] if len(sys.argv) >= 4 else ""
            phase = sys.argv[4] if len(sys.argv) >= 5 else ""
            cmd_constraint_verify(stage, phase)
        else:
            print("ERROR: usage: pdf-engine.py constraint verify [stage] [phase]", file=sys.stderr)

    elif cmd == "archive":
        cmd_archive_route()

    elif cmd == "intel":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "inject":
            cmd_intel_inject()
        else:
            print("ERROR: usage: pdf-engine.py intel inject", file=sys.stderr)

    elif cmd == "factor":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "analyze":
            cmd_factor_analyze()
        else:
            print("ERROR: usage: pdf-engine.py factor analyze", file=sys.stderr)

    elif cmd == "meta":
        sub = sys.argv[2] if len(sys.argv) >= 3 else ""
        if sub == "review":
            cmd_meta_review()
        else:
            print("ERROR: usage: pdf-engine.py meta review", file=sys.stderr)

    elif cmd == "density-check":
        cmd_density_check()

    elif cmd == "converge":
        cmd_converge()

    else:
        print(f"ERROR: unknown command '{cmd}'", file=sys.stderr)
        print(__doc__)


if __name__ == "__main__":
    main()
