"""Archive consolidation and skip-audit commands for pdf-engine."""
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict

try:
    import yaml
except ImportError:
    yaml = None

from pdf_engine_shared import (
    ARCHIVE_DIR,
    SESSIONS_DIR,
    PDF_ARCHIVE_MODEL,
    _load_state,
    _load_state_with_migration,
    _save_state,
    _timestamp,
    _parse_frontmatter,
    _parse_markdown_sections,
    _write_markdown_sections,
    _estimate_tokens,
    _load_archive_state,
    _save_archive_state,
    _get_iso_week_from_mtime,
    _validate_act_frontmatter,
)


# ── Helpers ───────────────────────────────────────────


def _scan_act_reports(weeks_back=4):
    """Scan session artifact dirs for act_report_*.md files within N weeks."""
    now = time.time()
    cutoff = now - (weeks_back * 7 * 86400)
    results = []

    if not os.path.exists(SESSIONS_DIR):
        return results

    for sid in sorted(os.listdir(SESSIONS_DIR)):
        art_dir = os.path.join(SESSIONS_DIR, sid, "artifacts")
        if not os.path.isdir(art_dir):
            continue
        for fname in sorted(os.listdir(art_dir)):
            if not fname.startswith("act_report_") or not fname.endswith(".md"):
                continue
            fpath = os.path.join(art_dir, fname)
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            if mtime < cutoff:
                continue
            iso_week = _get_iso_week_from_mtime(mtime)
            try:
                with open(fpath) as f:
                    content = f.read()
            except (IOError, OSError):
                continue
            fm, _ = _parse_frontmatter(content)
            preview = content[:200]
            results.append({
                "path": fpath, "mtime": mtime, "iso_week": iso_week,
                "frontmatter": fm, "content_preview": preview, "session_id": sid,
            })
    return results


def _merge_frontmatter_only(reports):
    """Fallback: merge frontmatter from multiple act_reports into plain text."""
    lines = ["## Frontmatter-Only Merge (degraded)"]
    for r in reports:
        fm = r.get("frontmatter")
        if fm and isinstance(fm, dict):
            lines.append(f"### Session {r['session_id']} ({r['iso_week']})")
            for key in ("decisions", "changes", "blockers", "milestones", "metrics"):
                val = fm.get(key)
                if val:
                    lines.append(f"{key}: {json.dumps(val, ensure_ascii=False)[:500]}")
        else:
            lines.append(f"### Session {r['session_id']} ({r['iso_week']}) — (no frontmatter)")
    return "\n".join(lines)


# ── Archive Commands ──────────────────────────────────


def cmd_archive_consolidate():
    """Consolidate act_reports into weekly summaries.

    Usage: pdf-engine.py archive consolidate --weeks N [--week-label <label>]
    """
    raw_args = sys.argv
    weeks_back = 4
    week_label = None
    for i, a in enumerate(raw_args):
        if a == "--weeks" and i + 1 < len(raw_args):
            try:
                weeks_back = int(raw_args[i + 1])
            except ValueError:
                pass
        if a == "--week-label" and i + 1 < len(raw_args):
            week_label = raw_args[i + 1]

    reports = _scan_act_reports(weeks_back)
    if not reports:
        print("Archive: no act_reports found in last {} week(s). Empty summary.".format(weeks_back))
        return

    week_groups = OrderedDict()
    for r in reports:
        wk = r["iso_week"]
        if wk not in week_groups:
            week_groups[wk] = []
        week_groups[wk].append(r)

    consolidated_count = 0
    for week, week_reports in week_groups.items():
        target_label = week_label or week
        target_path = os.path.join(ARCHIVE_DIR, f"w{target_label}.md")

        existing_source_reports = set()
        if os.path.exists(target_path):
            try:
                with open(target_path) as f:
                    existing_text = f.read()
                existing_fm, _ = _parse_frontmatter(existing_text)
                if existing_fm and "source_reports" in existing_fm:
                    srs = existing_fm["source_reports"]
                    existing_source_reports = set(
                        str(s) for s in (srs if isinstance(srs, list) else [srs])
                    )
            except Exception:
                pass

        new_reports = [r for r in week_reports if r["path"] not in existing_source_reports]
        if not new_reports:
            print(f"  w{target_label}: all {len(week_reports)} reports already archived (skipped)")
            continue

        n_total = len(week_reports)
        feed_reports = new_reports[:10]
        extra_count = max(0, len(new_reports) - 10)

        prompt_lines = [
            "# 上下文压缩任务",
            "",
            f"以下是一段时间内的 {len(feed_reports)} 份 act_report（共 {n_total} 份）。请将它们压缩为一份周摘要。",
            "",
            "## 输出格式",
            "---",
            f"week: {target_label}",
            f"source_reports: {n_total}",
            f"date_range: {week_reports[0]['iso_week']}",
            "---",
            "",
            "## 总体进展摘要",
            "<2-3 句话概述>",
            "",
            "## 决策汇总",
            "- <决策1> (置信度)",
            "",
            "## 变更汇总",
            "- <文件>: <摘要> (+lines/-lines)",
            "",
            "## 未解决的阻碍",
            "- <阻碍1> (severity/status)",
            "",
            "## 关键数字",
            "- 总修改文件: <N>",
            "- 总新增行: <N>",
            "- 总删除行: <N>",
            "- 总决策数: <N>",
            "",
            "---",
            "## act_reports",
        ]
        for r in feed_reports:
            prompt_lines.append(f"### {r['session_id']} ({r['iso_week']})")
            prompt_lines.append(r.get("content_preview", "")[:1500])
            prompt_lines.append("")

        if extra_count > 0:
            prompt_lines.append(f"(+{extra_count} additional reports summarized by frontmatter only)")

        prompt_text = "\n".join(prompt_lines)
        compression_method = "haiku"
        output_text = None

        try:
            which_claude = subprocess.run(["which", "claude"], capture_output=True, text=True, timeout=5)
            if which_claude.returncode == 0:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix="archive_consolidate_") as tf:
                    tf.write(prompt_text)
                    prompt_file = tf.name
                result = subprocess.run(
                    ["claude", "-p", f"$(cat {prompt_file})", "-m", PDF_ARCHIVE_MODEL, "--output-format", "text"],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0 and result.stdout.strip():
                    output_text = result.stdout.strip()
                else:
                    sdk_result = subprocess.run(
                        [sys.executable, "-c", f"""
import anthropic, sys, os
client = anthropic.Anthropic()
model = os.environ.get('PDF_ARCHIVE_MODEL', '{PDF_ARCHIVE_MODEL}')
with open('{prompt_file}') as f:
    prompt = f.read()
message = client.messages.create(model=model, max_tokens=4096, messages=[{{'role': 'user', 'content': prompt}}])
print(message.content[0].text)
                        """],
                        capture_output=True, text=True, timeout=60,
                    )
                    if sdk_result.returncode == 0 and sdk_result.stdout.strip():
                        output_text = sdk_result.stdout.strip()
                    else:
                        compression_method = "degraded"
                os.unlink(prompt_file)
            else:
                compression_method = "degraded"
        except (subprocess.TimeoutExpired, Exception):
            compression_method = "degraded"

        if output_text is None:
            output_text = _merge_frontmatter_only(new_reports)
            compression_method = "frontmatter_only"

        final_lines = []
        total_decisions = 0
        total_changes = 0
        for r in new_reports:
            fm = r.get("frontmatter")
            if fm:
                total_decisions += len(fm.get("decisions", []))
                total_changes += len(fm.get("changes", []))
        final_lines.append("---")
        final_lines.append(f"week: {target_label}")
        final_lines.append(f"source_reports: {n_total}")
        final_lines.append(f"total_decisions: {total_decisions}")
        final_lines.append(f"total_changes: {total_changes}")
        final_lines.append(f"date_range: {week_reports[0]['iso_week']}")
        final_lines.append(f"compression_method: {compression_method}")
        final_lines.append("---")
        final_lines.append("")
        final_lines.append(output_text)
        final_lines.append("")

        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        tmp_path = target_path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write("\n".join(final_lines))
        os.rename(tmp_path, target_path)

        line_count = len(final_lines)
        consolidated_count += len(new_reports)
        print(f"  w{target_label}: consolidated {len(new_reports)} report(s) → {line_count} lines (method={compression_method})")

    astate = _load_archive_state()
    astate["last_consolidated"] = _timestamp()
    astate["consolidated_weeks"] = list(set(astate.get("consolidated_weeks", []) + list(week_groups.keys())))
    astate["total_reports_processed"] = astate.get("total_reports_processed", 0) + len(reports)
    astate["compression_method"] = compression_method
    _save_archive_state(astate)

    sess_state = _load_state()
    if sess_state:
        sess_state.setdefault("session", {})["archive_consolidated_at"] = _timestamp()
        _save_state(sess_state)

    print(f"Archive consolidated: {consolidated_count} reports into {len(week_groups)} week(s)")


def cmd_archive_list():
    """List archived weekly summaries.

    Usage: pdf-engine.py archive list [--json]
    """
    use_json = "--json" in sys.argv

    if not os.path.exists(ARCHIVE_DIR):
        if use_json:
            print("[]")
        else:
            print("week | reports | date_range | decisions | changes | estimated_tokens")
            print("(no archive)")
        return

    entries = []
    for fname in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
        if not fname.startswith("w") or not fname.endswith(".md") or fname.startswith("."):
            continue
        fpath = os.path.join(ARCHIVE_DIR, fname)
        try:
            with open(fpath) as f:
                text = f.read()
        except (IOError, OSError):
            continue
        fm, _ = _parse_frontmatter(text)
        week = fm.get("week", fname[:-3]) if fm else fname[:-3]
        reports = fm.get("source_reports", 0) if fm else 0
        date_range = fm.get("date_range", "?") if fm else "?"
        decisions = fm.get("total_decisions", 0) if fm else 0
        changes = fm.get("total_changes", 0) if fm else 0
        estimated = _estimate_tokens(text)
        entries.append({
            "week": week, "reports": reports, "date_range": date_range,
            "decisions": decisions, "changes": changes, "estimated_tokens": estimated,
        })

    if use_json:
        print(json.dumps(entries, ensure_ascii=False))
        return

    if not entries:
        print("week | reports | date_range | decisions | changes | estimated_tokens")
        print("(no archive)")
        return

    print(f"{'week':<14} {'reports':<9} {'date_range':<28} {'decisions':<10} {'changes':<8} {'tokens':<7}")
    for e in entries:
        print(f"{e['week']:<14} {e['reports']:<9} {e['date_range']:<28} {e['decisions']:<10} {e['changes']:<8} {e['estimated_tokens']:<7}")


def cmd_archive_inject(week_number):
    """Inject an archived weekly summary as context.

    Usage: pdf-engine.py archive inject <week-number>
    """
    target = os.path.join(ARCHIVE_DIR, f"w{week_number}.md")
    if not os.path.exists(target):
        print(f"ERROR: archive w{week_number}.md not found", file=sys.stderr)
        return

    try:
        with open(target) as f:
            content = f.read()
    except IOError as e:
        print(f"ERROR: failed to read {target}: {e}", file=sys.stderr)
        return

    line_count = len(content.split("\n"))
    tokens = _estimate_tokens(content)

    if tokens > 3000:
        lines = content.split("\n")
        kept = []
        for line in lines:
            if line.startswith("## ") and line.lower().strip("# ") in ("blockers", "metrics", "未解决的阻碍", "关键数字"):
                kept.append(line)
                kept.append("(truncated — see original for details)")
                continue
            kept.append(line)
        content = "\n".join(kept)
        new_tokens = _estimate_tokens(content)
        if new_tokens > 3000:
            content = content[:12000] + "\n... (truncated)"
            tokens = _estimate_tokens(content)
        else:
            tokens = new_tokens

    print(f"## Archived Context (w{week_number})")
    print("")
    print(content)
    print(f"<!-- injected from w{week_number}.md ({line_count} lines, ~{tokens} tokens) -->")
    print(f"Archive w{week_number}.md injected ({line_count} lines)")


def cmd_archive_clean():
    """Clean old act_reports, keeping only those with weekly summaries.

    Usage: pdf-engine.py archive clean --weeks N --force
    """
    raw_args = sys.argv
    weeks_back = 4
    force = "--force" in raw_args
    for i, a in enumerate(raw_args):
        if a == "--weeks" and i + 1 < len(raw_args):
            try:
                weeks_back = int(raw_args[i + 1])
            except ValueError:
                pass

    now = time.time()
    cutoff = now - (weeks_back * 7 * 86400)

    archived_weeks = set()
    if os.path.exists(ARCHIVE_DIR):
        for fname in os.listdir(ARCHIVE_DIR):
            if fname.startswith("w") and fname.endswith(".md") and not fname.startswith("."):
                archived_weeks.add(fname[1:-3])

    candidates = []
    if not os.path.exists(SESSIONS_DIR):
        print("Archive: no session artifacts directory")
        return

    for sid in sorted(os.listdir(SESSIONS_DIR)):
        art_dir = os.path.join(SESSIONS_DIR, sid, "artifacts")
        if not os.path.isdir(art_dir):
            continue
        for fname in sorted(os.listdir(art_dir)):
            if not fname.startswith("act_report_") or not fname.endswith(".md"):
                continue
            fpath = os.path.join(art_dir, fname)
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            iso_week = _get_iso_week_from_mtime(mtime)
            if iso_week not in archived_weeks:
                candidates.append((fpath, iso_week, "skip (no weekly summary)"))
            else:
                candidates.append((fpath, iso_week, "delete"))

    to_delete = [c for c in candidates if c[2] == "delete"]
    skipped = [c for c in candidates if c[2] != "delete"]

    if not force:
        print(f"Would delete {len(to_delete)} files. Use --force to confirm.")
        if skipped:
            print(f"  {len(skipped)} skipped (no weekly summary):")
            for path, wk, _ in skipped[:3]:
                print(f"    {path} (w{wk} not archived)")
        return

    removed = 0
    for fpath, wk, action in candidates:
        if action == "delete":
            try:
                os.remove(fpath)
                removed += 1
            except OSError as e:
                print(f"WARNING: failed to delete {fpath}: {e}", file=sys.stderr)

    astate = _load_archive_state()
    astate["last_clean"] = _timestamp()
    _save_archive_state(astate)

    print(f"Archive clean: {removed} files removed, {len(skipped)} skipped (no weekly summary)")


def cmd_archive_context_inject():
    """Auto-inject last 4 weekly summaries as context prefix.

    Usage: pdf-engine.py archive context-inject
    """
    if not os.path.exists(ARCHIVE_DIR):
        print("archive: no archive directory (skipped)")
        return

    weeks = []
    for fname in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
        if not fname.startswith("w") or not fname.endswith(".md") or fname.startswith("."):
            continue
        fpath = os.path.join(ARCHIVE_DIR, fname)
        try:
            with open(fpath) as f:
                text = f.read()
        except (IOError, OSError):
            continue
        fm, _ = _parse_frontmatter(text)
        week_label = fm.get("week", fname[:-3]) if fm else fname[:-3]
        weeks.append({"label": week_label, "content": text, "tokens": _estimate_tokens(text)})

    weeks = weeks[:4]
    if not weeks:
        print("archive: no weekly summaries found (skipped)")
        return

    lines = ["## 项目上下文归档摘要（最近 {} 周）".format(len(weeks)), ""]
    total_tokens = 0

    for w in reversed(weeks):
        content = w["content"]
        tokens = w["tokens"]
        if total_tokens + tokens > 3000:
            budget = 3000 - total_tokens
            if budget > 50:
                char_budget = budget * 4
                content = content[:char_budget] + "\n... (truncated)"
            else:
                continue
        lines.append(content)
        lines.append("")
        total_tokens += min(tokens, 3000 - total_tokens)

    output = "\n".join(lines)
    print(output)
    print(f"archive: injected {len(weeks)} week(s) ({total_tokens} estimated tokens)")

    sess_state = _load_state()
    if sess_state:
        sess_state.setdefault("session", {})["archive_injected"] = True
        _save_state(sess_state)


def cmd_archive_route():
    """Route archive subcommands."""
    sub = sys.argv[2] if len(sys.argv) >= 3 else ""
    if sub == "consolidate":
        cmd_archive_consolidate()
    elif sub == "list":
        cmd_archive_list()
    elif sub == "inject" and len(sys.argv) >= 4:
        cmd_archive_inject(sys.argv[3])
    elif sub == "clean":
        cmd_archive_clean()
    elif sub == "context-inject":
        cmd_archive_context_inject()
    else:
        print("ERROR: usage: pdf-engine.py archive <consolidate|list|inject <week>|clean [--weeks N] [--force]|context-inject>", file=sys.stderr)


# ── Skip-Audit Commands ──────────────────────────────


def cmd_skip_audit_route():
    """Route skip-audit subcommands."""
    sub = sys.argv[2] if len(sys.argv) >= 3 else ""
    if sub == "add" and len(sys.argv) >= 4:
        phase = sys.argv[3]
        reason = ""
        impact = "unknown"
        for i, a in enumerate(sys.argv):
            if a == "--reason" and i + 1 < len(sys.argv):
                reason = sys.argv[i + 1]
            if a == "--impact" and i + 1 < len(sys.argv):
                impact = sys.argv[i + 1]
        cmd_skip_audit_add(phase, reason, impact)
    elif sub == "summary":
        cmd_skip_audit_summary()
    elif sub == "clear":
        cmd_skip_audit_clear()
    else:
        print("ERROR: usage: pdf-engine.py skip-audit <add <phase> [--reason <r>] [--impact <i>]|summary|clear>", file=sys.stderr)


def cmd_skip_audit_add(phase, reason, impact):
    """Record a skip event in state."""
    state = _load_state_with_migration()
    if state is None:
        print("WARN: no state, skip record not persisted")
        return
    audit = state.setdefault("skip_audit", [])
    for entry in audit[-10:]:
        if entry.get("phase") == phase and entry.get("reason") == reason:
            print(f"skip-audit: already recorded {phase}/{reason} (dedup)")
            return
    record = {
        "phase": phase,
        "reason": reason,
        "impact": impact,
        "skipped_at": _timestamp(),
    }
    audit.append(record)
    _save_state(state)
    print(f"skip-audit: recorded {phase} skip (reason={reason}, impact={impact})")


def cmd_skip_audit_summary():
    """Print accumulated skip audit summary."""
    state = _load_state_with_migration()
    if state is None:
        print("(no state)")
        return
    audit = state.get("skip_audit", [])
    if not audit:
        print("(no skips recorded)")
        return
    print(f"Skip Audit: {len(audit)} skipped information phases")
    for entry in audit:
        print(f"  - {entry.get('phase')}: {entry.get('reason')} (impact: {entry.get('impact')})")
    if len(audit) >= 3:
        print(f"\n⚠️  [Skip Audit Warning] {len(audit)} information pipeline phases skipped. "
              "LLM should be aware of reduced context quality.")


def cmd_skip_audit_clear():
    """Clear skip audit records from state (called at Plan start)."""
    state = _load_state_with_migration()
    if state is None:
        return
    state["skip_audit"] = []
    _save_state(state)
    print("skip-audit: cleared")


def _check_skip_audit_warning():
    """Return warning string if >=3 skips accumulated, else None."""
    state = _load_state_with_migration()
    if state is None:
        return None
    audit = state.get("skip_audit", [])
    if len(audit) >= 3:
        phases = ", ".join(e.get("phase", "?") for e in audit)
        return f"[Skip Audit Warning] {len(audit)} info phases skipped ({phases}) — reduced context quality"
    return None
