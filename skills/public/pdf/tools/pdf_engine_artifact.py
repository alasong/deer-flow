"""Artifact and agent management commands for pdf-engine."""
import glob
import json
import os
import shutil
import sys
import time

from pdf_engine_shared import (
    STAGE_ORDER,
    PDFContext,
    _agent_status,
    _require_state,
    _resolve_context,
    _save_state,
)


def cmd_artifact_add(path, role="", move=False):
    """Register artifact. When session mode is active, translate relative paths
    to the session artifacts subdirectory. With --move, also move the file from
    its current location (e.g. CWD) to the session artifact directory."""
    state = _require_state()
    session_id = state.get("session_id") or state.get("multi_session", {}).get("session_id")

    original_path = path  # save before rewriting

    if session_id and not os.path.isabs(path):
        basename = os.path.basename(path)
        path = os.path.join(".fat", "pdf", "sessions", session_id, "artifacts", basename)

    if move and session_id:
        # Move file from its current location to the session artifact directory
        ctx = _resolve_context()
        dest = os.path.join(ctx.artifact_dir, os.path.basename(original_path))
        src = os.path.abspath(original_path)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            print(f"  moved: {src} → {dest}")
        else:
            print(f"WARN: source not found, skipping move: {src}", file=sys.stderr)

    arts = state.setdefault("artifacts", [])
    entry = {"path": path, "stage": state.get("stage", "?"), "role": role, "id": len(arts) + 1}
    arts.append(entry)
    _save_state(state)
    print(f"OK: artifact #{entry['id']} registered: {path}")


def cmd_artifact_list():
    """List all artifacts."""
    state = _require_state()
    arts = state.get("artifacts", [])
    if not arts:
        print("(no artifacts)")
        return
    for a in arts:
        print(f"  #{a.get('id', '?')} {a.get('path')} [{a.get('stage')}/{a.get('role')}]")


def cmd_artifact_get(aid_str):
    """Get artifact path by id."""
    state = _require_state()
    try:
        aid = int(aid_str)
    except ValueError:
        print(f"ERROR: invalid id: {aid_str}", file=sys.stderr)
        return
    for a in state.get("artifacts", []):
        if a.get("id") == aid:
            print(a.get("path"))
            return
    print(f"ERROR: artifact #{aid} not found", file=sys.stderr)


def cmd_artifact_compress():
    """Compress multiple artifact files by stage into single files."""
    ctx = PDFContext.get_default()
    if not os.path.exists(ctx.state_dir):
        print("nothing to compress")
        return

    now = time.time()
    five_min = 300

    stage_configs = [
        ("do_output", "do_output_*.md", "doer"),
        ("explore", "explore_*.md", "explorer"),
        ("check_report", "check_report_*.md", "checker"),
        ("act_report", "act_report_*.md", "actor"),
    ]

    compressed = []
    for display_name, pattern, section_label in stage_configs:
        files = sorted(glob.glob(os.path.join(ctx.state_dir, pattern)))
        if display_name == "explore":
            made_dir = ctx.made_dir
            if os.path.isdir(made_dir):
                files.extend(sorted(glob.glob(os.path.join(made_dir, pattern))))

        if len(files) <= 1:
            continue

        old_files = []
        for f in files:
            try:
                mtime = os.path.getmtime(f)
                if now - mtime > five_min:
                    old_files.append(f)
            except OSError as e:
                print(f"WARN: could not get mtime for {os.path.basename(f)}: {e}", file=sys.stderr)
                continue

        if len(old_files) <= 1:
            continue

        merged_lines = []
        for i, f in enumerate(old_files):
            try:
                with open(f) as fh:
                    content = fh.read().strip()
                if not content:
                    continue
            except (IOError, OSError) as e:
                print(f"WARN: skipping {os.path.basename(f)}: {e}", file=sys.stderr)
                continue

            if section_label == "explorer":
                base = os.path.basename(f)
                lens = base[len("explore_"):-len(".md")]
                section_header = f"## explorer-{lens}"
            else:
                section_header = f"## {section_label}-{i + 1}"

            merged_lines.append(section_header)
            merged_lines.append("")
            merged_lines.append(content)
            merged_lines.append("")

        if not merged_lines:
            continue

        output_path = os.path.join(ctx.state_dir, f"compressed_{display_name}.md")
        try:
            with open(output_path, "w") as fh:
                fh.write("\n".join(merged_lines) + "\n")
            compressed.append(f"{display_name} ({len(old_files)}→1)")
        except (IOError, OSError) as e:
            print(f"WARN: could not write {output_path}: {e}", file=sys.stderr)

    if compressed:
        print(f"[COMPRESSED] {len(compressed)} groups: {', '.join(compressed)}")
    else:
        print("nothing to compress")


def cmd_artifact_resolve_path(filename=""):
    """Resolve artifact path for current session. Returns absolute directory path.
    If filename is given, appends it to the artifact dir path."""
    from pdf_engine_shared import _resolve_context
    ctx = _resolve_context()
    base_dir = ctx.artifact_dir
    if filename:
        print(os.path.join(base_dir, filename))
    else:
        print(base_dir)


def cmd_agent_pending():
    """List pending (running) agents."""
    state = _require_state()
    found = False
    for sname in STAGE_ORDER:
        if sname == "done":
            continue
        scfg = state.get("stages", {}).get(sname, {})
        for container_name in ("agents", "reviewers"):
            for aname, avalue in scfg.get(container_name, {}).items():
                if _agent_status(avalue) in ("running", "pending"):
                    role = avalue.get("role", "?") if isinstance(avalue, dict) else "?"
                    print(f"  {aname} [{sname}/{container_name}/{role}]")
                    found = True
    if not found:
        print("(no pending agents)")


def cmd_agent_status_list():
    """List all agents with status."""
    state = _require_state()
    found = False
    for sname in STAGE_ORDER:
        if sname == "done":
            continue
        scfg = state.get("stages", {}).get(sname, {})
        for container_name in ("agents", "reviewers"):
            for aname, avalue in scfg.get(container_name, {}).items():
                status = _agent_status(avalue)
                role = avalue.get("role", "?") if isinstance(avalue, dict) else "?"
                spawned = avalue.get("spawned_at", "")[:19] if isinstance(avalue, dict) else ""
                print(f"  {aname}: {status} [{sname}/{container_name}/{role}] {spawned}")
                found = True
    if not found:
        print("(no agents)")
