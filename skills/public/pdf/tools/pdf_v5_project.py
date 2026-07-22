#!/usr/bin/env python3
"""PDF v5.0 ProjectStateManager — project-level persistent state.

Architecture 1: project-state.json as an aggregate read-only cache of session state.
    Primary (source of truth): session state.json files.
    Aggregate view: project-state.json (reconstructable from sessions).

Only 3 write triggers:
    A: project init       — first creation
    B: project add-task   — register new task in active_tasks
    C: project close-task — move task to completed_tasks (all sessions done)

This module is decoupled from pdf-engine.py. It provides a self-contained
ProjectStateManager class plus helper functions for session directory traversal.

Usage (by main agent, not CLI):
    from pdf_v5_project import ProjectStateManager
    summary = ProjectStateManager.init(project_root)
    result = ProjectStateManager.add_task(project_root, "my-task", "My task desc")
"""

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone


# === Schema ===

PROJECT_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer"},
        "project_id": {"type": "string"},
        "project_name": {"type": "string"},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
        "active_tasks": {"type": "array"},
        "completed_tasks": {"type": "array"},
        "sessions": {"type": "array"},
    },
    "required": [
        "schema_version", "project_id", "project_name",
        "created_at", "updated_at",
        "active_tasks", "completed_tasks", "sessions",
    ],
}

ACTIVE_TASK_SCHEMA = {
    "task_slug": str,
    "task_desc": str,
    "channel": str,
    "stage": str,
    "plan_version": int,
    "started_at": str,
    "session_ids": list,
}

COMPLETED_TASK_SCHEMA = {
    "task_slug": str,
    "task_desc": str,
    "channel": str,
    "completed_at": str,
    "final_plan_version": int,
    "session_ids": list,
}

SESSION_ENTRY_SCHEMA = {
    "session_id": str,
    "session_slug": str,
    "task_slug": str,
    "stage": str,
    "status": str,
    "last_updated": str,
}

# Default channels per risk profile
VALID_CHANNELS = ("lite", "standard", "full")

# Session state.json field paths for reading (session state is the source of truth)
_SESSION_STATE_FIELDS = {
    "session_id": ("session_id", None),
    "session_slug": ("session_slug", "main"),
    "task_slug": ("task_slug", None),
    "stage": ("stage", None),
    "project_id": ("multi_session", "project_id"),
    "project_root": ("multi_session", "project_root"),
    "session_created_at": ("multi_session", "session_created_at"),
    "session_updated_at": ("multi_session", "session_updated_at"),
}

# === Helpers (public) ===


def get_session_state_dir(project_root):
    """Return .fat/pdf/sessions/ path for a project.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        str: Path to the sessions directory.
    """
    return os.path.join(project_root, ".fat", "pdf", "sessions")


def list_session_ids(project_root):
    """List all session IDs (directory names) under sessions/.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        list: Sorted list of session directory names. Empty list if sessions/ does not exist.
    """
    sessions_dir = get_session_state_dir(project_root)
    if not os.path.isdir(sessions_dir):
        return []
    return sorted([
        d for d in os.listdir(sessions_dir)
        if os.path.isdir(os.path.join(sessions_dir, d))
    ])


def read_session_state(project_root, session_id):
    """Read a session's state.json file.

    Args:
        project_root: Absolute path to the project root.
        session_id: Session ID (directory name under sessions/).

    Returns:
        dict: Parsed JSON content, or a dict with "error" key on failure.
    """
    path = os.path.join(get_session_state_dir(project_root), session_id, "state.json")
    if not os.path.isfile(path):
        return {"error": f"session state file not found: {path}"}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        return {"error": f"invalid JSON in session state: {e}"}
    except IOError as e:
        return {"error": f"cannot read session state: {e}"}


def _timestamp():
    """Return current UTC ISO 8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _short_uuid():
    """Generate a short 12-char hex project ID.

    Using uuid4().hex[:12] yields ~2^48 possible values — sufficient
    for single-user project identification collision avoidance.
    """
    return uuid.uuid4().hex[:12]


def _deep_get(d, keys, default=None):
    """Safely traverse nested dicts to get a value.

    Args:
        d: The dict to traverse.
        keys: A key or list of keys (nested path).
        default: Value to return if any key in the path is missing.

    Returns:
        The value at the nested path, or default.
    """
    if not isinstance(keys, (list, tuple)):
        keys = [keys]
    current = d
    for k in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(k)
        if current is None:
            return default
    return current


def _sanitize_slug(slug):
    """Sanitize a task slug to lowercase alphanumeric with hyphens.

    Replaces whitespace and underscores with hyphens, removes non-alphanumeric
    characters (except hyphens), and collapses repeated hyphens.
    """
    s = slug.lower().strip()
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'[^a-z0-9-]', '', s)
    s = re.sub(r'-+', '-', s)
    s = s.strip('-')
    return s or "untitled"


# === ProjectStateManager ===


class ProjectStateManager:
    """Manage project-state.json as an aggregate view of session states.

    All methods return printable strings (not dicts). Error conditions
    are reported as clear text messages, not exceptions.

    Thread safety: Not implemented. Single-user scenario assumed.
    If multi-process writes become necessary, consider fcntl.flock:
        import fcntl
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            data = json.load(f)
            # ... mutate ...
            f.seek(0)
            json.dump(data, f)
            f.truncate()
            fcntl.flock(f, fcntl.LOCK_UN)
    """

    STATE_FILE = ".fat/pdf/project-state.json"  # relative to project_root

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _state_path(project_root):
        """Return the absolute path to project-state.json.

        Args:
            project_root: Absolute path to the project root.

        Returns:
            str: Absolute path to project-state.json.
        """
        return os.path.join(project_root, ProjectStateManager.STATE_FILE)

    @staticmethod
    def _load(project_root):
        """Load project-state.json from disk.

        Returns the parsed dict, or a minimal default structure if the file
        does not exist or is corrupted. Does NOT raise on missing/corrupt files
        — the caller decides what to do with a default structure.

        Args:
            project_root: Absolute path to the project root.

        Returns:
            dict: Parsed state, or default structure with "error" key set on failure.
        """
        path = ProjectStateManager._state_path(project_root)
        if not os.path.isfile(path):
            return {"error": f"project-state.json not found at {path}"}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"project-state.json corrupted: {e}"}
        except IOError as e:
            return {"error": f"cannot read project-state.json: {e}"}

    @staticmethod
    def _save(project_root, data):
        """Save project-state.json to disk.

        Automatically creates .fat/pdf/ directory if it does not exist.
        Updates `updated_at` to current time.

        Args:
            project_root: Absolute path to the project root.
            data: Dict to serialize as JSON.
        """
        data["updated_at"] = _timestamp()
        pdf_dir = os.path.join(project_root, ".fat", "pdf")
        os.makedirs(pdf_dir, exist_ok=True)

        path = ProjectStateManager._state_path(project_root)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _default_state(project_root, project_name):
        """Create a default (empty) project state structure.

        Args:
            project_root: Absolute path to the project root.
            project_name: Display name for the project.

        Returns:
            dict: A new project state with empty task/session arrays.
        """
        now = _timestamp()
        return {
            "schema_version": 1,
            "project_id": _short_uuid(),
            "project_name": project_name or os.path.basename(os.path.normpath(project_root)),
            "created_at": now,
            "updated_at": now,
            "active_tasks": [],
            "completed_tasks": [],
            "sessions": [],
        }

    @staticmethod
    def _read_session_state_safe(project_root, session_id):
        """Read a session state with error wrapping.

        Returns {"error": ...} dict on any failure, or the full session state dict.
        """
        state = read_session_state(project_root, session_id)
        # read_session_state already returns {"error": msg} on failure
        return state

    @staticmethod
    def _extract_session_entry(state, session_id):
        """Extract a project-state sessions[] entry from a loaded session state dict.

        Fields:
            session_id, session_slug, task_slug, stage, status, last_updated

        Args:
            state: Parsed session state.json dict.
            session_id: The session ID (used as fallback).

        Returns:
            dict with keys session_id, session_slug, task_slug, stage, status, last_updated.
            On error (state has "error" key), returns a minimal entry with status="error".
        """
        if "error" in state:
            return {
                "session_id": session_id,
                "session_slug": "unknown",
                "task_slug": "unknown",
                "stage": "unknown",
                "status": "error",
                "last_updated": _timestamp(),
            }

        # Extract from session state — use multi_session block or top-level fields
        session_slug = (
            state.get("session_slug")
            or _deep_get(state, ("multi_session", "session_slug"), "")
            or "main"
        )
        task_slug = state.get("task_slug") or _deep_get(state, ("multi_session", "task_slug"), "unknown")
        stage = state.get("stage", "unknown")

        # Determine status: check if stage is "done"
        status = "done" if stage == "done" else "active"

        last_updated = (
            _deep_get(state, ("multi_session", "session_updated_at"), "")
            or _deep_get(state, ("multi_session", "session_created_at"), "")
            or ""
        )

        return {
            "session_id": session_id,
            "session_slug": session_slug,
            "task_slug": task_slug,
            "stage": stage,
            "status": status,
            "last_updated": last_updated,
        }

    # ------------------------------------------------------------------
    # 3 write triggers
    # ------------------------------------------------------------------

    @staticmethod
    def init(project_root, project_name=None, force=False):
        """Create project-state.json.

        If the file already exists and force=False, silently skip creation
        and return a summary of the existing state. Use force=True to
        overwrite with a fresh project ID (discards existing task/session info).

        Args:
            project_root: Absolute path to the project root.
            project_name: Optional display name (defaults to directory basename).
            force: If True, overwrite existing project-state.json.

        Returns:
            str: JSON summary of the resulting project state.
        """
        path = ProjectStateManager._state_path(project_root)
        exists = os.path.isfile(path)
        pdf_dir = os.path.join(project_root, ".fat", "pdf")
        os.makedirs(pdf_dir, exist_ok=True)

        if exists and not force:
            # Silently skip — return existing state summary
            existing = ProjectStateManager._load(project_root)
            if "error" not in existing:
                return json.dumps({
                    "action": "skipped",
                    "reason": "already exists",
                    "project_id": existing.get("project_id"),
                    "project_name": existing.get("project_name"),
                    "active_tasks": len(existing.get("active_tasks", [])),
                    "completed_tasks": len(existing.get("completed_tasks", [])),
                }, indent=2, ensure_ascii=False)

        state = ProjectStateManager._default_state(project_root, project_name)
        ProjectStateManager._save(project_root, state)

        return json.dumps({
            "action": "created" if not force else "recreated",
            "project_id": state["project_id"],
            "project_name": state["project_name"],
            "project_root": project_root,
        }, indent=2, ensure_ascii=False)

    @staticmethod
    def add_task(project_root, task_slug, task_desc, channel="standard"):
        """Register a new task in active_tasks.

        A task_slug must be unique across active_tasks. Duplicate slugs
        (including matching completed task slugs) result in an error message.
        Channel must be one of: lite, standard, full.

        Args:
            project_root: Absolute path to the project root.
            task_slug: Short kebab-case identifier for the task.
            task_desc: Human-readable description of the task.
            channel: Channel name (lite/standard/full). Default: standard.

        Returns:
            str: Success confirmation or error message.
        """
        project_root = os.path.normpath(project_root)
        slug = _sanitize_slug(task_slug)

        # Validate channel
        if channel not in VALID_CHANNELS:
            return f"ERROR: invalid channel '{channel}'. Must be one of: {', '.join(VALID_CHANNELS)}"

        state = ProjectStateManager._load(project_root)
        if "error" in state:
            return f"ERROR: {state['error']}"

        # Check duplicate slug across active AND completed tasks
        # assumption: a slug must be globally unique across the project's lifetime
        for task in state.get("active_tasks", []):
            if task["task_slug"] == slug:
                return (f"ERROR: task slug '{slug}' already exists in active_tasks "
                        f"(desc: {task.get('task_desc', 'N/A')})")

        for task in state.get("completed_tasks", []):
            if task["task_slug"] == slug:
                return (f"ERROR: task slug '{slug}' already exists in completed_tasks "
                        f"(desc: {task.get('task_desc', 'N/A')}). "
                        f"Use a different slug to avoid confusion.")

        task_entry = {
            "task_slug": slug,
            "task_desc": task_desc,
            "channel": channel,
            "stage": "plan",
            "plan_version": 1,
            "started_at": _timestamp(),
            "session_ids": [],
        }

        state.setdefault("active_tasks", []).append(task_entry)
        ProjectStateManager._save(project_root, state)

        return json.dumps({
            "action": "task_added",
            "task_slug": slug,
            "channel": channel,
            "active_tasks_count": len(state["active_tasks"]),
        }, indent=2, ensure_ascii=False)

    @staticmethod
    def close_task(project_root, task_slug):
        """Move a task from active_tasks to completed_tasks.

        A task can only be closed when ALL associated sessions have stage="done".
        If any session is still active (stage != "done"), output a warning and
        do NOT move the task.

        Args:
            project_root: Absolute path to the project root.
            task_slug: The task slug to close.

        Returns:
            str: Success confirmation, warning, or error message.
        """
        project_root = os.path.normpath(project_root)
        slug = _sanitize_slug(task_slug)

        state = ProjectStateManager._load(project_root)
        if "error" in state:
            return f"ERROR: {state['error']}"

        # Find the task in active_tasks
        tasks = state.get("active_tasks", [])
        task_idx = None
        task_entry = None
        for i, t in enumerate(tasks):
            if t["task_slug"] == slug:
                task_idx = i
                task_entry = t
                break

        if task_entry is None:
            # Check if already completed
            for t in state.get("completed_tasks", []):
                if t["task_slug"] == slug:
                    return f"WARNING: task '{slug}' is already in completed_tasks (closed at {t.get('completed_at', 'N/A')})"
            return f"ERROR: task slug '{slug}' not found in active_tasks"

        # Check all associated sessions for done status
        session_ids = task_entry.get("session_ids", [])
        active_sessions = []

        for sid in session_ids:
            session_state = ProjectStateManager._read_session_state_safe(project_root, sid)
            if "error" not in session_state:
                stage = session_state.get("stage", "")
                if stage != "done":
                    active_sessions.append(sid)
            # If session state file is missing, treat as non-blocking warning
            # confidence: medium — a missing session file could indicate cleanup;
            # we proceed but warn so the user can investigate.

        if active_sessions:
            return (f"WARNING: task '{slug}' has {len(active_sessions)} active session(s) "
                    f"that are not done: {', '.join(active_sessions)}. "
                    f"Close them before moving to completed_tasks.")

        # Also scan all sessions in the project to find any other sessions
        # referencing this task slug that might not be in session_ids.
        # assumption: session_ids covers all sessions for this task; but we
        # cross-check via project-state sessions[] entries for safety.
        all_sessions = state.get("sessions", [])
        for sess in all_sessions:
            if sess.get("task_slug") == slug and sess.get("session_id") not in session_ids:
                if sess.get("status") != "done" and sess.get("stage") != "done":
                    return (f"WARNING: orphan session '{sess.get('session_id')}' references task '{slug}' "
                            f"but is not in the task's session_ids list. "
                            f"Run 'project rebuild' to synchronize.")

        # Move to completed_tasks
        completed_entry = {
            "task_slug": task_entry["task_slug"],
            "task_desc": task_entry.get("task_desc", ""),
            "channel": task_entry.get("channel", "standard"),
            "completed_at": _timestamp(),
            "final_plan_version": task_entry.get("plan_version", 1),
            "session_ids": session_ids,
        }

        state.setdefault("completed_tasks", []).append(completed_entry)
        # Remove from active_tasks
        state["active_tasks"].pop(task_idx)
        ProjectStateManager._save(project_root, state)

        return json.dumps({
            "action": "task_closed",
            "task_slug": slug,
            "active_tasks_remaining": len(state["active_tasks"]),
            "completed_tasks_total": len(state["completed_tasks"]),
        }, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Read-only queries
    # ------------------------------------------------------------------

    @staticmethod
    def status(project_root):
        """Display a project-level overview.

        Shows: project_id, project_name, created_at, active task count,
        completed task count, session count.

        Args:
            project_root: Absolute path to the project root.

        Returns:
            str: Human-readable status report.
        """
        project_root = os.path.normpath(project_root)
        state = ProjectStateManager._load(project_root)
        if "error" in state:
            return f"ERROR: {state['error']}"

        lines = []
        lines.append(f"Project: {state.get('project_name', 'N/A')}")
        lines.append(f"  Project ID:    {state.get('project_id', 'N/A')}")
        lines.append(f"  Created:       {state.get('created_at', 'N/A')}")
        lines.append(f"  Updated:       {state.get('updated_at', 'N/A')}")
        lines.append(f"")

        active = state.get("active_tasks", [])
        completed = state.get("completed_tasks", [])
        sessions = state.get("sessions", [])

        lines.append(f"Active Tasks:   {len(active)}")
        for t in active:
            sid_count = len(t.get("session_ids", []))
            lines.append(f"  - {t['task_slug']}: {t.get('task_desc', 'N/A')} "
                         f"[{t.get('channel', 'N/A')}] stage={t.get('stage', 'N/A')} "
                         f"plan_v{t.get('plan_version', 1)} sessions={sid_count}")

        lines.append(f"Completed Tasks: {len(completed)}")
        for t in completed:
            sid_count = len(t.get("session_ids", []))
            lines.append(f"  - {t['task_slug']}: {t.get('task_desc', 'N/A')} "
                         f"[{t.get('channel', 'N/A')}] closed={t.get('completed_at', 'N/A')[:19]} "
                         f"sessions={sid_count}")

        lines.append(f"Sessions:       {len(sessions)}")
        # Show sessions grouped by status
        active_sessions = [s for s in sessions if s.get("status") == "active"]
        done_sessions = [s for s in sessions if s.get("status") == "done"]
        if active_sessions:
            lines.append(f"  Active ({len(active_sessions)}):")
            for s in active_sessions:
                lines.append(f"    - {s['session_id']} [{s.get('task_slug', 'N/A')}] stage={s.get('stage', 'N/A')}")
        if done_sessions:
            lines.append(f"  Done ({len(done_sessions)}):")
            for s in done_sessions:
                lines.append(f"    - {s['session_id']} [{s.get('task_slug', 'N/A')}]")

        return "\n".join(lines)

    @staticmethod
    def task_status(project_root, task_slug):
        """Display details for a specific task.

        Shows task metadata, associated sessions (with status), and plan version.

        Args:
            project_root: Absolute path to the project root.
            task_slug: The task slug to inspect.

        Returns:
            str: Human-readable task detail report, or error if not found.
        """
        project_root = os.path.normpath(project_root)
        slug = _sanitize_slug(task_slug)
        state = ProjectStateManager._load(project_root)
        if "error" in state:
            return f"ERROR: {state['error']}"

        # Search in active first, then completed
        task = None
        location = None
        for t in state.get("active_tasks", []):
            if t["task_slug"] == slug:
                task = t
                location = "active"
                break
        if task is None:
            for t in state.get("completed_tasks", []):
                if t["task_slug"] == slug:
                    task = t
                    location = "completed"
                    break

        if task is None:
            return f"ERROR: task slug '{slug}' not found in active or completed tasks"

        lines = []
        lines.append(f"Task: {slug}")
        lines.append(f"  Description:   {task.get('task_desc', 'N/A')}")
        lines.append(f"  Status:        {location}")
        lines.append(f"  Channel:       {task.get('channel', 'N/A')}")

        if location == "active":
            lines.append(f"  Stage:         {task.get('stage', 'N/A')}")
            lines.append(f"  Plan Version:  {task.get('plan_version', 1)}")
            lines.append(f"  Started:       {task.get('started_at', 'N/A')}")
        else:
            lines.append(f"  Completed:     {task.get('completed_at', 'N/A')}")
            lines.append(f"  Final Plan:    v{task.get('final_plan_version', 1)}")

        session_ids = task.get("session_ids", [])
        lines.append(f"  Sessions:      {len(session_ids)}")
        # Cross-reference with project-state sessions[] for details
        all_sessions = {s["session_id"]: s for s in state.get("sessions", []) if s.get("session_id")}
        for sid in session_ids:
            sess = all_sessions.get(sid, {})
            status_str = sess.get("status", "unknown")
            stage_str = sess.get("stage", "unknown")
            lines.append(f"    - {sid} [{status_str}] stage={stage_str}")
            if sid not in all_sessions:
                lines.append(f"      (not registered in project-state sessions array)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Maintenance commands
    # ------------------------------------------------------------------

    @staticmethod
    def archive(project_root):
        """Archive completed tasks to a timestamped JSON file under .fat/pdf/archive/.

        Completed tasks are moved from project-state.json to
        .fat/pdf/archive/completed-<timestamp>.json. They are then removed
        from the main project-state.json (to keep the state file lean).

        Args:
            project_root: Absolute path to the project root.

        Returns:
            str: Confirmation message with count of archived tasks, or warning
                 if no completed tasks exist.
        """
        project_root = os.path.normpath(project_root)
        state = ProjectStateManager._load(project_root)
        if "error" in state:
            return f"ERROR: {state['error']}"

        completed = state.get("completed_tasks", [])
        if not completed:
            return "No completed tasks to archive."

        # Create archive directory
        archive_dir = os.path.join(project_root, ".fat", "pdf", "archive")
        os.makedirs(archive_dir, exist_ok=True)

        # Write archive file with timestamp
        ts = _timestamp().replace(":", "-")  # Windows-safe filename
        archive_path = os.path.join(archive_dir, f"completed-{ts}.json")
        archive_data = {
            "archived_at": _timestamp(),
            "schema_version": 1,
            "completed_tasks": completed,
        }
        with open(archive_path, "w") as f:
            json.dump(archive_data, f, indent=2, ensure_ascii=False)

        # Clear completed_tasks in main state
        state["completed_tasks"] = []
        ProjectStateManager._save(project_root, state)

        return json.dumps({
            "action": "archived",
            "count": len(completed),
            "archive_path": archive_path,
        }, indent=2, ensure_ascii=False)

    @staticmethod
    def rebuild(project_root):
        """Fully rebuild project-state.json from session state files.

        Scans .fat/pdf/sessions/<session_id>/state.json for every session,
        extracts task_slug, stage, project_id, and aggregates them into
        active_tasks, completed_tasks, and sessions arrays.

        If project-state.json already exists, its project_id and project_name
        are preserved. If it does not exist, a new project_id is generated.

        This is a full rebuild — any manual edits to project-state.json will
        be overwritten.

        Args:
            project_root: Absolute path to the project root.

        Returns:
            str: Rebuild summary JSON.
        """
        project_root = os.path.normpath(project_root)
        session_ids = list_session_ids(project_root)

        # Load existing state to preserve project identity
        existing = ProjectStateManager._load(project_root)
        if "error" not in existing:
            project_id = existing.get("project_id", _short_uuid())
            project_name = existing.get("project_name", os.path.basename(project_root))
        else:
            project_id = _short_uuid()
            project_name = os.path.basename(project_root)

        now = _timestamp()
        new_state = {
            "schema_version": 1,
            "project_id": project_id,
            "project_name": project_name,
            "created_at": existing.get("created_at", now) if "error" not in existing else now,
            "updated_at": now,
            "active_tasks": [],
            "completed_tasks": [],
            "sessions": [],
        }

        # Collect task info across all sessions
        # assumption: a task_slug may appear in multiple sessions; we need
        # to aggregate session_ids per task and determine if all are done.
        task_aggregator = {}  # task_slug -> {desc, channel, sessions, plan_versions, stages}

        for sid in session_ids:
            session_state = ProjectStateManager._read_session_state_safe(project_root, sid)
            if "error" in session_state:
                continue  # skip broken sessions

            task_slug = session_state.get("task_slug") or _deep_get(session_state, ("multi_session", "task_slug"), sid)
            stage = session_state.get("stage", "unknown")
            session_entry = ProjectStateManager._extract_session_entry(session_state, sid)

            new_state["sessions"].append(session_entry)

            # Aggregate into tasks
            if task_slug not in task_aggregator:
                # Extract description and channel from session state if available
                task_desc = session_state.get("task_description", "")
                channel = session_state.get("channel", "standard")
                # Fallback: look for stored plan_version
                plan_version = session_state.get("plan_version", 1) or _deep_get(session_state, ("multi_session", "plan_version"), 1)
                task_aggregator[task_slug] = {
                    "task_desc": task_desc or task_slug,
                    "channel": channel,
                    "plan_versions": [],
                    "stages": [],
                    "session_ids": [],
                }
            task_aggregator[task_slug]["session_ids"].append(sid)
            if stage:
                task_aggregator[task_slug]["stages"].append(stage)

        # Classify tasks as active or completed
        for task_slug, agg in task_aggregator.items():
            # A task is completed if ALL referencing sessions are stage="done"
            all_done = all(s == "done" for s in agg["stages"])

            if all_done:
                new_state["completed_tasks"].append({
                    "task_slug": task_slug,
                    "task_desc": agg.get("task_desc", ""),
                    "channel": agg.get("channel", "standard"),
                    "completed_at": now,
                    "final_plan_version": 1,
                    "session_ids": agg["session_ids"],
                })
            else:
                # Use the earliest stage found across sessions
                # Sort stages by PDCA order: plan < do < check < act < done
                stage_order = {"plan": 0, "do": 1, "check": 2, "act": 3, "done": 4}
                current_stage = min(
                    (s for s in agg["stages"] if s in stage_order),
                    key=lambda s: stage_order[s],
                    default="plan",
                )
                new_state["active_tasks"].append({
                    "task_slug": task_slug,
                    "task_desc": agg.get("task_desc", ""),
                    "channel": agg.get("channel", "standard"),
                    "stage": current_stage,
                    "plan_version": 1,
                    "started_at": now,
                    "session_ids": agg["session_ids"],
                })

        # Sort for deterministic output
        new_state["active_tasks"].sort(key=lambda t: t["task_slug"])
        new_state["completed_tasks"].sort(key=lambda t: t["task_slug"])
        new_state["sessions"].sort(key=lambda s: s["session_id"])

        ProjectStateManager._save(project_root, new_state)

        return json.dumps({
            "action": "rebuild",
            "project_id": project_id,
            "sessions_scanned": len(session_ids),
            "active_tasks_rebuilt": len(new_state["active_tasks"]),
            "completed_tasks_rebuilt": len(new_state["completed_tasks"]),
            "sessions_rebuilt": len(new_state["sessions"]),
        }, indent=2, ensure_ascii=False)

    @staticmethod
    def verify(project_root):
        """Verify consistency between project-state.json and session state files.

        Checks:
        1. All session entries in project-state.json have a corresponding
           state.json file on disk.
        2. All session state.json files on disk are represented in project-state.json.
        3. For each session, the task_slug and stage match between project-state
           and session state.
        4. session_ids in active_tasks/completed_tasks entries reference sessions
           that exist in the sessions[] array.

        Returns:
            str: "consistent" if no issues found, or a detailed diff report.
        """
        project_root = os.path.normpath(project_root)
        state = ProjectStateManager._load(project_root)
        if "error" in state:
            return f"ERROR: {state['error']}"

        issues = []

        # 1. Cross-reference projected sessions vs on-disk sessions
        projected_session_ids = set()
        for sess in state.get("sessions", []):
            sid = sess.get("session_id")
            if not sid:
                issues.append("Session entry missing session_id in project-state.json")
                continue
            projected_session_ids.add(sid)

            # Check that the session directory exists on disk
            session_dir = os.path.join(get_session_state_dir(project_root), sid)
            session_state_path = os.path.join(session_dir, "state.json")
            if not os.path.isfile(session_state_path):
                issues.append(f"Session '{sid}' in project-state but state.json not found on disk")
                continue

            # Check task_slug and stage consistency
            disk_state = read_session_state(project_root, sid)
            if "error" in disk_state:
                issues.append(f"Session '{sid}' state.json read error: {disk_state['error']}")
                continue

            disk_task_slug = disk_state.get("task_slug") or _deep_get(disk_state, ("multi_session", "task_slug"), "")
            disk_stage = disk_state.get("stage", "")

            if disk_task_slug and disk_task_slug != sess.get("task_slug"):
                issues.append(
                    f"Session '{sid}' task_slug mismatch: "
                    f"project-state says '{sess.get('task_slug')}', "
                    f"session state says '{disk_task_slug}'"
                )

            if disk_stage and disk_stage != sess.get("stage"):
                issues.append(
                    f"Session '{sid}' stage mismatch: "
                    f"project-state says '{sess.get('stage')}', "
                    f"session state says '{disk_stage}'"
                )

        # 2. On-disk sessions not in project-state
        on_disk_ids = set(list_session_ids(project_root))
        orphan_sessions = on_disk_ids - projected_session_ids
        for sid in sorted(orphan_sessions):
            issues.append(f"Session '{sid}' exists on disk but not registered in project-state.json")

        # 3. Verify session_ids in active_tasks and completed_tasks
        all_task_session_ids = set()
        for task_list_name in ("active_tasks", "completed_tasks"):
            for task in state.get(task_list_name, []):
                for sid in task.get("session_ids", []):
                    all_task_session_ids.add(sid)
                    if sid not in projected_session_ids:
                        issues.append(
                            f"Task '{task.get('task_slug')}' references session '{sid}' "
                            f"which is not in the sessions[] array"
                        )

        if not issues:
            return "consistent"

        issues.sort()
        lines = ["Inconsistencies found:", ""]
        for i, issue in enumerate(issues, 1):
            lines.append(f"  {i}. {issue}")
        lines.append("")
        lines.append(f"Total: {len(issues)} issue(s)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Auto-init hook
    # ------------------------------------------------------------------

    @staticmethod
    def ensure_init(project_root, project_name=None):
        """Auto-init hook called by session init.

        Detects whether project-state.json exists. If not, silently creates
        one (no banner, no output). If it already exists, returns a summary.

        This is designed to be called during session initialization without
        distracting the user with banner messages.

        Args:
            project_root: Absolute path to the project root.
            project_name: Optional project name (defaults to directory basename).

        Returns:
            str: Short JSON summary (one line) or empty if freshly created.
        """
        path = ProjectStateManager._state_path(project_root)
        if os.path.isfile(path):
            state = ProjectStateManager._load(project_root)
            if "error" not in state:
                return json.dumps({
                    "action": "exists",
                    "project_id": state.get("project_id"),
                    "project_name": state.get("project_name"),
                }, indent=2, ensure_ascii=False)

        # Silently create — no banner
        pdf_dir = os.path.join(project_root, ".fat", "pdf")
        os.makedirs(pdf_dir, exist_ok=True)
        state = ProjectStateManager._default_state(project_root, project_name)
        ProjectStateManager._save(project_root, state)

        return json.dumps({
            "action": "auto_initialized",
            "project_id": state["project_id"],
            "project_name": state["project_name"],
        }, indent=2, ensure_ascii=False)


# === Module-level alias for convenience ===


def _cli_main():
    """Minimal CLI entry point for testing / ad-hoc use.

    Usage:
        python3 pdf_v5_project.py init [--name <name>] [--force]
        python3 pdf_v5_project.py status
        python3 pdf_v5_project.py add-task <slug> --desc <desc> [--channel <ch>]
        python3 pdf_v5_project.py close-task <slug>
        python3 pdf_v5_project.py archive
        python3 pdf_v5_project.py rebuild
        python3 pdf_v5_project.py verify
        python3 pdf_v5_project.py ensure-init [--name <name>]

    This CLI is minimal and intended for manual testing. The primary
    integration path is via the ProjectStateManager class methods called
    by the PDF agent (not CLI).
    """
    import argparse

    parser = argparse.ArgumentParser(description="PDF v5.0 Project State Manager")
    parser.add_argument("--project-root", default=os.getcwd(),
                        help="Project root directory (default: cwd)")

    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # init
    p_init = subparsers.add_parser("init", help="Initialize project-state.json")
    p_init.add_argument("--name", help="Project display name")
    p_init.add_argument("--force", action="store_true", help="Force recreate")

    # status
    subparsers.add_parser("status", help="Show project overview")

    # task-status
    p_ts = subparsers.add_parser("task-status", help="Show task details")
    p_ts.add_argument("slug", help="Task slug")

    # add-task
    p_add = subparsers.add_parser("add-task", help="Register a new task")
    p_add.add_argument("slug", help="Task slug")
    p_add.add_argument("--desc", required=True, help="Task description")
    p_add.add_argument("--channel", default="standard",
                       choices=VALID_CHANNELS, help="Channel (default: standard)")

    # close-task
    p_close = subparsers.add_parser("close-task", help="Close a task")
    p_close.add_argument("slug", help="Task slug")

    # archive
    subparsers.add_parser("archive", help="Archive completed tasks")

    # rebuild
    subparsers.add_parser("rebuild", help="Rebuild from session state")

    # verify
    subparsers.add_parser("verify", help="Verify consistency")

    # ensure-init
    p_ensure = subparsers.add_parser("ensure-init", help="Auto-init if not exists")
    p_ensure.add_argument("--name", help="Project display name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    project_root = os.path.abspath(args.project_root)

    psm = ProjectStateManager

    if args.command == "init":
        result = psm.init(project_root, args.name, args.force)
    elif args.command == "status":
        result = psm.status(project_root)
    elif args.command == "task-status":
        result = psm.task_status(project_root, args.slug)
    elif args.command == "add-task":
        result = psm.add_task(project_root, args.slug, args.desc, args.channel)
    elif args.command == "close-task":
        result = psm.close_task(project_root, args.slug)
    elif args.command == "archive":
        result = psm.archive(project_root)
    elif args.command == "rebuild":
        result = psm.rebuild(project_root)
    elif args.command == "verify":
        result = psm.verify(project_root)
    elif args.command == "ensure-init":
        result = psm.ensure_init(project_root, args.name)
    else:
        result = f"Unknown command: {args.command}"

    print(result)


if __name__ == "__main__":
    _cli_main()
