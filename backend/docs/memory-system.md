# Memory System (`packages/harness/deerflow/agents/memory/`)

## Components

- `updater.py` — LLM-based memory updates with fact extraction, deduplication, and atomic file I/O
- `queue.py` — Debounced update queue (per-thread dedup, configurable wait time)
- `prompt.py` — Prompt templates for memory updates
- `storage.py` — File-based storage with per-user isolation
- `tools.py` — Tool-driven memory mode (`memory_search`, `memory_add`, `memory_update`, `memory_delete`)

## Per-User Isolation

Memory is stored per-user at `{base_dir}/users/{user_id}/memory.json`. Per-agent per-user memory at `{base_dir}/users/{user_id}/agents/{agent_name}/memory.json`. Custom agent definitions (`SOUL.md` + `config.yaml`) are also per-user. The legacy shared layout remains a read-only fallback.

`/api/memory*` endpoints resolve the owner through `_resolve_memory_user_id(request)`:
trusted internal callers (IM channel workers with `X-DeerFlow-Owner-User-Id`)
act for the connection owner; browser/API callers fall back to `get_effective_user_id()`.
In no-auth mode, defaults to `"default"`.

## Data Structure

- **User Context**: `workContext`, `personalContext`, `topOfMind` (1-3 sentence summaries)
- **History**: `recentMonths`, `earlierContext`, `longTermBackground`
- **Facts**: Discrete facts with `id`, `content`, `category`, `confidence` (0-1), `createdAt`, `source`

## Workflow

- **`memory.mode: middleware`** (default): `MemoryMiddleware` filters messages, queues conversation, debounced background thread invokes LLM to extract updates
- **`memory.mode: tool`**: Registers `memory_search`/`add`/`update`/`delete` tools on the agent. Model decides when to use them. Experimental.
- **Staleness pass** (same LLM call, no extra API): LLM judges aged facts as KEEP/REMOVE/EXTEND; `_apply_updates` enforces guardrails (protected categories, per-cycle caps)
- **Consolidation pass** (same LLM call): merges fragmented categories when threshold exceeded
- **Run-level memory identity**: each run with a hidden memory block records a `context:memory` event with `content_sha256` for cross-run comparison

## Configuration (`config.yaml` → `memory`)

- `enabled` / `injection_enabled` — Master switches
- `mode` — `middleware` (default) or `tool` (experimental)
- `storage_path` — absolute path opts out of per-user isolation
- `debounce_seconds` — 30s default
- `shutdown_flush_timeout_seconds` — 30s (1-300) host-shared budget for draining pending updates on Gateway shutdown
- `model_name` — LLM for updates (null = default)
- `max_facts` / `fact_confidence_threshold` — 100 / 0.7
- `max_injection_tokens` — 2000
- `token_counting` — `tiktoken` (default) or `char` (CJK-aware char estimate, network-free)
- Staleness: `staleness_review_enabled` (default true), `age_days` (90), `min_candidates` (3), `max_removals_per_cycle` (10), `protected_categories` (`["correction"]`), `max_lifetime_multiplier` (20.0), `max_extension_days` (3650)
- Consolidation: `consolidation_enabled` (default true), `min_facts` (8), `max_groups_per_cycle` (3), `max_sources` (8)
