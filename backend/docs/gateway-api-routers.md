# Gateway API Routers

FastAPI application on port 8001. Full router reference.

| Router | Endpoints |
|--------|-----------|
| **Models** (`/api/models`) | `GET /` — list models; `GET /{name}` — model details |
| **Features** (`/api/features`) | `GET /` — report config-gated feature availability for frontend UI gating |
| **Console** (`/api/console`) | `GET /stats` — headline counters; `GET /runs` — paginated run history with cost; `GET /usage` — zero-filled daily token series + per-model spend. Requires SQL DB; returns 503 on `memory` backend. Cache-aware pricing via `models[*].pricing` |
| **MCP** (`/api/mcp`) | `GET /config` — get config; `PUT /config` — update config |
| **Skills** (`/api/skills`) | `GET /` — list; `GET /{name}` — details; `PUT /{name}` — update enabled; `POST /install` — install .skill archive; `POST /reload` — admin-only cache invalidation |
| **Memory** (`/api/memory`) | `GET /` — data; `POST /reload`; `GET /config`; `GET /status` |
| **Uploads** (`/api/threads/{id}/uploads`) | `POST /` — upload with auto-convert; `GET /list`; `DELETE /{filename}` |
| **Threads** (`/api/threads/{id}`) | `DELETE /` — remove thread data; `POST /branches` — branch from checkpoint; `GET/PUT/DELETE /goal`; `POST /compact` — manual summarization |
| **Artifacts** (`/api/threads/{id}/artifacts`) | `GET /{path}` — serve artifacts (active content forced as download) |
| **Suggestions** (`/api/suggestions`) | `GET /config`; `POST /threads/{id}/suggestions` — generate follow-ups |
| **Input Polish** (`/api/input-polish`) | `POST /` — rewrite composer draft (one-shot LLM, no graph run) |
| **Thread Runs** (`/api/threads/{id}/runs`) | `POST /` — background run; `POST /stream` — SSE; `POST /wait` — block; `POST /regenerate/prepare`; `GET /` — list; `GET /{rid}` — details; `POST /{rid}/cancel`; `GET /{rid}/join`; `GET /{rid}/messages`; `GET /{rid}/events`; `GET /{rid}/workspace-changes` |
| **Feedback** (`/api/threads/{id}/runs/{rid}/feedback`) | `PUT /` — upsert; `DELETE /` — delete user; `POST /` — create; `GET /` — list; `GET /stats`; `DELETE /{fid}` |
| **Runs** (`/api/runs`) | `POST /stream` — stateless SSE; `POST /wait` — stateless block; `GET /{rid}/messages`; `GET /{rid}/feedback` |
| **GitHub Webhooks** (`/api/webhooks/github`) | `POST /` — receive GitHub deliveries (HMAC-verified, fail-closed) |

## RunManager / RunStore Contract

- `RunManager.get()` is async; callers must `await` it.
- History helpers default to `user_id=AUTO` (fail-closed); pass `user_id=None` for unscoped reads.
- `cancel()` returns `CancelOutcome` enum: `cancelled`, `taken_over`, `lease_valid_elsewhere`, `not_active_locally`, `not_cancellable`, `unknown`.
- `POST /wait` drains stream bridge via `wait_for_run_completion()` (honours `on_disconnect`).
- Redis `StreamBridge` uses rolling TTL as leak safety net, not run timeout.
- Thread-scoped run creation accepts `checkpoint`/`checkpoint_id` for LangGraph branching.
- Goal evaluation: no-progress evaluator fires after visible turn completes; cap is 8 continuations; no-progress breaker stops after 2 consecutive no-new-evidence turns.

## Workspace Change Review

`packages/harness/deerflow/workspace_changes/` captures pre/post snapshots of thread-owned workspace and outputs directories. Text diffs are size-limited; binary/large paths are metadata-only.

## Nginx Proxy

`/api/langgraph/*` → Gateway LangGraph-compatible runtime, all other `/api/*` → Gateway REST APIs.
