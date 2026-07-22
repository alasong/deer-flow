#!/usr/bin/env python3
"""
PDF v5.0 Plan & DAG Manager.

Architecture 2 (--append) + Architecture 3 (DAG).
v5.1: DAG stub injection (StubManager).

PlanManager: Plan versioning, --append, conflict detection, session lifecycle.
DagManager: DAG topological sort, status, check, next-step, visualization.
StubManager: DAG stub injection, mapping, interface change detection.

Usage:
    from pdf_v5_plan_dag import PlanManager, DagManager, StubManager,
                                 build_dag, detect_cycle
    from pdf_v5_plan_dag import parse_upstream_interface_contracts

    pm = PlanManager()
    print(pm.plan_append("/my/project", "feature-x", "Implement auth middleware"))

    dm = DagManager()
    print(dm.dag_build(plan_content))

    sm = StubManager()
    print(sm.stub_inject(project_root, session_id, plan_content))

Design principles:
    - Zero third-party dependencies (no pyyaml).
    - All methods return printable strings (caller prints them).
    - Error paths always produce clear messages.
    - Decoupled from pdf-engine.py; works as a standalone module.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Regex patterns for YAML-like frontmatter and task list parsing (no pyyaml).
# These handle the subset of YAML used in PDF plan files.

# Match a YAML frontmatter block between --- delimiters (must start the file)
RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

# Match optional task-slug or slug: entries under a list item
RE_TASK_ENTRY = re.compile(
    r"^-\s+(?:slug|task_slug)\s*:\s*(.+)$", re.MULTILINE
)

# Match depends_on: [a, b] or depends_on: [a] or depends_on: [a, b, c] etc.
RE_DEPENDS_BRACKET = re.compile(r"depends_on\s*:\s*\[([^\]]*)\]")

# Match depends_on: a,b (comma-separated, no brackets)
RE_DEPENDS_COMMA = re.compile(r"depends_on\s*:\s*(.+)$", re.MULTILINE)

# Match a heading like ## Tasks, ## Task List, ## Active Tasks
RE_TASK_HEADING = re.compile(r"^##\s+.*(?:Task|task|Tasks|tasks).*$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def _ts() -> str:
    """Return current UTC ISO-8601 timestamp string."""
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FMT)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _sessions_dir(project_root: str) -> str:
    """Return the absolute path to the project-local sessions directory."""
    return os.path.join(project_root, ".fat", "pdf", "sessions")


def _project_state_path(project_root: str) -> str:
    """Return the absolute path to the project-state registry file."""
    return os.path.join(project_root, ".fat", "pdf", "project-state.json")


def _session_dir(project_root: str, session_id: str) -> str:
    """Return the session directory for a given session_id."""
    return os.path.join(_sessions_dir(project_root), session_id)


def _session_state_path(project_root: str, session_id: str) -> str:
    """Return the path to a session's state.json."""
    return os.path.join(_session_dir(project_root, session_id), "state.json")


def _plan_path(project_root: str, session_id: str, version: int) -> str:
    """Return the path for plan_v<N>.md in the session directory.

    Why N -- not "%03d": the filenames use a clean v<N> suffix for readability.
    The directory scan for "plan_v*.md" versions is sorted numerically.
    """
    return os.path.join(
        _session_dir(project_root, session_id), f"plan_v{version}.md"
    )


# ---------------------------------------------------------------------------
# Project-state registry
# ---------------------------------------------------------------------------

DEFAULT_PROJECT_STATE: Dict[str, Any] = {
    "schema_version": 1,
    "sessions": [],
    "updated_at": "",
}


def _get_session_slug(entry):
    """Get the session slug from a session entry dict, checking both key names.

    ProjectStateManager uses `session_slug`; PlanManager originally used `slug`.
    This helper normalises access so either key name works.
    """
    return entry.get("session_slug") or entry.get("slug", "")


def _sessions_to_lookup(sessions_list):
    """Convert sessions array [{session_id, slug/session_slug, ...}, ...] to
    {session_id: info, ...} dict.

    Consistent with ProjectStateManager: sessions is an array of dicts,
    each containing a 'session_id' field. This helper builds O(1) lookup
    for PlanManager operations that need dict-style access.
    """
    if isinstance(sessions_list, dict):
        # Handle legacy dict format gracefully (migration path)
        sessions_list = list(sessions_list.values())
    return {
        s.get("session_id"): s
        for s in sessions_list
        if isinstance(s, dict) and s.get("session_id")
    }


def _load_project_state(project_root: str) -> Optional[Dict[str, Any]]:
    """Load the project-state.json registry. Returns None if missing or corrupt."""
    path = _project_state_path(project_root)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, IOError):
        return None


def _save_project_state(project_root: str, state: Dict[str, Any]) -> bool:
    """Save the project-state.json registry. Returns True on success."""
    path = _project_state_path(project_root)
    state["updated_at"] = _ts()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return True
    except (IOError, OSError) as e:
        return False


# ---------------------------------------------------------------------------
# YAML-like frontmatter parsing (zero dependency)
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> Dict[str, Any]:
    """Parse --- delimited YAML frontmatter into a dict.

    Supports simple key: value pairs, quoted strings, and list values.
    Does NOT support nested dicts or YAML anchors/aliases.
    Only the subset of YAML used in PDF plan files is supported.
    """
    m = RE_FRONTMATTER.search(content)
    if not m:
        return {}
    block = m.group(1)
    result: Dict[str, Any] = {}
    for line in block.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val_str = val.strip()
        # Strip surrounding quotes
        if val_str.startswith('"') and val_str.endswith('"'):
            val_str = val_str[1:-1]
        elif val_str.startswith("'") and val_str.endswith("'"):
            val_str = val_str[1:-1]
        # Parse booleans and ints
        if val_str.lower() == "true":
            result[key] = True
        elif val_str.lower() == "false":
            result[key] = False
        elif val_str.lower() == "null" or val_str.lower() == "~":
            result[key] = None
        else:
            try:
                result[key] = int(val_str)
            except ValueError:
                try:
                    result[key] = float(val_str)
                except ValueError:
                    result[key] = val_str
    return result


def _build_frontmatter(fields: Dict[str, Any]) -> str:
    """Build a YAML-like frontmatter string from a dict.

    Inverse of _parse_frontmatter. Produces clean, human-readable output.
    """
    lines = ["---"]
    for key, val in fields.items():
        if isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        elif val is None:
            lines.append(f"{key}: null")
        elif isinstance(val, (int, float)):
            lines.append(f"{key}: {val}")
        else:
            # Quote strings that contain special characters
            sval = str(val)
            if any(c in sval for c in (":", "#", "{", "}", "[", "]", ",")):
                lines.append(f'{key}: "{sval}"')
            else:
                lines.append(f"{key}: {sval}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task list parsing from plan content
# ---------------------------------------------------------------------------


def parse_tasks_from_plan(plan_content: str) -> List[Dict[str, Any]]:
    """Parse task entries from plan.md content. Returns list of task dicts.

    Supported formats:
        1. YAML list items under a ## Tasks / ## Active Tasks heading:
           - slug: task-a
             desc: "..."
             depends_on: [task-b]

        2. Bare YAML list items (no heading required):
           - task_slug: task-a
             depends_on: [task-b]

        3. Inline format:
           - task-a (depends_on: task-b, task-c)

    Each returned dict has at minimum: {"slug": str, "depends_on": list}.
    """
    tasks: List[Dict[str, Any]] = []

    # Strategy 1: Try to find YAML list items with slug/task_slug fields.
    # We locate task entries by scanning for lines starting with "- slug:" or "- task_slug:".
    lines = plan_content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match a list entry: "- slug: xxx" or "- task_slug: xxx"
        entry_match = re.match(r"^\s*-\s+(?:slug|task_slug)\s*:\s*(.+)$", line)
        if entry_match:
            slug = entry_match.group(1).strip().strip('"').strip("'")
            task: Dict[str, Any] = {"slug": slug, "depends_on": []}

            # Look ahead a few lines for depends_on field
            # (YAML multiline list items span consecutive indented lines)
            j = i + 1
            max_lookahead = 10  # safety: don't scan too far
            while j < len(lines) and j < i + max_lookahead:
                next_line = lines[j]
                # Check if we hit the next top-level list item or a heading
                stripped = next_line.strip()
                if not stripped or stripped.startswith("#"):
                    j += 1
                    continue
                # Stop at the next top-level list entry
                if re.match(r"^\s*-\s+", next_line) and not next_line.startswith(" " * 4):
                    break
                # Stop at a heading
                if next_line.startswith("## "):
                    break
                # Check for depends_on field
                dep_match = re.match(r"^\s+depends_on\s*:\s*(.+)$", next_line)
                if dep_match:
                    dep_val = dep_match.group(1).strip()
                    deps = _parse_depends_value(dep_val)
                    for d in deps:
                        if d not in task["depends_on"]:
                            task["depends_on"].append(d)
                    break  # found depends_on, done for this entry
                j += 1
            tasks.append(task)
            i = j  # skip ahead
        else:
            # Strategy 2: Check for inline format: "- <slug> (depends_on: a, b)"
            inline_match = re.match(r"^\s*-\s+([^\s(]+)\s*\(depends_on:\s*([^)]+)\)\s*$", line)
            if inline_match:
                slug = inline_match.group(1).strip()
                dep_str = inline_match.group(2).strip()
                deps = [d.strip() for d in dep_str.split(",") if d.strip()]
                tasks.append({"slug": slug, "depends_on": deps})
            i += 1

    # Strategy 3: Fallback to regex-based extraction from the entire content
    if not tasks:
        # Try extracting slug entries without depending on line-by-line structure.
        # This handles plan files where tasks are in a YAML block under a heading.
        task_matches = list(RE_TASK_ENTRY.finditer(plan_content))
        for idx, m in enumerate(task_matches):
            slug = m.group(1).strip().strip('"').strip("'")
            task = {"slug": slug, "depends_on": []}
            # Find the second "(depends_on: ...)" or depends_on field for this entry
            entry_start = m.start()
            # Look for a depends_on within the next ~15 lines or until next slug
            remaining = plan_content[entry_start:]
            lines_after = remaining.split("\n", 16)[:15]
            for la_line in lines_after:
                dep_match = re.match(
                    r".*depends_on\s*:\s*\[([^\]]*)\]", la_line
                ) or re.match(
                    r".*depends_on\s*:\s*(.+)$", la_line
                )
                if dep_match:
                    deps = _parse_depends_value(dep_match.group(1))
                    for d in deps:
                        if d not in task["depends_on"]:
                            task["depends_on"].append(d)
                    break
            # Deduplicate by slug
            if not any(t["slug"] == task["slug"] for t in tasks):
                tasks.append(task)

    return tasks


def _parse_depends_value(val: str) -> List[str]:
    """Parse a depends_on value string into a list of dependency slugs.

    Supports:
        [a, b]    -- bracket list
        a, b      -- comma-separated
        [a]       -- single item in brackets
        a         -- single item, no brackets
    """
    val = val.strip()
    if not val:
        return []
    # Remove surrounding brackets
    if val.startswith("[") and val.endswith("]"):
        val = val[1:-1]
    # Split by comma
    parts = [p.strip().strip('"').strip("'") for p in val.split(",")]
    return [p for p in parts if p]  # filter empty strings


# ---------------------------------------------------------------------------
# Decisions extraction
# ---------------------------------------------------------------------------


def _extract_decisions_section(content: str) -> Dict[str, Any]:
    """Extract structured decisions from a ## Decisions or ## Key Decisions block.

    Returns a dict with extracted keys or empty dict on failure.
    Since we can't use pyyaml, we use simple heuristics for common decision formats.
    """
    # Look for ## Decisions or ## Key Decisions
    pattern = re.compile(r"^##\s+(?:Key\s+)?Decisions\s*\n(.*?)(?=\n##|\Z)", re.DOTALL)
    m = pattern.search(content)
    if not m:
        return {}

    block = m.group(1).strip()
    # Attempt to extract decision entries
    decisions = []

    # Format 1: YAML list of dicts with "key:" fields
    #   - key: decision-1
    #     decision: "description"
    #     rationale: "..."
    if "key:" in block:
        keys = re.findall(r"^\s*-\s+key\s*:\s*(.+)$", block, re.MULTILINE)
        for k in keys:
            decisions.append(k.strip().strip('"').strip("'"))
        return {"keys": decisions}

    # Format 2: Simple list items
    #   - decision item 1
    decision_items = re.findall(r"^\s*-\s+(.+)$", block, re.MULTILINE)
    for item in decision_items:
        item = item.strip()
        if item and not item.startswith("#"):
            decisions.append(item)

    return {"keys": decisions} if decisions else {}


# ---------------------------------------------------------------------------
# DAG utilities
# ---------------------------------------------------------------------------


def build_dag(tasks: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """Build adjacency list and in-degree table from task list.

    Args:
        tasks: List of task dicts, each with "slug" and "depends_on" keys.

    Returns:
        (graph, in_degree):
            graph:      {task_slug: [dependent_slug, ...]}
            in_degree:  {task_slug: int}

    Why adjacency from task to its dependents (not dependencies):
        This direction makes Kahn traversal easier -- when a task completes,
        we decrement in_degree of its dependents and check if any reach 0.
    """
    graph: Dict[str, List[str]] = {}
    in_degree: Dict[str, int] = {}
    all_slugs: set = set()

    # Collect all slugs and their depends_on
    for task in tasks:
        slug = task.get("slug", "")
        deps = task.get("depends_on", [])
        if not slug:
            continue
        all_slugs.add(slug)
        # Initialize in_degree (each task starts with 0; we increment for each dep)
        if slug not in in_degree:
            in_degree[slug] = 0
        for dep in deps:
            if dep:
                all_slugs.add(dep)
                # graph[dep] lists tasks that depend on dep
                if dep not in graph:
                    graph[dep] = []
                if slug not in graph[dep]:
                    graph[dep].append(slug)

    # Ensure every known slug has an entry (even nodes with 0 deps and 0 dependents)
    for slug in all_slugs:
        graph.setdefault(slug, [])
        in_degree.setdefault(slug, 0)

    # Compute in_degree: for each task, count how many of its deps exist
    # Actually we reversed direction in graph, so in_degree = count of dependencies
    in_degree = {}
    for task in tasks:
        slug = task.get("slug", "")
        deps = task.get("depends_on", [])
        if slug:
            # Filter deps to those that are actually in the task set
            valid_deps = [d for d in deps if d in all_slugs]
            in_degree[slug] = len(valid_deps)
        # Register zero-degree for orphan deps so they appear in graph
        for d in deps:
            if d not in in_degree:
                in_degree[d] = 0

    return graph, in_degree


def detect_cycle(graph: Dict[str, List[str]]) -> List[List[str]]:
    """DFS-based cycle detection in a directed graph.

    Uses WHITE/GRAY/BACK coloring (0=unvisited, 1=in-progress, 2=done).
    This is the standard textbook approach for detecting directed cycles.

    Args:
        graph: Adjacency list {node: [neighbor, ...]}

    Returns:
        List of cycle paths (each is a list of nodes forming a cycle).
        Empty list means no cycle detected.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {node: WHITE for node in graph}
    parent: Dict[str, Optional[str]] = {node: None for node in graph}
    cycles: List[List[str]] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if color.get(neighbor, WHITE) == GRAY:
                # Found a back edge -- reconstruct the cycle path
                cycle_path: List[str] = []
                cursor: Optional[str] = node
                while cursor is not None:
                    cycle_path.append(cursor)
                    if cursor == neighbor:
                        break
                    cursor = parent.get(cursor)
                if cycle_path and cycle_path[-1] == neighbor:
                    cycle_path.reverse()
                    # Deduplicate: avoid recording the same cycle multiple times
                    # Normalize by rotating so the smallest node is first
                    if cycle_path:
                        min_idx = cycle_path.index(min(cycle_path))
                        normalized = cycle_path[min_idx:] + cycle_path[:min_idx]
                        if normalized not in cycles:
                            cycles.append(normalized)
            elif color.get(neighbor, WHITE) == WHITE:
                parent[neighbor] = node
                dfs(neighbor)
        color[node] = BLACK

    for node in sorted(graph.keys()):
        if color.get(node, WHITE) == WHITE:
            dfs(node)

    return cycles


def kahn_topological_sort(
    graph: Dict[str, List[str]], in_degree: Dict[str, int]
) -> List[List[str]]:
    """Kahn's algorithm for topological sort with levelization.

    Returns list of levels, where each level is a list of tasks that can
    execute in parallel (all upstream dependencies satisfied).

    Args:
        graph:     Adjacency list {node: [dependent_node, ...]}
        in_degree: {node: int} -- number of incoming edges (dependencies)

    Returns:
        List of lists: [[level_0_tasks], [level_1_tasks], ...]
        Each level's tasks have no dependency order among themselves.
        Returns empty list if the graph has a cycle (can't topologically sort).
    """
    from collections import deque

    # Make a mutable copy of in_degree
    in_deg = dict(in_degree)
    # Make a working copy of graph for the algorithm
    graph_cp = {k: list(v) for k, v in graph.items()}

    # Seed queue with nodes that have in_degree == 0
    queue: deque = deque()
    for node, deg in in_deg.items():
        if deg == 0:
            queue.append(node)

    # Handle disconnected nodes not in in_degree
    for node in graph_cp:
        if node not in in_deg:
            queue.append(node)
            in_deg[node] = 0

    levels: List[List[str]] = []
    visited_count = 0
    total_nodes = len(graph_cp)

    while queue:
        level_nodes: List[str] = []
        # Process all nodes currently at the frontier
        for _ in range(len(queue)):
            node = queue.popleft()
            level_nodes.append(node)
            visited_count += 1

            # Decrease in_degree of all dependents
            for dependent in graph_cp.get(node, []):
                in_deg[dependent] = in_deg.get(dependent, 1) - 1
                if in_deg[dependent] == 0:
                    queue.append(dependent)

        if level_nodes:
            levels.append(sorted(level_nodes))

    # If we didn't visit all nodes, a cycle exists
    if visited_count < total_nodes:
        return []  # cycle detected -- Kahn cannot complete

    return levels


# ---------------------------------------------------------------------------
# PlanManager
# ---------------------------------------------------------------------------


class PlanManager:
    """Plan versioning, --append, conflict detection, session lifecycle.

    All public methods return printable strings for the caller to emit.
    Error paths produce descriptive messages prefixed with "ERROR:".
    """

    @staticmethod
    def _sessions_dir(project_root: str) -> str:
        """Return sessions directory under project_root."""
        return _sessions_dir(project_root)

    # ---- Plan operations ----

    def plan_append(
        self,
        project_root: str,
        slug: str,
        new_task_desc: str,
        no_detect_overlap: bool = False,
    ) -> str:
        """Append a new task to the latest plan of the active session for slug.

        Steps:
            1. Load project-state.json (error if missing).
            2. Resolve session via resolve_append_session():
               - Active session exists -> use it.
               - All done -> create new session.
               - None -> error (slug never had a session).
            3. Find latest plan_vN.md in the session directory.
            4. Write plan_v{N+1}.md with frontmatter + existing content + new task.
            5. Return confirmation string.

        Overlap detection (unless suppressed):
            - In-memory: compares decisions in the plan vs. new task desc.
            - File: scans other session dirs for do artifacts.

        Args:
            project_root:  Absolute path to project root.
            slug:          Session slug (e.g. "feature-x").
            new_task_desc: Description of the new task to append.
            no_detect_overlap: If True, skip overlap detection entirely.

        Returns:
            Confirmation string like:
                "Plan v{N+1} appended (v{N} + 1 new task), session <session_id>"
        """
        # Step 1: Load project-state.json
        ps = _load_project_state(project_root)
        if ps is None:
            return (
                "ERROR: project-state.json not found in "
                f"'{os.path.join(project_root, '.fat', 'pdf')}'. "
                "A PDF session must exist before --append can be used."
            )

        # Step 2: Resolve session
        session_id, mode = self.resolve_append_session(project_root, slug)
        if session_id is None and mode == "none":
            return (
                f"ERROR: no sessions found for slug '{slug}'. "
                "No session exists for this slug yet. "
                "Create a session first (e.g., via pdf-engine.py init)."
            )
        if session_id is None:
            return f"ERROR: resolve_append_session returned None for slug '{slug}'"

        # Step 3: Find the latest plan version
        session_dir = _session_dir(project_root, session_id)
        existing_plans = sorted(
            [
                int(m.group(1))
                for fname in os.listdir(session_dir)
                if (m := re.match(r"plan_v(\d+)\.md$", fname)) and os.path.isfile(os.path.join(session_dir, fname))
            ]
        )

        latest_version = existing_plans[-1] if existing_plans else 0
        new_version = latest_version + 1

        # Step 4: Build the plan content
        existing_content = ""
        if latest_version > 0:
            existing_path = _plan_path(project_root, session_id, latest_version)
            try:
                with open(existing_path) as f:
                    existing_content = f.read()
            except (IOError, OSError) as e:
                return (
                    f"ERROR: could not read existing plan {existing_path}: {e}"
                )

        # Build new frontmatter
        fm = {
            "version": new_version,
            "parent_version": latest_version,
            "appended_at": _ts(),
            "appended_task": new_task_desc,
        }
        fm_str = _build_frontmatter(fm)

        # Build the new task entry
        task_slug = self._slugify_task_desc(new_task_desc)
        task_entry = self._format_task_entry(task_slug, new_task_desc)

        # Compose the new plan content
        if existing_content:
            # Strip existing frontmatter if present (we replace it with new)
            body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", existing_content, count=1, flags=re.DOTALL)
            new_content = fm_str + "\n\n" + body.strip() + "\n\n" + task_entry + "\n"
        else:
            # First plan in a brand-new session
            new_content = fm_str + "\n\n## Tasks\n\n" + task_entry + "\n"

        # Write new plan
        new_path = _plan_path(project_root, session_id, new_version)
        try:
            with open(new_path, "w") as f:
                f.write(new_content)
        except (IOError, OSError) as e:
            return f"ERROR: could not write plan {new_path}: {e}"

        # Step 5: Overlap detection (unless suppressed)
        overlap_warnings: List[str] = []
        if not no_detect_overlap:
            try:
                overlap_result = self.detect_overlap(project_root, slug, new_task_desc)
                if overlap_result not in ("(no overlap detected)", ""):
                    overlap_warnings.append(overlap_result)
            except Exception:
                overlap_warnings.append(
                    "WARNING: overlap detection encountered an error (skipped)"
                )

        msg = (
            f"Plan v{new_version} appended (v{latest_version} + 1 new task), "
            f"session {session_id}"
        )
        if overlap_warnings:
            msg += "\n" + "\n".join(overlap_warnings)

        # Update project-state.json session timestamps
        sessions_list = ps.setdefault("sessions", [])
        sess_lookup = _sessions_to_lookup(sessions_list)
        if session_id in sess_lookup:
            sess_lookup[session_id]["updated_at"] = _ts()
        _save_project_state(project_root, ps)

        return msg

    def plan_list(self, project_root: str, slug: str) -> str:
        """List all plan versions for sessions matching the given slug.

        Args:
            project_root: Absolute path to project root.
            slug:         Session slug to filter by.

        Returns:
            Formatted listing string.
        """
        ps = _load_project_state(project_root)
        if ps is None:
            return "ERROR: project-state.json not found."

        sessions_list = ps.get("sessions", [])
        sess_lookup = _sessions_to_lookup(sessions_list)
        # Find all session IDs for this slug
        matching_sessions = [
            sid for sid, info in sess_lookup.items()
            if _get_session_slug(info) == slug
        ]

        if not matching_sessions:
            return f"(no sessions for slug '{slug}')"

        lines: List[str] = []
        for sid in sorted(matching_sessions):
            session_dir = _session_dir(project_root, sid)
            if not os.path.isdir(session_dir):
                continue
            plans = sorted(
                [
                    int(m.group(1))
                    for fname in os.listdir(session_dir)
                    if (m := re.match(r"plan_v(\d+)\.md$", fname)) and os.path.isfile(os.path.join(session_dir, fname))
                ]
            )
            if plans:
                plan_info = ", ".join(f"v{v}" for v in plans)
                lines.append(f"Session {sid}: [{plan_info}]")
            else:
                lines.append(f"Session {sid}: (no plan files)")

        return "\n".join(lines) if lines else f"(no plan files for slug '{slug}')"

    def plan_current(self, project_root: str, slug: str) -> str:
        """Show info about the current (latest) plan version for the given slug.

        Finds the active or most recent session and reports its latest plan.

        Args:
            project_root: Absolute path to project root.
            slug:         Session slug.

        Returns:
            Formatted plan info string.
        """
        session_id, mode = self.resolve_append_session(project_root, slug)
        if session_id is None and mode == "none":
            return f"(no session for slug '{slug}')"

        if session_id is None:
            # Fall back: check if there are any sessions for this slug at all
            ps = _load_project_state(project_root)
            if ps is None:
                return "ERROR: project-state.json not found."
            sessions_list = ps.get("sessions", [])
            sess_lookup = _sessions_to_lookup(sessions_list)
            matching = [
                sid for sid, info in sess_lookup.items()
                if _get_session_slug(info) == slug
            ]
            if not matching:
                return f"(no session for slug '{slug}')"
            session_id = matching[-1]  # take the last one

        session_dir = _session_dir(project_root, session_id)

        # Read session state for stage info
        stage = "?"
        state_path = _session_state_path(project_root, session_id)
        if os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    state_data = json.load(f)
                stage = state_data.get("stage", "?")
            except Exception:
                pass

        # Find latest plan
        plans = sorted(
            [
                int(m.group(1))
                for fname in os.listdir(session_dir)
                if (m := re.match(r"plan_v(\d+)\.md$", fname)) and os.path.isfile(os.path.join(session_dir, fname))
            ]
        )

        if not plans:
            return (
                f"Session: {session_id}\n"
                f"  Slug: {slug}\n"
                f"  Stage: {stage}\n"
                f"  Plans: (none yet)"
            )

        latest = plans[-1]
        # Read the frontmatter of the latest plan for details
        latest_path = _plan_path(project_root, session_id, latest)
        detail_parts = [f"Session: {session_id}", f"  Slug: {slug}", f"  Stage: {stage}"]
        try:
            with open(latest_path) as f:
                content = f.read()
            fm = _parse_frontmatter(content)
            if fm:
                detail_parts.append(f"  Latest Plan: v{fm.get('version', latest)}")
                if fm.get("parent_version", 0) > 0:
                    detail_parts.append(f"  Parent: v{fm['parent_version']}")
                if fm.get("appended_task"):
                    detail_parts.append(f"  Last Appended: {fm['appended_task']}")
                detail_parts.append(f"  Appended At: {fm.get('appended_at', '?')}")
            else:
                detail_parts.append(f"  Latest Plan: v{latest}")
        except (IOError, OSError):
            detail_parts.append(f"  Latest Plan: v{latest} (unreadable)")

        # Parse tasks from the plan
        try:
            with open(latest_path) as f:
                content = f.read()
            plan_tasks = parse_tasks_from_plan(content)
            if plan_tasks:
                detail_parts.append(f"  Tasks ({len(plan_tasks)}):")
                for t in plan_tasks:
                    deps = t.get("depends_on", [])
                    dep_str = f" (depends_on: {', '.join(deps)})" if deps else ""
                    detail_parts.append(f"    - {t.get('slug', '?')}{dep_str}")
        except Exception:
            pass

        return "\n".join(detail_parts)

    def plan_diff(self, project_root: str, slug: str, v1: int, v2: int) -> str:
        """Compare two plan versions for the given slug.

        Performs a text-level diff of the plan bodies (frontmatter excluded)
        and a structured comparison of tasks and decisions.

        Args:
            project_root: Absolute path to project root.
            slug:         Session slug.
            v1:           First version number.
            v2:           Second version number.

        Returns:
            Formatted diff string.
        """
        # Find the session first
        session_id, _ = self.resolve_append_session(project_root, slug)
        if session_id is None:
            return f"ERROR: no session for slug '{slug}'."

        p1 = _plan_path(project_root, session_id, v1)
        p2 = _plan_path(project_root, session_id, v2)

        if not os.path.exists(p1):
            return f"ERROR: plan_v{v1}.md not found for session '{session_id}'."
        if not os.path.exists(p2):
            return f"ERROR: plan_v{v2}.md not found for session '{session_id}'."

        try:
            with open(p1) as f:
                c1 = f.read()
            with open(p2) as f:
                c2 = f.read()
        except (IOError, OSError) as e:
            return f"ERROR: could not read plan files: {e}"

        # Strip frontmatter for body comparison
        body1 = re.sub(r"^---\s*\n.*?\n---\s*\n", "", c1, count=1, flags=re.DOTALL)
        body2 = re.sub(r"^---\s*\n.*?\n---\s*\n", "", c2, count=1, flags=re.DOTALL)

        lines1 = body1.strip().split("\n")
        lines2 = body2.strip().split("\n")

        # Simple line-based diff (no external library)
        diff_lines: List[str] = self._simple_diff(
            lines1, lines2, f"plan_v{v1}.md", f"plan_v{v2}.md"
        )

        # Compare tasks
        tasks1 = parse_tasks_from_plan(c1)
        tasks2 = parse_tasks_from_plan(c2)
        slugs1 = {t["slug"] for t in tasks1}
        slugs2 = {t["slug"] for t in tasks2}

        task_diff: List[str] = []

        added = slugs2 - slugs1
        removed = slugs1 - slugs2
        common = slugs1 & slugs2

        if added:
            task_diff.append(f"  Added tasks ({len(added)}): {', '.join(sorted(added))}")
        if removed:
            task_diff.append(f"  Removed tasks ({len(removed)}): {', '.join(sorted(removed))}")

        for slug_name in sorted(common):
            t1 = next((t for t in tasks1 if t["slug"] == slug_name), None)
            t2 = next((t for t in tasks2 if t["slug"] == slug_name), None)
            if t1 and t2 and t1.get("depends_on") != t2.get("depends_on"):
                task_diff.append(
                    f"  Changed: {slug_name} "
                    f"depends_on: {t1['depends_on']} -> {t2['depends_on']}"
                )

        # Compare decisions
        dec1 = _extract_decisions_section(c1)
        dec2 = _extract_decisions_section(c2)
        keys1 = set(dec1.get("keys", []))
        keys2 = set(dec2.get("keys", []))
        added_dec = keys2 - keys1
        removed_dec = keys1 - keys2
        if added_dec:
            task_diff.append(f"  Added decisions ({len(added_dec)}): {', '.join(sorted(added_dec))}")
        if removed_dec:
            task_diff.append(f"  Removed decisions ({len(removed_dec)}): {', '.join(sorted(removed_dec))}")

        # Compare frontmatter
        fm1 = _parse_frontmatter(c1)
        fm2 = _parse_frontmatter(c2)

        result = (
            f"Diff: v{v1} (session {session_id}) vs v{v2}\n"
        )

        task_result = "\n".join(task_diff) if task_diff else "  (no task changes)"
        result += f"Tasks:\n{task_result}\n"

        if diff_lines:
            result += f"Body diff ({len(diff_lines)} differing lines):\n"
            # Show max 20 diff lines to avoid overwhelming output
            for dl in diff_lines[:20]:
                result += f"  {dl}\n"
            if len(diff_lines) > 20:
                result += f"  ... (+{len(diff_lines) - 20} more)\n"
        else:
            result += "Body: (identical)\n"

        return result

    def plan_history(self, project_root: str) -> str:
        """List all plan versions and change summaries across all sessions.

        Iterates every session in project-state.json and reads their plan files
        for a summary.

        Args:
            project_root: Absolute path to project root.

        Returns:
            Formatted history string.
        """
        ps = _load_project_state(project_root)
        if ps is None:
            return "ERROR: project-state.json not found."

        sessions_list = ps.get("sessions", [])
        sess_lookup = _sessions_to_lookup(sessions_list)
        if not sess_lookup:
            return "(no sessions)"

        lines: List[str] = []
        for sid in sorted(sess_lookup.keys()):
            info = sess_lookup.get(sid, {})
            slug = _get_session_slug(info) or "?"
            created = info.get("created_at", "?")[:10]
            session_dir = _session_dir(project_root, sid)
            plans = sorted(
                [
                    int(m.group(1))
                    for fname in os.listdir(session_dir)
                    if (m := re.match(r"plan_v(\d+)\.md$", fname))
                    and os.path.isfile(os.path.join(session_dir, fname))
                ]
            )
            if plans:
                plan_summaries: List[str] = []
                for v in plans:
                    plan_path = _plan_path(project_root, sid, v)
                    summary = f"v{v}"
                    try:
                        with open(plan_path) as f:
                            content = f.read()
                        fm = _parse_frontmatter(content)
                        if fm.get("appended_task"):
                            task_short = fm["appended_task"][:40]
                            summary += f" ({task_short})"
                    except Exception:
                        pass
                    plan_summaries.append(summary)
                lines.append(
                    f"Session: {sid} slug={slug} created={created}\n"
                    f"  Plans: {' -> '.join(plan_summaries)}"
                )
            else:
                lines.append(f"Session: {sid} slug={slug} created={created} (no plans)")

        return "\n".join(lines)

    def detect_overlap(self, project_root: str, slug: str, new_task_desc: str) -> str:
        """Detect potential file/concept overlap with existing session artifacts.

        Scans:
            1. Decisions in existing plans for the slug.
            2. Do artifacts in all session directories for this slug.

        Args:
            project_root:  Absolute path to project root.
            slug:          Session slug to inspect.
            new_task_desc: Description of the proposed new task.

        Returns:
            Analysis string. "(no overlap detected)" if clean.
        """
        ps = _load_project_state(project_root)
        if ps is None:
            return "ERROR: project-state.json not found."

        sessions_list = ps.get("sessions", [])
        sess_lookup = _sessions_to_lookup(sessions_list)
        matching_sessions = [
            sid for sid, info in sess_lookup.items()
            if _get_session_slug(info) == slug
        ]

        findings: List[str] = []

        # --- Check 1: Decision overlap ---
        existing_decisions: List[str] = []
        for sid in matching_sessions:
            session_dir = _session_dir(project_root, sid)
            plans = sorted(
                [
                    int(m.group(1))
                    for fname in os.listdir(session_dir)
                    if (m := re.match(r"plan_v(\d+)\.md$", fname))
                    and os.path.isfile(os.path.join(session_dir, fname))
                ]
            )
            for v in plans[-3:]:  # check last 3 versions
                plan_path = _plan_path(project_root, sid, v)
                try:
                    with open(plan_path) as f:
                        content = f.read()
                    dec = _extract_decisions_section(content)
                    existing_decisions.extend(dec.get("keys", []))
                except Exception:
                    continue

        # Check if any existing decision keys or words appear in the new task desc
        desc_lower = new_task_desc.lower()
        overlapping_decisions: List[str] = []
        for dec_key in existing_decisions:
            dk_lower = dec_key.lower()
            # Decision overlaps if the key is a substring of the description or vice versa
            if len(dk_lower) > 4 and (dk_lower in desc_lower or desc_lower in dk_lower):
                overlapping_decisions.append(dec_key)

        if overlapping_decisions:
            findings.append(
                f"  Decision overlap: {len(overlapping_decisions)} existing "
                f"decision(s) semantically overlap with new task: "
                f"{', '.join(overlapping_decisions[:5])}"
            )

        # --- Check 2: File overlap (do artifacts in other sessions) ---
        artifact_overlaps: List[str] = []
        for sid in matching_sessions:
            session_dir = _session_dir(project_root, sid)
            # Scan for do output files
            do_artifacts: List[str] = []
            for fname in os.listdir(session_dir):
                if fname.startswith("do_output") and fname.endswith(".md"):
                    do_artifacts.append(os.path.join(session_dir, fname))
            if do_artifacts:
                artifact_overlaps.append(
                    f"  Session {sid}: {len(do_artifacts)} do artifact(s)"
                )

        if artifact_overlaps:
            findings.append("File overlap (other sessions' Do artifacts):")
            findings.extend(artifact_overlaps)

        if not findings:
            return "(no overlap detected)"

        return "Overlap analysis:\n" + "\n".join(findings)

    def resolve_append_session(
        self, project_root: str, slug: str
    ) -> Tuple[Optional[str], str]:
        """Session lifecycle routing engine.

        Routes the --append request to the appropriate session:

        - Active session exists for slug -> return (session_id, "active")
        - All sessions done -> create new session, return (new_id, "new")
        - No sessions exist -> return (None, "none")

        "Active" means the session's stage is not "done" (or has no state.json).
        "All done" means every session for this slug has stage == "done".

        Args:
            project_root: Absolute path to project root.
            slug:         Session slug.

        Returns:
            Tuple of (session_id_or_None, mode_string).
        """
        ps = _load_project_state(project_root)
        if ps is None:
            return (None, "none")

        sessions_list = ps.get("sessions", [])
        sess_lookup = _sessions_to_lookup(sessions_list)
        matching_ids = [
            sid for sid, info in sess_lookup.items()
            if _get_session_slug(info) == slug
        ]

        if not matching_ids:
            return (None, "none")

        # Check each session's stage from its state.json
        active_sessions: List[str] = []
        done_sessions: List[str] = []
        for sid in matching_ids:
            state_path = _session_state_path(project_root, sid)
            if not os.path.exists(state_path):
                # No state file -> treat as active (not yet initialized stage)
                active_sessions.append(sid)
                continue
            try:
                with open(state_path) as f:
                    state = json.load(f)
                stage = state.get("stage", "?")
                if stage == "done":
                    done_sessions.append(sid)
                else:
                    active_sessions.append(sid)
            except Exception:
                # Corrupt state -> treat as active
                active_sessions.append(sid)

        if active_sessions:
            # Return the most recently created active session (last in list)
            return (active_sessions[-1], "active")

        # All done (or no state to check) -- create a new session
        new_sid = self.session_create(project_root, slug, project_id=os.path.basename(project_root))
        return (new_sid, "new")

    # ---- Session operations ----

    def session_list_by_slug(self, project_root: str, slug: str) -> str:
        """List all sessions for a given slug, including their stage and plan count.

        Args:
            project_root: Absolute path to project root.
            slug:         Session slug filter.

        Returns:
            Formatted listing.
        """
        ps = _load_project_state(project_root)
        if ps is None:
            return "ERROR: project-state.json not found."

        sessions_list = ps.get("sessions", [])
        sess_lookup = _sessions_to_lookup(sessions_list)
        matching = [
            (sid, info)
            for sid, info in sess_lookup.items()
            if _get_session_slug(info) == slug
        ]

        if not matching:
            return f"(no sessions for slug '{slug}')"

        lines: List[str] = [f"Sessions for slug '{slug}':"]
        for sid, info in sorted(matching):
            stage = "?"
            state_path = _session_state_path(project_root, sid)
            if os.path.exists(state_path):
                try:
                    with open(state_path) as f:
                        sdata = json.load(f)
                    stage = sdata.get("stage", "?")
                except Exception:
                    pass

            session_dir = _session_dir(project_root, sid)
            plan_count = 0
            if os.path.isdir(session_dir):
                plan_count = len(
                    [
                        f for f in os.listdir(session_dir)
                        if re.match(r"plan_v\d+\.md$", f) and os.path.isfile(os.path.join(session_dir, f))
                    ]
                )

            created = info.get("created_at", "?")[:10]
            lines.append(
                f"  {sid}: stage={stage} plans={plan_count} created={created}"
            )

        return "\n".join(lines)

    def session_create(self, project_root: str, slug: str, project_id: str = "") -> str:
        """Create a new session for the given slug.

        Session ID format: f"{slug}-main-{uuid4().hex[:8]}"
        This creates the session directory under .fat/pdf/sessions/ and registers
        it in project-state.json.

        Args:
            project_root: Absolute path to project root.
            slug:         Session slug.
            project_id:   Project identifier (e.g., directory basename).

        Returns:
            The new session ID string.
        """
        session_id = f"{slug}-main-{uuid.uuid4().hex[:8]}"

        # Create session directory
        session_dir = _session_dir(project_root, session_id)
        os.makedirs(session_dir, exist_ok=True)

        # Register in project-state.json (array format, consistent with ProjectStateManager)
        ps = _load_project_state(project_root)
        if ps is None:
            ps = dict(DEFAULT_PROJECT_STATE)

        ps.setdefault("sessions", []).append({
            "session_id": session_id,
            "session_slug": slug,
            "slug": slug,  # legacy key for backward compat
            "project_id": project_id or os.path.basename(project_root),
            "created_at": _ts(),
            "updated_at": _ts(),
        })
        _save_project_state(project_root, ps)

        return session_id

    # ---- Private helpers ----

    @staticmethod
    def _slugify_task_desc(desc: str) -> str:
        """Convert a task description into a URL-friendly slug.

        E.g., "Implement auth middleware" -> "implement-auth-middleware"
        """
        slug = desc.lower().strip()
        # Replace non-alphanumeric characters (except spaces/hyphens) with hyphens
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        slug = slug.strip("-")
        # Limit length to 48 chars, truncating at last whole word
        if len(slug) > 48:
            slug = slug[:48].rstrip("-")
        return slug or "task"

    @staticmethod
    def _format_task_entry(task_slug: str, task_desc: str) -> str:
        """Format a task entry for inclusion in a plan file."""
        return (
            f"- slug: {task_slug}\n"
            f"  desc: \"{task_desc}\"\n"
            f"  depends_on: []\n"
            f"  status: pending"
        )

    @staticmethod
    def _simple_diff(
        lines_a: List[str], lines_b: List[str],
        label_a: str, label_b: str, context_lines: int = 1
    ) -> List[str]:
        """Very simple line diff implementation without external libraries.

        Uses LCS (longest common subsequence) to identify added/removed lines.
        Only shows differences, not full unified diff.
        """
        # Convert to set for quick membership checks
        set_a = set(lines_a)
        set_b = set(lines_b)

        result: List[str] = []

        # Lines only in A (removed)
        only_a = set_a - set_b
        if only_a:
            result.append(f"Only in {label_a} ({len(only_a)} lines):")
            for line in lines_a:
                if line in only_a:
                    result.append(f"  -{line[:80]}")
                    break  # one sample per unique line is sufficient

        # Lines only in B (added)
        only_b = set_b - set_a
        if only_b:
            result.append(f"Only in {label_b} ({len(only_b)} lines):")
            for line in lines_b:
                if line in only_b:
                    result.append(f"  +{line[:80]}")
                    break

        # Count differences in line-by-line comparison
        max_len = max(len(lines_a), len(lines_b))
        diff_count = 0
        for i in range(max_len):
            la = lines_a[i] if i < len(lines_a) else ""
            lb = lines_b[i] if i < len(lines_b) else ""
            if la != lb:
                diff_count += 1

        if diff_count > 0:
            result.append(f"Lines differ: {diff_count}")

        return result


# ---------------------------------------------------------------------------
# DagManager
# ---------------------------------------------------------------------------


class DagManager:
    """DAG topological sort + dag commands for plan task management.

    All public methods return printable strings.
    """

    # ---- Top-level DAG pipeline ----

    def dag_build(self, plan_content: str) -> str:
        """Full DAG build pipeline from plan content.

        Steps:
            1. Parse tasks from plan content.
            2. Build adjacency graph and in-degree table via build_dag().
            3. Cycle detection via detect_cycle().
            4. Topological sort via kahn_topological_sort().
            5. Output formatted levels + dependency matrix summary.

        Args:
            plan_content: Raw markdown content of the plan file.

        Returns:
            Formatted DAG summary string.
        """
        tasks = parse_tasks_from_plan(plan_content)
        if not tasks:
            return "DAG: (no tasks found in plan content)"

        graph, in_degree = build_dag(tasks)
        cycles = detect_cycle(graph)
        levels = kahn_topological_sort(graph, in_degree)

        lines: List[str] = [
            f"DAG: {len(tasks)} tasks, {len(graph)} nodes, {sum(len(v) for v in graph.values())} edges"
        ]

        # Cycle report
        if cycles:
            lines.append(f"CYCLES DETECTED ({len(cycles)}):")
            for i, cycle in enumerate(cycles, 1):
                cycle_str = " -> ".join(cycle) + f" -> {cycle[0]}"
                lines.append(f"  Cycle {i}: {cycle_str}")
            lines.append("(topological sort unavailable due to cycle(s))")
            return "\n".join(lines)

        lines.append("Cycles: none")

        # Topological levels
        if levels:
            n_levels = len(levels)
            total_parallel = sum(len(lvl) for lvl in levels)
            lines.append(f"Levels: {n_levels} (levels 1..{n_levels})")
            for i, level in enumerate(levels, 1):
                parallel_str = " (parallel)" if len(level) > 1 else ""
                task_list = ", ".join(level)
                lines.append(f"  Level {i}{parallel_str}: [{task_list}]")
        else:
            lines.append("Levels: (non-empty graph, possibly disconnected nodes)")

        # Dependency matrix summary
        if len(tasks) <= 15:
            lines.append("\nDependency matrix:")
            # Header
            all_slugs = [t["slug"] for t in tasks]
            header = f"{'':>20}" + "".join(f"{s[:8]:>9}" for s in all_slugs)
            lines.append(header)
            diag = "\\"
            for t in tasks:
                slug = t["slug"]
                deps = set(t.get("depends_on", []))
                row = f"{slug[:20]:>20}"
                for other in all_slugs:
                    if other == slug:
                        row += f"{diag:>9}"
                    elif other in deps:
                        row += f"{'X':>9}"
                    else:
                        row += f"{'.':>9}"
                lines.append(row)

        return "\n".join(lines)

    def dag_status(self, task_states: Dict[str, str], stub_states: Optional[Dict[str, str]] = None) -> str:
        """Display a table of task states.

        Args:
            task_states: {task_slug: state}, where state is one of:
                         pending, running, done, blocked,
                         stub_available, pending_review (v5.1)
            stub_states: Optional {task_slug: stub_status} for stub column
                         (active, draft, replaced, stale)

        Returns:
            Formatted status table string.
        """
        if not task_states:
            return "(no tasks)"

        stub_states = stub_states or {}
        has_stub_info = any(slug in stub_states for slug in task_states)

        # Define sort order for states
        state_order = {"done": 0, "running": 1, "stub_available": 2, "pending": 3, "pending_review": 4, "blocked": 5}

        sorted_tasks = sorted(
            task_states.items(),
            key=lambda kv: (state_order.get(kv[1], 99), kv[0]),
        )

        lines: List[str] = []
        if has_stub_info:
            lines.append(f"{'Task':<30} {'Status':<18} {'Stub':<12}")
            lines.append("-" * 60)
            for slug, state in sorted_tasks:
                stub = stub_states.get(slug, "—")
                lines.append(f"{slug:<30} {state:<18} {stub:<12}")
        else:
            lines.append(f"{'Task':<30} {'Status':<12}")
            lines.append("-" * 42)
            for slug, state in sorted_tasks:
                lines.append(f"{slug:<30} {state:<12}")

        # Summary counts
        counts: Dict[str, int] = {}
        for state in task_states.values():
            counts[state] = counts.get(state, 0) + 1
        summary_parts = [f"{s}: {c}" for s, c in sorted(counts.items())]
        lines.append(f"\nSummary: {', '.join(summary_parts)}")

        return "\n".join(lines)

    def dag_check(self, plan_content: str, stub_states: Optional[Dict[str, str]] = None) -> str:
        """Integrity check of the DAG defined in the plan content.

        Checks:
            1. Cycle detection.
            2. Reference existence (every depends_on slug must exist as a task).
            3. Orphan node detection (tasks with no depends_on and no dependents).
            4. (v5.1) Stub integrity — every stub_available task has a valid stub.

        Args:
            plan_content: Raw markdown content of the plan file.
            stub_states:  Optional {task_slug: stub_status} for stub integrity check.

        Returns:
            Formatted check results string.
        """
        tasks = parse_tasks_from_plan(plan_content)
        if not tasks:
            return "CHECK: (no tasks found in plan content)"

        graph, in_degree = build_dag(tasks)
        all_slugs = {t["slug"] for t in tasks}
        total_nodes = len(graph)

        issues: List[str] = []
        warnings: List[str] = []

        # 1. Cycle detection
        cycles = detect_cycle(graph)
        if cycles:
            for i, cycle in enumerate(cycles, 1):
                cycle_str = " -> ".join(cycle) + f" -> {cycle[0]}"
                issues.append(f"Cycle {i}: {cycle_str}")
        else:
            warnings.append("Cycles: none (OK)")

        # 2. Reference existence
        for task in tasks:
            slug = task.get("slug", "")
            for dep in task.get("depends_on", []):
                if dep and dep not in all_slugs:
                    issues.append(
                        f"Broken reference: task '{slug}' depends_on '{dep}', "
                        f"but '{dep}' is not defined as a task"
                    )

        # 3. Orphan detection
        tasks_with_deps = {t["slug"] for t in tasks if t.get("depends_on")}
        tasks_as_dep = set()
        for t in tasks:
            tasks_as_dep.update(t.get("depends_on", []))

        # Orphans: tasks that no other task depends on AND that don't depend on anyone
        orphans = all_slugs - tasks_with_deps - (tasks_as_dep & all_slugs)
        # But exclude tasks that are dependencies for others
        # Actually, orphans are: leaf tasks that nobody depends on
        # An orphan has: in_degree == 0 AND out_degree == 0
        for task in tasks:
            slug = task.get("slug", "")
            out_degree = sum(1 for deps in graph.values() if slug in deps)
            if in_degree.get(slug, 0) == 0 and out_degree == 0:
                if slug in all_slugs:
                    orphans.add(slug)

        orphans = {o for o in orphans if o in all_slugs}
        # Remove root tasks (no deps, but others depend on them) from orphans
        for task in tasks:
            slug = task.get("slug", "")
            out_degree = sum(1 for deps in graph.values() if slug in deps)
            if in_degree.get(slug, 0) == 0 and out_degree > 0:
                # This is a root (entry point), not an orphan
                orphans.discard(slug)
            elif in_degree.get(slug, 0) > 0 and out_degree == 0:
                # This is a leaf, not an orphan
                orphans.discard(slug)

        if orphans:
            warnings.append(
                f"Isolated nodes ({len(orphans)}): {', '.join(sorted(orphans))}"
            )

        # 4. Topological sort
        if not cycles:
            levels = kahn_topological_sort(graph, in_degree)
            if levels:
                max_width = max(len(lvl) for lvl in levels)
                warnings.append(
                    f"Topological sort: {len(levels)} levels, "
                    f"max parallel width = {max_width}"
                )
            else:
                warnings.append("Topological sort: (empty or degenerate)")
        else:
            warnings.append("Topological sort: unavailable (cycles present)")

        # 4. (v5.1) Stub integrity
        stub_states = stub_states or {}
        if stub_states:
            stub_available_tasks = [s for s, st in stub_states.items() if st == "active"]
            pending_review_tasks = [s for s, st in stub_states.items() if st in ("draft", "replaced", "stale")]
            if pending_review_tasks:
                warnings.append(
                    f"Stubs pending review ({len(pending_review_tasks)}): "
                    f"{', '.join(sorted(pending_review_tasks))}"
                )
            if stub_available_tasks:
                warnings.append(
                    f"Active stubs ({len(stub_available_tasks)}): "
                    f"{', '.join(sorted(stub_available_tasks))}"
                )

        # Build result
        result_parts: List[str] = [
            f"DAG Integrity Check: {total_nodes} nodes, {len(tasks)} tasks"
        ]

        if issues:
            result_parts.append(f"\nISSUES ({len(issues)}):")
            for issue in issues:
                result_parts.append(f"  [FAIL] {issue}")
            result_parts.append("\nResult: FAIL")
        else:
            result_parts.append("Issues: none")

        for warning in warnings:
            result_parts.append(f"  {warning}")

        if not issues:
            result_parts.append("\nResult: PASS")

        return "\n".join(result_parts)

    def dag_next(
        self, tasks: List[Dict[str, Any]], task_states: Dict[str, str]
    ) -> str:
        """Compute the set of tasks ready to execute next.

        A task is "next" when ALL of its upstream dependencies are "done"
        AND the task itself is NOT "done" (including "pending" or absent
        from task_states).

        v5.1: Recognizes "stub_available" as a state where the task has
        stub info from upstream but still needs full implementation.

        Args:
            tasks:       List of task dicts from parse_tasks_from_plan().
            task_states: {task_slug: state}. Valid states: pending, running,
                         done, blocked, stub_available, pending_review.

        Returns:
            Formatted next-tasks string.
        """
        if not tasks:
            return "Next: (no tasks)"

        ready: List[str] = []
        blocked: List[str] = []
        stub_ready: List[str] = []

        for task in tasks:
            slug = task.get("slug", "")
            deps = task.get("depends_on", [])
            current_state = task_states.get(slug, "pending")

            # Skip already-finished tasks
            if current_state == "done":
                continue

            # Check if all dependencies are done
            all_deps_done = all(
                task_states.get(d, "done") == "done"
                for d in deps if d
            )

            if all_deps_done:
                if current_state in ("pending", "running"):
                    ready.append(slug)
                elif current_state == "stub_available":
                    stub_ready.append(slug)
                elif current_state == "pending_review":
                    blocked.append(f"{slug} (pending_review: upstream interface changed)")
            else:
                if current_state == "running":
                    blocked.append(f"{slug} (running with incomplete deps)")
                else:
                    dep_list = [
                        d for d in deps
                        if task_states.get(d, "done") != "done"
                    ]
                    blocked.append(f"{slug} (waiting: {', '.join(dep_list)})")

        lines: List[str] = []

        if ready:
            lines.append(f"Ready to execute ({len(ready)}):")
            for s in ready:
                lines.append(f"  * {s}")
        else:
            lines.append("Ready to execute: (none)")

        if stub_ready:
            lines.append(f"\nDesign-ready (stub) ({len(stub_ready)}):")
            for s in stub_ready:
                lines.append(f"  ~ {s} (stub available — can start design)")

        if blocked:
            lines.append(f"\nBlocked ({len(blocked)}):")
            for b in blocked:
                lines.append(f"  - {b}")

        return "\n".join(lines)

    def dag_visualize(
        self,
        tasks: List[Dict[str, Any]],
        task_states: Optional[Dict[str, str]] = None,
        stub_slugs: Optional[set] = None,
    ) -> str:
        """Generate an ASCII topology tree for the DAG.

        Tasks are grouped by topological level. Within each level, tasks are
        rendered with tree-drawing characters (├── └──).

        v5.1: stub_slugs adds an "S" marker for tasks that have a stub available.

        Args:
            tasks:       List of task dicts from parse_tasks_from_plan().
            task_states: Optional {task_slug: state} for status annotations.
            stub_slugs:  Optional set of task slugs that have stubs.

        Returns:
            ASCII topology string.
        """
        if not tasks:
            return "(no tasks to visualize)"

        task_states = task_states or {}
        stub_slugs = stub_slugs or set()
        graph, in_degree = build_dag(tasks)
        cycles = detect_cycle(graph)

        if cycles:
            return (
                "Cannot visualize: cycle(s) detected\n"
                + "\n".join(
                    f"  Cycle: {' -> '.join(c)} -> {c[0]}"
                    for c in cycles
                )
            )

        levels = kahn_topological_sort(graph, in_degree)
        if not levels:
            return "(empty graph -- nothing to visualize)"

        # Build a lookup for depends_on
        dep_map: Dict[str, List[str]] = {}
        for task in tasks:
            slug = task.get("slug", "")
            dep_map[slug] = task.get("depends_on", [])

        lines: List[str] = []
        for i, level in enumerate(levels, 1):
            n_tasks = len(level)
            parallel_tag = " (parallel)" if n_tasks > 1 else ""
            lines.append(f"Level {i}{parallel_tag}:")

            for j, slug in enumerate(level):
                is_last = j == n_tasks - 1
                prefix = "└── " if is_last else "├── "

                # Status annotation
                state = task_states.get(slug, "pending") if slug in task_states else "pending"
                state_str = f" [{state}]"

                # Stub marker
                stub_marker = " S" if slug in stub_slugs else ""

                # Dependency annotation
                deps = dep_map.get(slug, [])
                dep_str = ""
                if deps:
                    dep_str = f" (depends_on: {', '.join(deps)})"

                lines.append(f"  {prefix}{slug}{stub_marker}{state_str}{dep_str}")

        return "\n".join(lines)


# =============================================================================
# Upstream Interface Contracts parsing (v5.1 DAG stub)
# =============================================================================

RE_CONTRACT_BLOCK = re.compile(
    r"### Upstream Interface Contracts\s*\n(.*?)(?=\n### |\Z)", re.DOTALL
)
RE_CONTRACT_ENTRY = re.compile(
    r"^(\S[\w-]+)\s*\(提供接口给\s*([^)]+)\):\s*$", re.MULTILINE
)


def parse_upstream_interface_contracts(
    plan_content: str,
) -> List[Dict[str, Any]]:
    """Parse 'Upstream Interface Contracts' section from plan content.

    Returns list of contract dicts.
    Returns empty list if section not found (backward compatible, no crash).
    """
    block_match = RE_CONTRACT_BLOCK.search(plan_content)
    if not block_match:
        return []

    block_text = block_match.group(1)
    contracts: List[Dict[str, Any]] = []
    lines = block_text.split("\n")
    current_contract: Optional[Dict[str, Any]] = None
    in_code_block = False
    re_code_block_start = re.compile(r"^\s*```")

    for line in lines:
        if re_code_block_start.match(line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        entry_match = RE_CONTRACT_ENTRY.match(line)
        if entry_match:
            source = entry_match.group(1).strip()
            downstream_str = entry_match.group(2).strip()
            downstream = [s.strip() for s in downstream_str.split(",") if s.strip()]
            current_contract = {
                "source_task_slug": source,
                "downstream_slugs": downstream,
                "expected_outputs": [],
                "interface_signature": {},
            }
            contracts.append(current_contract)
            continue

        if current_contract is None:
            continue

        # expected_outputs list items: "- name: xxx / type: xxx"
        list_match = re.match(r"^\s{6}-\s+(.+)", line)
        if list_match:
            kv_text = list_match.group(1)
            if ":" in kv_text:
                k, v = kv_text.split(":", 1)
                if k.strip() in ("name", "type", "description"):
                    current_contract["expected_outputs"].append({
                        k.strip(): v.strip().strip('"'),
                    })

    return contracts


# =============================================================================
# StubManager (v5.1 DAG stub injection)
# =============================================================================


class StubManager:
    """DAG stub injection manager.

    Manages stub file lifecycle, mapping table, interface change detection,
    and notification delivery for DAG-based task dependency contracts.
    """

    # ---- Path helpers ----

    @staticmethod
    def _dag_stubs_dir(project_root: str, session_id: str) -> str:
        return os.path.join(
            project_root, ".fat", "pdf", "sessions", session_id, "dag", "stubs"
        )

    @staticmethod
    def _stub_path(project_root: str, session_id: str, task_slug: str) -> str:
        return os.path.join(
            StubManager._dag_stubs_dir(project_root, session_id), f"{task_slug}.json",
        )

    @staticmethod
    def _mapping_path(project_root: str, session_id: str) -> str:
        return os.path.join(
            StubManager._dag_stubs_dir(project_root, session_id), "stub-mapping.json",
        )

    # ---- Core stub operations ----

    def _read_stub(self, project_root: str, session_id: str, task_slug: str) -> Optional[Dict[str, Any]]:
        path = self._stub_path(project_root, session_id, task_slug)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _write_stub(self, project_root: str, session_id: str, stub: Dict[str, Any]) -> None:
        from pdf_engine_shared import _validate_stub_json, _timestamp
        validation = _validate_stub_json(stub)
        if not validation["valid"]:
            raise ValueError(f"Stub validation failed: {'; '.join(validation['errors'])}")
        path = self._stub_path(project_root, session_id, stub["source_task_slug"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stub["last_updated"] = _timestamp()
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(stub, f, indent=2, ensure_ascii=False)
        os.rename(tmp_path, path)

    def _load_mapping(self, project_root: str, session_id: str) -> Dict[str, Any]:
        path = self._mapping_path(project_root, session_id)
        if not os.path.exists(path):
            return {"schema_version": 1, "updated_at": "", "entries": []}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"schema_version": 1, "updated_at": "", "entries": []}

    def _save_mapping(self, project_root: str, session_id: str, mapping: Dict[str, Any]) -> None:
        from pdf_engine_shared import _timestamp
        path = self._mapping_path(project_root, session_id)
        mapping["updated_at"] = _timestamp()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        os.rename(tmp_path, path)

    def _rebuild_mapping(self, project_root: str, session_id: str) -> Dict[str, Any]:
        from pdf_engine_shared import _timestamp
        stubs_dir = self._dag_stubs_dir(project_root, session_id)
        mapping: Dict[str, Any] = {"schema_version": 1, "updated_at": _timestamp(), "entries": []}
        if not os.path.exists(stubs_dir):
            return mapping
        for fname in sorted(os.listdir(stubs_dir)):
            if not fname.endswith(".json") or fname == "stub-mapping.json":
                continue
            try:
                with open(os.path.join(stubs_dir, fname)) as f:
                    stub = json.load(f)
                mapping["entries"].append({
                    "stub_id": stub.get("stub_id", ""),
                    "source_task_slug": stub.get("source_task_slug", fname[:-5]),
                    "injected_at": stub.get("injected_at", ""),
                    "status": stub.get("status", "unknown"),
                    "interface_changed": False,
                    "change_severity": None,
                    "replaced_at": stub.get("replaced_at", None),
                    "replace_reference": stub.get("replace_reference", None),
                    "notified_downstream": [],
                    "pending_notifications": [],
                    "downstream_acknowledged": [],
                    "stub_file_path": fname,
                    "plan_reference": stub.get("plan_reference", ""),
                })
            except (json.JSONDecodeError, IOError):
                continue
        return mapping

    # ---- Interface change detection ----

    @staticmethod
    def _detect_interface_change(stub: Dict[str, Any], actual_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compare stub interface_signature with actual implementation.
        Returns {changed, severity, diffs}.
        """
        diffs: List[str] = []
        expected = stub.get("expected_outputs", [])
        expected_names = {e.get("name") for e in expected if e.get("name")}
        actual_names = {a.get("name") for a in actual_outputs if a.get("name")}

        added = actual_names - expected_names
        if added:
            diffs.append(f"new_outputs: {', '.join(sorted(added))}")
        removed = expected_names - actual_names
        if removed:
            diffs.append(f"removed_outputs: {', '.join(sorted(removed))}")
        for e_out in expected:
            name = e_out.get("name", "")
            for a_out in actual_outputs:
                if a_out.get("name") == name and a_out.get("type") != e_out.get("type"):
                    diffs.append(f"type_changed: {name}: {e_out.get('type')} -> {a_out.get('type')}")

        if not diffs:
            return {"changed": False, "severity": "none", "diffs": []}
        has_removed = any("removed" in d for d in diffs)
        has_type_change = any("type_changed" in d for d in diffs)
        severity = "breaking" if has_removed else ("major" if has_type_change else "minor")
        return {"changed": True, "severity": severity, "diffs": diffs}

    # ---- Public commands ----

    def stub_inject(self, project_root: str, session_id: str, plan_content: str, force: bool = False, upstream_filter: Optional[str] = None) -> str:
        """Inject/update stubs for uncompleted upstream tasks."""
        from pdf_engine_shared import _timestamp
        import uuid as _uuid

        tasks = parse_tasks_from_plan(plan_content)
        if not tasks:
            return "Stub inject: (no tasks found)"

        if not any(t.get("depends_on") for t in tasks):
            return "No DAG: stub injection skipped"

        all_slugs = {t["slug"] for t in tasks}
        downstream_map: Dict[str, List[str]] = {}
        for task in tasks:
            for dep in task.get("depends_on", []):
                if dep in all_slugs:
                    downstream_map.setdefault(dep, []).append(task["slug"])

        contracts = parse_upstream_interface_contracts(plan_content)
        contract_map = {c["source_task_slug"]: c for c in contracts}

        upstreams = [slug for slug, ds in downstream_map.items() if ds]
        if upstream_filter:
            upstreams = [s for s in upstreams if s == upstream_filter]

        if not upstreams:
            return "Stub inject: (no upstream tasks with dependents)"

        injected = 0
        lines: List[str] = []
        for slug in upstreams:
            existing = self._read_stub(project_root, session_id, slug)
            if existing and existing.get("status") == "replaced" and not force:
                lines.append(f"  {slug}: already replaced (use --force)")
                continue

            contract = contract_map.get(slug, {})
            expected_outputs = contract.get("expected_outputs", [])
            downstream_slugs = downstream_map.get(slug, [])

            stub: Dict[str, Any] = {
                "stub_id": f"stub-{slug}-{_uuid.uuid4().hex[:6]}",
                "source_task_slug": slug,
                "source_task_desc": "",
                "injected_at": _timestamp(),
                "last_updated": _timestamp(),
                "status": "draft" if not expected_outputs else "active",
                "expected_inputs": [],
                "expected_outputs": expected_outputs,
                "interface_signature": contract.get("interface_signature", {}),
                "downstream_tasks": downstream_slugs,
                "downstream_consumers": [],
                "replace_reference": None,
                "plan_reference": "plan_vN.md",
            }
            try:
                self._write_stub(project_root, session_id, stub)
                injected += 1
                lines.append(f"  {slug}: stub injected status={stub['status']}")
            except ValueError as e:
                lines.append(f"  {slug}: FAILED — {e}")

        mapping = self._rebuild_mapping(project_root, session_id)
        self._save_mapping(project_root, session_id, mapping)
        result = f"Stub inject: {injected} stub(s) for {len(upstreams)} upstream task(s)"
        if lines:
            result += "\n" + "\n".join(lines)
        return result

    def stub_status(self, project_root: str, session_id: str, source_filter: Optional[str] = None, downstream_filter: Optional[str] = None) -> str:
        """Show stub status overview table."""
        mapping = self._load_mapping(project_root, session_id)
        entries = mapping.get("entries", [])
        if not entries:
            return "Stubs: (no stubs)"

        if source_filter:
            entries = [e for e in entries if e.get("source_task_slug") == source_filter]
        if downstream_filter:
            entries = [e for e in entries if downstream_filter in e.get("downstream_acknowledged", [])]

        if not entries:
            return "Stubs: (no matching stubs)"

        lines = [f"{'Source':<24} {'Status':<12} {'Downstream':<14} {'Ack':<6} {'Changed':<9}",
                 "-" * 65]
        for e in entries:
            src = e.get("source_task_slug", "?")[:24]
            status = e.get("status", "?")
            ds_count = len(e.get("notified_downstream", [])) + len(e.get("downstream_acknowledged", []))
            ack_count = len(e.get("downstream_acknowledged", []))
            changed = "YES" if e.get("interface_changed") else "no"
            lines.append(f"{src:<24} {status:<12} {ds_count:<14} {ack_count:<6} {changed:<9}")

        counts: Dict[str, int] = {}
        for e in entries:
            counts[e.get("status", "?")] = counts.get(e.get("status", "?"), 0) + 1
        pending = sum(len(e.get("pending_notifications", [])) for e in entries)
        summary = ", ".join(f"{s}: {c}" for s, c in sorted(counts.items()))
        lines.append(f"\nSummary: {summary}")
        if pending > 0:
            lines.append(f"Notifications: {pending} pending")
        return "\n".join(lines)

    def stub_show(self, project_root: str, session_id: str, task_slug: str) -> str:
        """Show a specific stub's content."""
        stub = self._read_stub(project_root, session_id, task_slug)
        if stub is None:
            return f"No stub for task '{task_slug}'"
        clean = {k: v for k, v in stub.items() if v not in (None, [], {})}
        return json.dumps(clean, indent=2, ensure_ascii=False)

    def stub_replace(self, project_root: str, session_id: str, task_slug: str, notify: bool = False) -> str:
        """Replace a stub with real implementation details."""
        from pdf_engine_shared import _timestamp
        stub = self._read_stub(project_root, session_id, task_slug)
        if stub is None:
            return f"ERROR: no stub for task '{task_slug}'"
        if stub.get("status") == "replaced":
            return f"Stub for '{task_slug}' is already replaced"

        # Check if upstream is done
        ssp = os.path.join(project_root, ".fat", "pdf", "sessions", session_id, "state.json")
        upstream_done = False
        if os.path.exists(ssp):
            try:
                with open(ssp) as f:
                    s = json.load(f)
                if s.get("stage") in ("done", "act"):
                    upstream_done = True
            except Exception:
                pass
        if not upstream_done:
            return f"ERROR: task '{task_slug}' is not yet done"

        output_dir = os.path.join(project_root, ".fat", "pdf", "sessions", session_id, "artifacts")
        impl_locs = [os.path.join(output_dir, f) for f in sorted(os.listdir(output_dir))] if os.path.exists(output_dir) else []

        # Try to detect interface changes by comparing against upstream's plan outputs
        upstream_plan_path = os.path.join(output_dir, "..", "plan.md")
        actual_outputs: List[Dict[str, Any]] = []
        if os.path.exists(upstream_plan_path):
            try:
                with open(upstream_plan_path) as f:
                    upstream_plan = f.read()
                contracts = parse_upstream_interface_contracts(upstream_plan)
                for c in contracts:
                    if c.get("task_name", "").replace(" ", "-") == task_slug:
                        actual_outputs = c.get("expected_outputs", [])
            except Exception:
                pass

        change_result = self._detect_interface_change(stub, actual_outputs)

        stub["status"] = "replaced"
        stub["replaced_at"] = _timestamp()
        stub["replace_reference"] = {"impl_type": "file", "impl_locations": impl_locs[:5], "replaced_at": _timestamp()}
        stub["interface_changed"] = change_result["changed"]
        stub["change_severity"] = change_result["severity"] if change_result["changed"] else "none"
        try:
            self._write_stub(project_root, session_id, stub)
        except ValueError as e:
            return f"ERROR: {e}"

        mapping = self._rebuild_mapping(project_root, session_id)
        for e in mapping.get("entries", []):
            if e.get("source_task_slug") == task_slug:
                e["status"] = "replaced"
                e["replaced_at"] = _timestamp()
                e["interface_changed"] = change_result["changed"]
                e["change_severity"] = change_result["severity"] if change_result["changed"] else "none"
                if change_result["changed"]:
                    # Populate pending_notifications for downstream tasks
                    for ds in e.get("notified_downstream", []):
                        if ds not in e.get("pending_notifications", []):
                            e.setdefault("pending_notifications", []).append(ds)
        self._save_mapping(project_root, session_id, mapping)

        result = f"Stub for '{task_slug}': replaced"
        if change_result["changed"]:
            result += f"\nInterface changed ({change_result['severity']}): {', '.join(change_result['diffs'])}"
            result += "\nNotification: downstream tasks may need review"
        elif notify:
            result += "\n(notify flag set, but no interface changes detected)"
        return result

    def stub_notify(self, project_root: str, session_id: str, task_slug: Optional[str] = None, ack: bool = False) -> str:
        """Show or acknowledge notifications."""
        mapping = self._load_mapping(project_root, session_id)
        entries = mapping.get("entries", [])

        if task_slug:
            relevant = [e for e in entries if task_slug in e.get("pending_notifications", [])
                        or task_slug in e.get("downstream_acknowledged", [])
                        or e.get("source_task_slug") == task_slug]
        else:
            relevant = [e for e in entries if e.get("pending_notifications")]

        if not relevant:
            return f"No notifications for '{task_slug}'" if task_slug else "No pending notifications"

        if ack and task_slug:
            for e in relevant:
                pending = e.get("pending_notifications", [])
                if task_slug in pending:
                    pending.remove(task_slug)
                    e.setdefault("downstream_acknowledged", []).append(task_slug)
            self._save_mapping(project_root, session_id, mapping)
            return f"Notification for '{task_slug}': acknowledged"

        lines = []
        for e in relevant:
            src = e.get("source_task_slug", "?")
            severity = e.get("change_severity", "?")
            for p in e.get("pending_notifications", []):
                flag = "WARNING: breaking change" if severity == "breaking" else ""
                lines.append(f"Notification for '{p}': stub '{src}' changed ({severity}) {flag}")
        return "\n".join(lines) if lines else "No pending notifications"

    def stub_mapping(self, project_root: str, session_id: str, source_filter: Optional[str] = None, downstream_filter: Optional[str] = None, rebuild: bool = False) -> str:
        """Show or rebuild stub-mapping table."""
        from pdf_engine_shared import _timestamp
        if rebuild:
            mapping = self._rebuild_mapping(project_root, session_id)
            self._save_mapping(project_root, session_id, mapping)
            return f"Mapping rebuilt: {len(mapping.get('entries', []))} entries"

        mapping = self._load_mapping(project_root, session_id)
        entries = mapping.get("entries", [])
        if source_filter:
            entries = [e for e in entries if e.get("source_task_slug") == source_filter]
        if downstream_filter:
            entries = [e for e in entries if downstream_filter in e.get("notified_downstream", [])]

        if not entries:
            return "Mapping: (no entries)"

        lines = [f"{'Stub ID':<36} {'Source':<20} {'Status':<12} {'Changed':<9} {'Pending':<10}",
                 "-" * 87]
        for e in entries:
            lines.append(f"{e.get('stub_id','?'):<36} {e.get('source_task_slug','?'):<20} {e.get('status','?'):<12} {'YES' if e.get('interface_changed') else 'no':<9} {len(e.get('pending_notifications',[])):<10}")
        return "\n".join(lines)

    def stub_cleanup(self, project_root: str, session_id: str, dry_run: bool = False) -> str:
        """Clean up completed stub files."""
        stubs_dir = self._dag_stubs_dir(project_root, session_id)
        if not os.path.exists(stubs_dir):
            return "No stubs to clean"

        to_clean = []
        for fname in os.listdir(stubs_dir):
            if not fname.endswith(".json") or fname == "stub-mapping.json":
                continue
            fpath = os.path.join(stubs_dir, fname)
            try:
                with open(fpath) as f:
                    stub = json.load(f)
                if stub.get("status") in ("replaced", "stale"):
                    to_clean.append(fpath)
            except (json.JSONDecodeError, IOError):
                to_clean.append(fpath)

        if not to_clean:
            return "No stubs to clean"
        if dry_run:
            return f"Would clean {len(to_clean)} stub file(s):\n" + "\n".join(to_clean)
        for fpath in to_clean:
            try:
                os.remove(fpath)
            except OSError as e:
                return f"ERROR: {e}"
        self._rebuild_mapping(project_root, session_id)
        return f"Cleaned {len(to_clean)} stub file(s)."

    def stub_config(self, project_root: str, session_id: str, task_slug: str, disable_notifications: Optional[bool] = None) -> str:
        """Configure stub notification settings for a task."""
        mapping = self._load_mapping(project_root, session_id)
        entry = next((e for e in mapping.get("entries", []) if e.get("source_task_slug") == task_slug), None)
        stub = self._read_stub(project_root, session_id, task_slug)

        if entry is None and stub is None:
            return f"No stub for task '{task_slug}'"
        if entry is None:
            return f"Stub config for '{task_slug}': notifications=enabled (no mapping entry)"

        if disable_notifications is None:
            disabled = entry.get("disable_notifications", False)
            return f"Stub config for '{task_slug}': notifications={'disabled' if disabled else 'enabled'}"
        entry["disable_notifications"] = disable_notifications
        self._save_mapping(project_root, session_id, mapping)
        return f"Stub config for '{task_slug}': notifications={'disabled' if disable_notifications else 'enabled'}"


# ---------------------------------------------------------------------------
# Module-level convenience aliases
# ---------------------------------------------------------------------------

plan_manager = PlanManager()
dag_manager = DagManager()


# ---------------------------------------------------------------------------
# CLI entry point (optional, for direct testing)
# ---------------------------------------------------------------------------

def main() -> None:
    """Minimal CLI for testing the module directly.

    Usage:
        python3 pdf_v5_plan_dag.py pm plan-append <project_root> <slug> <desc>
        python3 pdf_v5_plan_dag.py pm plan-list <project_root> <slug>
        python3 pdf_v5_plan_dag.py pm plan-current <project_root> <slug>
        python3 pdf_v5_plan_dag.py pm plan-diff <project_root> <slug> <v1> <v2>
        python3 pdf_v5_plan_dag.py pm plan-history <project_root>
        python3 pdf_v5_plan_dag.py pm detect-overlap <project_root> <slug> <desc>
        python3 pdf_v5_plan_dag.py pm session-list <project_root> <slug>
        python3 pdf_v5_plan_dag.py pm session-create <project_root> <slug>
        python3 pdf_v5_plan_dag.py dm dag-build "<plan_content>"
        python3 pdf_v5_plan_dag.py dm dag-status '<json_task_states>'
        python3 pdf_v5_plan_dag.py dm dag-check "<plan_content>"
        python3 pdf_v5_plan_dag.py dm dag-next '<json_tasks>' '<json_task_states>'
        python3 pdf_v5_plan_dag.py dm dag-visualize '<json_tasks>' '[json_task_states]'
    """
    import sys
    import json as _json

    if len(sys.argv) < 3:
        print(__doc__)
        return

    manager = sys.argv[1]
    cmd = sys.argv[2]

    pm = PlanManager()
    dm = DagManager()

    try:
        if manager == "pm":
            if cmd == "plan-append" and len(sys.argv) >= 6:
                print(pm.plan_append(sys.argv[3], sys.argv[4], sys.argv[5]))
            elif cmd == "plan-list" and len(sys.argv) >= 5:
                print(pm.plan_list(sys.argv[3], sys.argv[4]))
            elif cmd == "plan-current" and len(sys.argv) >= 5:
                print(pm.plan_current(sys.argv[3], sys.argv[4]))
            elif cmd == "plan-diff" and len(sys.argv) >= 7:
                print(pm.plan_diff(sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])))
            elif cmd == "plan-history" and len(sys.argv) >= 4:
                print(pm.plan_history(sys.argv[3]))
            elif cmd == "detect-overlap" and len(sys.argv) >= 6:
                print(pm.detect_overlap(sys.argv[3], sys.argv[4], sys.argv[5]))
            elif cmd == "session-list" and len(sys.argv) >= 5:
                print(pm.session_list_by_slug(sys.argv[3], sys.argv[4]))
            elif cmd == "session-create" and len(sys.argv) >= 5:
                print(pm.session_create(sys.argv[3], sys.argv[4]))
            else:
                print(f"ERROR: unknown pm command '{cmd}'", file=sys.stderr)
        elif manager == "dm":
            if cmd == "dag-build" and len(sys.argv) >= 4:
                print(dm.dag_build(sys.argv[3]))
            elif cmd == "dag-status" and len(sys.argv) >= 4:
                states = _json.loads(sys.argv[3])
                print(dm.dag_status(states))
            elif cmd == "dag-check" and len(sys.argv) >= 4:
                print(dm.dag_check(sys.argv[3]))
            elif cmd == "dag-next" and len(sys.argv) >= 5:
                task_list = _json.loads(sys.argv[3])
                states = _json.loads(sys.argv[4])
                print(dm.dag_next(task_list, states))
            elif cmd == "dag-visualize" and len(sys.argv) >= 5:
                task_list = _json.loads(sys.argv[3])
                states = _json.loads(sys.argv[4]) if len(sys.argv) >= 5 else {}
                print(dm.dag_visualize(task_list, states))
            else:
                print(f"ERROR: unknown dm command '{cmd}'", file=sys.stderr)
        else:
            print(f"ERROR: unknown manager '{manager}'. Use 'pm' or 'dm'.", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
