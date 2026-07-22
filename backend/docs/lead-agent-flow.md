# Lead Agent 流程

Lead Agent 是 DeerFlow 唯一的 LangGraph Agent 运行时入口点。本文档覆盖已有
[middleware-chain.md](middleware-chain.md) 和
[middleware-execution-flow.md](middleware-execution-flow.md) 未涉及的端到端执行流程：
请求入口、Agent 构建、工具组装、运行时流循环以及技能集成。

---

## 1. 请求入口

```
外部客户端（Web UI / IM / CLI / SDK）
        │
        ▼
  Nginx（端口 2026）
        │  重写 /api/langgraph/* → /api/*
        ▼
  Gateway FastAPI（端口 8001）
        │  路由定义在 app/gateway/routers/
        │
        ├── /api/threads/{thread_id}/runs/stream  →  run_agent()
        ├── /api/runs/{run_id}/stream              →  run_agent()
        ├── /api/runs/{run_id}/wait                →  run_agent()
        └── /api/runs                              →  run_agent()
                │
                ▼
        RunManager.create_run()
                │  - 校验 thread 是否存在
                │  - 创建 RunRecord（run_id, thread_id, status, assistant_id）
                │  - 生成 run_id、model_name、stream_modes
                │  - 返回 RunRecord 给 worker
                │
                ▼
        run_agent(bridge, run_manager, record, ...)
```

`run_agent()` 位于 `runtime/runs/worker.py`，是唯一的异步入口点。它驱动单次 turn 的完整 Agent 生命周期：

1. 等待同一 thread 上之前的 finalizing 运行完成
2. 标记 run 状态为 `running`
3. 对 pre-run 状态做 checkpoint 快照（用于回滚）
4. 发布元数据（run_id + thread_id）到 SSE bridge
5. 通过 `agent_factory(config)` → `make_lead_agent()` 构建 Agent
6. 打开 LangGraph `astream` 循环并将事件转发到 bridge
7. 完成时：持久化 RunJournal，发布 `end` 事件
8. 出错时：发布 `error` 事件，标记 run 为 failed
9. 中止时：排空剩余 chunk，标记 run 为 interrupted

---

## 2. Agent 构建（`make_lead_agent`）

`agent_factory` 即 `make_lead_agent(config)`（在 `langgraph.json` 中注册）。
内部调用 `_make_lead_agent(config, app_config)`，执行七步顺序组装：

```
_make_lead_agent(config, app_config)
  │
  ├── 1. 解析运行时配置
  │     ├── model_name       （请求 → agent 配置 → 全局默认值）
  │     ├── thinking_enabled（model.supports_thinking）
  │     ├── is_plan_mode
  │     ├── subagent_enabled / max_concurrent_subagents / max_total_subagents
  │     ├── is_bootstrap     （严格受限的 Agent，用于 setup_agent 流程）
  │     ├── non_interactive  （定时任务路径：抑制 ask_clarification）
  │     └── agent_name       （→ load_agent_config 加载 SOUL.md / tool_groups / skills）
  │
  ├── 2. 注册到所有者 Agent 注册表（非 bootstrap）
  │     → register_agent(agent_name, capabilities)
  │     → 外部 Agent 可通过 GET /api/agents 发现此 Agent
  │
  ├── 3. 加载 Skills
  │     ├── available_skills = agent_config.skills（或 None = 全部启用）
  │     └── enabled_skills = _load_enabled_available_skills(
  │                            available_skills, app_config, user_id)
  │
  ├── 4. 组装工具
  │     ├── raw_tools = get_available_tools(model_name, groups, subagent_enabled)
  │     │                （sandbox + 内置 + MCP + 社区 + subagent）
  │     ├── + extra_tools  （update_agent 用于自定义 Agent，非 webhook）
  │     ├── - ask_clarification（当 non_interactive 时）
  │     ├── → assemble_deferred_tools(raw_tools) → final_tools + deferred_setup
  │     ├── → build_mcp_routing_middleware(final_tools, setup)
  │     ├── + describe_skill 工具（当 skills.deferred_discovery 时）
  │     └── + memory 工具（当 memory.mode == "tool" 时）
  │
  ├── 5. 构建中间件链
  │     ├── build_lead_runtime_middlewares(app_config)
  │     │     → 13 个中间件（共享基础：InputSanitization 到 ToolErrorHandling）
  │     └── build_middlewares(config, model_name, agent_name, ...)
  │           → +20 个 lead-only 中间件（DynamicContext 到 Clarification）
  │
  ├── 6. 构建系统提示
  │     ├── apply_prompt_template(subagent_enabled, available_skills, agent_name, ...)
  │     ├── 包含：SOUL.md、skill 索引/块、subagent 配置、memory、
  │     │          MCP 路由提示、延迟发现部分
  │     └── 返回单个 SystemMessage
  │
  └── 7. 创建 LangGraph Agent
        └── create_agent(
              model=create_chat_model(name, thinking_enabled),
              tools=final_tools,
              middleware=build_middlewares(...),
              system_prompt=apply_prompt_template(...),
              state_schema=ThreadState,
            )
```

### Bootstrap 与普通路径对比

| 方面 | Bootstrap | 普通（设置 agent_name） | 默认（无 agent_name） |
|--------|-----------|------------------------|-------------------------|
| Skills | 仅 `bootstrap` | `agent_config.skills` | 全部启用 |
| 额外工具 | `setup_agent` | `update_agent` | 无 |
| 系统提示 | 最小化 | 完整 + SOUL.md | 完整 |

---

## 3. 工具组装（`get_available_tools`）

```
get_available_tools(model_name, groups, subagent_enabled, app_config)
  │
  ├── 1. 配置定义的工具
  │     └── resolve_variable(path) → 工具实例
  │         （如果设置了 agent_config.tool_groups，按 groups 过滤）
  │
  ├── 2. MCP 工具
  │     └── get_cached_mcp_tools() → 惰性初始化 + 缓存
  │         （缓存由配置路径 + 内容签名变更失效）
  │
  ├── 3. 内置工具
  │     ├── present_files       ↔  向用户暴露输出
  │     ├── ask_clarification   →  ClarificationMiddleware 拦截
  │     ├── view_image          ↔  仅当 model.supports_vision
  │     └── review_skill_package  ↔  skill 质量审查
  │
  ├── 4. 社区工具
  │     └── 搜索/抓取/爬取、图片搜索等
  │         （通过 config.yaml 接入，各工具在独立子包中）
  │
  └── 5. Subagent 工具（如果 subagent_enabled）
        └── task → SubagentExecutor → 后台线程池
```

组装后，`assemble_deferred_tools` 将每个工具包装到延迟目录中。
高基数工具（来自多个 MCP 服务器的工具）从模型的 schema 中隐藏，直到
`tool_search` 将其提升，从而保持 prompt 简洁和每次调用的 token 预算可预测。

---

## 4. 运行时循环（`run_agent`）

```
run_agent(bridge, run_manager, record, ...)
  │
  ├── 1. 等待同一 thread 上的 prior finalizing 运行
  │     └── run_manager.wait_for_prior_finalizing(thread_id, run_id)
  │
  ├── 2. 标记运行中 + 快照 pre-run 状态
  │     ├── run_manager.set_status(run_id, running)
  │     ├── 捕获 workspace 快照
  │     ├── 捕获 checkpointer 快照（用于回滚）
  │     └── 收集已有的消息 id
  │
  ├── 3. 发布元数据
  │     └── bridge.publish(run_id, "metadata", {run_id, thread_id})
  │
  ├── 4. 构建 Agent
  │     ├── _build_runtime_context() → 包含 thread_id, run_id, app_config 的字典
  │     ├── 通过 ContextVar 安装运行时上下文
  │     ├── 注入追踪回调（Langfuse/LangSmith 在图根级）
  │     ├── 注入 RunJournal 回调（token 用量追踪）
  │     └── agent = agent_factory(config) → make_lead_agent
  │
  ├── 5. 附加 checkpointer + store
  │     ├── agent.checkpointer = checkpointer
  │     └── agent.store = store
  │
  ├── 6. 流循环
  │     │
  │     │  agent.astream(input_payload, config, stream_mode=[values, messages])
  │     │
  │     │  LangGraph Agent 内部循环：
  │     │  ┌────────────────────────────────────────────────────┐
  │     │  │  before_model 链（33 个中间件，顺序 1→33）        │
  │     │  │         │                                          │
  │     │  │         ▼                                          │
  │     │  │  LLM（thinking → tool_calls 或 text）               │
  │     │  │         │                                          │
  │     │  │         ├── text → after_model（逆序 33→1）        │
  │     │  │         │         → bridge.publish("messages", ...) │
  │     │  │         │                                          │
  │     │  │         └── tool_calls → after_model（部分）       │
  │     │  │                       → 执行工具                   │
  │     │  │                       → 注入结果为 ToolMessage      │
  │     │  │                       → 回到 before_model           │
  │     │  └────────────────────────────────────────────────────┘
  │     │
  │     │  每个流 chunk 被序列化并转发到 SSE bridge，
  │     │  用于 HTTP 响应流。
  │     │
  │     ├── 每个 chunk 后：检查 record.abort_event
  │     ├── 跟踪 llm_error_fallback_message（保留用于错误报告）
  │     └── 缓冲 subagent 事件 → 通过 _SubagentEventBuffer 批量持久化
  │
  ├── 7. 完成
  │     ├── 刷新 subagent 事件
  │     ├── 持久化 RunJournal（token 用量、审计事件）
  │     ├── 发布 "end" 事件（累计用量）
  │     ├── run_manager.set_status(run_id, completed)
  │     └── run_manager.finalize_run()（更新 threads_meta）
  │
  ├── 8. 错误路径
  │     ├── 发布 "error" 事件
  │     ├── run_manager.set_status(run_id, failed)
  │     └── run_manager.finalize_run()
  │
  └── 9. 中止路径
        ├── 排空剩余的流 chunk
        └── run_manager.set_status(run_id, interrupted)
```

### 延续运行（Continuation Runs）

当 thread 已有现存 checkpoint（上次对话）时，`run_agent`：

1. 构造 `_continuation_runnable_config()`，剥离 `checkpoint_id` 和 `checkpoint_map`，
   使 LangGraph 从最新 checkpoint 恢复
2. 设置 `input_payload` 为 `None`（没有新的用户消息——checkpoint 中的现有消息
   即是完整状态）
3. Checkpointer 加载持久化的 checkpoint，图从那里继续执行

---

## 5. 中间件链

中间件链是核心处理管道，分为两个阶段：

- **before_model** — 33 个中间件按 1→33 的顺序运行，每个都在 LLM 调用前
  修改消息或状态
- **after_model** — 同样的 33 个中间件按 33→1 的逆序运行，每个处理 LLM 的响应

完整的详细描述见：
- [middleware-chain.md](middleware-chain.md) — 英文，每个中间件的详细说明
- [middleware-execution-flow.md](middleware-execution-flow.md) — 中文，Mermaid 执行图

中间件速查表（来自 `backend/AGENTS.md`）：

| # | 中间件 | 阶段 | 可选 |
|---|--------|------|------|
| 1 | InputSanitization | before/after | |
| 2 | ToolOutputBudget | wrap_tool | |
| 3 | ToolResultSanitization | after | |
| 4 | ThreadData | before_agent | |
| 5 | Uploads | before_agent | |
| 6 | Sandbox | before_agent | |
| 7 | DanglingToolCall | wrap_model | |
| 8 | LLMErrorHandling | after | |
| 9 | Guardrail | wrap_tool | 配置 |
| 10 | SandboxAudit | before_tool | |
| 11 | ReadBeforeWrite | wrap_tool | 配置 |
| 12 | ToolProgress | wrap_tool | 配置 |
| 13 | ToolErrorHandling | wrap_tool | |
| 14 | DynamicContext | before_model | |
| 15 | SkillActivation | before_model | |
| 16 | SkillToolPolicy | before_model | |
| 17 | DurableContext | before_model | |
| 18 | Summarization | before_model | 配置 |
| 19 | TodoList | before/after | plan_mode |
| 20 | TokenUsage | after | 配置 |
| 21 | Title | after | |
| 22 | Memory | after_agent | |
| 23 | ViewImage | before_model | 视觉模型 |
| 24 | McpRouting | before_model | tool_search |
| 25 | DeferredToolFilter | before_model | 延迟工具 |
| 26 | SystemMessageCoalescing | before_model | |
| 27 | SubagentLimit | after | subagent |
| 28 | LoopDetection | before/after | 配置 |
| 29 | TokenBudget | before_model | 配置 |
| 30 | Custom | 任意 | 自定义 |
| 31 | TerminalResponse | after_model | |
| 32 | SafetyFinishReason | after | 配置 |
| 33 | Clarification | wrap_tool | — 最后一个 |

### Skill 相关中间件协作

三个中间件协作实现技能激活和工具范围限定。
它们的顺序具有决定性作用：

```
SkillActivationMiddleware  (#15)
    ↓  将 slash source token 写入 runtime.secret_context
    ↓  将 SKILL.md 主体作为隐藏上下文注入
    │
SkillToolPolicyMiddleware  (#16)
    ↓  读取 slash source token（经过验证）
    ↓  按激活的 skill 过滤模型可见的 schema
    ↓  阻止未授权的工具执行
    │
DurableContextMiddleware   (#17)
    ↓  在摘要化之前捕获 skill_context 条目
    ↓  投影持久化上下文（authority + skill 引用）
```

**Slash 激活**（`/skill-name task`）：#15 检测最新用户消息中的 slash，
加载 SKILL.md，将其注入为当轮上下文，并将规范容器路径记录为 slash source。
#16 通过 token 认证读取该 source，并应用 skill 的 `allowed-tools` 策略。
#17 在 #18（摘要化）可能压缩本轮消息之前捕获 skill 引用。

**自主调用**：无 slash 时，#16 应用 `ThreadState.skill_context` 中所有 skill
的并集（由 #17 在上轮对话中从配置的 `read_file` 加载捕获）。
#17 将 `summary_text` + skill 引用投影到下一个模型请求中作为受保护的隐藏上下文。

---

## 6. 技能集成

技能不会预加载到 Agent 中。它们在运行时通过两条路径之一发现和激活：

### 延迟发现（`skills.deferred_discovery: true`）

```
系统提示 ── 紧凑的 <skill_index> 块（仅名称）
    │
LLM 调用 describe_skill("skill-name") ── 获取完整元数据
    │
LLM 调用 read_file("/mnt/skills/public/skill-name/SKILL.md") ── 加载正文
    │
DurableContextMiddleware 捕获 skill_context 条目
    │
下一次模型调用：SkillToolPolicyMiddleware 应用 allowed-tools
```

### 传统注入（`skills.deferred_discovery: false`）

```
系统提示 ── 完整的 <available_skills> 块（名称 + 描述 + 路径）
    │
LLM 通过 read_file 读取 SKILL.md
    │
DurableContextMiddleware 捕获 skill_context 条目
    │
下一次模型调用：SkillToolPolicyMiddleware 应用 allowed-tools
```

### PDF 技能（示例）

PDF 技能（`skills/public/pdf/`）是一个非 Agent 技能，实现自己的 PDCA 管道。
激活时：

1. SkillActivationMiddleware 注入 `SKILL.md`
2. LLM 调用 `bash` 运行 `pdf-engine.py` 命令
3. 引擎的 `pipeline_tick → node_start → node_complete` 循环在沙箱内运行，
   而非在 LangGraph Agent 循环内
4. 结果作为 bash 输出 → ToolMessage → 下一次 LLM 调用返回

---

## 7. 响应流程

```
LLM 返回文本
    │
    ▼
after_model 链（逆序 33→1）
    │  例如 TitleMiddleware 生成 thread 标题
    │  例如 TokenUsageMiddleware 记录用量
    │  例如 SubagentLimitMiddleware 截断多余的 task 调用
    │
    ▼
StreamChunk 发布到 SSE bridge
    │
    ▼
Gateway 流式响应
    │
    ├── Web UI:  agent.astream → SSE → Next.js
    ├── IM:      ChannelManager → 平台 API
    ├── SDK:     LangGraph SDK → 客户端代码
    └── CLI/TUI: DeerFlowClient.stream → 控制台
```

响应是通过 SSE 流传输的，产生三种事件类型：

| 模式 | 事件 | 内容 |
|------|------|------|
| `values` | 完整状态快照 | messages, artifacts, todos, title |
| `messages` | 逐 chunk delta | 文本 delta 或完整 tool_call/tool_result |
| `custom` | Subagent 事件 | task_started, task_running, task_completed |

每个 chunk 通过 `serialize(chunk, mode)` 序列化并发布到 `StreamBridge`，
`StreamBridge` 将其写入 HTTP 响应。前端按消息 id 重组 delta。

---

## 8. 初始化序列（冷启动）

Gateway 进程启动时：

```
Gateway FastAPI lifespan startup
    │
    ├── 1. AppConfig.from_file() → 加载 config.yaml + 校验
    ├── 2. init_engine() → 数据库连接 + alembic upgrade
    ├── 3. Checkpointer 初始化（异步）
    ├── 4. RunStore 初始化（异步）
    ├── 5. SandboxProvider 初始化（异步）
    ├── 6. Skill 存储初始化（同步：从磁盘加载 skills）
    ├── 7. Channel 服务启动（异步，连接 IM 平台）
    │     └── ChannelManager._dispatch_loop()（后台任务）
    ├── 8. 后台启用 skills 刷新线程启动
    └── 9. 开始接受请求
```

Agent 本身不在启动时构建。每次 `run_agent()` 调用都通过 `make_lead_agent`
使用当前配置构建一个全新的 Agent，因此配置编辑（基础设施字段除外）
在下一次运行时生效，无需重启。

---

## 文件位置

| 组件 | 路径 |
|------|------|
| Agent 工厂 | `packages/harness/deerflow/agents/lead_agent/agent.py` |
| 系统提示 | `packages/harness/deerflow/agents/lead_agent/prompt.py` |
| 中间件链 | `packages/harness/deerflow/agents/middlewares/` |
| 运行时入口 | `packages/harness/deerflow/runtime/runs/worker.py` |
| Run 管理器 | `packages/harness/deerflow/runtime/runs/manager.py` |
| 工具组装 | `packages/harness/deerflow/tools/tools.py` |
| MCP 工具 | `packages/harness/deerflow/mcp/tools.py` |
| Skills 系统 | `packages/harness/deerflow/skills/` |
| ThreadState schema | `packages/harness/deerflow/agents/thread_state.py` |
| Gateway 路由 | `app/gateway/routers/` |
