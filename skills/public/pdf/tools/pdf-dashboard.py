#!/usr/bin/env python3
"""PDD Dashboard — terminal summary of current state and recent findings."""
import json, os, sys, subprocess
from datetime import datetime

LEGACY_STATE_DIR = os.path.join(os.getcwd(), ".fat", "pdf")
LEGACY_STATE_FILE = os.path.join(LEGACY_STATE_DIR, ".pdf_state.json")
CYCLE_DB = os.path.join(os.path.expanduser('~'), '.fat', 'pdf', 'cycle-log.db')


def find_project_root():
    """Find project root via git, falling back to CWD."""
    try:
        root = subprocess.check_output(
            ['git', 'rev-parse', '--show-toplevel'],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return root
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.getcwd()


def load_state():
    """Load state from --session flag, sessions dir, or legacy path."""
    # Check for --session flag
    sessions_dir_global = os.path.expanduser("~/.fat/pdf/sessions")
    sessions_dir_local = os.path.join(os.getcwd(), ".fat", "pdf", "sessions")
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--session" and i + 1 < len(sys.argv):
            sid = sys.argv[i + 1]
            # Try project-local first, then global
            for sd in (sessions_dir_local, sessions_dir_global):
                session_path = os.path.join(sd, sid, "state.json")
                if os.path.exists(session_path):
                    with open(session_path) as f:
                        state = json.load(f)
                    state["_session_id"] = sid
                    state["_session_path"] = session_path
                    return state
            print(f"ERROR: session '{sid}' not found", file=sys.stderr)
            sys.exit(1)

    # Try legacy project-local state
    if os.path.exists(LEGACY_STATE_FILE):
        try:
            with open(LEGACY_STATE_FILE) as f:
                state = json.load(f)
            state["_state_file"] = LEGACY_STATE_FILE
            return state
        except Exception:
            return None

    # Search sessions dir for a matching project
    project_root = os.path.realpath(find_project_root())
    search_dirs = [sessions_dir_local, sessions_dir_global]
    for sd in search_dirs:
        if not os.path.exists(sd):
            continue
        for sid in sorted(os.listdir(sd)):
            state_path = os.path.join(sd, sid, "state.json")
            if os.path.exists(state_path):
                try:
                    with open(state_path) as f:
                        state = json.load(f)
                    if os.path.realpath(state.get("project_root", "")) == project_root:
                        state["_session_id"] = sid
                        state["_session_path"] = state_path
                        return state
                except Exception:
                    continue
    return None


def state_summary(state):
    return {
        'task': state.get('task_slug', '?'),
        'stage': state.get('stage', '?'),
        'round': state.get('round', 0),
        'remediation': state.get('remediation_loop', 0),
        'p1_found': state.get('check_findings_p1', 0),
        'artifacts': state.get('artifacts', []),
    }


def recent_findings():
    try:
        import sqlite3
        if not os.path.exists(CYCLE_DB):
            return []
        with sqlite3.connect(CYCLE_DB) as conn:
            rows = conn.execute(
                "SELECT c.completed, c.task_slug, f.dimension, f.severity, f.description "
                "FROM findings f JOIN cycles c ON f.cycle_id=c.id "
                "ORDER BY c.completed DESC LIMIT 10"
            ).fetchall()
        return [{'date': r[0], 'task': r[1], 'dim': r[2], 'severity': r[3], 'desc': r[4][:60]} for r in rows]
    except (sqlite3.OperationalError, sqlite3.DatabaseError, ImportError):
        return []


def main():
    print("=" * 50)
    print("  PDD Dashboard")
    print("=" * 50)

    state = load_state()
    if state is None:
        print(f"\n  No active PDF task found in {os.getcwd()}")
        print(f"  Use --session <id> or 'pdf-engine.py init' to start.")
        return

    sid = state.get("_session_id")
    sfile = state.get("_session_path") or state.get("_state_file", "?")
    project_root = state.get("project_root", find_project_root())

    print(f"\n  Active: {state.get('task_slug', '?')} | Stage: {state.get('stage', '?')} | Round: {state.get('round', 1)}")
    if sid:
        print(f"  Session: {sid}")
    print(f"  Project: {project_root}")
    print(f"  P1 found: {state.get('check_findings_p1', 0)} | Remediation: {state.get('remediation_loop', 0)}")
    artifacts = state.get('artifacts', [])
    print(f"  Artifacts: {', '.join(a.get('path', '?') for a in artifacts) if artifacts else 'none'}")

    findings = recent_findings()
    if findings:
        print(f"\n  Recent findings (last 10):")
        print(f"  {'Date':<12} {'Task':<20} {'Dim':<16} {'Sev':<5} Description")
        for f in findings:
            print(f"  {f['date']:<12} {f['task']:<20} {f['dim']:<16} {f['severity']:<5} {f['desc']}")


if __name__ == '__main__':
    main()
