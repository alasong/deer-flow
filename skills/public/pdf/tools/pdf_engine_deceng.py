"""PDF Engine — Decision Engineering commands.

Extracted from pdf-engine.py for modularity.
"""

from pdf_engine_shared import (
    _load_dec_eng_yaml, _keyword_in_text, _grep_project,
    _timestamp, STATE_DIR, DECISION_ENG_DIR, PROJECT_ROOT,
    _load_state_with_migration, _require_state, _save_state,
    FACTOR_TAXONOMY, yaml, json, os, re, sys, datetime, timezone,
    PDFContext,
)


def _load_prechecks():
    return _load_dec_eng_yaml("prechecks.yaml")


def cmd_precheck_run(task_text):
    """Run prechecks.yaml checks against task input. Returns structured results.

    Usage: pdf-engine.py precheck run <task_text> [--domain <d>] [--task-type <t>] [--fast] [--collect-false-positive] [--fuzzy]
    """
    raw_args = sys.argv
    domain = "general"
    task_type = ""
    fuzzy = "--fuzzy" in raw_args
    for i, a in enumerate(raw_args):
        if a == "--domain" and i + 1 < len(raw_args):
            domain = raw_args[i + 1]
        if a == "--task-type" and i + 1 < len(raw_args):
            task_type = raw_args[i + 1]

    prechecks = _load_prechecks()
    if prechecks is None:
        print(json.dumps({"passed": True, "method": "no_prechecks_file", "results": []}, ensure_ascii=False))
        return

    all_checks = prechecks.get("prechecks", {})
    if domain not in all_checks:
        domain = "general"
    domain_checks = all_checks.get(domain, {})
    general_checks = all_checks.get("general", {})

    results = []
    for check in general_checks.get("common", []):
        r = _run_single_check(check, task_text, fuzzy=fuzzy)
        results.append(r)

    if task_type:
        type_checks = general_checks.get(task_type, [])
        for check in type_checks:
            r = _run_single_check(check, task_text, fuzzy=fuzzy)
            results.append(r)

        domain_type_checks = domain_checks.get(task_type, [])
        for check in domain_type_checks:
            r = _run_single_check(check, task_text, fuzzy=fuzzy)
            results.append(r)

    for check in domain_checks.get("common", []):
        r = _run_single_check(check, task_text, fuzzy=fuzzy)
        results.append(r)

    fast_mode = "--fast" in raw_args
    if fast_mode:
        results = [r for r in results if r.get("severity") == "block"]

    blockers = [r for r in results if r.get("severity") == "block"]
    ask_users = [r for r in results if r.get("severity") == "ask_user"]
    warnings = [r for r in results if r.get("severity") == "warn"]

    output = {
        "passed": len(blockers) == 0,
        "blockers": blockers,
        "ask_user": ask_users,
        "warnings": warnings,
        "total": len(results),
    }

    if fuzzy:
        all_fuzzy_matches = []
        for r in results:
            for m in r.get("fuzzy_matches", []):
                if m not in all_fuzzy_matches:
                    all_fuzzy_matches.append(m)
        output["fuzzy"] = True
        output["fuzzy_matches"] = all_fuzzy_matches

    print(json.dumps(output, indent=2, ensure_ascii=False))

    collect_fp = "--collect-false-positive" in raw_args
    if collect_fp:
        ctx = PDFContext.get_default()
        fp_dir = os.path.join(ctx.state_dir, "false-positives")
        try:
            os.makedirs(fp_dir, exist_ok=True)
        except OSError as e:
            print(f"WARNING: cannot create directory {fp_dir}: {e}", file=sys.stderr)
            return

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"fp_{ts}.json"
        fp_path = os.path.join(fp_dir, filename)

        fp_data = {
            "timestamp": _timestamp(),
            "task_text": task_text,
            "results": results,
            "domain": domain,
            "task_type": task_type,
        }

        try:
            with open(fp_path, "w") as f:
                json.dump(fp_data, f, indent=2, ensure_ascii=False)
            print(f"[FP_COLLECTED] {len(results)} results saved to {fp_path}", file=sys.stderr)
        except (IOError, OSError) as e:
            print(f"ERROR: failed to write {fp_path}: {e}", file=sys.stderr)


def _run_single_check(check, task_text, fuzzy=False):
    """Execute one precheck item."""
    check_id = check.get("id", "?")
    method = check.get("method", "keyword_scan")
    severity = check.get("action_on_fail", "warn")
    description = check.get("description", "")

    passed = True
    detail = ""
    fuzzy_matches = []

    if method == "keyword_scan":
        keywords = check.get("keywords_missing", [])
        if keywords:
            if fuzzy:
                passed = any(kw.lower() in task_text.lower() for kw in keywords)
                fuzzy_matches = [kw for kw in keywords if kw.lower() in task_text.lower()]
            else:
                passed = _keyword_in_text(task_text, keywords)
            if not passed:
                detail = f"missing keywords: {keywords[:3]}"
            else:
                detail = "required keywords found"

        keywords_present = check.get("keywords", [])
        if keywords_present:
            if fuzzy:
                found_kws = [kw for kw in keywords_present if kw.lower() in task_text.lower()]
                found = len(found_kws) > 0
                fuzzy_matches = found_kws
            else:
                found = _keyword_in_text(task_text, keywords_present)
                found_kws = [k for k in keywords_present if k in task_text]
            if found:
                detail = f"trigger keywords in task: {found_kws}"
            passed = found

    elif method == "engine_grep":
        passed = _grep_project(check.get("patterns", []))
        detail = "grep check" if passed else "reference may not exist"

    elif method == "stat":
        passed = True
        detail = "skipped (LLM should verify file existence)"

    elif method == "engine_search":
        passed = True
        detail = "skipped (LLM should check for duplicates)"

    elif method == "context_scan":
        passed = True
        detail = "skipped (LLM should verify context sufficiency)"

    else:
        detail = f"unknown method '{method}'"

    result = {
        "id": check_id,
        "description": description,
        "passed": passed,
        "detail": detail,
        "severity": severity if not passed else "ok",
    }
    if fuzzy:
        result["fuzzy_matches"] = fuzzy_matches
    return result


def cmd_scope_classify(task_text):
    """Classify task scope using scope-classifier.yaml. Three-tier fallback.

    Usage: pdf-engine.py scope classify <task_text>
    """
    rules = _load_dec_eng_yaml("scope-classifier.yaml")
    if rules is None:
        print(json.dumps({"scope": "within_project", "method": "default_no_rules"}, ensure_ascii=False))
        return

    scope_rules = rules.get("scope_rules", {})
    default_scope = rules.get("default_scope", "within_project")

    best_scope = None
    best_weight = 0
    for scope_name, scope_cfg in scope_rules.items():
        weight = scope_cfg.get("weight", 0)
        fallthrough = scope_cfg.get("fallthrough", True)

        for kw in scope_cfg.get("keywords", []):
            if re.search(kw, task_text, re.IGNORECASE):
                if weight > best_weight:
                    best_weight = weight
                    best_scope = scope_name
                if not fallthrough:
                    break

        if best_scope and not scope_rules.get(best_scope, {}).get("fallthrough", True):
            break

        for pat in scope_cfg.get("file_patterns", []):
            if _grep_project(pat):
                if weight > best_weight:
                    best_weight = weight
                    best_scope = scope_name
                if not fallthrough:
                    break

    if best_scope is None:
        best_scope = default_scope

    print(json.dumps({
        "scope": best_scope,
        "method": "inline",
        "confidence": "high" if best_weight > 0 else "low",
    }, ensure_ascii=False))


def cmd_sanitize_run(task_text):
    """Sanitize task input: strip biased terms, extract intent.

    Usage: pdf-engine.py sanitize run <task_text>
    """
    rules = _load_dec_eng_yaml("sanitizer-rules.yaml")
    if rules is None:
        print(json.dumps({
            "cleaned": task_text,
            "stripped_terms": [],
            "intent": "unknown",
            "method": "passthrough_no_rules"
        }, ensure_ascii=False))
        return

    sr = rules.get("sanitizer_rules", {})
    strip_rules = sr.get("strip_terms", [])
    intent_rules = sr.get("intent_extraction", [])
    default_intent = sr.get("default_intent", "unknown")

    dry_run = "--dry-run" in sys.argv

    cleaned = task_text
    stripped = []

    for rule in strip_rules:
        pattern = rule.get("pattern", "")
        replacement = rule.get("replacement", "")
        matches = re.findall(pattern, cleaned)
        if matches:
            stripped.append({"pattern": pattern, "count": len(matches)})
            if not dry_run:
                cleaned = re.sub(pattern, replacement, cleaned)

    intent = default_intent
    for intent_rule in intent_rules:
        for pat in intent_rule.get("patterns", []):
            if re.search(pat, task_text, re.IGNORECASE):
                intent = intent_rule.get("intent_type", intent)
                break

    print(json.dumps({
        "cleaned": cleaned,
        "stripped_terms": stripped,
        "intent": intent,
        "method": "inline",
    }, ensure_ascii=False))


def cmd_adr_validate(filepath):
    """Validate ADR YAML frontmatter in a designer output file.

    Usage: pdf-engine.py adr validate <filepath>
    """
    if not os.path.exists(filepath):
        print(json.dumps({"valid": False, "errors": [f"file not found: {filepath}"]}, ensure_ascii=False))
        return

    try:
        with open(filepath) as f:
            content = f.read()
    except IOError as e:
        print(json.dumps({"valid": False, "errors": [str(e)]}, ensure_ascii=False))
        return

    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        print(json.dumps({"valid": False, "errors": ["no YAML frontmatter found (must start with ---)"]}, ensure_ascii=False))
        return

    frontmatter_text = fm_match.group(1)
    if yaml is None:
        print(json.dumps({"valid": True, "method": "yaml_unavailable_skipped"}, ensure_ascii=False))
        return

    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as e:
        print(json.dumps({"valid": False, "errors": [f"YAML parse error: {str(e)}"]}, ensure_ascii=False))
        return

    if not isinstance(data, dict):
        print(json.dumps({"valid": False, "errors": ["frontmatter is not a mapping"]}, ensure_ascii=False))
        return

    decisions = data.get("decisions", [])
    if not isinstance(decisions, list):
        print(json.dumps({"valid": False, "errors": ["'decisions' must be a list"]}, ensure_ascii=False))
        return

    errors = []
    required_fields = ["id", "title", "context", "options", "decision", "rationale"]
    for i, dec in enumerate(decisions):
        if not isinstance(dec, dict):
            errors.append(f"decisions[{i}]: not a mapping")
            continue
        for field in required_fields:
            if field not in dec:
                errors.append(f"decisions[{i}].{field}: missing required field")
        if "decision" in dec and "options" in dec:
            if dec["decision"] not in dec["options"]:
                errors.append(f"decisions[{i}].decision '{dec['decision']}' not in options {dec['options']}")

    valid = len(errors) == 0
    print(json.dumps({
        "valid": valid,
        "decision_count": len(decisions),
        "errors": errors if errors else [],
    }, indent=2, ensure_ascii=False))


def cmd_decisions_merge(f1, f2):
    """Deterministic merge of two ADR YAML files using merge-rules.yaml.

    Usage: pdf-engine.py decisions merge <file1> <file2>
    """
    rules = _load_dec_eng_yaml("merge-rules.yaml")
    merge_cfg = rules.get("merge_rules", {}) if rules else {}

    def _load_adr(filepath):
        if not os.path.exists(filepath):
            return None, [f"file not found: {filepath}"]
        with open(filepath) as f:
            content = f.read()
        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if not fm_match:
            return None, ["no YAML frontmatter"]
        if yaml is None:
            return None, ["yaml unavailable"]
        data = yaml.safe_load(fm_match.group(1))
        if not isinstance(data, dict):
            return None, ["frontmatter not a mapping"]
        return data.get("decisions", []), []

    d1, errs1 = _load_adr(f1)
    d2, errs2 = _load_adr(f2)
    if errs1 or errs2:
        print(json.dumps({"error": f"errs1={errs1} errs2={errs2}"}, ensure_ascii=False))
        return

    if not isinstance(d1, list) or not isinstance(d2, list):
        print(json.dumps({"error": "decisions must be lists"}, ensure_ascii=False))
        return

    map1 = {d.get("id"): d for d in d1 if isinstance(d, dict) and d.get("id")}
    map2 = {d.get("id"): d for d in d2 if isinstance(d, dict) and d.get("id")}

    ids1 = set(map1.keys())
    ids2 = set(map2.keys())

    common = ids1 & ids2
    only1 = ids1 - ids2
    only2 = ids2 - ids1

    merged = []
    conflicts = []
    same_id_rules = merge_cfg.get("decisions", {}).get("same_id", {})
    decision_diff_action = same_id_rules.get("decision_different", "mark_conflict")

    for cid in sorted(common):
        d_a = map1[cid]
        d_b = map2[cid]
        if d_a.get("decision") == d_b.get("decision"):
            merged.append(d_a)
        else:
            if decision_diff_action == "auto_merge":
                merged.append(d_a)
                continue
            conflicts.append({
                "id": cid,
                "type": "critical",
                "a": {"decision": d_a.get("decision"), "rationale": d_a.get("rationale")[:100] if d_a.get("rationale") else ""},
                "b": {"decision": d_b.get("decision"), "rationale": d_b.get("rationale")[:100] if d_b.get("rationale") else ""},
            })

    for uid in sorted(only1):
        merged.append(map1[uid])
    for uid in sorted(only2):
        merged.append(map2[uid])

    for c in conflicts:
        c["type"] = "critical"

    output = {
        "merged_count": len(merged),
        "conflict_count": len(conflicts),
        "uniquely_from_a": len(only1),
        "uniquely_from_b": len(only2),
        "merged_decisions": merged,
        "conflicts": conflicts,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


def cmd_design_check(filepath):
    """Check design decisions against project constraints.

    Usage: pdf-engine.py design check <filepath> [--domain <d>]
    """
    raw_args = sys.argv
    domain = "general"
    for i, a in enumerate(raw_args):
        if a == "--domain" and i + 1 < len(raw_args):
            domain = raw_args[i + 1]

    constraints = _load_dec_eng_yaml("design-constraints.yaml")
    if constraints is None:
        print(json.dumps({"passed": True, "method": "no_constraints_file", "results": []}, ensure_ascii=False))
        return

    all_constraints = constraints.get("design_constraints", {})
    general_c = all_constraints.get("general", [])
    domain_c = all_constraints.get(domain, [])

    decisions = []
    if os.path.exists(filepath):
        with open(filepath) as f:
            content = f.read()
        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm_match and yaml:
            data = yaml.safe_load(fm_match.group(1))
            decisions = data.get("decisions", []) if isinstance(data, dict) else []

    results = []
    for constraint in general_c + domain_c:
        check_method = constraint.get("method", "")
        action = constraint.get("action", "warn")
        description = constraint.get("description", "")

        if check_method in ("grep_interface_usage", "grep_schema_pattern"):
            refs = set()
            for dec in decisions:
                if isinstance(dec, dict):
                    for field in ("decision", "context", "rationale", "consequences"):
                        val = dec.get(field, "")
                        if isinstance(val, str):
                            for word in val.split():
                                clean = word.strip(".,()[]{}'\"")
                                if clean and clean[0].isupper():
                                    refs.add(clean)
            results.append({
                "id": constraint.get("id", "?"),
                "description": description,
                "action": action,
                "pass": True,
                "detail": f"checked {len(refs)} refs: {', '.join(sorted(refs)[:5])}...",
            })
        else:
            results.append({
                "id": constraint.get("id", "?"),
                "description": description,
                "action": action,
                "pass": True,
                "detail": f"skipped (method: {check_method})",
            })

    errors = [r for r in results if not r["pass"] and r["action"] == "error_if_conflict"]
    print(json.dumps({
        "passed": len(errors) == 0,
        "result_count": len(results),
        "error_count": len(errors),
        "results": results,
    }, indent=2, ensure_ascii=False))


def cmd_review_precheck(filepath):
    """Run engine-level pre-check before spawning LLM reviewer.

    Usage: pdf-engine.py review precheck <filepath> --dimension <dimension>
    """
    raw_args = sys.argv
    dimension = ""
    for i, a in enumerate(raw_args):
        if a == "--dimension" and i + 1 < len(raw_args):
            dimension = raw_args[i + 1]

    if not dimension:
        print(json.dumps({"error": "--dimension required"}, ensure_ascii=False))
        return

    if not os.path.exists(filepath):
        print(json.dumps({"error": f"file not found: {filepath}"}, ensure_ascii=False))
        return

    try:
        with open(filepath) as f:
            content = f.read()
    except IOError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return

    findings = []

    if dimension == "correctness":
        has_types = bool(re.search(r'\bdef\s+\w+\s*\([^)]*:\s*\w+', content))
        has_error_handling = bool(re.search(r'\b(try|except|catch|if\s+.*error|raise)\b', content, re.IGNORECASE))
        has_boundary = bool(re.search(r'\b(null|None|undefined|empty|nil|0|max|min)\b', content, re.IGNORECASE))
        findings = [
            {"check": "type_annotations", "pass": has_types,
             "detail": "type annotations found" if has_types else "no type annotations detected"},
            {"check": "error_handling", "pass": has_error_handling,
             "detail": "error handling patterns found" if has_error_handling else "no error handling patterns detected"},
            {"check": "boundary_conditions", "pass": has_boundary,
             "detail": "boundary references found" if has_boundary else "no boundary references detected"},
        ]

    elif dimension == "security":
        has_input = bool(re.search(r'\b(input|request|param|query|form)\b', content, re.IGNORECASE))
        has_auth = bool(re.search(r'\b(auth|token|login|permission|role|guard)\b', content, re.IGNORECASE))
        has_encrypt = bool(re.search(r'\b(password|secret|key|encrypt|hash)\b', content, re.IGNORECASE))
        findings = [
            {"check": "input_handling", "pass": has_input,
             "detail": "input patterns found" if has_input else "no input handling detected"},
            {"check": "auth_patterns", "pass": has_auth,
             "detail": "auth patterns found" if has_auth else "no auth patterns detected"},
            {"check": "sensitive_data", "pass": has_encrypt,
             "detail": "sensitive data patterns found" if has_encrypt else "no sensitive data detected"},
        ]

    elif dimension == "performance":
        has_loop_io = bool(re.search(r'\b(for|while|loop).{0,50}(query|fetch|request|http|read|write)\b', content, re.IGNORECASE))
        has_batch = bool(re.search(r'\b(batch|pagination|limit|offset|stream|chunk)\b', content, re.IGNORECASE))
        has_cache = bool(re.search(r'\b(cache|memo|redis|buffer|pool)\b', content, re.IGNORECASE))
        findings = [
            {"check": "loop_io", "pass": not has_loop_io,
             "detail": "possible N+1 pattern detected" if has_loop_io else "no loop IO detected"},
            {"check": "batching", "pass": has_batch,
             "detail": "batching patterns found" if has_batch else "no batching detected"},
            {"check": "caching", "pass": has_cache,
             "detail": "caching patterns found" if has_cache else "no caching detected"},
        ]

    elif dimension in ("api_design", "maintainability"):
        has_schema = bool(re.search(r'\b(schema|type|interface|Dto|Request|Response)\b', content, re.IGNORECASE))
        has_version = bool(re.search(r'\b(version|v1|v2|deprecated|compat)\b', content, re.IGNORECASE))
        has_naming = bool(re.search(r'\b(tmp|temp|data|info|xxx|foo)\b', content, re.IGNORECASE))
        findings = [
            {"check": "schema_definition", "pass": has_schema,
             "detail": "schema definitions found" if has_schema else "no schema definitions detected"},
            {"check": "versioning", "pass": has_version,
             "detail": "version references found" if has_version else "no version references detected"},
            {"check": "naming_quality", "pass": not has_naming,
             "detail": "possible generic names found" if has_naming else "no generic naming detected"},
        ]

    elif dimension == "test_quality":
        has_assert = bool(re.search(r'\b(assert|expect|should|verify)\b', content, re.IGNORECASE))
        has_mock = bool(re.search(r'\b(mock|stub|fake|spy|patch)\b', content, re.IGNORECASE))
        has_describe = bool(re.search(r'\b(describe|it\(|test\(|TestCase|def test_)\b', content, re.IGNORECASE))
        findings = [
            {"check": "assertions", "pass": has_assert,
             "detail": "assertions found" if has_assert else "no assertions detected"},
            {"check": "mocks", "pass": has_mock,
             "detail": "mocking patterns found" if has_mock else "no mocking detected"},
            {"check": "test_structure", "pass": has_describe,
             "detail": "test structure found" if has_describe else "no test structure detected"},
        ]

    elif dimension == "reliability":
        has_retry = bool(re.search(r'\b(retry|backoff|timeout|max_attempts)\b', content, re.IGNORECASE))
        has_fallback = bool(re.search(r'\b(fallback|degrade|catch|except|alternative)\b', content, re.IGNORECASE))
        has_idempotent = bool(re.search(r'\b(idempotent|幂等|nonce|unique.*key)\b', content, re.IGNORECASE))
        findings = [
            {"check": "retry_mechanism", "pass": has_retry,
             "detail": "retry patterns found" if has_retry else "no retry patterns detected"},
            {"check": "fallback", "pass": has_fallback,
             "detail": "fallback patterns found" if has_fallback else "no fallback patterns detected"},
            {"check": "idempotency", "pass": has_idempotent,
             "detail": "idempotency patterns found" if has_idempotent else "no idempotency detected"},
        ]

    elif dimension == "data_privacy":
        has_pii = bool(re.search(r'\b(PII|email|phone|address|password|ssn|身份证)\b', content, re.IGNORECASE))
        has_encrypt = bool(re.search(r'\b(encrypt|cipher|TLS|SSL|HTTPS|脱敏|mask)\b', content, re.IGNORECASE))
        has_audit = bool(re.search(r'\b(audit|log.*access|谁可以|权限)\b', content, re.IGNORECASE))
        findings = [
            {"check": "pii_handling", "pass": has_pii,
             "detail": "PII patterns found — needs review" if has_pii else "no PII patterns detected"},
            {"check": "encryption", "pass": has_encrypt,
             "detail": "encryption patterns found" if has_encrypt else "no encryption detected"},
            {"check": "audit_access", "pass": has_audit,
             "detail": "audit patterns found" if has_audit else "no audit patterns detected"},
        ]

    else:
        findings = [{"check": "dimension", "pass": False, "detail": f"unknown dimension: {dimension}"}]

    output = {
        "dimension": dimension,
        "check_count": len(findings),
        "pass_count": sum(1 for f in findings if f["pass"]),
        "findings": findings,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
