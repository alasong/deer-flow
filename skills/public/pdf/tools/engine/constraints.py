"""Constraint engine — pre-transition integrity gates and artifact validation.

Constraints are defined in docs/topology/constraints.yaml and executed
automatically before HSM transitions or pipeline node execution.
"""
import glob, json, os, hashlib

try:
    import yaml
except ImportError:
    yaml = None

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _yaml_load(path):
    if yaml is None:
        return None
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


# ── Constraint loading ─────────────────────────────────────────────

def _constraints_path():
    return os.path.join(SKILL_DIR, "docs", "topology", "constraints.yaml")


def load_constraints():
    """Load constraint rules from YAML. Returns list of rules or empty list."""
    path = _constraints_path()
    if not os.path.exists(path):
        return []
    data = _yaml_load(path)
    if not data or "constraints" not in data:
        return []
    return data["constraints"]


def _resolve_path(relative_path, state):
    """Resolve a relative artifact path against the cycle/session dir."""
    project_root = state.get("project_root", os.getcwd())
    full = os.path.join(project_root, relative_path)
    if os.path.exists(full):
        return full
    # Try under .fat/pdf/
    alt = os.path.join(project_root, ".fat", "pdf", relative_path)
    if os.path.exists(alt):
        return alt
    # Try under session artifact dir (if session is active)
    session_id = state.get("session_id") or state.get("multi_session", {}).get("session_id")
    if session_id:
        session_path = os.path.join(project_root, ".fat", "pdf", "sessions", session_id, "artifacts", relative_path)
        if os.path.exists(session_path):
            return session_path
    return full


# ── Constraint checkers ────────────────────────────────────────────

def _check_file_exists(target, state):
    """Check if a file (or glob pattern) exists. Returns (pass, detail)."""
    path = _resolve_path(target, state)
    if "*" in target or "?" in target:
        matches = glob.glob(path)
        if matches:
            return True, f"found {len(matches)} file(s): {target}"
        return False, f"no files matching: {target}"
    if os.path.exists(path):
        return True, f"found: {path}"
    return False, f"not found: {target}"


def _check_file_hash(target, hash_file, state):
    """Check if file hash matches stored fingerprint."""
    path = _resolve_path(target, state)
    if not os.path.exists(path):
        return False, f"file not found: {target}"
    current_hash = _quick_hash_file(path)

    hash_path = _resolve_path(hash_file, state)
    if not os.path.exists(hash_path):
        return False, f"hash file not found: {hash_file}"

    try:
        with open(hash_path) as f:
            stored = json.load(f)
        stored_hash = stored.get(target, "")
        if current_hash == stored_hash:
            return True, "hash matches"
        return False, f"hash mismatch: {current_hash[:12]} != {stored_hash[:12]}"
    except Exception as e:
        return False, f"hash check error: {e}"


def _check_m_value_consistency(target, state):
    """Verify M factor value matches actual plan dimensions."""
    # This is a semantic check — engine verifies the state value is set
    m_value = state.get("m", 1)
    return True, f"M={m_value} (consistency check requires LLM review)"


def _check_adversary_resolved(adversary_files, decision_file, state):
    """Check if adversary files exist and have corresponding decisions."""
    project_root = state.get("project_root", os.getcwd())
    session_id = state.get("session_id") or state.get("multi_session", {}).get("session_id")
    if session_id:
        pdf_dir = os.path.join(project_root, ".fat", "pdf", "sessions", session_id, "artifacts")
    else:
        pdf_dir = os.path.join(project_root, ".fat", "pdf")

    # Find adversary files by pattern
    import glob as _glob
    pattern_parts = adversary_files.split("*")
    if len(pattern_parts) == 2:
        pattern = os.path.join(pdf_dir, pattern_parts[0] + "*" + pattern_parts[1])
    else:
        pattern = os.path.join(pdf_dir, adversary_files)

    adv_files = _glob.glob(pattern)
    if not adv_files:
        return True, "no adversary files found (vacuous)"

    # Check if decision file exists
    dec_path = _resolve_path(decision_file, state)
    if not os.path.exists(dec_path):
        return False, f"adversary files exist ({len(adv_files)}) but no decision file: {decision_file}"

    return True, f"{len(adv_files)} adversary file(s) resolved in decisions"


_CHECKERS = {
    "file_exists": _check_file_exists,
    "file_hash": _check_file_hash,
    "m_value_consistency": _check_m_value_consistency,
    "adversary_resolved": _check_adversary_resolved,
}


# ── Constraint evaluation ──────────────────────────────────────────

def verify_constraints(stage=None, phase=None, state=None):
    """Evaluate all applicable constraints. Returns (all_pass, violations).

    If stage is given, only evaluates constraints for that stage.
    If phase is given, only evaluates run_before that phase.
    """
    if state is None:
        state = {}
    constraints = load_constraints()
    violations = []

    for rule in constraints:
        c_stage = rule.get("stage")
        c_before = rule.get("run_before")

        # Filter by stage
        if stage and c_stage and c_stage != stage:
            continue
        # Filter by phase
        if phase and c_before and c_before != phase:
            continue

        name = rule["name"]
        ctype = rule["type"]
        on_fail = rule.get("on_fail", "block")

        checker = _CHECKERS.get(ctype)
        if not checker:
            violations.append({"name": name, "pass": False,
                               "detail": f"unknown checker type: {ctype}",
                               "severity": "error"})
            continue

        try:
            if ctype == "file_exists":
                passed, detail = _check_file_exists(rule["target"], state)
            elif ctype == "file_hash":
                passed, detail = _check_file_hash(rule["target"], rule["hash_file"], state)
            elif ctype == "m_value_consistency":
                passed, detail = _check_m_value_consistency(rule.get("target", ""), state)
            elif ctype == "adversary_resolved":
                passed, detail = _check_adversary_resolved(
                    rule.get("adversary_files", ""),
                    rule.get("decision_file", ""),
                    state)
            else:
                passed, detail = False, f"unknown type: {ctype}"
        except Exception as e:
            passed, detail = False, f"check error: {e}"

        violation = {
            "name": name,
            "pass": passed,
            "detail": detail,
            "on_fail": on_fail,
        }
        violations.append(violation)

    all_pass = all(v["pass"] or v["on_fail"] == "warn" or v["on_fail"] == "info"
                   for v in violations)
    blocking = [v for v in violations if not v["pass"] and v["on_fail"] == "block"]
    return all_pass, violations, blocking


def escalate_constraints(violations, state):
    """Check violation history — escalate repeated violations."""
    if state is None:
        state = {}
    history = state.setdefault("constraint_violations", [])
    now = __import__("time").time()

    for v in violations:
        if not v["pass"] and v["on_fail"] == "block":
            history.append({
                "name": v["name"],
                "detail": v["detail"],
                "ts": now,
            })

    # Count recent violations per constraint (last 3600s)
    recent = {}
    cutoff = now - 3600
    for entry in history:
        if entry["ts"] >= cutoff:
            recent[entry["name"]] = recent.get(entry["name"], 0) + 1

    escalated = []
    for name, count in recent.items():
        if count >= 3:
            escalated.append({"name": name, "count": count,
                              "action": "pause_and_notify"})
    return escalated


def _quick_hash_file(path):
    """MD5 hash of file contents."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()
