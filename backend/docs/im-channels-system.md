# IM Channels System (`app/channels/`)

Bridges external messaging platforms (Feishu, Slack, Telegram, Discord, DingTalk, GitHub) to the DeerFlow agent via Gateway's LangGraph-compatible API.

## Architecture

Channels communicate with Gateway through the `langgraph-sdk` HTTP client (same as the frontend). The internal SDK client injects process-local internal auth plus a matching CSRF cookie/header pair so Gateway accepts state-changing thread/run requests from channel workers without relying on browser session cookies.

## Components

- `message_bus.py` — Async pub/sub hub (`InboundMessage` → queue → dispatcher; `OutboundMessage` → callbacks → channels)
- `store.py` — JSON-file persistence mapping `channel_name:chat_id[:topic_id]` → `thread_id`
- `manager.py` — Core dispatcher: creates threads, routes commands (`/goal`, `/new`, etc.), handles sync (`runs.wait()`) and streaming (`runs.stream()`) dispatch policies, fire-and-forget (`runs.create()`) for autonomous channels like GitHub
- `base.py` — Abstract `Channel` base class
- `service.py` — Lifecycle management for all configured channels
- `slack.py` / `feishu.py` / `telegram.py` / `discord.py` / `dingtalk.py` — Platform-specific implementations. Feishu patches running reply cards in place; Telegram edits a placeholder message; DingTalk optionally uses AI Card streaming; Slack/Discord use blocking `runs.wait()` and publish the final response
- `github.py` — Webhook-driven channel: inbound from `POST /api/webhooks/github`, outbound is log-only (agents post their own replies via `gh` CLI from sandbox)
- `app/gateway/routers/channel_connections.py` — Browser-facing user connection APIs
- `deerflow.persistence.channel_connections` — SQL-backed user-owned connection store

## Message Flow

1. External platform → Channel impl → `MessageBus.publish_inbound()`
2. `ChannelManager._dispatch_loop()` consumes from queue
3. For user-owned connections: `owner_user_id` becomes the DeerFlow run `user_id`; `channel_user_id` (raw platform id) goes into runtime context only
4. For chat: look up/create thread via LangGraph-compatible API
5. Stream/wait for run, accumulate text, publish outbound updates
6. GitHub: `runs.create()` (fire-and-forget); agent posts its own mid-run reply

## Owner-Scoped File Storage

Inbound files, uploads, and output artifacts are staged under the DeerFlow owner's bucket (`users/{owner_user_id}/threads/{thread_id}/user-data/{uploads,outputs}`), resolved once and cached for both blocking and streaming paths.

## Configuration (`config.yaml` → `channels`)

- `langgraph_url` — Gateway API base URL (default `http://localhost:8001/api`)
- `gateway_url` — Gateway URL for auxiliary commands (default `http://localhost:8001`)
- Per-channel configs: `feishu` (app_id, app_secret), `slack` (bot_token, app_token), `telegram` (bot_token), `dingtalk` (client_id, client_secret, optional `card_template_id`), `github` (enabled + default_mention_login)

## User-Owned Channel Connections

Disabled by default. A user-binding layer over existing `channels.*` runtime config. Uses `/connect <code>` or Telegram `/start <code>` flow. Single-active-owner transfer semantics enforced at DB layer. See [docs/IM_CHANNEL_CONNECTIONS.md](docs/IM_CHANNEL_CONNECTIONS.md).

## GitHub Event-Driven Agents

Custom agents declare a `github:` block in `config.yaml` to bind repos and event triggers. Webhook → fan-out → `InboundMessage` dispatch; `fire_and_forget=True` — agents post their own replies via `gh`. See [docs/GITHUB_AGENTS.md](docs/GITHUB_AGENTS.md).
