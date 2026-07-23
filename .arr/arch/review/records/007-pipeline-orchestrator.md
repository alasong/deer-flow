---
title: "Pipeline Orchestrator — 跨 Lead 开发管线"
status: "accepted"
date: "2026-07-22"
context: "Lead Agent 是单体架构，一个 agent 在一个 thread 内完成所有工作。多 lead 组成开发管线（设计→编码→审查→测试）需要跨 agent 状态传递、拓扑编排、持久化协调。"
rationale: "不在 Lead 内部加管线逻辑（已经太复杂，33个中间件/700行factory），而是加一个正交的编排层在 Lead 之上，通过多次 run_agent() 调用来实现管线"
---

> **PDF → RPD 注解 (2026-07-23)：** 本文中的 PDF 引用（D002、D003、架构格局图、PDF 管线）已过时。PDF 引擎已被 RPD（Recursive PDCA，见 `skills/public/rpd/`）取代：
> - D002 "Goal × PDF 不做运行时集成" → 等价于 Goal × RPD 不做运行时集成
> - D003 "PDF = 确定性蓝图" → 已演进为 RPD = 递归树 + 灵活映射
> - "架构格局"中的 PDF 蓝图管线 → 由 RPD 替代
> - Pipeline Orchestrator 本身不受影响，它编排的是 Lead Agent 而非具体 skill
decisions:
  - id: "D001"
    title: "Pipeline Orchestrator 独立于 Goal 和 PDF"
    status: "accepted"
    context: "Lead Agent 之上需要跨 Agent 编排能力"
    decision: "Pipeline Orchestrator 作为正交编排层，不和 Goal 系统或 PDF skill 争夺控制权。每个 step 就是一次普通的 run_agent() 调用，step 间通过 workspace manifest 文件传递状态。"
  - id: "D002"
    title: "Goal × PDF 不做运行时集成"
    status: "rejected"
    context: "曾考虑将 Goal 系统、Lead Agent、PDF skill 做三层集成（Goal → Lead → PDF）"
    decision: "否决。PDF 有完整的 HSM/6节点/5通道，Goal 有独立的 evaluator/续跑/residency，两者各有完整生命周期。PDF 的 goal_doer 已经是轻量借壳，不需要真正 Goal 系统的介入。集成后状态空间翻倍且无仲裁规则。"
  - id: "D003"
    title: "PDF = 确定性蓝图，Goal = 不确定性探索"
    status: "accepted"
    context: "两者独立但互补"
    decision: "已知工作流走 PDF 蓝图管线，探索性任务走 Goal 延续循环。Goal 探索成功后沉淀为 PDF blueprint。两者通过文件契约（manifest）传递产物，不做运行时耦合。"
  - id: "D004"
    title: "中间件 Profile 裁剪去掉"
    status: "rejected"
    context: "原计划 M4 做 step 级中间件裁剪，M1 设计含 middleware_profile 字段"
    decision: "否决。过早优化，无运行时数据支撑。即使需要，也可在 M1 后简单追加，不设独立里程碑。"
---

## 设计

### 核心概念

```
PipelineOrchestrator（编排层，在 Lead 之上）
       │
       │  读 config.pipelines → DAG 定义
       │
       ├── Step 1: Lead-A（设计）   → output_manifest.json
       ├── Step 2: Lead-B（编码）   → output_manifest.json
       ├── Step 3: Lead-C（审查）   → output_manifest.json
       └── Step 4: Lead-D（修复）   → output_manifest.json (if review failed)
              │
              └── 每个 step 就是一个普通的 run_agent() 调用
```

### 管线拓扑定义

```yaml
# config.yaml 新增 pipelines 节
pipelines:
  enabled: true

  # 定义可编排的管线模板
  definitions:
    dev-flow:
      description: "设计→编码→审查→测试 完整开发管线"
      steps:
        - id: design
          agent: lead-design          # 指向 agent_name
          input: null                 # 初始输入来自用户
          output_contract:            # 期望产出
            - path: workspace/design/arch.md
            - path: workspace/design/specs.json

        - id: code
          agent: lead-code
          depends_on: [design]        # 隐式依赖（串行）
          input: workspace/design/    # 读取上一步 workspace
          output_contract:
            - path: workspace/code/src/

        - id: review
          agent: lead-review
          depends_on: [code]
          input: workspace/code/
          output_contract:
            - path: workspace/review/findings.json

        - id: fix
          agent: lead-code            # 复用 code agent
          depends_on: [review]
          condition: "review.has_issues == true"
          input: workspace/review/
          output_contract:
            - path: workspace/code/src/

        - id: test
          agent: lead-test
          depends_on: [code, fix]     # 或依赖，任一个完成即可
          condition: "always"
          input: workspace/code/
          output_contract:
            - path: workspace/test/results.json

      # 管线级别的输出契约
      outputs:
        - path: workspace/src/
        - path: workspace/test-results/
```

### 架构格局

```
                    用户需求
                       │
           ┌───────────┴───────────┐
           │                       │
       确定的                  不确定的
       (已知工作流)           (探索性任务)
           │                       │
           ▼                       ▼
    PDF 蓝图管线           Goal 延续循环
    (blueprint.yaml)      (evaluator + continuation)
           │                       │
           │                 总结沉淀为
           │                 新 blueprint
           │                       │
           └───────────┬───────────┘
                       │
                       ▼
               Pipeline Orchestrator
           (跨 Lead Agent DAG 编排)
```

### Workspace Manifest 契约

管线 step 之间通过结构化的 manifest 传递信息，不是靠纯文本 prompt：

```json
// workspace/.pipeline-manifest.json（每个 step 完成后自动写入）
{
  "pipeline_id": "pl_abc123",
  "step_id": "design",
  "agent": "lead-design",
  "status": "completed",
  "outputs": [
    {"path": "design/arch.md", "type": "document", "summary": "系统架构设计 v2"},
    {"path": "design/specs.json", "type": "spec", "summary": "API 规格定义"}
  ],
  "decisions": [
    {"type": "arch:database", "summary": "选择 PostgreSQL 作为主存储"},
    {"type": "arch:language", "summary": "使用 Python 3.12+"}
  ],
  "next_steps_suggestion": ["code", "review"],
  "token_used": 15000,
  "completed_at": "2026-07-22T10:00:00Z"
}
```

### 编排架构

```
PipelineOrchestrator
  │
  ├── PipelineConfig        — 读 config.yaml 的 pipelines 节
  ├── PipelineRun           — 一次管线执行实例（持久化到 DB）
  │     ├── id, definition_id, status
  │     ├── steps: [PipelineStepRun]
  │     └── current_step_index
  │
  ├── PipelineStepRun       — 单个 step 执行
  │     ├── id, step_id, agent_name
  │     ├── status: pending → running → completed | failed | skipped
  │     ├── condition_result: bool | None
  │     └── manifest_path: str | None
  │
  ├── ManifestManager       — 读写 workspace/.pipeline-manifest.json
  │     ├── write_manifest(step, outputs) → path
  │     └── read_manifest(path) → dict
  │
  └── ConditionEvaluator    — 条件门禁求值
        └── evaluate(condition, manifest) → bool
```

### 与现有架构的关系

```
现有层                          新增层
─────────                      ────────
Gateway API                    POST /api/pipelines/{name}/run
  │                              │
  ▼                              ▼
run_agent() ← ← ← ← ←  PipelineOrchestrator.run()
  │                              │
  │                         ┌────┴────┐
  │                         │         │
  │                    step 1     step 2  ...
  │                    run_agent  run_agent
  │                         │         │
  ▼                         ▼         ▼
checkpointer + store     同一个 workspace（或同一个 thread 的不同 checkpoint）
```

### 线程模型选择

方案 A：**单 thread 连续 checkpoint**（推荐 v1）

```
Thread: "pl_abc123"
  ├── checkpoint_0:  初始状态
  ├── checkpoint_1:  step design 完成 ← run_agent(graph_input, config)
  ├── checkpoint_2:  step code 完成   ← run_agent(graph_input, config)
  └── checkpoint_3:  step review 完成 ← run_agent(graph_input, config)
```

- 所有 step 共享一个 thread 的 workspace
- LangGraph 的 checkpoint 天然记录每个 step 后的状态快照
- 用户可以在任意 checkpoint 处接管

方案 B：**每 step 独立 thread**（可后续支持）

```
Thread: "pl_abc123-step-design"  → checkpoint 设计输出
Thread: "pl_abc123-step-code"    → checkpoint 代码输出
```

- 更严格隔离，适合多团队协作
- 但跨 thread 传状态需要额外逻辑

v1 选方案 A。

### 执行流程

```
1. POST /api/pipelines/dev-flow/run
   → PipelineOrchestrator.create_run("dev-flow")
   → 创建 PipelineRun（status=active, step_index=0）

2. Orchestrator.step_loop(prun):
   for each step in topological order:
     step_run = resolve_dependencies(step, condition)
     if step_run is None:  # 条件不满足或依赖未满足
         step_run.status = "skipped"
         continue

     step_run.status = "running"
     
     # 准备 step 输入
     graph_input = build_step_input(step_run, workspace_manifest)
     config = build_step_config(step_run, agent_name)
     
     # 执行
     await run_agent(bridge, run_manager, record, ..., graph_input=graph_input, config=config)
     
     # 收集输出
     manifest = ManifestManager.collect_outputs(step_run)
     step_run.status = "completed"
     
     # 写入 manifest
     ManifestManager.write_manifest(step_run, manifest)
     
     # 广播进度
     bridge.publish_custom(step_run.id, "step_completed", manifest)

3. 全部 step 完成 → PipelineRun.status = "completed"
   有 step failed 且无 fallback → PipelineRun.status = "failed"
```

### 改动文件

| 文件 | 改动 | 预计行数 |
|------|------|---------|
| `packages/harness/deerflow/pipeline/config.py` | PipelineConfig 模型 + 解析 | ~150 |
| `packages/harness/deerflow/pipeline/models.py` | PipelineRun / PipelineStepRun 数据模型 | ~100 |
| `packages/harness/deerflow/pipeline/orchestrator.py` | PipelineOrchestrator 核心编排逻辑 | ~400 |
| `packages/harness/deerflow/pipeline/manifest.py` | ManifestManager 读写 + 校验 | ~150 |
| `packages/harness/deerflow/pipeline/conditions.py` | ConditionEvaluator 条件门禁引擎 | ~100 |
| `packages/harness/deerflow/config/app_config.py` | 注册 pipelines 配置段 | ~30 |
| `app/gateway/routers/pipelines.py` | POST /api/pipelines/{name}/run + GET /api/pipelines/{id} | ~100 |
| `tests/` | 单元测试 | ~300 |
| Total | | ~1330 |

### 否决项

以下方案经分析后否决，保留为设计上下文：

1. **Goal × PDF 三层集成**（D002）
   - PDF 有完整 HSM/6 节点类型/5 通道
   - Goal 有独立 evaluator/续跑/residency 退避
   - PDF 的 goal_doer 已是轻量借壳（spawn subagent + LLM 自行判断完成）
   - 强制集成无实际收益，状态空间翻倍

2. **中间件 Profile 裁剪**（D004）
   - 过早优化，无运行时数据支撑
   - 如需可在 M1 后追加，不设独立里程碑

### 安全/边界考虑

1. **Step 隔离** — run_agent 的 graph_input 只包含上一步 manifest 中的 outputs 路径，不传递完整状态，防止 step 越权
2. **令牌预算** — 每个 step 有独立的 token_budget，防止单步耗尽整个管线预算
3. **超时** — 每个 step 独立超时（默认 10min），不因一步卡死阻塞后续 step 发现
4. **重入** — 如果 pipeline 被终止后重新启动，orchestrator 从最后完成的 step 开始恢复（通过 DB 持久化的 PipelineRun）
5. **拒绝循环** — condition 中不允许 self-referencing dependency

### 优先级

| 版本 | 里程碑 | 预计行数 | 建议时间 |
|------|--------|---------|---------|
| v0.1 | M0 — 数据模型 | ~250 | 1 天 |
| v0.2 | M1 — 顺序执行管线 | ~550 | 2-3 天 |
| v0.3 | M2 — 条件门禁 | ~150 | 1 天 |
| v0.4 | M3 — Gateway API | ~150 | 1 天 |
| **合计** | | **~1100** | **5-7 天** |
