# PDF RAG Architecture

> Status: v4.1 — infrastructure stubs ready
> Next: v4.2 — enable semantic search

## Overview

PDF RAG is a **local-only, zero-infrastructure semantic search layer** layered on top of `~/.fat/pdf/knowledge/`. It enables LLM to search across all tech-stack knowledge, project KBs, cycle-logs, and past ADRs using natural language queries — without knowing which file the answer lives in.

## Architecture

```
User Query: "async endpoint best practices"
                    │
                    ▼
        ┌─────────────────────┐
        │  knowledge search   │  ← pdf-engine.py command
        │  "async endpoint.." │
        └────────┬────────────┘
                 │
        ┌────────▼────────────┐
        │  SentenceTransformer │  ← all-MiniLM-L6-v2 (384d, CPU)
        │  embed(query) → q    │
        └────────┬────────────┘
                 │
        ┌────────▼────────────┐
        │  FAISS Index         │  ← ~/.fat/pdf/knowledge/.rag-index/index.faiss
        │  index.search(q, k)  │
        └────────┬────────────┘
                 │
        ┌────────▼────────────┐
        │  Chunk Resolver     │  ← ~/.fat/pdf/knowledge/.rag-index/chunks.json
        │  chunk_id → content │      maps index positions to source+text
        └────────┬────────────┘
                 │
        ┌────────▼────────────┐
        │  Top-K Result       │
        │  [{source, text,    │
        │    score, file}]     │
        └─────────────────────┘
```

## Index Structure

```
~/.fat/pdf/knowledge/.rag-index/
  ├── index.faiss         # FAISS flat index (float32, inner product)
  ├── chunks.json         # Chunk metadata list
  │   └── [{"id": 0, "source": "tech-stack/fastapi.md",
  │          "section": "Conventions", "text": "...",
  │          "file": "fastapi.md"}]
  └── version.txt         # Index schema version
```

## Chunking Strategy

| Source | Chunk Strategy | Max Chunk Size |
|--------|---------------|---------------|
| tech-stack/*.md | By `##` section header | ~200 lines |
| L3 KB (JSON) | By top-level key | ~50 lines |
| cycle-log | By cycle entry | ~30 lines |
| ADR | By decision record | ~20 lines |

每个 chunk 携带 metadata：source 文件、section 标题、技术栈名（如适用）。用于结果展示和溯源。

## Embedding Model

- **Model**: `all-MiniLM-L6-v2`
- **Dimensions**: 384
- **Size**: ~80MB
- **Speed**: <100ms per query (CPU)
- **Why this model**: Smallest sentence-transformer with good enough retrieval quality. No GPU needed. FAISS flat L2 index is exact (not approximate).

## Indexing

### 首次构建

```bash
pip install sentence-transformers faiss-cpu
pdf-engine.py knowledge reindex   # Build index from all sources
```

### 增量更新

Act 4.5 append-ts 之后自动增量更新索引：

```python
# In append-ts:
#   1. Append to file (existing logic)
#   2. Embed the new chunk
#   3. faiss.IndexIDMap.add_with_ids(new_embedding, new_id)
#   4. Append to chunks.json
```

### 全量重建

```bash
pdf-engine.py knowledge reindex --force
# Delete and rebuild from scratch
```

### 源过滤

```bash
# 只重建 tech-stack 索引
pdf-engine.py knowledge reindex --source tech-stack

# 重建所有
pdf-engine.py knowledge reindex --source all
```

## Query

```bash
# 默认搜索所有源
pdf-engine.py knowledge search "how to handle errors in async endpoints" --top-k 5

# 限制源
pdf-engine.py knowledge search "React state management" --source tech-stack

# 输出格式 (JSON)
# {"results": [
#   {"text": "...", "source": "tech-stack/react.md",
#    "section": "Conventions", "score": 0.89},
# ]}
```

## P0.45 Integration (v4.2)

v4.2 中 P0.45 升级为**混合检索**：

```
P0.45（v4.2 — 混合检索）:

  1. 检测技术栈（与 v4 相同）
  2. python3 tools/pdf-engine.py knowledge reindex （如果索引不存在，自动触发）
  3. 对每个检测到的技术栈:
     python3 tools/pdf-engine.py knowledge search "<tech> conventions"
     python3 tools/pdf-engine.py knowledge search "<tech> anti-patterns"
  4. 无相关结果 → 降级到 get-ts（与 v4 相同）
  5. 有相关结果 → 注入 top-3 chunks 到 designer/doer/reviewer prompt
```

## Fallback Chain

```
knowledge search
    ├── FAISS index exists → semantic search → return top-k
    ├── FAISS index missing → warn + fallback to get-ts (tech-stack only)
    └── sentence-transformers not installed → 
        "pip install sentence-transformers faiss-cpu" message + fallback to get-ts
```

## v4.1 → v4.2 Upgrade

| # | Step | Impact |
|---|------|--------|
| 1 | `pip install sentence-transformers faiss-cpu` | ~80MB, user action |
| 2 | Fill `reindex` implementation | Engine change only |
| 3 | Run `pdf-engine.py knowledge reindex` | One-time build |
| 4 | Fill `search` implementation | Engine change only |
| 5 | Update P0.45 for `search` path | SKILL.md change |
| 6 | Update Act 4.5 for incremental index | SKILL.md + engine change |

Zero schema migration — chunks.json is the schema, and `reindex --force` rebuilds from scratch.
