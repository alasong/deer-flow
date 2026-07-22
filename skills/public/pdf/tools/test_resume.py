#!/usr/bin/env python3
"""
PDF Multi-Session Resume — Smoke Test (Engine CLI)

Tests the 4 new PDF engine commands against the live engine:
  1. checkpoint update --phase <name>
  2. resume-state (next_action state machine)
  3. resume verify (artifact + git integrity)
  4. resume summary (session context injection)

Creates temp directories with mock .pdf_state.json and verifies
the engine output for each command.

Usage:
  python3 skills/pdf/tools/test_resume.py
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
ENGINE_PATH = os.path.join(SCRIPT_DIR, "pdf-engine.py")

# ==============================================================================
# Phase Definitions (from the engine's PHASE_ORDER — must match exactly)
# ==============================================================================

PHASE_ORDER = [
    # Plan: 17 phases
    "precheck", "scope", "sanitize", "channel_select", "tech_stack_kb",
    "cycle_history", "intel_injection", "factor_bootstrap", "factor_analysis",
    "made", "meta_review", "analysis", "design", "merge_plan",
    "plan_review", "plan_convergence", "plan_context",
    # Do: 3 phases
    "do_p1", "do_p2", "do_repair",
    # Check: 3 phases
    "check_p1", "check_p2", "repair_gate",
    # Act: 2 phases
    "act_p1", "act_p2",
    # Terminal
    "done",
]

assert len(PHASE_ORDER) == 26, f"expected 26 phases, got {len(PHASE_ORDER)}"

# Map engine's next_action output for each phase
PHASE_NEXT_ACTION = {
    "precheck": "resume_plan", "scope": "resume_plan", "sanitize": "resume_plan",
    "channel_select": "resume_plan", "tech_stack_kb": "resume_plan",
    "cycle_history": "resume_plan", "intel_injection": "resume_plan",
    "factor_bootstrap": "resume_plan", "factor_analysis": "resume_plan",
    "made": "resume_plan", "meta_review": "resume_plan",
    "analysis": "resume_plan", "design": "resume_plan",
    "merge_plan": "resume_plan", "plan_review": "resume_plan",
    "plan_convergence": "resume_plan", "plan_context": "resume_plan",
    "do_p1": "resume_do", "do_p2": "resume_do", "do_repair": "resume_do",
    "check_p1": "resume_check", "check_p2": "resume_check", "repair_gate": "resume_check",
    "act_p1": "resume_act", "act_p2": "resume_act",
    "done": None,  # done maps to "already_complete" but via different code path
}

# Build stage->phases map for bulk testing
STAGE_PHASES = {
    "plan": PHASE_ORDER[:17],
    "do": PHASE_ORDER[17:20],
    "check": PHASE_ORDER[20:23],
    "act": PHASE_ORDER[23:25],
}

# ==============================================================================
# Helpers
# ==============================================================================

def _write_state(state: dict, dirpath: str):
    """Write .pdf_state.json into dirpath/.fat/pdf/."""
    pdf_dir = os.path.join(dirpath, ".fat", "pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    with open(os.path.join(pdf_dir, ".pdf_state.json"), "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    return pdf_dir


def _run_engine(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run pdf-engine.py from cwd and return completed process."""
    return subprocess.run(
        [sys.executable, ENGINE_PATH] + cmd,
        capture_output=True, text=True, cwd=cwd, timeout=10,
    )


def make_state(
    stage: str = "plan",
    phase: str = "precheck",
    phase_history: list[str] | None = None,
    artifacts: list[dict] | None = None,
    session_num: int = 1,
    resume_count: int = 0,
) -> dict:
    """Build a PDF state dict matching the engine's expected v2 + checkpoint schema."""
    return {
        "schema_version": 2,
        "engine": "pdf",
        "stage": stage,
        "task_slug": "test-task",
        "channel": "full",
        "checkpoint": {
            "phase": phase,
            "phase_history": list(phase_history) if phase_history else [],
            "log": [],
        },
        "session": {
            "current": session_num,
            "sessions": [{
                "n": session_num,
                "started_at": "2026-06-22T10:00:00Z",
                "ended_at": None,
                "phase_start": phase_history[0] if phase_history else "precheck",
                "phase_end": phase,
            }],
            "resume_count": resume_count,
        },
        "artifacts": list(artifacts) if artifacts is not None else [
            {"path": ".fat/pdf/plan.md", "stage": "plan", "role": "plan", "id": 1},
        ],
        "git_head": "",
    }


def _parse_resume_state_output(stdout: str) -> dict[str, str]:
    """Parse key=value lines from resume-state output into a dict."""
    result = {}
    for line in stdout.strip().splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _has_error(stderr: str) -> bool:
    """Check if engine output contains ERROR."""
    return "ERROR:" in stderr


def _get_issues(stdout: str, stderr: str) -> list[str]:
    """Get issues from resume verify output."""
    all_output = (stdout + "\n" + stderr).strip()
    if all_output == "OK":
        return []
    return [l.strip() for l in all_output.split("\n") if l.strip()]

# ==============================================================================
# Test Framework
# ==============================================================================

_total = 0
_passed = 0
_failed = 0


def test(name: str, fn):
    global _total, _passed, _failed
    _total += 1
    try:
        fn()
        _passed += 1
        print(f"  PASS: {name}")
    except Exception as e:
        _failed += 1
        print(f"  FAIL: {name}: {e}")


def assert_eq(label: str, got, expected):
    if got != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {got!r}")


def assert_in(label: str, needle: str, haystack: str):
    if needle not in haystack:
        raise AssertionError(f"{label}: expected {needle!r} not found in output:\n{haystack}")


# ==============================================================================
# Test 1: checkpoint update --phase <name>
# ==============================================================================

def test_checkpoint_update():
    print("\n[Test 1] checkpoint update --phase <name>")

    # 1a. Sequential phase accumulation via CLI
    def _1a():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="plan", phase="precheck", phase_history=["precheck"]), td)
            r = _run_engine(["checkpoint", "update", "--phase", "scope"], td)
            assert r.returncode == 0, f"checkpoint update failed: {r.stderr}"
            assert_in("checkpoint output", "checkpoint phase=scope", r.stdout)
            # read back state
            with open(os.path.join(td, ".fat", "pdf", ".pdf_state.json")) as f:
                state = json.load(f)
            assert_eq("phase", state["checkpoint"]["phase"], "scope")
            assert_eq("history len", len(state["checkpoint"]["phase_history"]), 2)
            assert_eq("history[1]", state["checkpoint"]["phase_history"][1], "scope")
            assert_eq("log len", len(state["checkpoint"]["log"]), 1)
    test("1a: sequential phase accumulation (precheck -> scope)", _1a)

    # 1b. 5-phase accumulation
    def _1b():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="plan", phase="", phase_history=[]), td)
            phases = ["precheck", "scope", "sanitize", "channel_select", "factor_analysis"]
            for ph in phases:
                r = _run_engine(["checkpoint", "update", "--phase", ph], td)
                assert r.returncode == 0, f"update to {ph} failed: {r.stderr}"
            with open(os.path.join(td, ".fat", "pdf", ".pdf_state.json")) as f:
                state = json.load(f)
            assert_eq("phase", state["checkpoint"]["phase"], "factor_analysis")
            assert_eq("history", state["checkpoint"]["phase_history"], phases)
            assert_eq("log entries", len(state["checkpoint"]["log"]), 5)
    test("1b: 5-phase accumulation", _1b)

    # 1c. Re-update same phase (engine checks last entry for dedup)
    def _1c():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="plan", phase="precheck", phase_history=["precheck"]), td)
            r = _run_engine(["checkpoint", "update", "--phase", "precheck"], td)
            assert r.returncode == 0, f"re-update failed: {r.stderr}"
            with open(os.path.join(td, ".fat", "pdf", ".pdf_state.json")) as f:
                state = json.load(f)
            assert_eq("phase", state["checkpoint"]["phase"], "precheck")
            # Engine dedups: checks if last history entry == phase, skips if so
            assert_eq("history deduped", len(state["checkpoint"]["phase_history"]), 1)
    test("1c: re-update same phase (dedup by last entry)", _1c)

    # 1d. checkpoint update with --resumed flag increments resume_count
    def _1d():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="plan", phase="precheck", phase_history=["precheck"]), td)
            r = _run_engine(["checkpoint", "update", "--phase", "scope", "--resumed"], td)
            assert r.returncode == 0, f"resumed update failed: {r.stderr}"
            with open(os.path.join(td, ".fat", "pdf", ".pdf_state.json")) as f:
                state = json.load(f)
            assert_eq("resume_count", state["session"]["resume_count"], 1)
    test("1d: --resumed flag increments resume_count", _1d)


# ==============================================================================
# Test 2: resume-state mapping
# ==============================================================================

def test_resume_state():
    print("\n[Test 2] resume-state (next_action mapping)")

    # 2a. All 26 phases -> correct next_action
    def _2a():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            for stage, phases in STAGE_PHASES.items():
                expected = {
                    "plan": "resume_plan",
                    "do": "resume_do",
                    "check": "resume_check",
                    "act": "resume_act",
                }[stage]
                for phase in phases:
                    _write_state(make_state(stage=stage, phase=phase, phase_history=[phase]), td)
                    r = _run_engine(["resume-state"], td)
                    assert r.returncode == 0, f"resume-state failed for phase={phase}: {r.stderr}"
                    parsed = _parse_resume_state_output(r.stdout)
                    got = parsed.get("next_action", "?")
                    assert_eq(f"phase={phase} (stage={stage})", got, expected)
    test("2a: all 26 phases -> correct next_action", _2a)

    # 2b. No state file -> next_action=init
    def _2b():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            # NOTE: we must NOT write a state file — make sure dir doesn't exist either
            r = _run_engine(["resume-state"], td)
            assert r.returncode == 0
            parsed = _parse_resume_state_output(r.stdout)
            assert_eq("phase=no_checkpoint", parsed.get("phase"), "no_checkpoint")
            assert_eq("next_action=init", parsed.get("next_action"), "init")
    test("2b: no state -> next_action=init", _2b)

    # 2c. stage=done -> next_action=already_complete
    def _2c():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="done", phase="done", phase_history=PHASE_ORDER[:-1]), td)
            r = _run_engine(["resume-state"], td)
            assert r.returncode == 0
            parsed = _parse_resume_state_output(r.stdout)
            assert_eq("done phase", parsed.get("phase"), "done")
            assert_eq("done next_action", parsed.get("next_action"), "already_complete")
    test("2c: stage=done -> already_complete", _2c)


# ==============================================================================
# Test 3: resume verify
# ==============================================================================

def test_resume_verify():
    print("\n[Test 3] resume verify")

    # 3a. All artifacts present -> OK
    def _3a():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            pdf_dir = _write_state(make_state(stage="plan", phase="converge"), td)
            # Create the artifact file
            with open(os.path.join(pdf_dir, "plan.md"), "w") as f:
                f.write("# test plan\n")
            r = _run_engine(["resume", "verify"], td)
            assert r.returncode == 0, f"verify failed: {r.stderr}"
            assert_eq("verify output", r.stdout.strip(), "OK")
    test("3a: all artifacts present -> OK", _3a)

    # 3b. Missing artifact -> error + exit 1
    def _3b():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(
                stage="plan", phase="converge",
                artifacts=[
                    {"path": ".fat/pdf/exists.md", "stage": "plan", "role": "plan", "id": 1},
                    {"path": ".fat/pdf/missing.md", "stage": "plan", "role": "analysis", "id": 2},
                ],
            ), td)
            # Create only the first artifact
            os.makedirs(os.path.join(td, ".fat", "pdf"), exist_ok=True)
            with open(os.path.join(td, ".fat", "pdf", "exists.md"), "w") as f:
                f.write("# exists\n")
            r = _run_engine(["resume", "verify"], td)
            assert r.returncode == 1, "verify should exit 1 when artifacts missing"
            all_out = r.stdout + "\n" + r.stderr
            assert_in("missing artifact detect", "missing", all_out)
    test("3b: missing artifact -> error exit 1", _3b)

    # 3c. No state at all -> error
    def _3c():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            r = _run_engine(["resume", "verify"], td)
            assert r.returncode == 1
            assert_in("no state error", "no state", (r.stdout + r.stderr))
    test("3c: no state -> error", _3c)


# ==============================================================================
# Test 4: resume summary
# ==============================================================================

def test_resume_summary():
    print("\n[Test 4] resume summary")

    # 4a. Full history summary
    def _4a():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(
                stage="plan", phase="plan_convergence",
                phase_history=PHASE_ORDER[:15],
                resume_count=2, session_num=3,
            ), td)
            r = _run_engine(["resume", "summary"], td)
            assert r.returncode == 0, f"summary failed: {r.stderr}"
            out = r.stdout
            assert_in("header", "=== PDF Resume Context ===", out)
            assert_in("task slug", "Task: test-task", out)
            assert_in("stage", "Stage: plan", out)
            assert_in("checkpoint phase", "Checkpoint Phase:", out)
            assert_in("resume count", "Resume Count: 2", out)
            assert_in("completed phases", "Completed Phases:", out)
            assert_in("next phase", "Next Phase:", out)
            assert_in("key artifacts", "Key Artifacts:", out)
    test("4a: full history summary has expected sections", _4a)

    # 4b. Empty history
    def _4b():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="plan", phase="precheck", phase_history=[]), td)
            r = _run_engine(["resume", "summary"], td)
            assert r.returncode == 0
            out = r.stdout
            assert_in("completed phases", "Completed Phases: none", out)
    test("4b: empty history summary", _4b)

    # 4c. No state -> error
    def _4c():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            r = _run_engine(["resume", "summary"], td)
            assert r.returncode == 1
    test("4c: no state -> error exit 1", _4c)

    # 4d. Done stage with full history
    def _4d():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="done", phase="done", phase_history=PHASE_ORDER[:-1]), td)
            r = _run_engine(["resume", "summary"], td)
            assert r.returncode == 0
            out = r.stdout
            assert_in("next phase end", "Next Phase: end", out)
    test("4d: done stage -> Next Phase: end", _4d)


# ==============================================================================
# Test 5: resume-state output format and key fields
# ==============================================================================

def test_resume_state_format():
    print("\n[Test 5] resume-state output format")

    # 5a. All key=value fields present
    def _5a():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="plan", phase="design", phase_history=PHASE_ORDER[:13]), td)
            r = _run_engine(["resume-state"], td)
            parsed = _parse_resume_state_output(r.stdout)
            required_keys = {"phase", "next_action", "resume_count", "git_head",
                             "git_drift", "phase_history_count", "artifact_count"}
            missing = required_keys - set(parsed.keys())
            assert_eq("missing keys", missing, set())
    test("5a: all expected key=value fields present", _5a)

    # 5b. phase_history_count matches history
    def _5b():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(stage="plan", phase="design", phase_history=PHASE_ORDER[:13]), td)
            r = _run_engine(["resume-state"], td)
            parsed = _parse_resume_state_output(r.stdout)
            assert_eq("history count", int(parsed.get("phase_history_count", "0")), 13)
    test("5b: phase_history_count matches", _5b)

    # 5c. artifact_count matches
    def _5c():
        with tempfile.TemporaryDirectory(prefix="pdf_test_") as td:
            _write_state(make_state(
                stage="plan", phase="design", phase_history=PHASE_ORDER[:13],
                artifacts=[
                    {"path": "a.md", "stage": "plan", "role": "a", "id": 1},
                    {"path": "b.md", "stage": "plan", "role": "b", "id": 2},
                    {"path": "c.md", "stage": "plan", "role": "c", "id": 3},
                ],
            ), td)
            r = _run_engine(["resume-state"], td)
            parsed = _parse_resume_state_output(r.stdout)
            assert_eq("artifact count", int(parsed.get("artifact_count", "0")), 3)
    test("5c: artifact_count matches", _5c)


# ==============================================================================
# Main
# ==============================================================================

def main():
    print("=" * 66)
    print("  PDF Multi-Session Resume — Smoke Test (Engine CLI)")
    print("=" * 66)
    print()
    print(f"  Engine: {ENGINE_PATH}")
    print(f"  CWD:    {PROJECT_ROOT}")
    print()

    test_checkpoint_update()
    test_resume_state()
    test_resume_verify()
    test_resume_summary()
    test_resume_state_format()

    print()
    print("-" * 66)
    total = _passed + _failed
    print(f"  Results: {_passed}/{total} passed, {_failed}/{total} failed")
    print("-" * 66)

    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
