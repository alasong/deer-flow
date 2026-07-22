"""Channel selection, config, and model resolution commands for pdf-engine."""
import json
import os
import random
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None

from pdf_engine_shared import (
    SKILL_DIR,
    FACTOR_TAXONOMY,
    _load_channel_rules,
    _load_state_with_migration,
    _require_state,
    _save_state,
    _query_cycle_db,
)


# ── Auto channel classification (no LLM JSON needed) ─────────────

_OUTPUT_KIND_KEYWORDS = {
    "analysis":        ["分析", "研究", "调研", "audit", "review", "investigate", "analyze", "research", "评估", "评估报告"],
    "planning":        ["规划", "计划", "需求", "roadmap", "planning", "requirement", "sprint",
                        "backlog", "milestone", "路线图", "迭代", "排期"],
    "bugfix":          ["修复", "bug", "fix", "hotfix", "defect", "故障", "异常"],
    "refactor":        ["重构", "refactor", "redesign", "重写", "优化", "optimize", "债务", "debt"],
    "architecture":    ["架构", "architecture", "设计", "design", "方案", "选型"],
    "docs":            ["文档", "doc", "readme", "manual", "说明", "guide"],
    "config":          ["配置", "config", "setup", "安装", "deploy", "部署"],
}


def _auto_classify_output_kind(text):
    """Classify output_kind from task text using keyword matching.

    Returns (output_kind, confidence) where confidence is 'high' | 'medium' | 'low'.
    """
    lower = text.lower()
    # Count keyword hits per category
    hits = {}
    for kind, keywords in _OUTPUT_KIND_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > 0:
            hits[kind] = count

    if not hits:
        return ("feature", "low")  # default: feature (→ full channel)

    best = max(hits, key=hits.get)
    confidence = "high" if hits[best] >= 2 else "medium"
    return (best, confidence)


def _detect_multi_module(project_root):
    """Check if project has multiple source modules (indicates complex task routing).

    Counts top-level dirs under src/ or project root that look like source modules.
    """
    if not project_root or not os.path.isdir(project_root):
        return False
    for src_candidate in [os.path.join(project_root, "src"), project_root]:
        if os.path.isdir(src_candidate):
            modules = 0
            try:
                for entry in os.listdir(src_candidate):
                    if entry.startswith(".") or entry.startswith("_"):
                        continue
                    full = os.path.join(src_candidate, entry)
                    if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                        modules += 1
                    elif os.path.isfile(full) and entry.endswith(".py"):
                        modules += 1
            except (OSError, PermissionError):
                pass
            return modules >= 3
    return False


def cmd_channel_auto():
    """Auto-detect channel from state + codebase analysis.

    Engine determines channel by reading:
      1. state.task_slug — keyword-matched to output_kind
      2. state.domain — set by P0_scope
      3. FACTOR_TAXONOMY — factor force_channel rules
      4. Project file tree — multi_module detection

    LLM can override with `channel set-override <name>` if auto-detect is wrong.
    No LLM JSON input required.
    """
    state = _load_state_with_migration()
    if state is None:
        print("ERROR: No state. Run 'init' first.", file=sys.stderr)
        sys.exit(1)

    # 1. LLM override (highest priority)
    cc = state.get("channel_config", {})
    llm_overrides = cc.get("overrides", {})
    llm_channel = llm_overrides.get("profile")
    if llm_channel:
        reason = cc.get("llm_override_reason", "LLM override")
        _save_channel_and_blueprint(state, llm_channel)
        print(f"channel={llm_channel} reason=llm_override: {reason}")
        return

    # 2. Gather signals from state
    task_slug = state.get("task_slug", "")
    task_description = state.get("task_description", "")
    domain = state.get("domain", "")
    task_text = f"{task_slug} {task_description} {domain}"
    project_root = state.get("project_root", os.getcwd())

    # 3. Factor forcing — check if any factor's force_channel is triggered
    matched_factors = []
    for fname, fdef in FACTOR_TAXONOMY.items():
        triggers = fdef.get("triggers", [])
        if any(kw in task_text.lower() for kw in triggers):
            matched_factors.append(fname)
            fc = fdef.get("force_channel")
            if fc:
                _save_channel_and_blueprint(state, fc)
                print(f"channel={fc} factor={fname} (auto)")
                return

    # 4. Auto-classify output_kind from task text
    output_kind, confidence = _auto_classify_output_kind(task_text)

    # 5. output_kind → channel mapping
    kind_to_channel = {
        "analysis":        "analysis",
        "research":        "analysis",
        "audit/report":    "analysis",
        "bugfix":          "standard",
        "docs":            "lite",
        "config":          "lite",
        "planning":        "planning",
        "project_plan":    "planning",
        "sprint_plan":     "planning",
        "feature":         "full",
        "refactor":        "full",
        "architecture":    "full",
    }
    ch = kind_to_channel.get(output_kind, "full")

    # 6. multi_module upgrade (don't upgrade analysis-type tasks)
    if output_kind not in ("analysis", "research", "audit/report", "planning", "docs", "config"):
        multi_module = _detect_multi_module(project_root)
        if multi_module and ch == "lite":
            ch = "standard"
        if multi_module and ch == "standard":
            ch = "full"

    # 7. Save result
    _save_channel_and_blueprint(state, ch)

    # Build a human-readable reason line
    reasons = [f"output_kind={output_kind}"]
    if matched_factors:
        reasons.append(f"factors={','.join(matched_factors)}")
    if ch in ("full", "standard") and _detect_multi_module(project_root):
        reasons.append("multi_module")
    print(f"channel={ch} {' '.join(reasons)}")


def _compute_n_check(channel, n_do, channel_rules=None):
    """Compute N for check stage based on channel and N_do."""
    rules = channel_rules or _load_channel_rules()
    if channel in rules:
        profile = rules[channel].get("check", {})
        if isinstance(profile, dict):
            n_formula = profile.get("n_check_formula", "min(1,n_do)")
            try:
                return eval(n_formula, {"n_do": n_do, "min": min, "max": max})
            except Exception:
                return min(1, n_do)
        n_static = profile.get("n", 1)
        if isinstance(n_static, int):
            return n_static
    return min(1, n_do)


def _get_domain_model_override(domain, stage, role):
    """Check domain's model_tier_overrides for a stage.role value."""
    if not domain:
        return None
    domain_path = os.path.join(SKILL_DIR, "docs", "domain", f"{domain}.yaml")
    if not os.path.exists(domain_path):
        return None
    try:
        with open(domain_path) as f:
            cfg = yaml.safe_load(f) if yaml else {}
    except Exception:
        return None
    overrides = cfg.get("model_tier_overrides", {})
    stage_ov = overrides.get(stage, {})
    if not stage_ov:
        return None
    val = stage_ov.get(role)
    if val is not None and isinstance(val, str):
        return val
    p_role = f"p{1 if role == 'p1' else 2}"
    val = stage_ov.get(p_role, stage_ov.get(role))
    if val is not None and isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("default")
    return None


def _get_spawn_config_domain_model_default(domain, stage, role):
    """Check spawn-config.yaml domain_model_defaults for domain/stage/role match."""
    if not domain:
        return None
    spawn_cfg_path = os.path.join(SKILL_DIR, "docs", "spawn-config.yaml")
    if not os.path.exists(spawn_cfg_path):
        return None
    try:
        with open(spawn_cfg_path) as f:
            cfg = yaml.safe_load(f) if yaml else {}
    except Exception:
        return None
    dmd = cfg.get("domain_model_defaults", {})
    domain_cfg = dmd.get(domain, {})
    if not domain_cfg:
        return None
    stage_cfg = domain_cfg.get(stage, {})
    if not stage_cfg:
        return None
    val = stage_cfg.get(role)
    if val is not None and isinstance(val, str):
        return val
    role_key = f"p{1 if role in ('analysis', 'design', 'p1') else 2}"
    val = stage_cfg.get(role_key)
    if val is not None and isinstance(val, str):
        return val
    return None


def _save_triggered_factors(state, matched_factors):
    """Save triggered factors and their add_dimensions to state."""
    if not matched_factors:
        return
    state["triggered_factors"] = matched_factors
    add_dims = set()
    for f in matched_factors:
        add_dims.update(FACTOR_TAXONOMY[f].get("add_dimensions", []))
    if add_dims:
        existing = state.get("triggered_dimensions", [])
        state["triggered_dimensions"] = list(set(existing + list(add_dims)))
    _save_state(state)


def _auto_fetch_history_recommendation(state):
    """Auto-fetch history recommendation from cycle-db and cache in state."""
    rows = _query_cycle_db(
        "SELECT model_allocation, effectiveness FROM cycles WHERE completed >= date('now', '-90 days') ORDER BY effectiveness DESC",
        None
    )
    if not rows:
        state["history_fetched"] = True
        _save_state(state)
        return

    merged = {}
    for r in rows:
        eff = r.get("effectiveness", 0.0) or 0.0
        raw = r.get("model_allocation", "{}")
        if isinstance(raw, str):
            try:
                alloc = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                alloc = {}
        elif isinstance(raw, dict):
            alloc = raw
        else:
            alloc = {}
        for key, model in alloc.items():
            if key not in merged or eff > merged[key][1]:
                merged[key] = (model, eff)

    recommendation = {k: v[0] for k, v in merged.items()}
    state["history_recommendation"] = recommendation
    state["history_fetched"] = True
    _save_state(state)


# ── Channel Commands ──────────────────────────────────


def _channel_to_blueprint(channel):
    """Map channel name to blueprint file name for index.yaml lookup."""
    mapping = {
        "lite": "lite",
        "standard": "standard",
        "full": "full",
        "analysis": "analysis",
        "planning": "planning",
    }
    return mapping.get(channel, "full")


def _save_channel_and_blueprint(state, channel):
    """Save channel, auto-select matching blueprint, and advance stage to plan."""
    state["channel"] = channel
    bp_name = _channel_to_blueprint(channel)
    state["blueprint"] = bp_name
    # Auto-advance stage from "init" to "plan" — init→plan is the only place
    # stage gets set before fire_event() takes over.
    if state.get("stage") in (None, "init"):
        state["stage"] = "plan"
    # Seed HSM state so hsm_path and stage are in sync (engine/hsm.py:_init_hsm_state)
    state.setdefault("hsm_path", ["plan"])
    state.setdefault("hsm_loop_counts", {})
    state.setdefault("hsm_paused", False)
    state.setdefault("hsm_pause_reason", None)
    _save_state(state)


def cmd_channel_select(rule_json):
    """Compute channel from JSON input. Saves channel + blueprint to state."""
    try:
        data = json.loads(rule_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    state = _load_state_with_migration()
    if state:
        cc = state.get("channel_config", {})
        llm_overrides = cc.get("overrides", {})
        llm_channel = llm_overrides.get("profile")
        if llm_channel:
            reason = cc.get("llm_override_reason", "LLM override")
            _save_channel_and_blueprint(state, llm_channel)
            print(f"channel={llm_channel} reason=llm_override: {reason}")
            return

    output_kind = data.get("output_kind", "")
    task_type = data.get("task_type", "")
    factors = data.get("factors", [])
    triggered_dims = data.get("triggered_dims", [])
    single_file = data.get("single_file", True)
    multi_module = data.get("multi_module", False)

    matched_factors = [f for f in factors if f in FACTOR_TAXONOMY]
    if matched_factors and state is not None:
        _save_triggered_factors(state, matched_factors)
    for factor_name in matched_factors:
        fc = FACTOR_TAXONOMY[factor_name].get("force_channel")
        if fc:
            if state is not None:
                _save_channel_and_blueprint(state, fc)
            print(f"channel={fc} factor={factor_name}")
            return

    # output_kind based routing — 重型优先，默认 full
    kind_to_channel = {
        # 分析类任务 → analysis 蓝图（零 P0 前置）
        "analysis": "analysis",
        "research": "analysis",
        "audit/report": "analysis",
        # 明显简单问题 → standard / lite
        "bugfix": "standard",
        # 规划类任务 → planning 蓝图
        "planning": "planning",
        "project_plan": "planning",
        "sprint_plan": "planning",
        # 复杂/重型任务 → full
        "feature": "full",
        "refactor": "full",
        "architecture": "full",
    }
    ch = kind_to_channel.get(output_kind, "full")

    # task_type override (weaker than output_kind)
    type_to_channel = {
        "config": "lite",
        "docs": "lite",
    }
    tc = type_to_channel.get(task_type)
    if tc and output_kind not in ("architecture",):
        ch = tc

    # multi_module → upgrade (除外 analysis/research 纯分析任务)
    if multi_module and ch == "lite":
        ch = "standard"
    if multi_module and ch == "standard":
        ch = "full"

    if state is not None:
        _save_channel_and_blueprint(state, ch)
    print(f"channel={ch}")


def cmd_channel_set_override(channel_name, reason=""):
    """Set LLM channel override — immediately effective.

    Calls _save_channel_and_blueprint to update state.channel and
    state.blueprint so that pipeline tick (which reads state.channel
    via _resolve_blueprint_name) picks up the override without needing
    a separate 'channel auto' call in between.
    """
    state = _require_state()
    cc = state.setdefault("channel_config", {})
    cc.setdefault("overrides", {})["profile"] = channel_name
    cc["llm_override_reason"] = reason
    _save_channel_and_blueprint(state, channel_name)
    print(f"channel override set to {channel_name}" + (f" ({reason})" if reason else ""))


def cmd_channel_clear_override():
    """Clear channel override."""
    state = _require_state()
    cc = state.get("channel_config", {})
    cc.get("overrides", {}).pop("profile", None)
    cc.pop("llm_override_reason", None)
    _save_state(state)
    print("channel override cleared")


# ── Config Commands ───────────────────────────────────


def cmd_config_list_channels():
    """List all channel definitions from channel-rules.yaml."""
    rules = _load_channel_rules()
    if not rules:
        print("{lite, standard, full} (built-in)")
        return
    for name, cfg in rules.items():
        desc = cfg.get("description", "") if isinstance(cfg, dict) else ""
        topo = cfg.get("topology", "?") if isinstance(cfg, dict) else "?"
        print(f"  {name}: {desc} (topology={topo})")


def cmd_config_get_channel(channel_name):
    """Show full config for a named channel."""
    rules = _load_channel_rules()
    if channel_name in rules:
        print(json.dumps(rules[channel_name], indent=2, ensure_ascii=False))
    else:
        print(f"ERROR: channel '{channel_name}' not found", file=sys.stderr)
        print("Available:", ", ".join(rules.keys()) if rules else "{lite, standard, full}")


def cmd_config_get_made():
    """Compute MADE config from channel-rules + domain override."""
    raw_args = sys.argv
    domain = ""
    for i, a in enumerate(raw_args):
        if a == "--domain" and i + 1 < len(raw_args):
            domain = raw_args[i + 1]

    rules = _load_channel_rules()
    default_made = {"trigger_depth": 2, "n_explorers_min": 2, "n_explorers_max": 6, "fallback_on_failure": "skip"}

    made_config = None
    for ch_name in ("full", "standard", "lite"):
        if ch_name not in rules:
            continue
        ch_cfg = rules[ch_name]
        if isinstance(ch_cfg, dict):
            mc = ch_cfg.get("made", ch_cfg.get("MADE"))
            if mc:
                made_config = mc
                break

    if made_config is None:
        made_config = default_made

    # Domain override
    if domain:
        domain_path = os.path.join(SKILL_DIR, "docs", "domain", f"{domain}.yaml")
        if os.path.exists(domain_path):
            try:
                with open(domain_path) as f:
                    dcfg = yaml.safe_load(f) if yaml else {}
                do = dcfg.get("made_overrides", {}).get("MADE", {})
                if do:
                    if do.get("always_explore"):
                        made_config["trigger_depth"] = 1
                    for k in ("n_explorers_min", "n_explorers_max", "fallback_on_failure"):
                        if k in do:
                            made_config[k] = do[k]
            except Exception:
                pass

    made_config.setdefault("fallback_on_failure", "skip")
    print(json.dumps(made_config, ensure_ascii=False))


def cmd_plan_made_allocate():
    """Allocate MADE budget across subtasks in plan.md.

    Usage: pdf-engine.py plan made-allocate [--subtasks <json>] [--made-config <json>]
    """
    raw_args = sys.argv
    subtasks_json = "[]"
    made_config_json = "{}"
    for i, a in enumerate(raw_args):
        if a == "--subtasks" and i + 1 < len(raw_args):
            subtasks_json = raw_args[i + 1]
        if a == "--made-config" and i + 1 < len(raw_args):
            made_config_json = raw_args[i + 1]

    try:
        subtasks = json.loads(subtasks_json)
        made_config = json.loads(made_config_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON parse: {e}", file=sys.stderr)
        return

    trigger_depth = made_config.get("trigger_depth", 2)
    n_explorers_min = made_config.get("n_explorers_min", 2)
    n_explorers_max = made_config.get("n_explorers_max", 6)

    # Sort by depth descending
    candidates = [s for s in subtasks if s.get("depth", 1) >= trigger_depth
                  and s.get("output_kind") not in ("config", "docs")]
    candidates.sort(key=lambda s: s.get("depth", 1), reverse=True)

    budget = n_explorers_max
    allocation = {}
    for c in candidates:
        sid = c["id"]
        n = min(n_explorers_min, budget)
        if n > 0:
            allocation[str(sid)] = {"made": True, "n_explorers": n}
            budget -= n
        if budget <= 0:
            break

    # Round-robin remaining budget
    idx = 0
    while budget > 0 and candidates:
        c = candidates[idx % len(candidates)]
        sid = c["id"]
        allocation[str(sid)]["n_explorers"] += 1
        budget -= 1
        idx += 1

    result = {
        "allocation": allocation,
        "total_explorers": sum(a["n_explorers"] for a in allocation.values()),
        "budget_exhausted": budget <= 0,
    }
    print(json.dumps(result, ensure_ascii=False))


def cmd_config_get_model(stage_role):
    """Resolve model tier for stage.role.

    Priority: CLI --model-tier > domain override > domain_model_defaults > spawn-config default > history recommendation > internal fallback.
    """
    raw_args = sys.argv
    domain = ""
    for i, a in enumerate(raw_args):
        if a == "--domain" and i + 1 < len(raw_args):
            domain = raw_args[i + 1]

    stage_role_parts = stage_role.split(".")
    if len(stage_role_parts) != 2:
        print("ERROR: usage: pdf-engine.py config get-model <stage>.<role> [--domain <d>]", file=sys.stderr)
        return
    stage, role = stage_role_parts
    valid_stages = {"plan": ["analysis", "design", "p2"], "do": ["p1", "p2"], "check": ["p1", "p2"], "act": ["p1", "p2"]}
    if stage not in valid_stages or role not in valid_stages[stage]:
        print(f"ERROR: invalid stage/role '{stage}.{role}'. Valid: ", file=sys.stderr)
        for s, roles in valid_stages.items():
            print(f"  {s}: {', '.join(roles)}", file=sys.stderr)
        return

    state = _load_state_with_migration()

    fallbacks = {"plan.analysis": "sonnet", "plan.design": "haiku", "plan.p2": "sonnet",
                 "do.p1": "sonnet", "do.p2": "sonnet",
                 "check.p1": "sonnet", "check.p2": "sonnet",
                 "act.p1": "sonnet", "act.p2": "sonnet"}

    if state and not state.get("history_fetched"):
        _auto_fetch_history_recommendation(state)

    # CLI override
    if state:
        overrides = state.get("model_tier", {}).get("overrides", {})
        cli_val = overrides.get(f"{stage}.{role}")
        if cli_val:
            print(cli_val)
            return

    # Domain override
    domain_val = _get_domain_model_override(domain, stage, role)
    if domain_val:
        print(domain_val)
        return

    # domain_model_defaults
    domain_md_val = _get_spawn_config_domain_model_default(domain, stage, role)
    if domain_md_val:
        print(domain_md_val)
        return

    # spawn-config.yaml default
    spawn_cfg_path = os.path.join(SKILL_DIR, "docs", "spawn-config.yaml")
    model = None
    if os.path.exists(spawn_cfg_path):
        try:
            with open(spawn_cfg_path) as f:
                cfg = yaml.safe_load(f) if yaml else {}
            mt = cfg.get("model_tier", {})
            defaults = mt.get("default", {})
            stage_cfg = defaults.get(stage, {})
            if role in ("analysis", "design", "p1"):
                val = stage_cfg.get(role, stage_cfg.get("p1", "haiku"))
                if isinstance(val, dict):
                    val = val.get("default", "haiku")
                model = val
            elif role == "p2":
                val = stage_cfg.get("p2", "sonnet")
                if isinstance(val, dict):
                    val = val.get("default", "sonnet")
                model = val
        except Exception:
            pass

    selected = model or fallbacks.get(f"{stage}.{role}", "haiku")

    # History recommendation
    if state:
        hr = state.get("history_recommendation", {})
        hist_val = hr.get(f"{stage}.{role}")
        if hist_val:
            selected = hist_val

    # Exploration
    exploration_rate = 0.2
    try:
        sc_path = os.path.join(SKILL_DIR, "docs", "spawn-config.yaml")
        if os.path.exists(sc_path):
            with open(sc_path) as _f:
                sc_cfg = yaml.safe_load(_f) if yaml else {}
            expl = sc_cfg.get("learning", {}).get("exploration", {})
            if expl:
                thresholds = expl.get("history_thresholds", {"cold_max": 3, "warm_max": 10})
                try:
                    import sqlite3
                    cycle_db = os.path.expanduser("~/.fat/pdf/cycle-log.db")
                    if os.path.exists(cycle_db):
                        conn = sqlite3.connect(cycle_db)
                        row = conn.execute("SELECT COUNT(*) FROM cycles WHERE is_seed IS NULL OR is_seed = 0").fetchone()
                        count = row[0] if row else 0
                        conn.close()
                    else:
                        count = 0
                except Exception:
                    count = 0

                if count <= 0:
                    exploration_rate = expl.get("cold_start_rate", 0.5)
                elif count <= thresholds.get("warm_max", 10):
                    exploration_rate = expl.get("warm_rate", 0.3)
                else:
                    exploration_rate = expl.get("mature_rate", 0.15)
    except Exception:
        exploration_rate = 0.2

    model_hierarchy = {"haiku": 0, "sonnet": 1, "opus": 2}
    if random.random() < exploration_rate:
        current_level = model_hierarchy.get(selected, 0)
        upgrade_map = {0: "sonnet", 1: "opus"}
        upgraded = upgrade_map.get(current_level)
        if upgraded:
            selected = upgraded

    print(selected)
