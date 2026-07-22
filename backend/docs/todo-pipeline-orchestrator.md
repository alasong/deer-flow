# Pipeline Orchestrator — 跨 Lead 编排管线

设计记录：[007-pipeline-orchestrator.md](../.arr/arch/review/records/007-pipeline-orchestrator.md)

---

## 架构格局（决策总结）

经过分析，Lead × Goal × PDF 三者的关系如下：

```
                    用户需求
                       │
           ┌───────────┴───────────┐
           │                       │
       确定的                  不确定的
       (已知工作流)            (探索性任务)
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

### 三条原则（源自架构分析）

1. **PDF 和 Goal 保持独立，不做运行时耦合**
   - PDF 有完整的 HSM 状态机、6 种节点类型、5 个通道
   - Goal 有独立的 evaluator model、续跑循环、residency 退避
   - PDF 的 `goal_doer` 是借 Goal 模式的轻量子 agent spawn，不碰真正的 `GoalState`/`evaluate_goal_completion()`
   - 强制集成会让状态空间翻倍，无实际收益

2. **PDF 产出固化蓝图，Goal 产出经验沉淀**
   - PDF blueprint = 确定的执行路径
   - Goal 探索成功后 → 写为 blueprint.yaml → 下次变成确定路径
   - 中间产物通过文件契约（manifest）传递，不跨系统耦合

3. **Pipeline Orchestrator 是正交的新能力，不改变现有架构**
   - 在 Lead Agent 之上做 DAG 编排，通过多次 `run_agent()` 调用驱动
   - 每个 step 就是一个普通的 run_agent()，step 间通过 workspace manifest 传递状态
   - 不和 PDF 或 Goal 争夺控制权

---

## M0 — 数据模型 + PipelineConfig 解析（~250 行）

**目标：** 管线数据模型能创建、持久化、恢复。暂不执行任何 step。

- [ ] **`packages/harness/deerflow/pipeline/models.py`** (~100 行)
  - `PipelineRun` — id, definition_id, thread_id, status, current_step_index, created_at
  - `PipelineStepRun` — id, step_id, agent_name, status (pending/running/completed/failed/skipped), manifest_path
  - `PipelineStatus` enum: active / completed / failed / cancelled
- [ ] **`packages/harness/deerflow/pipeline/config.py`** (~100 行)
  - `PipelineDefinition` — id, description, steps[...]
  - `PipelineStepDef` — id, agent, depends_on, condition, input, output_contract, middleware_profile
  - `OutputContractEntry` — path, type, summary
  - 从 config.yaml 解析 `pipelines.definitions`
- [ ] **`packages/harness/deerflow/config/app_config.py`** (~30 行)
  - 注册 `pipelines` 配置段，支持 `enabled` / `definitions` / `middleware_profiles`
- [ ] **测试** (`tests/test_pipeline_models.py`, ~100 行)
  - PipelineRun 状态机合法转换
  - PipelineStepRun 初始化
  - config 解析（合法输入 + 非法输入 + 空定义）

---

## M1 — Orchestrator 核心：顺序执行 + manifest 传递（~400 行）

**目标：** 能按定义顺序依次执行多步，step 间通过 manifest 传递中间产物。

- [ ] **`packages/harness/deerflow/pipeline/orchestrator.py`** (~400 行)
  - `PipelineOrchestrator.create_run(definition_name, thread_id, user_input)` — 创建管线实例
  - `orchestrator.run(prun)` — 管线主循环：
    - 构建 `step_loop()` 按拓扑序遍历 step
    - 为每个 step 准备 `graph_input`（上一步 workspace + manifest 摘要）
    - 构建 `RunnableConfig`（agent_name 来自 step 定义）
    - 调用 `run_agent(bridge, run_manager, record, graph_input=..., config=...)`
    - 等待 run 完成后收集 workspace 输出
    - 写入 `.pipeline-manifest.json`
  - 错误处理：step failed → 管线标记 failed，可配置 fallback
  - 中断恢复：`resume_run(prun_id)` 从最后未完成的 step 重试
- [ ] **`packages/harness/deerflow/pipeline/manifest.py`** (~150 行)
  - `write_manifest(step_run, outputs, decisions)` → 写入 `workspace/.pipeline-manifest.json`
  - `read_manifest(step_run)` → 读取解析
  - `collect_outputs(workspace_path, output_contract)` → 校验 step 产生了约定的文件
  - `Manifest` 数据模型（含 outputs, decisions, next_steps_suggestion, token_used）
- [ ] **测试** (`tests/test_pipeline_orchestrator.py`, ~200 行)
  - 两 step 顺序执行
  - step 产出校验（contract 匹配/不匹配）
  - step failed → 管线 fail
  - `resume_run` 从中断处恢复

---

## M2 — 条件门禁 + 条件依赖（~150 行）

**目标：** step 之间的 `depends_on` 支持条件跳转，`condition` 可基于上一步 manifest 决策。

- [ ] **`packages/harness/deerflow/pipeline/conditions.py`** (~100 行)
  - `ConditionEvaluator.evaluate(condition, manifest) → bool`
  - 支持的 condition 语法：
    - `"review.has_issues == true"` → `manifest["outputs"]["review"]["has_issues"]`
    - `"code.coverage > 80"` → `manifest["outputs"]["code"]["coverage"]`
    - `"always"` → 无条件跳过
    - `"never"` → 禁用（用于调试）
  - 循环检测：condition 不能引用自身的 manifest
- [ ] **Orchestrator 增强** (~50 行)
  - `resolve_dependencies(step, manifests)` → 返回可用的 input manifest 列表
  - `depends_on` 支持或逻辑：`[code, fix]` 表示任一完成即可
- [ ] **测试** (`tests/test_pipeline_conditions.py`, ~50 行)
  - condition 求值（true/false/error）
  - 多依赖解析（and/or）
  - 循环依赖拒绝

---

## M3 — Gateway API（~150 行）

**目标：** 用户可以通过 API 启动和查看管线进度。

- [ ] **`app/gateway/routers/pipelines.py`** (~100 行)
  - `POST /api/pipelines/{name}/run` — 启动管线，返回 `PipelineRun`
  - `GET /api/pipelines/{id}` — 查询管线状态 + 各 step 状态
  - `GET /api/pipelines` — 列出管线定义
  - `POST /api/pipelines/{id}/cancel` — 取消运行中的管线
- [ ] **SSE 事件** (~50 行)
  - 管线 step 变更发布 `custom` 事件 `pipeline_step_changed`（step_id, status）
- [ ] **测试** (`tests/test_pipeline_api.py`, ~100 行)
  - API 创建、查询、取消管线
  - SSE 事件验证

---

## M4 — 去掉（经架构分析后否决）

原始计划包含 M4（中间件链按角色裁剪 + workspace 沙箱隔离），经分析后否决：

- **中间件裁剪是过早优化** — 管线场景需运行时数据才有意义，不应预先设计裁剪策略
- **Profile 系统增加配置复杂度** — 每个 step 的 middleware_profile 的维护成本大于收益
- **Workspace 子目录隔离** — 如需要，可在 M1 中简单实现（`workspace/pipeline/{step_id}/`），不需要单独里程碑

**当需要时再做**：如果管线 step 数量 > 10 或有可测量的 token/延迟瓶颈。

---

## 优先级总结

| 版本 | 里程碑 | 预计行数 | 建议时间 |
|------|--------|---------|---------|
| v0.1 | M0 — 数据模型 | ~250 | 1 天 |
| v0.2 | M1 — 顺序执行管线 | ~550 | 2-3 天 |
| v0.3 | M2 — 条件门禁 | ~150 | 1 天 |
| v0.4 | M3 — Gateway API | ~150 | 1 天 |
| **合计** | | **~1100** | **5-7 天** |

## 非目标（明确不做的）

- ❌ Goal × PDF 运行时集成 — 各自独立，通过文件契约传递产物
- ❌ 并行 step 执行 — v1 只做串行，并行放后续迭代
- ❌ 中间件 Profile 裁剪 — 等有数据再说
- ❌ 跨进程编排 — v1 只支持同一个 Gateway 进程内的管线
- ❌ 管线回滚 — step failed 只终止，不擦除已产生数据
- ❌ 定时触发管线 — v1 只支持 API 触发
