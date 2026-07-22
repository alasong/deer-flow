"""Session and workspace management commands for pdf-engine."""
import json
import os
import shutil
import subprocess
import sys

from pdf_engine_shared import (
    BASE_STATE,
    SESSIONS_DIR,
    WORKSPACE_FILE,
    PDFContext,
    SessionManager,
    generate_session_id,
    _load_state,
    _load_state_with_migration,
    _save_state,
    _timestamp,
    _load_workspace_registry,
    _save_workspace_registry,
    _write_active_session,
    _read_active_session,
    _clear_active_session,
)


# ── Session Commands ──────────────────────────────────


def cmd_session_list():
    """List all sessions. Usage: pdf-engine.py session list [--active]"""
    sessions = SessionManager.list_sessions()
    if not sessions:
        print("(no sessions)")
        return

    active_only = "--active" in sys.argv
    print(f"{'ID':<28} {'Slug':<24} {'Task':<20} {'Stage':<10} {'Created':<14}")
    current_ctx = PDFContext.get_default()
    for sid in sessions:
        state_path = SessionManager.state_file(sid)
        if not os.path.exists(state_path):
            continue
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception:
            continue
        stage = state.get("stage", "?")
        if active_only and stage == "done":
            continue
        slug = state.get("session_slug", "")[:24]
        task = state.get("task_slug", "")[:20]
        created = state.get("multi_session", {}).get("session_created_at", "")[:10]
        marker = "*" if current_ctx.session_id == sid else " "
        print(f"{marker}{sid:<27} {slug:<24} {task:<20} {stage:<10} {created:<14}")


def cmd_session_switch(session_id):
    """Switch to a session by ID. Usage: pdf-engine.py session switch <id>"""
    sid = session_id
    state_path = SessionManager.state_file(sid)
    if not os.path.exists(state_path):
        print(f"ERROR: session '{sid}' not found", file=sys.stderr)
        cmd_session_list()
        return
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception as e:
        print(f"ERROR: session '{sid}' has invalid state file: {e}", file=sys.stderr)
        return

    stored_root = state.get("project_root", os.getcwd())
    new_ctx = PDFContext(project_root=stored_root, session_id=sid)
    PDFContext.set_default(new_ctx)
    _write_active_session(sid)

    task = state.get("task_slug", "?")
    stage = state.get("stage", "?")
    slug = state.get("session_slug", "")
    slug_str = f" ({slug})" if slug else ""
    print(f"Switched to session {sid}{slug_str}: {task} (stage={stage}, project_root={stored_root})")


def cmd_session_current():
    """Show current session info. Usage: pdf-engine.py session current"""
    ctx = PDFContext.get_default()
    if not ctx.is_session_mode:
        print("(no active session — using project-local state)")
        return

    state = _load_state_with_migration(ctx=ctx)
    if state is None:
        print(f"Session: {ctx.session_id}")
        print(f"  (state file not found: {ctx.state_file})")
        return

    print(f"Session ID:      {ctx.session_id}")
    print(f"Session Slug:    {state.get('session_slug', '-')}")
    print(f"Task:            {state.get('task_slug', '?')}")
    print(f"Stage:           {state.get('stage', '?')}")
    print(f"Project Root:    {ctx.project_root}")
    print(f"State File:      {ctx.state_file}")
    print(f"Artifact Dir:    {ctx.artifact_dir}")
    artifacts = state.get("artifacts", [])
    print(f"Artifacts:       {len(artifacts)}")
    cp = state.get("checkpoint", {})
    phase_history = cp.get("phase_history", [])
    print(f"Phase History:   {len(phase_history)} phases")
    sess = state.get("session", {})
    print(f"Resume Count:    {sess.get('resume_count', 0)}")
    print(f"Schema Version:  {state.get('schema_version', '?')}")


def cmd_session_delete(session_id):
    """Delete a session. Usage: pdf-engine.py session delete <id> [--force] [--dry-run]"""
    sid = session_id
    session_path = SessionManager.session_path(sid)
    if not os.path.exists(session_path):
        print(f"ERROR: session '{sid}' not found", file=sys.stderr)
        return

    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    total_size = 0
    file_count = 0
    for root, dirs, files in os.walk(session_path):
        for f in files:
            fp = os.path.join(root, f)
            total_size += os.path.getsize(fp)
            file_count += 1

    print(f"Session: {sid}")
    print(f"  Path: {session_path}")
    print(f"  Files: {file_count} ({total_size} bytes)")

    if dry_run:
        print("  (dry-run — not deleted)")
        return

    if not force:
        confirm = input("Are you sure? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    shutil.rmtree(session_path)
    active_id = _read_active_session()
    if active_id == sid:
        _clear_active_session()
    print(f"OK: session '{sid}' deleted")


def cmd_session_create(task_slug):
    """Create and switch to a new session.
    Usage: pdf-engine.py session create <task-slug> [--project-root <path>] [--session-slug <slug>]
    """
    raw_args = sys.argv
    session_slug = None
    project_root = os.getcwd()
    for i, a in enumerate(raw_args):
        if a == "--session-slug" and i + 1 < len(raw_args):
            session_slug = raw_args[i + 1]
        if a == "--project-root" and i + 1 < len(raw_args):
            project_root = raw_args[i + 1]

    session_id = generate_session_id(task_slug, session_slug)
    SessionManager.create_session(session_id, project_root, task_slug)

    state = BASE_STATE.copy()
    state["task_slug"] = task_slug
    if session_slug:
        state["session_slug"] = session_slug
    state["session_id"] = session_id
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

    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            state["git_head"] = result.stdout.strip()
    except Exception:
        pass

    ctx = PDFContext(project_root=project_root, session_id=session_id)
    _save_state(state, ctx=ctx)
    PDFContext.set_default(ctx)
    _write_active_session(session_id)

    slug_str = f" ({session_slug})" if session_slug else ""
    print(f"OK: created session {session_id}{slug_str} for '{task_slug}'")
    print(f"  State: {ctx.state_file}")


# ── Workspace Commands ────────────────────────────────


def cmd_workspace_add(name):
    """Add workspace. Usage: pdf-engine.py workspace add <name> [--root <path>] [--tag <tag>]"""
    raw_args = sys.argv
    root_path = os.getcwd()
    tags = []
    for i, a in enumerate(raw_args):
        if a == "--root" and i + 1 < len(raw_args):
            root_path = raw_args[i + 1]
        if a == "--tag" and i + 1 < len(raw_args):
            tags.append(raw_args[i + 1])

    if not os.path.isdir(root_path):
        print(f"ERROR: root path does not exist: {root_path}", file=sys.stderr)
        return

    registry = _load_workspace_registry()
    if name in registry.get("workspaces", {}):
        existing = registry["workspaces"][name]
        print(f"WARNING: workspace '{name}' already exists (root={existing.get('path')})", file=sys.stderr)
        return

    registry.setdefault("workspaces", {})[name] = {
        "path": root_path,
        "added_at": _timestamp(),
        "tags": tags,
        "last_used": _timestamp(),
    }
    _save_workspace_registry(registry)
    tag_str = f" tags={tags}" if tags else ""
    print(f"OK: workspace '{name}' added (root={root_path}){tag_str}")


def cmd_workspace_remove(name):
    """Remove workspace. Usage: pdf-engine.py workspace remove <name>"""
    registry = _load_workspace_registry()
    if name not in registry.get("workspaces", {}):
        print(f"ERROR: workspace '{name}' not found", file=sys.stderr)
        return

    force = "--force" in sys.argv
    if not force:
        confirm = input(f"Remove workspace '{name}'? (sessions will be preserved) [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    del registry["workspaces"][name]
    if registry.get("current") == name:
        registry["current"] = None
    _save_workspace_registry(registry)
    print(f"OK: workspace '{name}' removed")


def cmd_workspace_list():
    """List workspaces. Usage: pdf-engine.py workspace list [--tag <tag>]"""
    raw_args = sys.argv
    tag_filter = None
    for i, a in enumerate(raw_args):
        if a == "--tag" and i + 1 < len(raw_args):
            tag_filter = raw_args[i + 1]

    registry = _load_workspace_registry()
    workspaces = registry.get("workspaces", {})
    current = registry.get("current")

    if not workspaces:
        print("(no workspaces registered)")
        return

    cwd = os.getcwd()
    print(f"{'Name':<24} {'Root':<48} {'Tags':<20} {'Last Used':<14}")
    for name, ws in sorted(workspaces.items()):
        if tag_filter and tag_filter not in ws.get("tags", []):
            continue
        root = ws.get("path", "?")
        tags = ",".join(ws.get("tags", []))[:20]
        last = (ws.get("last_used") or "")[:10]
        is_cwd = os.path.exists(root) and os.path.samefile(root, cwd)
        marker = "*" if current == name else ("~" if is_cwd else " ")
        print(f"{marker}{name:<23} {root:<48} {tags:<20} {last:<14}")


def cmd_workspace_switch(name):
    """Switch to a workspace. Usage: pdf-engine.py workspace switch <name>"""
    registry = _load_workspace_registry()
    ws = registry.get("workspaces", {}).get(name)
    if ws is None:
        print(f"ERROR: workspace '{name}' not found", file=sys.stderr)
        return

    root_path = ws.get("path")
    if not os.path.isdir(root_path):
        print(f"ERROR: workspace root not found: {root_path}", file=sys.stderr)
        return

    registry["current"] = name
    ws["last_used"] = _timestamp()
    _save_workspace_registry(registry)

    ctx = PDFContext(project_root=root_path)
    PDFContext.set_default(ctx)
    print(f"Switched to workspace '{name}': {root_path}")
