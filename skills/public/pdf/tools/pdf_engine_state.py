"""State, checkpoint, and resume commands for pdf-engine."""
import json
import os
import subprocess
import sys

from pdf_engine_shared import (
    STAGE_ORDER,
    PHASE_ORDER,
    PHASE_NEXT_ACTION,
    PDFContext,
    _agent_status,
    _load_state,
    _load_state_with_migration,
    _require_state,
    _save_state,
    _timestamp,
)


def cmd_state(key=None):
    """Display state. Optional key for single field."""
    state = _require_state()
    if key:
        val = state.get(key, "?")
        if isinstance(val, dict) or isinstance(val, list):
            print(json.dumps(val, indent=2, ensure_ascii=False))
        else:
            print(val)
        return

    stage = state.get("stage", "unknown")
    print(f"stage={stage}")
    print(f"channel={state.get('channel', '?')}")
    print(f"task={state.get('task_slug', '?')}")
    if state.get("domain"):
        print(f"domain={state['domain']} output_kind={state.get('output_kind', '?')}")
    print(f"round={state.get('round', 1)}")
    rg = state.get("repair_gate_config", {})
    max_plan = rg.get("max_plan_loop", 2)
    max_do = rg.get("max_do_loop", 3)
    plan_rem = state.get("plan_rem_loop", 0)
    do_rem = state.get("do_rem_loop", 0)
    print(f"plan_rem_loop={plan_rem}/{max_plan} (limit_from_config)")
    print(f"do_rem_loop={do_rem}/{max_do} (limit_from_config)")
    mt = state.get("model_tier", {})
    for sname in STAGE_ORDER:
        if sname == "done":
            continue
        scfg = mt.get(sname, {})
        if scfg.get("p1_model"):
            print(f"  {sname}_tier: p1={scfg.get('p1_model','?')} p2={scfg.get('p2_model','?')}")
    mc = state.get("made_config", {})
    if mc:
        print(f"made_config: depth>={mc.get('trigger_depth',2)} explorers=[{mc.get('n_explorers_min',2)},{mc.get('n_explorers_max',6)}] "
              f"fallback={mc.get('fallback_on_failure','skip')}")
    arts = state.get("artifacts", [])
    print(f"artifacts={len(arts)}")

    for sname in STAGE_ORDER:
        if sname == "done":
            continue
        scfg = state.get("stages", {}).get(sname, {})
        n = scfg.get("N", scfg.get("N_design", "?"))
        m = scfg.get("M", "?")
        agents_done = sum(1 for v in scfg.get("agents", {}).values() if _agent_status(v) == "done")
        agents_total = len(scfg.get("agents", {}))
        rev_done = sum(1 for v in scfg.get("reviewers", {}).values() if _agent_status(v) == "done")
        rev_total = len(scfg.get("reviewers", {}))
        print(f"  {sname}: N={n} M={m} agents={agents_done}/{agents_total} rev={rev_done}/{rev_total}")

    forgeries = state.get("forgery_log", [])
    if forgeries:
        print(f"forgery_suspected={len(forgeries)}")
        for fg in forgeries[-3:]:
            print(f"  {fg.get('agent')} elapsed={fg.get('elapsed_seconds', '?')}s "
                  f"stage={fg.get('stage')}")

    um = state.get("user_memory", {})
    if um.get("loaded"):
        print(f"memory: {um.get('summary', 'loaded')}")

    if state.get("_history_seed_info"):
        si = state["_history_seed_info"]
        print(f"seed={si.get('source', '?')} ({si.get('n_entries', 0)} entries)")


def cmd_state_set(kv):
    """Set state key=value."""
    state = _require_state()
    if "=" not in kv:
        print("ERROR: usage: pdf-engine.py state set <key>=<value>", file=sys.stderr)
        return
    key, val = kv.split("=", 1)
    if val in ("true", "True"):
        val = True
    elif val in ("false", "False"):
        val = False
    elif val in ("null", "None"):
        val = None
    else:
        try:
            val = int(val)
        except ValueError:
            try:
                val = float(val)
            except ValueError:
                pass
    state[key] = val
    _save_state(state)
    print(f"OK: state.{key} = {json.dumps(val, ensure_ascii=False)}")


# ── Checkpoint Commands ───────────────────────────────


def cmd_resume_state():
    """[LEGACY] Output structured resume info from old phase-based checkpoint."""
    state = _load_state_with_migration()
    if state is None:
        print("phase=no_checkpoint")
        print("next_action=init")
        print("resume_count=0")
        print("git_head=")
        print("git_drift=no_state")
        print("phase_history_count=0")
        print("artifact_count=0")
        return

    cp = state.get("checkpoint", {"phase": None, "phase_history": [], "log": []})
    phase = cp.get("phase")
    next_action = PHASE_NEXT_ACTION.get(phase, "init") if phase else "init"
    sess = state.get("session", {"current": 0, "sessions": [], "resume_count": 0})
    resume_count = sess.get("resume_count", 0)
    phase_history = cp.get("phase_history", [])
    artifacts = state.get("artifacts", [])

    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        git_head = result.stdout.strip()
    except Exception:
        git_head = ""

    stored_git = state.get("git_head", "")
    if not git_head:
        git_drift = "no_git"
    elif not stored_git:
        git_drift = "unknown"
    elif git_head == stored_git:
        git_drift = "same"
    else:
        git_drift = "changed"

    print(f"phase={phase or 'none'}")
    print(f"next_action={next_action}")
    print(f"resume_count={resume_count}")
    print(f"git_head={git_head}")
    print(f"git_drift={git_drift}")
    print(f"phase_history_count={len(phase_history)}")
    print(f"artifact_count={len(artifacts)}")


def cmd_checkpoint_update(phase, resumed=False):
    """Update checkpoint phase tracking."""
    state = _require_state()
    cp = state.setdefault("checkpoint", {"phase": None, "phase_history": [], "log": []})
    cp["phase"] = phase

    if not cp["phase_history"] or cp["phase_history"][-1] != phase:
        cp.setdefault("phase_history", []).append(phase)

    cp.setdefault("log", []).append({"phase": phase, "at": _timestamp()})

    if resumed:
        sess = state.setdefault("session", {"current": 0, "sessions": [], "resume_count": 0})
        sess["resume_count"] = sess.get("resume_count", 0) + 1

    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            state["git_head"] = result.stdout.strip()
    except Exception:
        pass

    _save_state(state)
    history_count = len(cp.get("phase_history", []))
    print(f"checkpoint phase={phase} (history: {history_count} entries)")


def cmd_resume_verify():
    """Verify integrity of artifacts for safe resume."""
    state = _load_state_with_migration()
    if state is None:
        print("ERROR: no state to verify")
        sys.exit(1)

    issues = []
    artifacts = state.get("artifacts", [])
    for art in artifacts:
        path = art.get("path", "")
        if not path:
            issues.append(f"artifact #{art.get('id', '?')}: no path")
            continue
        if not os.path.exists(path):
            issues.append(f"artifact #{art.get('id', '?')}: missing {path}")
        elif os.path.getsize(path) == 0:
            issues.append(f"artifact #{art.get('id', '?')}: empty {path}")

    stored_git = state.get("git_head", "")
    if stored_git:
        try:
            result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
            current_git = result.stdout.strip()
            if current_git and current_git != stored_git:
                issues.append(f"git HEAD drift: stored={stored_git} current={current_git}")
        except Exception:
            pass

    if issues:
        for iss in issues:
            print(iss)
        sys.exit(1)
    else:
        print("OK")


def cmd_resume_summary():
    """Output structured session context for LLM injection."""
    state = _load_state_with_migration()
    if state is None:
        print("ERROR: no state available")
        sys.exit(1)

    cp = state.get("checkpoint", {"phase": None, "phase_history": []})
    phase = cp.get("phase", "none")
    phase_history = cp.get("phase_history", [])
    sess = state.get("session", {"current": 0, "sessions": [], "resume_count": 0})

    next_phase = ""
    if phase and phase in PHASE_ORDER:
        idx = PHASE_ORDER.index(phase)
        if idx + 1 < len(PHASE_ORDER):
            next_phase = PHASE_ORDER[idx + 1]

    artifacts = state.get("artifacts", [])
    art_paths = [a.get("path", "?") for a in artifacts]
    art_str = ", ".join(art_paths[:5])
    if len(art_paths) > 5:
        art_str += f" ... (+{len(art_paths) - 5} more)"

    print("=== PDF Resume Context ===")
    print(f"Task: {state.get('task_slug', '?')}")
    print(f"Stage: {state.get('stage', '?')}")
    print(f"Checkpoint Phase: {phase} / {len(phase_history)} total")
    print(f"Resume Count: {sess.get('resume_count', 0)}")
    print(f"Session: {sess.get('current', 0)}")
    print(f"Completed Phases: {', '.join(phase_history) if phase_history else 'none'}")
    print(f"Next Phase: {next_phase or 'end'}")
    print(f"Key Artifacts: {art_str or 'none'}")
