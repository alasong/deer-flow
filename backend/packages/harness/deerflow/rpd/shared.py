"""RPD Engine — shared constants, state persistence, and helpers.

DeerFlow port of the original RPD skill's rpd_engine_shared.py.
State file is compatible: .fat/rpd/rpd_state.json
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone

# ── Schema ──────────────────────────────────────────────────────────────
SCHEMA_VERSION = 1

# ── Paths ────────────────────────────────────────────────────────────────
# Methodologies live with the DeerFlow RPD skill
_RPD_METHODOLOGIES_DIR = os.environ.get("RPD_METHODOLOGIES_DIR")
if _RPD_METHODOLOGIES_DIR:
    METHODOLOGIES_DIR = _RPD_METHODOLOGIES_DIR
else:
    METHODOLOGIES_DIR = os.path.join(os.getcwd(), "skills", "public", "rpd", "docs", "methodologies")

# State directory: $RPD_STATE_DIR / .fat/rpd/ / cwd/.fat/rpd/
_RPD_STATE_DIR_ENV = os.environ.get("RPD_STATE_DIR")
if _RPD_STATE_DIR_ENV:
    STATE_DIR = _RPD_STATE_DIR_ENV
    STATE_FILE = os.path.join(STATE_DIR, "rpd_state.json")
else:
    PROJECT_ROOT = os.getcwd()
    STATE_DIR = os.path.join(PROJECT_ROOT, ".fat", "rpd")
    STATE_FILE = os.path.join(STATE_DIR, "rpd_state.json")

# ── Constants ───────────────────────────────────────────────────────────
NODE_STATUSES = frozenset({"pending", "running", "done", "failed", "skipped", "waiting_for_children"})
PHASES = frozenset({"P", "D", "C", "A"})
PHASE_ORDER = {"P": 0, "D": 1, "C": 2, "A": 3}

# Execution style: controls LLM's posture when executing a node
#   divergent  — explore broadly, generate options
#   convergent — narrow down to a precise conclusion
#   strict     — follow specification exactly, no creative deviation
#   balanced   — default, LLM's own judgment
STYLES = frozenset({"divergent", "convergent", "strict", "balanced"})
DEFAULT_STYLE = "balanced"

# Valid phase+mode combinations
PHASE_MODES = {
    "P": ["decompose", "architecture", "research", "plan", "spike"],
    "D": ["implement", "design", "configure", "explore", "synthesize"],
    "C": ["review", "verify", "evaluate", "audit"],
    "A": ["standardize", "merge", "reflect", "document"],
}

# Methodology registry: (phase, mode) → recommended methodology names
METHODOLOGY_REGISTRY = {
    ("P", "architecture"): ["adr-first", "spike-and-stabilize"],
    ("P", "spike"): ["spike-and-stabilize"],
    ("P", "decompose"): [],
    ("P", "research"): [],
    ("P", "plan"): [],
    ("D", "implement"): ["tdd", "api-first"],
    ("D", "design"): ["adr-first"],
    ("D", "configure"): [],
    ("D", "explore"): [],
    ("C", "review"): [],
    ("C", "verify"): [],
    ("C", "evaluate"): [],
    ("C", "audit"): [],
    ("A", "standardize"): [],
    ("A", "merge"): [],
    ("A", "reflect"): [],
    ("A", "document"): [],
}
_ALL_METHODOLOGIES: list[str] | None = None


# ── Helpers ─────────────────────────────────────────────────────────────

def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_id() -> str:
    return uuid.uuid4().hex[:12]


# ── Tree Node Factory ───────────────────────────────────────────────────

def make_node(
    phase: str = "P",
    mode: str | None = None,
    title: str = "",
    description: str = "",
    methodology: str | None = None,
    dependencies: list[str] | None = None,
    style: str = "balanced",
) -> dict:
    if style not in STYLES:
        style = DEFAULT_STYLE
    return {
        "id": generate_id(),
        "phase": phase,
        "mode": mode,
        "title": title,
        "description": description,
        "methodology": methodology,
        "style": style,
        "status": "pending",
        "dependencies": dependencies or [],
        "children": [],
        "decision_log": [],
    }


def make_default_state(slug: str, goal: str = "") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "goal": goal,
        "status": "pending",
        "created_at": timestamp(),
        "updated_at": timestamp(),
        "session": {
            "id": generate_id(),
            "slug": slug,
            "created_at": timestamp(),
            "artifacts_dir": None,
        },
        "root": make_node(phase="P", title=f"Task: {slug}", description=goal),
        "artifacts": {},
    }


# ── State I/O ───────────────────────────────────────────────────────────

def _ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)


def load_state() -> dict | None:
    if not os.path.isfile(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        # Schema migration placeholder
        if data.get("schema_version", 0) < SCHEMA_VERSION:
            pass  # future: migrate
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_state(state: dict):
    _ensure_state_dir()
    state["updated_at"] = timestamp()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def require_state() -> dict:
    state = load_state()
    if state is None:
        raise RuntimeError("No active RPD task. Use `rpd init` first.")
    return state


# ── Methodology I/O ─────────────────────────────────────────────────────

def list_available_methodologies() -> list[str]:
    global _ALL_METHODOLOGIES
    if _ALL_METHODOLOGIES is not None:
        return _ALL_METHODOLOGIES
    if not os.path.isdir(METHODOLOGIES_DIR):
        _ALL_METHODOLOGIES = []
        return _ALL_METHODOLOGIES
    files = sorted(f for f in os.listdir(METHODOLOGIES_DIR) if f.endswith(".md"))
    _ALL_METHODOLOGIES = [f.removesuffix(".md") for f in files]
    return _ALL_METHODOLOGIES


def get_methodology(name: str) -> str | None:
    path = os.path.join(METHODOLOGIES_DIR, f"{name}.md")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return f.read()


def get_methodology_names_for(phase: str, mode: str) -> list[str]:
    return METHODOLOGY_REGISTRY.get((phase, mode), [])


# ── Tree Helpers ────────────────────────────────────────────────────────

def _collect_leaves(node: dict) -> list[dict]:
    """Collect leaf nodes from the tree (nodes with no children)."""
    if not node.get("children"):
        return [node]
    leaves = []
    for child in node["children"]:
        leaves.extend(_collect_leaves(child))
    return leaves


def _collect_all_pending_ancestors(node: dict) -> list[dict]:
    """Collect all pending nodes upward."""
    result = []
    if node.get("children"):
        for child in node["children"]:
            result.extend(_collect_all_pending_ancestors(child))
    if node["status"] in ("pending", "running", "waiting_for_children"):
        result.append(node)
    return result


def _all_deps_done(node: dict, node_map: dict[str, dict]) -> bool:
    """Check if all dependencies of node are done."""
    for dep_id in node.get("dependencies", []):
        dep = node_map.get(dep_id)
        if dep is None:
            continue
        if dep["status"] != "done":
            return False
    return True


def _resolve_waves(nodes: list[dict]) -> dict[str, int]:
    """Assign wave numbers to nodes based on dependency depth.

    Wave 0: no dependencies
    Wave N: max(dependency_waves) + 1
    """
    node_map = {n["id"]: n for n in nodes}
    wave: dict[str, int] = {}

    changed = True
    while changed:
        changed = False
        for n in nodes:
            nid = n["id"]
            if nid in wave:
                continue
            deps = n.get("dependencies", [])
            if not deps:
                wave[nid] = 0
                changed = True
            else:
                dep_waves = [wave.get(d) for d in deps if d in node_map]
                if dep_waves and all(w is not None for w in dep_waves):
                    wave[nid] = max(dep_waves) + 1
                    changed = True

    # Default remaining to 0
    for n in nodes:
        if n["id"] not in wave:
            wave[n["id"]] = 0
    return wave


def _count_tree_nodes(node: dict) -> int:
    """Count all nodes in the tree rooted at node."""
    count = 0
    stack = [node]
    while stack:
        n = stack.pop()
        count += 1
        for c in n.get("children", []):
            stack.append(c)
    return count


def _find_node_chain(node: dict, target_id: str) -> list[dict] | None:
    """Find path from root to the node with target_id.

    Returns [root, ..., target] or None.
    """
    if node["id"] == target_id:
        return [node]
    for child in node.get("children", []):
        chain = _find_node_chain(child, target_id)
        if chain is not None:
            return [node] + chain
    return None


def _find_parent_child_index(node: dict, target_id: str) -> tuple[dict | None, int]:
    """Find parent and child index of target_id. Returns (parent, index) or (None, -1)."""
    for i, child in enumerate(node.get("children", [])):
        if child["id"] == target_id:
            return node, i
        parent, idx = _find_parent_child_index(child, target_id)
        if parent is not None:
            return parent, idx
    return None, -1
