"""PDF Engine — shared utilities and constants.

Extracted from pdf-engine.py for modularity.
All CLI-facing code stays in pdf-engine.py.
"""

import glob
import json
import math
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    yaml = None

# === Configuration (evaluated at module load time) ===
PROJECT_ROOT = os.getcwd()
STATE_DIR = os.path.join(PROJECT_ROOT, ".fat", "pdf")
STATE_FILE = os.path.join(STATE_DIR, ".pdf_state.json")
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNEL_RULES_FILE = os.path.join(SKILL_DIR, "docs", "channel-rules.yaml")
DECISION_ENG_DIR = os.path.join(PROJECT_ROOT, "docs", "decision-engineering")
CYCLE_DB_PATH = os.path.expanduser("~/.fat/pdf/cycle-log.db")
KNOWLEDGE_DIR = os.path.join(PROJECT_ROOT, ".fat", "pdf", "knowledge")

# === Session / Workspace Constants (project-local, following PDU's {PROJECT}.fat/ convention) ===
SESSIONS_DIR = os.path.join(PROJECT_ROOT, ".fat", "pdf", "sessions")
WORKSPACE_FILE = os.path.join(PROJECT_ROOT, ".fat", "pdf", "workspaces.json")

# === v5.1 Archive Constants ===
ARCHIVE_DIR = os.path.join(STATE_DIR, "archive")
PDF_ARCHIVE_MODEL = os.environ.get("PDF_ARCHIVE_MODEL", "claude-3-haiku-20240307")

# === Active Session Marker ===
ACTIVE_SESSION_FILE = os.path.join(STATE_DIR, ".active")


class SessionManager:
    """Session path management — project-local under .fat/pdf/sessions/<id>/"""

    @staticmethod
    def _sessions_root(ctx=None):
        """Get project-local sessions root, resolved from context or module constant."""
        if ctx is None:
            ctx = PDFContext.get_default()
        return os.path.join(ctx.project_root, ".fat", "pdf", "sessions")

    @staticmethod
    def session_path(session_id, ctx=None):
        return os.path.join(SessionManager._sessions_root(ctx), session_id)

    @staticmethod
    def state_file(session_id, ctx=None):
        return os.path.join(SessionManager.session_path(session_id, ctx), "state.json")

    @staticmethod
    def artifact_dir(session_id, ctx=None):
        return os.path.join(SessionManager.session_path(session_id, ctx), "artifacts")

    @staticmethod
    def made_dir(session_id, ctx=None):
        return os.path.join(SessionManager.session_path(session_id, ctx), "made")

    @staticmethod
    def knowledge_dir(session_id, ctx=None):
        return os.path.join(SessionManager.session_path(session_id, ctx), "knowledge")

    @staticmethod
    def list_sessions(ctx=None):
        base = SessionManager._sessions_root(ctx)
        if not os.path.exists(base):
            return []
        return sorted([
            d for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))
        ])

    @staticmethod
    def create_session(session_id, project_root, task_slug, ctx=None):
        session_dir = SessionManager.session_path(session_id, ctx)
        for subdir in ["artifacts", "made", "knowledge"]:
            os.makedirs(os.path.join(session_dir, subdir), exist_ok=True)
        return session_dir

    @staticmethod
    def read_marker(session_id, ctx=None):
        """Read .session marker file for a session. Returns dict or None."""
        marker_path = os.path.join(SessionManager.session_path(session_id, ctx), ".session")
        if not os.path.exists(marker_path):
            return None
        try:
            with open(marker_path) as f:
                return json.load(f)
        except Exception:
            return None


class PDFContext:
    """Mutable context for PDF engine path resolution.

    Singleton pattern via get_default()/set_default().
    When session_id is set: paths resolve to .fat/pdf/sessions/<id>/ under project_root.
    When session_id is None: paths fall back to project-local .fat/pdf/.
    """

    _default_instance = None

    @classmethod
    def get_default(cls):
        if cls._default_instance is None:
            cls._default_instance = cls()
        return cls._default_instance

    @classmethod
    def set_default(cls, ctx):
        cls._default_instance = ctx

    @staticmethod
    def _discover_project_root(candidate=None):
        """Walk up from CWD (or candidate) to find project-root with .fat/pdf/.pdf_state.json.
        Falls back to candidate (or CWD) if none found."""
        start = candidate or os.getcwd()
        parts = start.split(os.path.sep)
        parents = [start] + [os.path.sep.join(parts[:i]) for i in range(len(parts)-1, 0, -1)]
        for parent in parents:
            if os.path.exists(os.path.join(parent, ".fat", "pdf", ".pdf_state.json")):
                return parent
        return start

    def __init__(self, project_root=None, session_id=None):
        self._session_id = session_id
        if project_root:
            self._project_root = project_root
        else:
            self._project_root = PDFContext._discover_project_root()
        self._recalc()

    def _recalc(self):
        pdf_dir = os.path.join(self._project_root, ".fat", "pdf")
        if self._session_id:
            sid = self._session_id
            session_dir = os.path.join(pdf_dir, "sessions", sid)
            self._state_dir = session_dir
            self._state_file = os.path.join(session_dir, "state.json")
            self._artifact_dir = os.path.join(session_dir, "artifacts")
            self._made_dir = os.path.join(session_dir, "made")
            self._session_knowledge_dir = os.path.join(session_dir, "knowledge")
            self._state_is_session = True
        else:
            self._state_dir = pdf_dir
            self._state_file = os.path.join(pdf_dir, ".pdf_state.json")
            self._artifact_dir = pdf_dir
            self._made_dir = os.path.join(pdf_dir, "made")
            self._session_knowledge_dir = None
            self._state_is_session = False
        # Project-local paths (shared across modes)
        self._project_knowledge_dir = os.path.join(pdf_dir, "knowledge")
        self._dec_eng_dir = os.path.join(self._project_root, "docs", "decision-engineering")
        self._sessions_dir = os.path.join(pdf_dir, "sessions")
        self._ws_file = os.path.join(pdf_dir, "workspaces.json")

    @property
    def project_root(self):
        return self._project_root

    @project_root.setter
    def project_root(self, value):
        self._project_root = value
        self._recalc()

    @property
    def session_id(self):
        return self._session_id

    @session_id.setter
    def session_id(self, value):
        self._session_id = value
        self._recalc()

    @property
    def state_dir(self):
        return self._state_dir

    @property
    def state_file(self):
        return self._state_file

    @property
    def artifact_dir(self):
        return self._artifact_dir

    @property
    def made_dir(self):
        return self._made_dir

    @property
    def project_knowledge_dir(self):
        return self._project_knowledge_dir

    @property
    def session_knowledge_dir(self):
        return self._session_knowledge_dir

    @property
    def decision_eng_dir(self):
        return self._dec_eng_dir

    @property
    def sessions_dir(self):
        """Project-local sessions directory (.fat/pdf/sessions/)."""
        return self._sessions_dir

    @property
    def workspaces_file(self):
        """Project-local workspace registry path."""
        return self._ws_file

    @property
    def is_session_mode(self):
        return self._state_is_session

    def reload_from_session(self, session_id):
        """Reload context from a session's state.json."""
        self._session_id = session_id
        pdf_dir = os.path.join(self._project_root, ".fat", "pdf")
        state_path = os.path.join(pdf_dir, "sessions", session_id, "state.json")
        if os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    state = json.load(f)
                stored_root = state.get("project_root", self._project_root)
                self._project_root = stored_root
            except Exception:
                pass
        self._recalc()


def generate_session_id(task_slug, custom_slug=None):
    """Generate a session_id string.

    Format: {YYYYMMDD}-{slug}-{random4}
    If custom_slug is provided, use it; otherwise use task_slug sanitized.
    """
    slug = custom_slug or re.sub(r'[^a-z0-9-]', '', task_slug.lower().replace(' ', '-'))[:20]
    if not slug:
        slug = "session"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:4]
    return f"{ts}-{slug}-{suffix}"


def _load_workspace_registry():
    """Load the global workspace registry. Returns dict or default structure."""
    if not os.path.exists(WORKSPACE_FILE):
        return {"schema_version": 1, "workspaces": {}, "current": None}
    try:
        with open(WORKSPACE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"schema_version": 1, "workspaces": {}, "current": None}


def _save_workspace_registry(registry):
    """Save the global workspace registry."""
    os.makedirs(os.path.dirname(WORKSPACE_FILE), exist_ok=True)
    with open(WORKSPACE_FILE, "w") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


# === Active Session Discovery ===

def _read_active_session():
    """Read active session ID from marker file. Returns None if not set."""
    if not os.path.exists(ACTIVE_SESSION_FILE):
        return None
    try:
        with open(ACTIVE_SESSION_FILE) as f:
            return f.read().strip()
    except (IOError, OSError):
        return None


def _write_active_session(session_id):
    """Write active session ID to marker file."""
    os.makedirs(os.path.dirname(ACTIVE_SESSION_FILE), exist_ok=True)
    with open(ACTIVE_SESSION_FILE, "w") as f:
        f.write(session_id)


def _clear_active_session():
    """Remove the active session marker."""
    if os.path.exists(ACTIVE_SESSION_FILE):
        os.remove(ACTIVE_SESSION_FILE)


def _resolve_context(ctx=None):
    """Resolve context: use provided ctx, or auto-detect active session, or fall back to default."""
    if ctx is not None:
        return ctx
    active_id = _read_active_session()
    if active_id:
        return PDFContext(session_id=active_id)
    return PDFContext.get_default()


# === Channel Rules Cache ===
_channel_rules_cache = None


def _load_channel_rules():
    """Load channel-rules.yaml. Returns dict or None."""
    global _channel_rules_cache
    if _channel_rules_cache is not None:
        return _channel_rules_cache
    if yaml is None or not os.path.exists(CHANNEL_RULES_FILE):
        return None
    try:
        with open(CHANNEL_RULES_FILE) as f:
            _channel_rules_cache = yaml.safe_load(f)
        return _channel_rules_cache
    except Exception:
        return None


# === State Schema ===
BASE_STATE = {
    "schema_version": 2,
    "engine": "pdf",
    "stage": "init",
    "round": 1,
    "channel": None,
    "task_slug": "",
    "task_description": "",
    "domain": "",
    "output_kind": "",
    "stages": {
        "plan": {"N_analysis": 0, "N_design": 1, "M": 0, "agents": {}, "reviewers": {}},
        "do": {"N": 1, "M": 1, "agents": {}, "reviewers": {}},
        "check": {"N": 1, "M": 1, "agents": {}, "reviewers": {}},
        "act": {"N": 1, "M": 1, "agents": {}, "reviewers": {}}
    },
    "model_tier": {
        "plan": {"p1_model": "haiku", "p2_model": "sonnet", "p1_subagent_type": "general-purpose", "p2_subagent_type": "general-purpose"},
        "do": {"p1_model": "haiku", "p2_model": "sonnet", "p1_subagent_type": "general-purpose", "p2_subagent_type": "general-purpose"},
        "check": {"p1_model": "sonnet", "p2_model": "sonnet", "p1_subagent_type": "general-purpose", "p2_subagent_type": "general-purpose"},
        "act": {"p1_model": "sonnet", "p2_model": "sonnet", "p1_subagent_type": "general-purpose", "p2_subagent_type": "general-purpose"},
        "overrides": {},
        "override_reason": ""
    },
    "channel_config": {
        "profile": None,
        "overrides": {},
        "llm_override_reason": ""
    },
    "repair_gate_config": {
        "profile": "full",
        "max_plan_loop": 2,
        "max_do_loop": 3,
        "flaky_retry_max": 3,
        "degrade_on_exceed": "pause_and_ask",
        "repair_gate_bypassed": False,
        "degrade_reason": ""
    },
    "made_config": {
        "trigger_depth": 2,
        "n_explorers_min": 2,
        "n_explorers_max": 6,
        "max_parallel": 2,
        "fallback_on_failure": "skip"
    },
    "plan_rem_loop": 0,
    "do_rem_loop": 0,
    "remediation_type": None,
    "pending_remediation": False,
    "artifacts": [],
    "forgery_log": [],
    "checkpoint": {
        "phase": None,
        "phase_history": [],
        "log": []
    },
    "session": {
        "current": 0,
        "sessions": [],
        "resume_count": 0,
        "archive_injected": False,
        "archive_consolidated_at": None
    }
}

STAGE_ORDER = ["plan", "do", "check", "act", "done"]

PHASE_ORDER = [
    "precheck", "scope", "sanitize", "channel_select", "archive_inject",
    "tech_stack_kb", "cycle_history", "intel_injection", "factor_bootstrap",
    "factor_analysis", "made", "meta_review", "analysis", "design", "merge_plan",
    "plan_review", "plan_convergence", "plan_context", "dag_stub_inject",
    "do_p1", "do_p2", "do_repair",
    "check_p1", "check_p2", "repair_gate",
    "act_p1", "act_p2", "done"
]

PHASE_NEXT_ACTION = {
    # Plan substeps — resume_plan
    "precheck": "resume_plan", "scope": "resume_plan", "sanitize": "resume_plan",
    "channel_select": "resume_plan", "archive_inject": "resume_plan",
    "tech_stack_kb": "resume_plan",
    "cycle_history": "resume_plan", "intel_injection": "resume_plan",
    "factor_bootstrap": "resume_plan", "factor_analysis": "resume_plan",
    "made": "resume_plan", "meta_review": "resume_plan",
    "analysis": "resume_plan", "design": "resume_plan",
    "merge_plan": "resume_plan", "plan_review": "resume_plan",
    "plan_convergence": "resume_plan", "plan_context": "resume_plan",
    "dag_stub_inject": "resume_plan",
    # Do substeps — resume_do
    "do_p1": "resume_do", "do_p2": "resume_do", "do_repair": "resume_do",
    # Check substeps — resume_check
    "check_p1": "resume_check", "check_p2": "resume_check",
    "repair_gate": "resume_check",
    # Act substeps — resume_act
    "act_p1": "resume_act", "act_p2": "resume_act",
    # Done — already complete
    "done": "already_complete"
}

EXIT_CHECKLISTS = {
    "plan": [
        "Plan 已收敛？P1=0 or P3 迭代完成",
        "plan.md 存在且非空",
        "channel 已确定（lite/standard/full）",
        "M 已计算",
    ],
    "do": [
        "所有 doer 完成？agent pending == 0",
        "reviewer 意见已合并？review_do.md 存在",
        "过拆分检测已执行",
    ],
    "check": [
        "check_report.md 存在",
        "P1 已处理（修复门已执行）",
        "fix_rem_loop 未超限",
    ],
    "act": [
        "act_report.md 存在",
        "area 演化已触发（若 P1/P2 存在）",
        "cycle-log 已写入",
    ],
}

CHANNEL_RULES = {} if _load_channel_rules() is None else {}

FACTOR_TAXONOMY = {
    "security_audit": {
        "triggers": ["auth", "encryption", "input_validation", "pii", "permission"],
        "force_channel": "full",
        "add_dimensions": ["security", "data_privacy"],
    },
    "api_compatibility": {
        "triggers": ["public_api", "schema", "contract", "interface"],
        "add_dimensions": ["api_design"],
    },
    "performance_sensitive": {
        "triggers": ["hot_path", "large_data", "io_loop", "cache"],
        "add_dimensions": ["performance", "reliability"],
    },
    "data_integrity": {
        "triggers": ["migration", "transaction", "state_machine"],
        "add_dimensions": ["reliability", "test_quality"],
    },
    "compliance": {
        "triggers": ["audit_log", "regulation", "compliance"],
        "force_channel": "full",
        "add_dimensions": ["data_privacy", "maintainability"],
    },
}


def _load_dec_eng_yaml(filename, ctx=None):
    """Load a YAML file from docs/decision-engineering/. Returns dict or None."""
    if ctx is None:
        ctx = PDFContext.get_default()
    path = os.path.join(ctx.decision_eng_dir, filename)
    if yaml is None or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _keyword_in_text(text, keywords):
    """Check if any keyword appears in text."""
    for kw in keywords:
        if kw in text:
            return True
    return False


def _grep_project(pattern, project_root=None):
    """Simple grep simulation — returns True if pattern likely exists."""
    return True


def _query_cycle_db(sql, params=None):
    """Query cycle-log SQLite DB. Returns list of rows or empty on failure."""
    if not os.path.exists(CYCLE_DB_PATH):
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(CYCLE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params or [])
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _write_knowledge_json(filename, data, ctx=None):
    """Write a file into .fat/pdf/knowledge/ or session knowledge dir."""
    if ctx is None:
        ctx = PDFContext.get_default()
    base_dir = ctx.session_knowledge_dir or ctx.project_knowledge_dir
    os.makedirs(base_dir, exist_ok=True)
    with open(os.path.join(base_dir, filename), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_knowledge_json(filename, ctx=None):
    """Read a file from .fat/pdf/knowledge/ or session knowledge dir. Returns None on failure."""
    if ctx is None:
        ctx = PDFContext.get_default()
    base_dir = ctx.session_knowledge_dir or ctx.project_knowledge_dir
    path = os.path.join(base_dir, filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _get_knowledge_root():
    """Get the PDF knowledge root directory path."""
    ctx = PDFContext.get_default()
    return ctx.session_knowledge_dir or ctx.project_knowledge_dir


def _chunk_markdown_by_headers(filepath):
    """Split a markdown file by ## headers into chunks of text."""
    chunks = []
    current_section = "preamble"
    current_lines = []
    with open(filepath) as f:
        for line in f:
            if line.startswith("## "):
                if current_lines:
                    text = "".join(current_lines).strip()
                    if len(text) > 20:
                        chunks.append({"section": current_section, "text": text})
                current_section = line.strip("# ").strip()
                current_lines = [line]
            else:
                current_lines.append(line)
    if current_lines:
        text = "".join(current_lines).strip()
        if len(text) > 20:
            chunks.append({"section": current_section, "text": text})
    return chunks


def _chunk_kb_json(filepath):
    """Split a KB JSON file by top-level keys into chunks."""
    entries = []
    with open(filepath) as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key, value in data.items():
            text = json.dumps({key: value}, indent=2, ensure_ascii=False)
            if len(text) > 20:
                entries.append({"section": key, "text": text})
    elif isinstance(data, list):
        text = json.dumps(data, indent=2, ensure_ascii=False)
        if len(text) > 20:
            entries.append({"section": os.path.basename(filepath), "text": text})
    return entries


def _detect_forgery(elapsed, role, stage, config=None):
    """Two-stage forgery detection: hard floor + statistical anomaly.

    Args:
        elapsed: elapsed seconds
        role: agent role string (e.g. 'doer-1', 'reviewer')
        stage: pdf stage string (e.g. 'do', 'check')
        config: dict with min_threshold_seconds, anomaly_ratio, enabled, history_lookback_days

    Returns:
        (bool, str) — (is_forgery, reason)
    """
    if config is None:
        config = {"min_threshold_seconds": 2.0, "anomaly_ratio": 0.1, "enabled": True, "history_lookback_days": 90}

    if not config.get("enabled", True):
        return (False, "forgery_detection_disabled")

    # Stage 1: Hard floor — any elapsed below this is always forgery
    min_threshold = config.get("min_threshold_seconds", 2.0)
    if elapsed < min_threshold:
        return (True, f"elapsed={elapsed:.1f}s below hard floor {min_threshold:.1f}s")

    # Stage 2: Statistical anomaly — compared to historical median
    try:
        import subprocess
        import os
        cycle_db = os.path.expanduser("~/.fat/pdf/cycle-log.db")
        if os.path.exists(cycle_db):
            lookback = config.get("history_lookback_days", 90)
            # Call pdf-cycle-db.py median query
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf-cycle-db.py")
            if os.path.exists(script):
                result = subprocess.run(
                    [sys.executable, script, "median", role, stage],
                    capture_output=True, text=True, timeout=5
                )
                median_str = result.stdout.strip()
                if median_str and median_str != "null":
                    median_val = float(median_str)
                    anomaly_ratio = config.get("anomaly_ratio", 0.1)
                    if elapsed < anomaly_ratio * median_val:
                        return (True, f"elapsed={elapsed:.1f}s < {anomaly_ratio}×median({median_val:.1f}s)")
    except Exception:
        pass  # cold start or non-critical failure — fall through

    return (False, "normal")


# === User Memory Manager (PDF v4.11) ===

class MemoryBundle:
    """Struct for loaded user memory data."""
    def __init__(self, user=None, feedback=None, project=None, raw=None):
        self.user = user or []
        self.feedback = feedback or []
        self.project = project or []
        self.raw = raw or []

    def total(self):
        return len(self.user) + len(self.feedback) + len(self.project)

    def summary(self):
        parts = []
        if self.user:
            parts.append(f"user={len(self.user)}")
        if self.feedback:
            parts.append(f"feedback={len(self.feedback)}")
        if self.project:
            parts.append(f"project={len(self.project)}")
        return "; ".join(parts) if parts else "no entries"

    def to_preamble(self, max_tokens=400):
        """Build a short plain-text preamble for Plan P0 injection.

        Rough estimation: Chinese ~4 chars/token, English ~1 word/token.
        Keep within max_tokens budget.
        """
        lines = ["[User Context]"]
        if self.user:
            lines.append("- User profile:")
            for m in self.user[:3]:
                # Extract first meaningful line
                content = m.get("content", "")
                first_line = content.split("\n")[0][:120]
                if first_line:
                    lines.append(f"  - {first_line}")
        if self.feedback:
            lines.append("- Past feedback:")
            for m in self.feedback[:3]:
                content = m.get("content", "")
                first_line = content.split("\n")[0][:120]
                if first_line:
                    lines.append(f"  - {first_line}")
        if self.project:
            lines.append("- Project context:")
            for m in self.project[:2]:
                content = m.get("content", "")
                first_line = content.split("\n")[0][:120]
                if first_line:
                    lines.append(f"  - {first_line}")

        text = "\n".join(lines)
        # Truncate to max_tokens rough budget (~4 chars per token for Chinese)
        budget_chars = max_tokens * 4
        if len(text) > budget_chars:
            text = text[:budget_chars] + "\n... (truncated)"
        return text


class MemoryManager:
    """Scan, load, and cache user memory from ~/.claude/projects/<project>/memory/.

    Methods:
        load_all(project_path) -> MemoryBundle
        inspect(project_path) -> str (text summary)
        reload(project_path) -> MemoryBundle
        clear_cache()
    """
    _cache = {}  # class-level cache: {project_hash: MemoryBundle}

    @classmethod
    def _find_memory_dir(cls, project_path):
        """Find the memory directory for a project. Returns None if not found."""
        import os, hashlib
        try:
            real_path = os.path.realpath(os.path.abspath(project_path))
            # Hash the path to find the directory (same hash claude uses)
            path_hash = hashlib.md5(real_path.encode()).hexdigest()
            base = os.path.expanduser(f"~/.claude/projects/{path_hash}/memory")
            if os.path.isdir(base):
                return base
            # Also try the direct name-based path
            base2 = os.path.expanduser(f"~/.claude/projects/{os.path.basename(real_path)}/memory")
            if os.path.isdir(base2):
                return base2
            # Try without the memory subdir
            base3 = os.path.expanduser(f"~/.claude/projects/{path_hash}")
            if os.path.isdir(base3):
                return base3
            return None
        except Exception:
            return None

    @classmethod
    def _classify_file(cls, filename):
        """Classify a memory file by its name prefix."""
        name = filename.lower()
        if name.startswith("user"):
            return "user"
        elif name.startswith("feedback"):
            return "feedback"
        elif name.startswith("project"):
            return "project"
        else:
            return "raw"

    @classmethod
    def load_all(cls, project_path):
        """Load all memory entries for a project. Returns MemoryBundle."""
        import os
        cache_key = os.path.realpath(os.path.abspath(project_path))

        # Check cache
        if cache_key in cls._cache:
            return cls._cache[cache_key]

        bundle = MemoryBundle()
        mem_dir = cls._find_memory_dir(project_path)
        if not mem_dir:
            cls._cache[cache_key] = bundle
            return bundle

        try:
            import time, glob
            start = time.time()
            for fpath in sorted(glob.glob(os.path.join(mem_dir, "*.md"))):
                fname = os.path.basename(fpath)
                try:
                    with open(fpath, "r", errors="replace") as f:
                        content = f.read(2000)  # Read first 2000 chars max per file
                    entry = {"file": fname, "content": content, "modified": os.path.getmtime(fpath)}
                    cat = cls._classify_file(fname)
                    getattr(bundle, cat).append(entry)
                except (IOError, OSError):
                    pass
                # Safety: max 50ms scanning
                if time.time() - start > 0.2:
                    break
        except Exception:
            pass

        cls._cache[cache_key] = bundle
        return bundle

    @classmethod
    def inspect(cls, project_path):
        """Print a text summary of memory files."""
        import os, time
        bundle = cls.load_all(project_path)
        if bundle.total() == 0:
            return "no user memory found"

        lines = [f"Memory entries: {bundle.total()}"]
        lines.append(f"  User: {len(bundle.user)}")
        lines.append(f"  Feedback: {len(bundle.feedback)}")
        lines.append(f"  Project: {len(bundle.project)}")
        lines.append(f"  Raw: {len(bundle.raw)}")

        for cat, items in [("user", bundle.user), ("feedback", bundle.feedback), ("project", bundle.project), ("raw", bundle.raw)]:
            for m in items:
                mod_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("modified", 0)))
                lines.append(f"  [{cat}] {m['file']} ({mod_time})")

        return "\n".join(lines)

    @classmethod
    def reload(cls, project_path):
        """Clear cache for a project and reload."""
        import os
        cache_key = os.path.realpath(os.path.abspath(project_path))
        cls._cache.pop(cache_key, None)
        bundle = cls.load_all(project_path)
        return bundle

    @classmethod
    def clear_cache(cls):
        """Clear all cached memory data."""
        cls._cache.clear()

    @classmethod
    def list(cls, project_path):
        """列出所有可注入记忆条目（简洁摘要）。返回字符串。"""
        bundle = cls.load_all(project_path)
        if bundle.total() == 0:
            return "no memory entries"
        lines = [f"Memory entries: {bundle.total()}"]
        for cat, label in [("user", "User"), ("feedback", "Feedback"), ("project", "Project"), ("raw", "Raw")]:
            items = getattr(bundle, cat)
            if items:
                for m in items:
                    content = m.get("content", "")
                    first_line = content.split("\n")[0][:80] if content else "(empty)"
                    lines.append(f"  [{label}] {m['file']}: {first_line}")
        return "\n".join(lines)

    @classmethod
    def inject(cls, project_path):
        """手动生成记忆注入文本（调用 to_preamble()）。返回 str。"""
        bundle = cls.load_all(project_path)
        if bundle.total() == 0:
            return "no memory to inject"
        preamble = bundle.to_preamble()
        return preamble

    @staticmethod
    def ignore_list_path(project_path):
        """返回 .fat/pdf/memory-ignore-list.json 路径。"""
        return os.path.join(project_path, ".fat", "pdf", "memory-ignore-list.json")

    @classmethod
    def ignore_add(cls, project_path, key_or_pattern):
        """添加忽略条目。key 是精确 key 名或正则。返回 confirmation。"""
        path = cls.ignore_list_path(project_path)
        data = {"schema_version": 1, "entries": []}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        # 检查是否已存在
        for e in data.get("entries", []):
            if e.get("key") == key_or_pattern or e.get("pattern") == key_or_pattern:
                return f"entry already exists (id={e['id']})"
        new_id = max([e.get("id", 0) for e in data.get("entries", [])] or [0]) + 1
        # 判断是 key 还是 pattern — pattern 含正则特殊字符
        is_pattern = bool(re.search(r'[.*+?^${}()|\[\]\\]', key_or_pattern))
        entry = {
            "id": new_id,
            "key": key_or_pattern if not is_pattern else None,
            "pattern": key_or_pattern if is_pattern else None,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        data.setdefault("entries", []).append(entry)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return f"ignore entry added (id={new_id}, key='{key_or_pattern}')"

    @classmethod
    def ignore_remove(cls, project_path, entry_id_or_key):
        """移除忽略条目。返回 confirmation。"""
        path = cls.ignore_list_path(project_path)
        if not os.path.exists(path):
            return "no ignore list found"
        with open(path) as f:
            data = json.load(f)
        entries = data.get("entries", [])
        # Try numeric id first
        try:
            eid = int(entry_id_or_key)
            new_entries = [e for e in entries if e.get("id") != eid]
        except ValueError:
            new_entries = [e for e in entries if e.get("key") != entry_id_or_key and e.get("pattern") != entry_id_or_key]
        if len(new_entries) == len(entries):
            return f"no matching entry found for '{entry_id_or_key}'"
        data["entries"] = new_entries
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return f"ignore entry removed ({len(entries) - len(new_entries)} deleted)"

    @classmethod
    def ignore_list(cls, project_path):
        """列出所有忽略条目。返回字符串。"""
        path = cls.ignore_list_path(project_path)
        if not os.path.exists(path):
            return "no ignore entries"
        with open(path) as f:
            data = json.load(f)
        entries = data.get("entries", [])
        if not entries:
            return "ignore list is empty"
        lines = [f"Ignore entries ({len(entries)}):"]
        for e in entries:
            key = e.get("key") or e.get("pattern", "")
            added = e.get("added_at", "?")[:10]
            lines.append(f"  id={e['id']}: {key} (added: {added})")
        return "\n".join(lines)


def _seed_history_data(state, project_root=None):
    """Generate synthetic seed history in cycle-log.db to bootstrap recommendations.

    Analyzes project code patterns to infer complexity, generates 2-4 seed cycles.
    Sets state[\"_history_seeded\"] = True to prevent duplicate generation.

    Returns: int — number of seed entries generated (0 on failure/skip)
    """
    import os, json

    if state.get("_history_seeded"):
        return 0  # already seeded

    cycle_db = os.path.expanduser("~/.fat/pdf/cycle-log.db")
    if not os.path.exists(cycle_db):
        return 0  # no cycle DB yet

    if project_root is None:
        project_root = os.getcwd()

    # Analyze project code patterns
    try:
        total_size = 0
        file_count = 0
        large_files = 0
        extensions = {}
        for root, dirs, files in os.walk(project_root):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules" and d != "__pycache__"]
            for f in files:
                ext = os.path.splitext(f)[1]
                if ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php", ".swift", ".kt"):
                    path = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(path)
                        total_size += sz
                        file_count += 1
                        if sz > 10000:
                            large_files += 1
                        extensions[ext] = extensions.get(ext, 0) + 1
                    except (OSError, IOError):
                        pass
    except Exception:
        pass

    # Infer complexity
    if file_count == 0:
        complexity = "simple"  # no code files found
    elif large_files > 5 or file_count > 50:
        complexity = "complex"
    elif large_files > 0 or file_count > 10:
        complexity = "moderate"
    else:
        complexity = "simple"

    # Map complexity to model allocation
    if complexity == "complex":
        seeds = [
            '{"do.p1":"sonnet","do.p2":"opus","plan.analysis":"sonnet","plan.design":"sonnet","check.p1":"sonnet","act.p1":"sonnet"}',
            '{"do.p1":"sonnet","do.p2":"sonnet","plan.analysis":"sonnet","plan.design":"haiku","check.p1":"sonnet","act.p1":"sonnet"}',
        ]
    elif complexity == "moderate":
        seeds = [
            '{"do.p1":"sonnet","do.p2":"sonnet","plan.analysis":"sonnet","plan.design":"haiku","check.p1":"sonnet","act.p1":"sonnet"}',
            '{"do.p1":"sonnet","do.p2":"sonnet","plan.analysis":"haiku","plan.design":"haiku"}',
        ]
    else:
        seeds = [
            '{"do.p1":"sonnet","do.p2":"sonnet","plan.analysis":"sonnet","plan.design":"haiku","check.p1":"sonnet","act.p1":"haiku"}',
        ]

    # Insert seed cycles with is_seed=1
    import sqlite3
    from datetime import datetime
    inserted = 0
    try:
        conn = sqlite3.connect(cycle_db)
        # Ensure is_seed column exists
        try:
            conn.execute("ALTER TABLE cycles ADD COLUMN is_seed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        for i, alloc_json in enumerate(seeds):
            conn.execute(
                "INSERT INTO cycles (task_slug, project, completed, stage, n, m, p1_found, p2_found, n_m_accuracy, missed_dimension, lesson, model_allocation, effectiveness, is_seed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"seed-{i}", os.path.basename(project_root), "1970-01-01", "do", 2, 2, 0, 0, "seed_data", "", "PDF v4.11 auto-seed: " + complexity, alloc_json, 0.3, 1)
            )
            inserted += 1
        conn.commit()
        conn.close()
        state["_history_seeded"] = True
        state["_history_seed_info"] = {"source": "auto", "generated_at": datetime.now().isoformat(), "n_entries": inserted, "complexity": complexity}
    except Exception:
        return 0

    return inserted


# === Helpers ===

def _estimate_tokens(text):
    """Simple char-to-token estimation: ~4 chars ≈ 1 token (Chinese-heavy workload)."""
    return math.ceil(len(text) / 4)


def _parse_frontmatter(text):
    """Parse YAML frontmatter from markdown text.

    Uses pyyaml if available, else regex fallback to extract --- blocks.
    Returns (dict, str) — (parsed_data, error_message).
    """
    match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not match:
        return None, "no_frontmatter"
    yaml_block = match.group(1)
    if yaml is not None:
        try:
            data = yaml.safe_load(yaml_block)
            if isinstance(data, dict):
                return data, "OK"
            return {}, "OK"
        except yaml.YAMLError as e:
            return None, f"yaml_parse_error: {e}"
    # Fallback: minimal key-value extraction without pyyaml
    result = {}
    for line in yaml_block.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            result[k.strip()] = v.strip()
    return result, "OK (fallback)"


def _validate_act_frontmatter(data):
    """Validate act_report frontmatter schema.

    Returns dict: {valid: bool, errors: list, warnings: list}
    """
    errors = []
    warnings = []

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["root is not a dict"], "warnings": []}

    # decisions[]
    decisions = data.get("decisions", [])
    if not isinstance(decisions, list):
        errors.append("decisions must be an array")
    else:
        for i, d in enumerate(decisions):
            if not isinstance(d, dict):
                errors.append(f"decisions[{i}]: must be an object")
                continue
            for field in ("id", "key", "value", "rationale", "confidence"):
                if field not in d:
                    errors.append(f"decisions[{i}]: missing required field '{field}'")
            if "confidence" in d and d["confidence"] not in ("high", "medium", "low"):
                warnings.append(f"decisions[{i}]: confidence '{d['confidence']}' not in {{high, medium, low}}")

    # changes[]
    changes = data.get("changes", [])
    if not isinstance(changes, list):
        errors.append("changes must be an array")
    else:
        for i, c in enumerate(changes):
            if not isinstance(c, dict):
                errors.append(f"changes[{i}]: must be an object")
                continue
            for field in ("file", "summary", "lines_added", "lines_removed"):
                if field not in c:
                    errors.append(f"changes[{i}]: missing required field '{field}'")

    # blockers[]
    blockers = data.get("blockers", [])
    if not isinstance(blockers, list):
        errors.append("blockers must be an array")
    else:
        for i, b in enumerate(blockers):
            if not isinstance(b, dict):
                errors.append(f"blockers[{i}]: must be an object")
                continue
            for field in ("id", "description", "severity", "status"):
                if field not in b:
                    errors.append(f"blockers[{i}]: missing required field '{field}'")

    # milestones[]
    milestones = data.get("milestones", [])
    if milestones is not None:
        if not isinstance(milestones, list):
            errors.append("milestones must be an array")
        else:
            for i, m in enumerate(milestones):
                if not isinstance(m, str):
                    errors.append(f"milestones[{i}]: must be a string")

    # metrics (optional mapping)
    metrics = data.get("metrics", {})
    if metrics is not None and not isinstance(metrics, dict):
        errors.append("metrics must be a mapping")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def _validate_stub_json(stub):
    """Validate a DAG stub JSON structure.

    Returns dict: {valid: bool, errors: list}
    """
    errors = []
    required = ("stub_id", "source_task_slug", "status", "injected_at")
    for field in required:
        if field not in stub:
            errors.append(f"missing required field: {field}")

    valid_statuses = ("active", "replaced", "stale", "removed", "draft")
    if "status" in stub and stub["status"] not in valid_statuses:
        errors.append(f"status must be one of {valid_statuses}")

    for arr_name in ("expected_inputs", "expected_outputs"):
        arr = stub.get(arr_name, [])
        if not isinstance(arr, list):
            errors.append(f"{arr_name} must be an array")
        else:
            for i, item in enumerate(arr):
                if not isinstance(item, dict):
                    errors.append(f"{arr_name}[{i}]: must be an object")
                    continue
                if "name" not in item or "type" not in item:
                    errors.append(f"{arr_name}[{i}]: missing name or type")

    return {"valid": len(errors) == 0, "errors": errors}


# === Archive State Persistence ===

ARCHIVE_STATE_FILE = os.path.join(ARCHIVE_DIR, ".archive-state.json")


def _load_archive_state():
    """Load archive state from .archive-state.json. Returns dict or default."""
    if not os.path.exists(ARCHIVE_STATE_FILE):
        return {
            "last_consolidated": None,
            "consolidated_weeks": [],
            "total_reports_processed": 0,
            "compression_method": None,
            "last_clean": None,
        }
    try:
        with open(ARCHIVE_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {
            "last_consolidated": None,
            "consolidated_weeks": [],
            "total_reports_processed": 0,
            "compression_method": None,
            "last_clean": None,
        }


def _save_archive_state(state):
    """Save archive state to .archive-state.json (atomic write)."""
    os.makedirs(os.path.dirname(ARCHIVE_STATE_FILE), exist_ok=True)
    tmp_path = ARCHIVE_STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.rename(tmp_path, ARCHIVE_STATE_FILE)


def _get_iso_week_from_mtime(mtime):
    """Get ISO week string (YYYY-WW) from a mtime timestamp."""
    from datetime import datetime
    dt = datetime.fromtimestamp(mtime)
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ===

def _load_state(ctx=None):
    ctx = _resolve_context(ctx)
    if not os.path.exists(ctx.state_file):
        return None
    try:
        with open(ctx.state_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _save_state(state, ctx=None):
    ctx = _resolve_context(ctx)
    os.makedirs(ctx.state_dir, exist_ok=True)
    with open(ctx.state_file, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _require_state(ctx=None):
    ctx = _resolve_context(ctx)
    state = _load_state_with_migration(ctx=ctx)
    if state is None:
        print("ERROR: No state found. Run 'pdf-engine.py init <task-slug>' first.", file=sys.stderr)
        sys.exit(1)
    return state


def _agent_status(agent_value):
    if isinstance(agent_value, str):
        return agent_value
    return agent_value.get("status", "unknown") if isinstance(agent_value, dict) else "unknown"


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _phase_index(phase_name):
    """Get index of a phase in PHASE_ORDER, or -1 if not found."""
    try:
        return PHASE_ORDER.index(phase_name)
    except ValueError:
        return -1


# === Migration ===

def _migrate_v1_to_v2(state):
    """Auto-migrate v1 state to v2 by filling in default config fields."""
    if state.get("schema_version", 1) < 2:
        state.setdefault("model_tier", {
            "plan": {"p1_model": "haiku", "p2_model": "sonnet", "p1_subagent_type": "general-purpose",
                     "p2_subagent_type": "general-purpose"},
            "do": {"p1_model": "haiku", "p2_model": "sonnet", "p1_subagent_type": "general-purpose",
                   "p2_subagent_type": "general-purpose"},
            "check": {"p1_model": "sonnet", "p2_model": "sonnet", "p1_subagent_type": "general-purpose",
                      "p2_subagent_type": "general-purpose"},
            "act": {"p1_model": "sonnet", "p2_model": "sonnet", "p1_subagent_type": "general-purpose",
                    "p2_subagent_type": "general-purpose"},
            "overrides": {},
            "override_reason": ""
        })
        state.setdefault("channel_config", {
            "profile": state.get("channel"),
            "overrides": {},
            "llm_override_reason": ""
        })
        state.setdefault("repair_gate_config", {
            "profile": "full",
            "max_plan_loop": 2,
            "max_do_loop": 3,
            "flaky_retry_max": 3,
            "degrade_on_exceed": "pause_and_ask",
            "repair_gate_bypassed": False,
            "degrade_reason": ""
        })
        state.setdefault("made_config", {
            "trigger_depth": 2,
            "n_explorers_min": 2,
            "n_explorers_max": 6,
            "max_parallel": 2,
            "fallback_on_failure": "skip"
        })
        state["schema_version"] = 2
        print("  (migrated v1 state to v2 — added model_tier/channel_config/repair_gate/made defaults)")

    # Ensure checkpoint/session keys exist (all schema versions)
    state.setdefault("checkpoint", {
        "phase": None,
        "phase_history": [],
        "log": []
    })
    state.setdefault("session", {
        "current": 0,
        "sessions": [],
        "resume_count": 0
    })
    return state


def _migrate_v2_to_v3(state):
    """Auto-migrate v2 state to v3 by adding multi_session block.

    Additive only — adds fields, never removes or modifies existing ones.
    """
    if state.get("schema_version", 1) >= 3:
        return state

    project_root = state.get("project_root") or os.getcwd()
    slug = state.get("task_slug", "migrated")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_id = f"{ts}-{slug}-{uuid.uuid4().hex[:4]}"

    state.setdefault("multi_session", {
        "version": 1,
        "session_id": session_id,
        "project_id": os.path.basename(project_root),
        "project_root": project_root,
        "session_created_at": _timestamp(),
        "session_updated_at": _timestamp(),
    })
    state["schema_version"] = 3
    print(f"  (migrated v2 state to v3 — assigned session_id={session_id})")
    return state


def _load_state_with_migration(ctx=None):
    # Do not force get_default() here — _load_state → _resolve_context
    # reads .active session marker to find the correct session across CLI invocations.
    state = _load_state(ctx=ctx)
    if state is None:
        return None
    state = _migrate_v1_to_v2(state)
    state = _migrate_v2_to_v3(state)
    return state


def _parse_markdown_sections(filepath):
    """Parse markdown file into {section_name: content_lines} dict.

    - ## section_name starts a new section
    - Content before first ## is "__preamble__"
    - Empty file returns {"__preamble__": []}
    """
    sections = {}
    current_section = "__preamble__"
    current_lines = []

    if not os.path.exists(filepath):
        return {"__preamble__": []}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (IOError, OSError):
        return {"__preamble__": []}

    for line in lines:
        if line.startswith("## "):
            sections[current_section] = current_lines
            current_section = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_section] = current_lines
    return sections


def _write_markdown_sections(filepath, sections, key, value):
    """Write sections dict back to markdown file with key/value appended/updated.

    Returns action: "created" | "updated" | "appended"
    """
    import tempfile

    if key in sections and key != "__preamble__":
        sections[key] = [value if value.endswith("\n") else value + "\n"]
        action = "updated"
    elif "__preamble__" in sections and sections["__preamble__"] and len(sections) == 1:
        old_preamble = "".join(sections["__preamble__"])
        sections["__preamble__"] = []
        sections["original"] = [old_preamble]
        sections[key] = [value if value.endswith("\n") else value + "\n"]
        action = "appended"
    else:
        sections[key] = [value if value.endswith("\n") else value + "\n"]
        action = "appended"

    ts = _timestamp()

    lines = []
    if sections.get("__preamble__"):
        lines.extend(sections["__preamble__"])
        if not lines[-1].endswith("\n"):
            lines[-1] += "\n"

    for section_name, section_lines in sections.items():
        if section_name == "__preamble__":
            continue
        lines.append(f"## {section_name}\n")
        for sl in section_lines:
            if not sl.endswith("\n"):
                sl += "\n"
            lines.append(sl)
        lines.append("\n")

    lines.append(f"<!-- last updated: {ts} -->\n")

    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(filepath) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.rename(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return action
