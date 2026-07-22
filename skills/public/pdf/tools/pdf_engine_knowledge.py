"""PDF Engine — Knowledge Management + History commands.

Extracted from pdf-engine.py for modularity.
"""

from pdf_engine_shared import (
    _query_cycle_db, _write_knowledge_json, _read_knowledge_json,
    _get_knowledge_root, _timestamp, _chunk_markdown_by_headers,
    _chunk_kb_json, _require_state, STATE_DIR, KNOWLEDGE_DIR,
    CYCLE_DB_PATH, PROJECT_ROOT, SKILL_DIR,
    yaml, json, os, re, sys, glob, datetime, timezone,
    PDFContext,
)


def cmd_knowledge_load_past_adrs(domain):
    """Load past ADR/decision records from cycle-log DB by domain.

    Usage: pdf-engine.py knowledge load-past-adrs <domain> [--days <n>]
    """
    raw_args = sys.argv
    days = 90
    for i, a in enumerate(raw_args):
        if a == "--days" and i + 1 < len(raw_args):
            try:
                days = int(raw_args[i + 1])
            except ValueError:
                pass

    rows = _query_cycle_db(
        "SELECT c.id, c.task_slug, c.project, c.completed, c.stage, "
        "c.n, c.m, c.p1_found, c.p2_found, c.n_m_accuracy, c.missed_dimension, c.lesson, "
        "f.dimension as finding_dim, f.severity, f.module, f.description "
        "FROM cycles c "
        "LEFT JOIN findings f ON f.cycle_id = c.id "
        "WHERE c.completed >= date('now', ?) AND (c.project LIKE ? OR c.lesson LIKE ?) "
        "ORDER BY c.completed DESC",
        [f"-{days} days", f"%{domain}%", f"%{domain}%"]
    )

    if not rows:
        print(json.dumps({
            "domain": domain,
            "days": days,
            "adr_count": 0,
            "recent_cycles": [],
            "knowledge_dir": KNOWLEDGE_DIR,
            "note": "cold start — no cycle-log data yet. Data accumulates over time.",
        }, indent=2, ensure_ascii=False))
        return

    cycles = {}
    for r in rows:
        cid = r["id"]
        if cid not in cycles:
            cycles[cid] = {
                "id": cid, "task_slug": r["task_slug"], "project": r["project"],
                "completed": r["completed"], "stage": r["stage"],
                "n": r["n"], "m": r["m"], "p1": r["p1_found"], "p2": r["p2_found"],
                "accuracy": r["n_m_accuracy"], "missed": r["missed_dimension"],
                "lesson": r["lesson"], "findings": [],
            }
        if r["finding_dim"]:
            cycles[cid]["findings"].append({
                "dimension": r["finding_dim"],
                "severity": r["severity"],
                "module": r["module"],
                "description": r["description"],
            })

    dim_counts = {}
    for c in cycles.values():
        for f in c["findings"]:
            d = f["dimension"]
            dim_counts[d] = dim_counts.get(d, 0) + 1

    lessons = [c["lesson"] for c in cycles.values() if c.get("lesson")]

    result = {
        "domain": domain,
        "days": days,
        "adr_count": len(cycles),
        "dimension_frequency": dim_counts,
        "recent_lessons": lessons[:5],
        "recent_cycles": list(cycles.values())[:10],
    }

    _write_knowledge_json(f"adr_domain_{domain}.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_knowledge_match_historical(dimension):
    """Match findings against cycle-log history. Flags recurring patterns.

    Usage: pdf-engine.py knowledge match-historical <dimension> --description <text> [--days <n>]
    """
    raw_args = sys.argv
    description = ""
    days = 90
    for i, a in enumerate(raw_args):
        if a == "--description" and i + 1 < len(raw_args):
            description = raw_args[i + 1]
        if a == "--days" and i + 1 < len(raw_args):
            try:
                days = int(raw_args[i + 1])
            except ValueError:
                pass

    rows = _query_cycle_db(
        "SELECT c.task_slug, c.completed, f.severity, f.module, f.description, f.dimension "
        "FROM findings f JOIN cycles c ON f.cycle_id = c.id "
        "WHERE f.dimension = ? AND c.completed >= date('now', ?) "
        "ORDER BY c.completed DESC",
        [dimension, f"-{days} days"]
    )

    if not rows:
        print(json.dumps({
            "dimension": dimension,
            "matched_count": 0,
            "recurring": False,
            "historical": [],
            "note": "cold start — no historical findings for this dimension",
        }, indent=2, ensure_ascii=False))
        return

    desc_keywords = set(description.lower().split()) if description else set()
    matched = []
    for r in rows:
        score = 0
        hist_desc = (r.get("description") or "").lower()
        if desc_keywords and hist_desc:
            overlap = desc_keywords & set(hist_desc.split())
            score = len(overlap) / max(len(desc_keywords), 1)
        if score > 0.2 or (not description):
            matched.append({
                "task_slug": r["task_slug"],
                "completed": r["completed"],
                "severity": r["severity"],
                "module": r["module"],
                "description": r["description"],
                "relevance_score": round(score, 2),
            })

    high_matches = [m for m in matched if m["relevance_score"] > 0.3]
    recurring = len(high_matches) >= 2

    if description and high_matches:
        history = _read_knowledge_json("match_history.json") or []
        history.append({
            "dimension": dimension,
            "description": description,
            "matches": len(high_matches),
            "recurring": recurring,
            "timestamp": _timestamp(),
        })
        _write_knowledge_json("match_history.json", history[-50:])

    result = {
        "dimension": dimension,
        "matched_count": len(matched),
        "recurring": recurring,
        "historical": matched[:10],
    }
    if recurring:
        result["alert"] = f"⚠ {len(high_matches)} similar {dimension} findings in {days}d — consider root cause analysis"
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_knowledge_resolve_precedent():
    """Search cycle-log for past conflict resolution patterns.

    Usage: pdf-engine.py knowledge resolve-precedent [--pattern <text>] [--days <n>]
    """
    raw_args = sys.argv
    pattern = ""
    days = 365
    for i, a in enumerate(raw_args):
        if a == "--pattern" and i + 1 < len(raw_args):
            pattern = raw_args[i + 1]
        if a == "--days" and i + 1 < len(raw_args):
            try:
                days = int(raw_args[i + 1])
            except ValueError:
                pass

    rows = _query_cycle_db(
        "SELECT c.task_slug, c.completed, c.lesson, "
        "f.dimension, f.severity, f.description, f.adversary_success "
        "FROM findings f JOIN cycles c ON f.cycle_id = c.id "
        "WHERE f.adversary_success = 1 AND c.completed >= date('now', ?) "
        "ORDER BY c.completed DESC",
        [f"-{days} days"]
    )

    if not rows:
        print(json.dumps({
            "precedent_found": False,
            "unanimous": False,
            "precedent_count": 0,
            "resolutions": [],
            "note": "cold start — no conflict resolution data yet",
        }, indent=2, ensure_ascii=False))
        return

    from collections import Counter
    dim_counter = Counter()
    resolutions = []
    for r in rows:
        dim_counter[r.get("dimension", "?")] += 1
        resolutions.append({
            "task_slug": r["task_slug"],
            "completed": r["completed"],
            "dimension": r.get("dimension"),
            "severity": r.get("severity"),
            "description": r.get("description"),
            "lesson": r.get("lesson"),
        })

    unanimous_dims = {d: c for d, c in dim_counter.items() if c >= 3}

    result = {
        "precedent_found": len(resolutions) > 0,
        "total_resolved": len(resolutions),
        "by_dimension": dict(dim_counter),
        "unanimous": len(unanimous_dims) > 0,
        "unanimous_dimensions": list(unanimous_dims.keys()),
        "resolutions": resolutions[:10],
    }
    if unanimous_dims:
        result["suggestion"] = (
            f"Unanimous pattern for {list(unanimous_dims.keys())} — "
            f"≥3 consistent resolutions. Consider auto-applying."
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_knowledge_get_ts(name):
    """Read tech-stack knowledge file (~/.fat/pdf/knowledge/tech-stack/<name>.md).

    Usage: pdf-engine.py knowledge get-ts <name>
    """
    root = _get_knowledge_root()
    path = os.path.join(root, "tech-stack", f"{name}.md")
    if os.path.exists(path):
        try:
            with open(path) as f:
                content = f.read()
            if content.strip():
                print(content, end="")
                return
        except OSError as e:
            print(f"WARN: knowledge get-ts {name} read error: {e}", file=sys.stderr)
    return


def cmd_knowledge_append_ts(name, content):
    """Append experience to tech-stack knowledge file.

    Usage: pdf-engine.py knowledge append-ts <name> --content "<text>"
    """
    if not content or not content.strip():
        print("ERROR: knowledge append-ts requires --content <text>", file=sys.stderr)
        return

    root = _get_knowledge_root()
    ts_dir = os.path.join(root, "tech-stack")
    os.makedirs(ts_dir, exist_ok=True)

    path = os.path.join(ts_dir, f"{name}.md")
    project = os.path.basename(os.getcwd())
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    entry = f"\n<!-- source: project={project} ts={ts} -->\n{content.strip()}\n"

    try:
        with open(path, "a") as f:
            f.write(entry)
        print(f"OK: appended to {path}")
    except OSError as e:
        print(f"ERROR: knowledge append-ts write error: {e}", file=sys.stderr)
        return

    index_dir = os.path.join(root, ".rag-index")
    index_path = os.path.join(index_dir, "index.faiss")
    chunks_path = os.path.join(index_dir, "chunks.json")
    if os.path.exists(index_path) and os.path.exists(chunks_path):
        try:
            from sentence_transformers import SentenceTransformer
            import faiss
            import numpy as np

            model = SentenceTransformer('all-MiniLM-L6-v2')
            new_vec = model.encode([content.strip()]).astype(np.float32)

            index = faiss.read_index(index_path)
            with open(chunks_path) as f:
                chunks = json.load(f)

            next_id = len(chunks)
            index.add(new_vec)
            chunks.append({
                "id": next_id,
                "source": f"tech-stack/{name}.md",
                "section": f"appended-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                "text": content.strip()
            })

            faiss.write_index(index, index_path)
            with open(chunks_path, "w") as f:
                json.dump(chunks, f, indent=2, ensure_ascii=False)

            print(f"OK: index updated ({next_id + 1} chunks total)")
        except Exception as e:
            print(f"WARN: index update skipped ({e})", file=sys.stderr)


def cmd_knowledge_search(query, top_k=3, source="all"):
    """Semantic search across the PDF knowledge base.

    Usage: pdf-engine.py knowledge search <query> [--top-k N] [--source tech-stack|kb|all]
    """
    try:
        from sentence_transformers import SentenceTransformer
        import faiss
        import numpy as np
    except ImportError:
        print(json.dumps({
            "results": [],
            "note": "semantic_search_disabled",
            "message": "FAISS index not found. To enable: "
                       "pip install sentence-transformers faiss-cpu && "
                       "pdf-engine.py knowledge reindex"
        }, indent=2, ensure_ascii=False))
        return

    index_dir = os.path.join(_get_knowledge_root(), ".rag-index")
    index_path = os.path.join(index_dir, "index.faiss")
    chunks_path = os.path.join(index_dir, "chunks.json")

    if not os.path.exists(index_path) or not os.path.exists(chunks_path):
        print(json.dumps({
            "results": [],
            "note": "index_not_found",
            "message": "Run: pdf-engine.py knowledge reindex"
        }, indent=2, ensure_ascii=False))
        return

    try:
        index = faiss.read_index(index_path)
        with open(chunks_path) as f:
            chunks = json.load(f)

        model = SentenceTransformer('all-MiniLM-L6-v2')
        q_vec = model.encode([query]).astype(np.float32)
        scores, indices = index.search(q_vec, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(chunks):
                continue
            chunk = chunks[idx]
            if source != "all" and not chunk.get("source", "").startswith(source):
                continue
            results.append({
                "text": chunk["text"][:500],
                "source": chunk["source"],
                "section": chunk.get("section", ""),
                "score": round(float(score), 4)
            })

        print(json.dumps({"results": results}, indent=2, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({
            "results": [],
            "note": "search_error",
            "message": str(e)
        }, indent=2, ensure_ascii=False))


def cmd_knowledge_reindex(source="all", force=False):
    """Rebuild FAISS index from knowledge sources.

    Usage: pdf-engine.py knowledge reindex [--source tech-stack|kb|all] [--force]
    """
    try:
        from sentence_transformers import SentenceTransformer
        import faiss
        import numpy as np
    except ImportError:
        print(
            "ERROR: sentence-transformers and faiss-cpu required.\n"
            "  pip install sentence-transformers faiss-cpu\n"
            "  Then re-run: pdf-engine.py knowledge reindex",
            file=sys.stderr
        )
        return

    knowledge_root = _get_knowledge_root()
    chunks = []

    if source in ("all", "tech-stack"):
        ts_dir = os.path.join(knowledge_root, "tech-stack")
        if os.path.exists(ts_dir):
            for fname in sorted(os.listdir(ts_dir)):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(ts_dir, fname)
                try:
                    sections = _chunk_markdown_by_headers(fpath)
                    for sec in sections:
                        sec["source"] = f"tech-stack/{fname}"
                        chunks.append(sec)
                except OSError:
                    continue

    if source in ("all", "kb"):
        kb_files = ["module-boundaries.json", "dependency-rules.json",
                     "convention-patterns.json"]
        for fname in kb_files:
            fpath = os.path.join(knowledge_root, fname)
            if os.path.exists(fpath):
                try:
                    entries = _chunk_kb_json(fpath)
                    for entry in entries:
                        entry["source"] = f"kb/{fname}"
                        chunks.append(entry)
                except (json.JSONDecodeError, OSError):
                    continue

    if not chunks:
        print(f"WARN: no content found for source='{source}'", file=sys.stderr)
        print(json.dumps({"ok": False, "chunks_count": 0}))
        return

    texts = [c["text"] for c in chunks]
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = model.encode(texts, show_progress_bar=False)

    dim = 384
    index = faiss.IndexFlatIP(dim)
    index.add(np.array(embeddings).astype(np.float32))

    index_dir = os.path.join(knowledge_root, ".rag-index")
    os.makedirs(index_dir, exist_ok=True)
    faiss.write_index(index, os.path.join(index_dir, "index.faiss"))

    chunks_meta = [
        {"id": i, "source": c["source"],
         "section": c.get("section", ""), "text": c["text"]}
        for i, c in enumerate(chunks)
    ]
    with open(os.path.join(index_dir, "chunks.json"), "w") as f:
        json.dump(chunks_meta, f, indent=2, ensure_ascii=False)

    print(json.dumps({"ok": True, "chunks_count": len(chunks)}, indent=2))


def cmd_knowledge_seed():
    """Generate seed knowledge entries from domain config files.

    Usage: pdf-engine.py knowledge seed
    """
    domain_dir = os.path.join(SKILL_DIR, "docs", "domain")
    seeds_dir = os.path.join(PDFContext.get_default().project_knowledge_dir, "seeds")

    if os.path.exists(seeds_dir):
        existing = glob.glob(os.path.join(seeds_dir, "*.md"))
        if existing:
            print(f"OK: seeds already exist ({len(existing)} files), skipping")
            return

    domain_files = sorted(glob.glob(os.path.join(domain_dir, "*.yaml")))
    domain_files = [f for f in domain_files if not f.endswith("index.yaml")]

    if not domain_files:
        print("OK: no domain configs found, nothing to seed")
        return

    if yaml is None:
        print("WARN: PyYAML not available, cannot parse domain configs", file=sys.stderr)
        return

    generated = 0
    for domain_file in domain_files:
        domain_name = os.path.splitext(os.path.basename(domain_file))[0]

        try:
            with open(domain_file) as f:
                config = yaml.safe_load(f)
        except Exception as e:
            print(f"WARN: failed to parse {domain_file}: {e}", file=sys.stderr)
            continue

        if not config or not isinstance(config, dict):
            continue

        partitions = config.get("analysis", {}).get("partitions", [])
        if not partitions:
            print(f"  domain '{domain_name}' has no analysis.partitions, skipping")
            continue

        lines = []
        lines.append("---")
        lines.append(f"domain: {domain_name}")
        lines.append(f"generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
        lines.append(f"source: domain/{domain_name}.yaml")
        lines.append("kind: seed")
        lines.append("---")
        lines.append("")
        lines.append(f"# Domain Seeds: {domain_name}")
        lines.append("")

        total_items = 0
        for partition in partitions:
            pname = partition.get("name", "unknown")
            focus_items = partition.get("focus", [])
            if focus_items:
                lines.append(f"## {pname}")
                lines.append("")
                for item in focus_items:
                    lines.append(f"- {item}")
                lines.append("")
                total_items += len(focus_items)

        os.makedirs(seeds_dir, exist_ok=True)
        seed_path = os.path.join(seeds_dir, f"{domain_name}.md")
        try:
            with open(seed_path, "w") as f:
                f.write("\n".join(lines) + "\n")
            print(f"  seeded {domain_name} ({total_items} focus items)")
            generated += 1
        except OSError as e:
            print(f"ERROR: write {seed_path}: {e}", file=sys.stderr)

    print(f"OK: generated {generated} domain seed files in {seeds_dir}")


# === History Commands ===

def cmd_history_query():
    """Query cycle-db for history matching given criteria.

    Usage: pdf-engine.py history query [--days <N>] [--dimension <d>] [--domain <domain>]
    """
    raw_args = sys.argv
    days = 30
    dimension = ""
    domain = ""
    for i, a in enumerate(raw_args):
        if a == "--days" and i + 1 < len(raw_args):
            try:
                days = int(raw_args[i + 1])
            except ValueError:
                pass
        if a == "--dimension" and i + 1 < len(raw_args):
            dimension = raw_args[i + 1]
        if a == "--domain" and i + 1 < len(raw_args):
            domain = raw_args[i + 1]

    if dimension or domain:
        sql = ("SELECT DISTINCT c.id, c.task_slug, c.project, c.completed, c.stage, "
               "c.n, c.m, c.p1_found, c.p2_found, c.n_m_accuracy, "
               "c.missed_dimension, c.lesson, c.model_allocation, c.effectiveness "
               "FROM cycles c "
               "LEFT JOIN findings f ON f.cycle_id = c.id "
               "WHERE c.completed >= date('now', ?)")
        params = [f"-{days} days"]
        if dimension:
            sql += " AND (f.dimension = ? OR c.missed_dimension LIKE ?)"
            params.append(dimension)
            params.append(f"%{dimension}%")
        if domain:
            sql += " AND (c.project LIKE ? OR c.lesson LIKE ?)"
            params.append(f"%{domain}%")
            params.append(f"%{domain}%")
        sql += " ORDER BY c.completed DESC"
    else:
        sql = ("SELECT c.id, c.task_slug, c.project, c.completed, c.stage, "
               "c.n, c.m, c.p1_found, c.p2_found, c.n_m_accuracy, "
               "c.missed_dimension, c.lesson, c.model_allocation, c.effectiveness "
               "FROM cycles c "
               "WHERE c.completed >= date('now', ?) "
               "ORDER BY c.completed DESC")
        params = [f"-{days} days"]

    rows = _query_cycle_db(sql, params)

    if not rows:
        print(json.dumps({
            "cycles": [], "count": 0,
            "note": "no matching cycles found (DB may be empty or cold start)"
        }, indent=2, ensure_ascii=False))
        return

    for r in rows:
        if isinstance(r.get("model_allocation"), str):
            try:
                r["model_allocation"] = json.loads(r["model_allocation"])
            except (json.JSONDecodeError, TypeError):
                r["model_allocation"] = {}

    result = {
        "cycles": rows,
        "count": len(rows),
        "days": days,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_history_update():
    """Record a cycle completion in cycle-db via subprocess call.

    Usage: pdf-engine.py history update --task-slug <slug> --stage <s>
           [--n <N>] [--m <M>] [--p1 <int>] [--p2 <int>]
           [--model-allocation <json>] [--effectiveness <score>]
           [--project <p>] [--accuracy <a>] [--missed <d>] [--lesson <l>]
    """
    import subprocess

    raw_args = sys.argv
    task_slug = ""
    stage = ""
    n = 1
    m = 1
    p1 = 0
    p2 = 0
    model_allocation = "{}"
    effectiveness = 0.0
    ctx = PDFContext.get_default()
    project = os.path.basename(ctx.project_root)
    accuracy = ""
    missed = ""
    lesson = ""

    for i, a in enumerate(raw_args):
        if a == "--task-slug" and i + 1 < len(raw_args):
            task_slug = raw_args[i + 1]
        if a == "--stage" and i + 1 < len(raw_args):
            stage = raw_args[i + 1]
        if a == "--n" and i + 1 < len(raw_args):
            try:
                n = int(raw_args[i + 1])
            except ValueError:
                pass
        if a == "--m" and i + 1 < len(raw_args):
            try:
                m = int(raw_args[i + 1])
            except ValueError:
                pass
        if a == "--p1" and i + 1 < len(raw_args):
            try:
                p1 = int(raw_args[i + 1])
            except ValueError:
                pass
        if a == "--p2" and i + 1 < len(raw_args):
            try:
                p2 = int(raw_args[i + 1])
            except ValueError:
                pass
        if a == "--model-allocation" and i + 1 < len(raw_args):
            model_allocation = raw_args[i + 1]
        if a == "--effectiveness" and i + 1 < len(raw_args):
            try:
                effectiveness = float(raw_args[i + 1])
            except ValueError:
                pass
        if a == "--project" and i + 1 < len(raw_args):
            project = raw_args[i + 1]
        if a == "--accuracy" and i + 1 < len(raw_args):
            accuracy = raw_args[i + 1]
        if a == "--missed" and i + 1 < len(raw_args):
            missed = raw_args[i + 1]
        if a == "--lesson" and i + 1 < len(raw_args):
            lesson = raw_args[i + 1]

    if not task_slug or not stage:
        print("ERROR: --task-slug and --stage are required", file=sys.stderr)
        return

    cycle_db_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf-cycle-db.py")

    try:
        result = subprocess.run(
            [sys.executable, cycle_db_script, "insert",
             task_slug, project, stage, str(n), str(m),
             str(p1), str(p2), accuracy, missed, lesson,
             model_allocation, str(effectiveness)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"OK: cycle recorded ({result.stdout.strip()})")
        else:
            print(f"ERROR: cycle-db insert failed: {result.stderr.strip()}", file=sys.stderr)
    except FileNotFoundError:
        print("WARN: pdf-cycle-db.py not found, cycle not recorded", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: cycle-db insert error: {e}", file=sys.stderr)


def cmd_knowledge_persist_factors():
    """Persist factor analysis results to cycle-log DB and factor knowledge JSON.

    Reads factor_analysis.md (primary) or state.triggered_factors (fallback),
    writes each factor to the cycle-log DB factor_findings table,
    and appends to .fat/pdf/knowledge/factors/factor_cycle_log.json.

    Usage: pdf-engine.py knowledge persist-factors
    """
    state = _require_state()
    ctx = PDFContext.get_default()
    pdf_dir = ctx.state_dir
    factor_path = os.path.join(pdf_dir, "factor_analysis.md")

    factors = {}
    data_complete = False

    if os.path.exists(factor_path):
        current_factor = None
        try:
            with open(factor_path) as f:
                for line in f:
                    m = re.match(r'^###\s+(\S+)', line)
                    if m:
                        current_factor = m.group(1)
                        factors[current_factor] = {
                            "matched_keywords": [], "add_dimensions": [], "force_channel": None
                        }
                    elif current_factor and line.strip().startswith('- matched_keywords:'):
                        kw = line.split(':', 1)[1].strip()
                        factors[current_factor]["matched_keywords"] = [
                            k.strip() for k in kw.split(',') if k.strip()
                        ]
                    elif current_factor and line.strip().startswith('- add_dimensions:'):
                        dims = line.split(':', 1)[1].strip()
                        factors[current_factor]["add_dimensions"] = [
                            d.strip() for d in dims.split(',') if d.strip()
                        ]
                    elif current_factor and line.strip().startswith('- force_channel:'):
                        fc = line.split(':', 1)[1].strip()
                        factors[current_factor]["force_channel"] = fc if fc and fc != '-' else None
        except OSError as e:
            print(f"WARN: read factor_analysis.md failed: {e}", file=sys.stderr)
        if factors:
            data_complete = True

    if not factors:
        tf = state.get("triggered_factors", [])
        if tf and isinstance(tf, list):
            if all(isinstance(f, str) for f in tf):
                for fk in tf:
                    factors[fk] = {"matched_keywords": [], "add_dimensions": [], "force_channel": None}
            elif all(isinstance(f, dict) for f in tf):
                for f in tf:
                    fk = f.get("factor_key", f.get("key", ""))
                    if fk:
                        factors[fk] = {
                            "matched_keywords": f.get("matched_keywords", []),
                            "add_dimensions": f.get("add_dimensions", []),
                            "force_channel": f.get("force_channel"),
                        }
            data_complete = False

    if not factors:
        print(json.dumps({
            "persisted": False, "factors_count": 0, "reason": "no_factor_data"
        }, indent=2, ensure_ascii=False))
        return

    task_slug = state.get("task_slug", "unknown")
    domain = state.get("domain", "general")
    project = os.path.basename(PROJECT_ROOT)
    now = _timestamp()
    factor_keys = list(factors.keys())

    db_ok = True
    try:
        import sqlite3
        conn = sqlite3.connect(CYCLE_DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS factor_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id INTEGER,
                factor_key TEXT NOT NULL,
                project TEXT,
                domain TEXT,
                task_slug TEXT,
                completed_at TEXT,
                matched_keywords TEXT,
                add_dimensions TEXT,
                force_channel TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_findings_key "
                     "ON factor_findings(factor_key, domain)")
        for fk, fv in factors.items():
            conn.execute(
                "INSERT INTO factor_findings "
                "(factor_key, project, domain, task_slug, completed_at, "
                "matched_keywords, add_dimensions, force_channel) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (fk, project, domain, task_slug, now,
                 json.dumps(fv.get("matched_keywords", [])),
                 json.dumps(fv.get("add_dimensions", [])),
                 fv.get("force_channel"))
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"WARN: DB write failed: {e}", file=sys.stderr)
        db_ok = False

    knowledge_root = _get_knowledge_root()
    factors_dir = os.path.join(knowledge_root, "factors")
    os.makedirs(factors_dir, exist_ok=True)
    factor_log_path = os.path.join(factors_dir, "factor_cycle_log.json")

    existing = []
    if os.path.exists(factor_log_path):
        try:
            with open(factor_log_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = []
    if not isinstance(existing, list):
        existing = []

    entry = {
        "cycle_info": {
            "task_slug": task_slug,
            "domain": domain,
            "completed_at": now,
            "data_complete": data_complete,
            "project": project,
        },
        "factors": [
            {
                "factor_key": fk,
                "matched_keywords": fv.get("matched_keywords", []),
                "add_dimensions": fv.get("add_dimensions", []),
                "force_channel": fv.get("force_channel"),
            }
            for fk, fv in factors.items()
        ]
    }
    existing.append(entry)
    with open(factor_log_path, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    output = {
        "persisted": True,
        "factors_count": len(factors),
        "factor_keys": factor_keys,
        "data_complete": data_complete,
        "db_written": db_ok,
        "json_path": factor_log_path,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


def cmd_knowledge_scan_promotion():
    """Query cycle-log DB for factors appearing >=3 times, generate promotion proposal.

    Scans factor_findings table grouped by factor_key + domain,
    produces factor_promotion_proposal.yaml in .fat/pdf/ when
    any factor reaches the promotion threshold.

    Usage: pdf-engine.py knowledge scan-promotion
    """
    promotion_rows = _query_cycle_db(
        "SELECT factor_key, domain, COUNT(*) as occurrence_count, "
        "GROUP_CONCAT(DISTINCT matched_keywords) as all_keywords, "
        "MIN(created_at) as first_seen, MAX(created_at) as latest_seen "
        "FROM factor_findings "
        "GROUP BY factor_key, domain "
        "HAVING COUNT(*) >= 3"
    )

    proposals = []
    for r in promotion_rows:
        samples = _query_cycle_db(
            "SELECT task_slug, domain, completed_at FROM factor_findings "
            "WHERE factor_key = ? AND domain = ? "
            "ORDER BY created_at DESC LIMIT 3",
            [r["factor_key"], r["domain"]]
        )
        proposal = {
            "factor": r["factor_key"],
            "domain": r["domain"],
            "occurrence_count": r["occurrence_count"],
            "first_seen": r.get("first_seen"),
            "latest_seen": r.get("latest_seen"),
            "sampled_cycles": [
                {"task_slug": s["task_slug"], "domain": s["domain"],
                 "completed_at": s["completed_at"]}
                for s in samples
            ],
        }
        proposals.append(proposal)

    total_rows = _query_cycle_db(
        "SELECT COUNT(DISTINCT task_slug) as cnt FROM factor_findings"
    )
    cycle_count = total_rows[0]["cnt"] if total_rows else 0

    ctx = PDFContext.get_default()
    pdf_dir = ctx.state_dir
    proposal_path = os.path.join(pdf_dir, "factor_promotion_proposal.yaml")
    os.makedirs(pdf_dir, exist_ok=True)

    lines = [
        "# Factor Promotion Proposal",
        f"# Generated: {_timestamp()}",
        "",
        f"cycle_count_query: {cycle_count}",
    ]
    if not proposals:
        lines.append("proposals: []")
        lines.append("message: \"No factor has reached the promotion threshold (>=3)\"")
    else:
        lines.append("proposals:")
        for p in proposals:
            lines.append(f"  - factor: {p['factor']}")
            lines.append(f"    domain: {p['domain']}")
            lines.append(f"    occurrence_count: {p['occurrence_count']}")
            lines.append(f"    first_seen: {p.get('first_seen') or 'unknown'}")
            lines.append(f"    latest_seen: {p.get('latest_seen') or 'unknown'}")
            lines.append("    sampled_cycles:")
            for s in p["sampled_cycles"]:
                lines.append(f"      - task_slug: {s['task_slug']}")
                lines.append(f"        domain: {s['domain']}")
                lines.append(f"        completed_at: {s['completed_at']}")

    with open(proposal_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    output = {
        "proposals_count": len(proposals),
        "threshold": 3,
        "cycle_count_query": cycle_count,
        "factors_eligible": [p["factor"] for p in proposals],
        "proposal_path": proposal_path,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
