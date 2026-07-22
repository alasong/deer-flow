"""Memory write-back commands for pdf-engine."""
import hashlib
import json
import os
import sys

from pdf_engine_shared import (
    MemoryManager,
    _load_state,
    _load_state_with_migration,
    _save_state,
    _timestamp,
)


# ── Helpers ───────────────────────────────────────────


def _compute_memory_dir(project_path):
    """Compute the Claude memory directory path for a given project.

    Hash algorithm: hashlib.md5(os.path.realpath(project_path).encode()).hexdigest()[:10]
    """
    real_path = os.path.realpath(os.path.abspath(project_path))
    path_hash = hashlib.md5(real_path.encode()).hexdigest()[:10]
    return os.path.expanduser(f"~/.claude/projects/{path_hash}/memory/")


_CATEGORY_MAP = {
    "user": "user.md",
    "feedback": "feedback.md",
    "project": "project.md",
    "raw": "raw.md",
}


def _resolve_category_file(project_path, key, loaded_bundle=None):
    """Resolve a key to a target memory file path using priority rules."""
    mem_dir = _compute_memory_dir(project_path)

    if ":" in key:
        category, entry = key.split(":", 1)
        category = category.strip().lower()
        if category in _CATEGORY_MAP:
            return os.path.join(mem_dir, _CATEGORY_MAP[category])

    bundle = loaded_bundle or MemoryManager.load_all(project_path)
    for cat in ("user", "feedback", "project", "raw"):
        for item in getattr(bundle, cat, []):
            fname = item.get("file", "")
            if fname == key or fname[:-3] == key:
                return os.path.join(mem_dir, fname)

    for cat in ("user", "feedback", "project", "raw"):
        for item in getattr(bundle, cat, []):
            content = item.get("content", "")
            first_line = content.split("\n")[0].strip()
            if key.lower() in first_line.lower():
                return os.path.join(mem_dir, item.get("file", _CATEGORY_MAP[cat]))

    key_lower = key.lower()
    if any(w in key_lower for w in ("prefer", "like", "习惯", "喜欢", "favorite")):
        return os.path.join(mem_dir, "user.md")
    if any(w in key_lower for w in ("feedback", "review", "suggest", "建议", "reviewer")):
        return os.path.join(mem_dir, "feedback.md")
    if any(w in key_lower for w in ("project", "arch", "deploy", "架构", "项目", "决策")):
        return os.path.join(mem_dir, "project.md")

    return os.path.join(mem_dir, "user.md")


from pdf_engine_shared import _parse_markdown_sections, _write_markdown_sections


def _get_entries_count(filepath):
    """Count number of ## sections in a memory file."""
    sections = _parse_markdown_sections(filepath)
    return len([s for s in sections if s != "__preamble__"])


# ── Memory Commands ───────────────────────────────────


def cmd_memory_update(key, value):
    """Write a memory entry.

    Usage: pdf-engine.py memory update <key> <value> [--project <path>] [--yes] [--dry-run]
    """
    raw_args = sys.argv
    project_root = os.getcwd()
    yes_mode = "--yes" in raw_args
    dry_run = "--dry-run" in raw_args

    if yes_mode and dry_run:
        print("ERROR: --yes and --dry-run are mutually exclusive", file=sys.stderr)
        return

    for i, a in enumerate(raw_args):
        if a == "--project" and i + 1 < len(raw_args):
            project_root = raw_args[i + 1]

    if not value or value.strip() == "":
        print("ERROR: empty value, nothing to write", file=sys.stderr)
        return

    if len(value) > 10000:
        print("WARNING: value exceeds 10K chars", file=sys.stderr)

    bundle = MemoryManager.load_all(project_root)
    target_file = _resolve_category_file(project_root, key, bundle)

    if target_file.endswith("memory-ignore-list.json") or target_file.endswith("state.json"):
        print("ERROR: target is a protected file (memory-ignore-list.json or state.json)", file=sys.stderr)
        return

    sections = _parse_markdown_sections(target_file) if os.path.exists(target_file) else {"__preamble__": []}
    if not os.path.exists(target_file):
        action_display = "create new file"
    elif key in sections:
        action_display = f"update existing section [{key}]"
    elif key == sections.get("__preamble__"):
        action_display = f"convert + append new section [{key}]"
    else:
        action_display = f"append new section [{key}]"

    preview_value = value[:500]
    preview_lines = preview_value.split("\n")[:20]
    preview_text = "\n".join(preview_lines)

    print("════════════════════════════════════")
    print("Memory Write Preview")
    print(f"Target: {target_file}")
    print(f"Action: {action_display}")
    print("────────────────────────────────────")
    print(preview_text)
    if len(value) > 500 or len(value.split("\n")) > 20:
        print(f"... ({len(value)} total chars, {len(value.split(chr(10)))} lines)")
    print("════════════════════════════════════")

    if dry_run:
        print("(dry-run — not written)")
        return

    if not yes_mode:
        if not sys.stdin.isatty():
            print("WARNING: non-TTY context, use --yes to confirm")
            print("memory update cancelled")
            return
        try:
            confirm = input("Confirm write? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nmemory update cancelled")
            return
        if confirm != "y":
            print("memory update cancelled")
            return

    os.makedirs(os.path.dirname(target_file), exist_ok=True)
    action = _write_markdown_sections(target_file, sections, key, value)

    try:
        with open(target_file) as f:
            written = f.read()
        if len(written) < len(value):
            print(f"WARNING: write may be incomplete ({len(written)} < {len(value)} chars)", file=sys.stderr)
    except (IOError, OSError) as e:
        print(f"ERROR: post-write verification failed: {e}", file=sys.stderr)
        return

    bundle = MemoryManager.reload(project_root)
    entries_count = bundle.total()
    print(f"memory reloaded: {entries_count} entries")

    try:
        ps_path = os.path.join(project_root, ".fat", "pdf", "project-state.json")
        if os.path.exists(ps_path):
            with open(ps_path) as f:
                ps = json.load(f)
            ps["memory_snapshot"] = {
                "source_file": target_file,
                "read_at": _timestamp(),
                "entries_count": entries_count,
                "key": key,
                "action": action,
            }
            with open(ps_path, "w") as f:
                json.dump(ps, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    print(f"OK: memory {action} key='{key}' → {target_file}")
