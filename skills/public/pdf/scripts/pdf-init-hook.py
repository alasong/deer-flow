#!/usr/bin/env python3
"""PDF Auto-Init Hook — PostToolUse hook that auto-initializes PDF session state.

When a Skill(pdf) tool call completes, this hook automatically:
  1. Parses flags (--blueprint, --plan-only, --resume, --new, etc.)
  2. Runs `channel select` to determine the best pipeline channel
  3. Sets `stage=plan` and saves the channel to state
  4. Loads the matching blueprint
  5. Runs `pipeline tick` to discover ready nodes
  6. Outputs the results as context for the LLM

This eliminates the need for the LLM to manually call these commands.
The state file is created by the hook (outside LLM control), breaking
the circular trust problem where the LLM enforces itself.

Usage (configured as Claude Code PostToolUse hook):
  echo '{"tool_name":"Skill","tool_input":{"skill":"pdf","args":"..."}}' | python3 pdf-init-hook.py
"""

import json
import os
import re
import subprocess
import sys

ENGINE = os.path.join(os.path.dirname(__file__), "..", "tools", "pdf-engine.py")
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _engine(*args):
    """Run pdf-engine.py with args, return (stdout, returncode)."""
    try:
        r = subprocess.run(
            [sys.executable, ENGINE] + list(args),
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return f"ERROR: {e}", 1


def parse_args(raw_args):
    """Extract flags and clean task text from raw args string.

    Returns (task_text, blueprint, plan_only, resume_slug, force_new).
    blueprint: None (auto-select) or 'lite'/'standard'/'full'/'analysis'/'planning'
    plan_only: bool
    resume_slug: str or None
    force_new: bool
    """
    blueprint = None
    plan_only = False
    resume_slug = None
    force_new = False

    task_parts = []
    words = raw_args.split()
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--blueprint" and i + 1 < len(words):
            blueprint = words[i + 1]
            i += 2
            continue
        elif w.startswith("--blueprint="):
            blueprint = w.split("=", 1)[1]
        elif w == "--plan-only":
            plan_only = True
        elif w == "--resume" and i + 1 < len(words):
            resume_slug = words[i + 1]
            i += 2
            continue
        elif w.startswith("--resume="):
            resume_slug = w.split("=", 1)[1]
        elif w == "--new":
            force_new = True
        elif w.startswith("--"):
            # Unknown flag, skip
            pass
        else:
            task_parts.append(w)
        i += 1

    return " ".join(task_parts), blueprint, plan_only, resume_slug, force_new


def estimate_channel(task_text, plan_only, blueprint_override):
    """Heuristic channel selection for auto-init.

    Returns a channel name.
    """
    if blueprint_override:
        return blueprint_override
    if plan_only:
        return "analysis"

    text_lower = task_text.lower()

    # Pure analysis/research tasks → analysis channel
    analysis_keywords = [
        "分析", "研究", "调研", "audit", "review", "评估",
        "比较", "区别", "对比", "architecture", "设计评审",
        "what if", "should i", "tradeoff", "suggestion",
    ]
    # Documentation tasks → lite
    doc_keywords = [
        "文档", "readme", "doc", "documentation", "注释",
        "写一个", "生成", "create", "init",
    ]
    # Bug fixes → standard (review is mandatory)
    bug_keywords = [
        "bug", "fix", "修复", "问题", "error", "不对",
        "broken", "crash", "fail", "错误",
    ]

    if any(kw in text_lower for kw in analysis_keywords):
        return "analysis"
    if any(kw in text_lower for kw in doc_keywords):
        return "lite"
    if any(kw in text_lower for kw in bug_keywords):
        return "standard"

    # Detect task type from sentence structure
    if re.search(r"(如何|怎样|怎么|how to|how do)", text_lower):
        return "analysis"
    if re.search(r"(改成|改为|添加|增加|实现|implement|add|modify)", text_lower):
        return "standard"

    return "full"


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, IOError):
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only trigger on Skill(pdf)
    if tool_name != "Skill":
        sys.exit(0)

    skill = tool_input.get("skill", "")
    if skill != "pdf":
        sys.exit(0)

    raw_args = tool_input.get("args", "").strip()
    if not raw_args:
        print("[PDF INIT HOOK] No task args — skipping auto-init", file=sys.stderr)
        sys.exit(0)

    # Parse flags
    task_text, blueprint_override, plan_only, resume_slug, force_new = parse_args(raw_args)
    if not task_text and not resume_slug:
        print("[PDF INIT HOOK] No task text or resume slug", file=sys.stderr)
        sys.exit(0)

    # ── Handle --resume: check state exists ──
    if resume_slug:
        resume_out, rc = _engine("state")
        if rc == 0 and resume_out:
            print()
            print("=" * 50)
            print("[PDF AUTO-INIT] Resuming session")
            print("=" * 50)
            print()
            print(resume_out)
            print()
            print("Session state loaded. Run pipeline tick or status to continue.")
            return
        else:
            print(f"[PDF INIT HOOK] Resume slug '{resume_slug}' but no state found", file=sys.stderr)
            sys.exit(0)

    # ── Select channel ──
    channel = estimate_channel(task_text, plan_only, blueprint_override)

    # Build channel select JSON
    channel_json = json.dumps({
        "task_type": "analysis" if channel == "analysis" else "modify",
        "output_kind": "architecture" if channel == "analysis" else "feature",
        "factors": [],
        "triggered_dims": [],
        "single_file": False,
        "multi_module": True,
    })

    # ── Run channel select ──
    cs_out, cs_rc = _engine("channel", "select", channel_json)
    if cs_rc != 0:
        # Fallback: just set channel manually
        _engine("state", "set", f"channel={channel}")

    # ── Set stage=plan ──
    _engine("state", "set", "stage=plan")

    # ── Load blueprint ──
    _engine("blueprint", "load", channel)

    # ── Pipeline tick ──
    tick_out, tick_rc = _engine("pipeline", "tick")

    # ── Output to LLM context ──
    print()
    print("=" * 50)
    print("[PDF AUTO-INIT] Engine initialized by harness hook")
    print(f"  Channel: {channel}")
    if plan_only:
        print("  Mode: plan-only")
    print("=" * 50)
    print()

    if tick_rc == 0 and tick_out:
        try:
            parsed = json.loads(tick_out)
            action = parsed.get("action", "unknown")
            stage = parsed.get("stage", "?")
            nodes = parsed.get("nodes", [])

            print(f"Stage: {stage} | Action: {action}")
            if nodes:
                print()
                print("Ready nodes:")
                for ref, node_def in nodes:
                    ntype = node_def.get("type", "?")
                    desc = node_def.get("description", "").strip()
                    print(f"  [{ref}] ({ntype})")
                    if desc:
                        print(f"    {desc}")
            print()
            print("Execute pipeline commands as needed.")
            print("Start with: pipeline node-start <stage> <ref>")
        except json.JSONDecodeError:
            print(tick_out)
    else:
        print("Pipeline tick completed. Run 'pipeline tick' manually to see ready nodes.")
        print()

    print("[END PDF AUTO-INIT]")


if __name__ == "__main__":
    main()
