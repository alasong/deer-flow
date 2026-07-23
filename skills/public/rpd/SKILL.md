---
name: rpd
description: Recursive PDCA — fractal tree PDCA with flexible phase mapping, parallel-by-default execution, and methodology guidance. Use this for complex, multi-step, or exploration-heavy tasks.
allowed-tools: [bash, read_file, write_file, web_search, web_fetch, task, rpd]
---

# /rpd — Recursive PDCA: Fractal Tree Execution

RPD 是 PDCA 的**递归自相似**实现。每个工作节点都是一个 PDCA 循环（PCCycle），如果节点太大就递归展开为子 PDCA 树。

**核心理念：PDCA 是思维框架，不是固定结构。**

## 对比 PDF

| 维度 | PDF | RPD |
|------|-----|------|
| 拓扑 | 固定蓝图 DAG | 递归树，LLM 运行时生长 |
| 阶段 | 严格的 Plan→Do→Check→Act | 灵活映射，"PDCA 作为思维" |
| 蓝图画法 | 必须提前写 blueprint.yaml | 无需，LLM 就是蓝图 |
| 适用场景 | 确定性、可审计流程 | 复杂、变粒度、探索性任务 |
| 引擎 | 复杂（DAG+HSM+约束+回滚） | 简单（树 CRUD+依赖解析+波次调度） |

---

## 执行策略

### 优先用 Subagent（调研/分析/编码类节点强制）

叶子节点用 `task` tool（subagent）还是直接执行，取决于节点"重量"：

| 节点特征 | 执行方式 | 原因 |
|----------|---------|------|
| **调研、分析、编码、多步工具调用** | **`task` 派发 subagent** | context 隔离，内部轮次不污染 lead |
| 同 wave 节点（无依赖，可并行） | `task` 并行派发，一次全发 | 默认 3 并发，同时推进 |
| 强依赖链中的中间节点 | 重节点→subagent，轻→advance | 链式无并行收益，但 isolation 仍有价值 |
| LLM 自带知识可完成、纯数据穿透 | `node-advance` 直接走 | subagent 冷启动开销 > 直接做 |

**注意**：调研类节点即使看起来"搜一下就行"，也应走 subagent。subagent 的 context 隔离价值 > 省 1 轮的开销。直觉上"1-2 步"的搜索在 subagent 内部仍然可以 advance，不增加额外轮次。

Subagent 适用示例：
```
# 调研类节点 → subagent，同 wave 可并行
rpd node-start NODE_ID_1
task prompt="采集杭州过去5年房价数据，分析走势" subagent_type="general-purpose"
rpd node-done NODE_ID_1

rpd node-start NODE_ID_2  
task prompt="调研杭州就业市场与经济形势" subagent_type="general-purpose"
rpd node-done NODE_ID_2

# 同一波次可批量并行：
rpd node-start NODE_ID_3
task prompt="调研 LangGraph" subagent_type="general-purpose"
rpd node-start NODE_ID_4
task prompt="调研 CrewAI"    subagent_type="general-purpose"
rpd node-start NODE_ID_5
task prompt="调研 AutoGen"   subagent_type="general-purpose"
# 三个 subagent 并行，完成后统一 batch-done
rpd batch-done node_ids=[NODE_ID_3,NODE_ID_4,NODE_ID_5]
```

### 效率指引（减少 Token 消耗）

RPD 工作流默认每轮 LLM 调用都有 ~8k 固定 system prompt 开销。**减少轮次 = 直接省 token。** 在上述 subagent 策略基础上，用以下命令进一步优化：

| 命令 | 替代 | 节省 |
|------|------|------|
| `init-and-expand` | `init` + `expand` 两步 | 省 1 轮 |
| `tree.batch-done` | N 次 `node-done` | 省 N-1 轮 |

### 使用规则

- **init-and-expand**: 不要 init 后再 expand。一步到位。init 时已经知道要展开什么就直接提供 children。
- **node-advance**: 仅用于 LLM 自带知识可完成的节点或纯数据穿透。调研/分析/编码类节点不要用 advance 跳过 subagent。
- **batch-done**: 同一波次多个节点同时完成时，批量上报。不要逐个 node-done。
- **避免多余 tick**: `tree.expand` 和 `phase.transition` 已返回 tick 结果。不需要再额外调 `tree.tick`。
- **避免 tree.status / state**: 只在需要诊断时使用。日常操作走 tick 就够了。

### 轮次计数规则

```
一轮 LLM 调用 ≈ ~8-13k input tokens
每个 tree.node-start → work → tree.node-done = 2-3 轮
每个 tree.node-advance = 1 轮
```

对于一个典型 H 节点 P→D→C→A：

```
旧流程: init + expand + tick + 4×(start+done) + tick + phase×3 + tick + status = ~20 轮
优化后: init-and-expand + 4×advance + phase×3 = ~8 轮  (省 60%)
```

---

### 代码类任务：利用项目索引（P/decompose 前置）

当 RPD 的 P/decompose 阶段涉及**代码分析或重构**时，不要自己 `ls`/`grep` 扫描文件结构。使用项目索引：

1. **检查索引** — `.fat/index/files.txt` 和 `.fat/index/symbols.json` 是否存在
2. **刷新索引**（如过期）— `bash("python3 ...")` 或调用 index-refresh 的等价操作刷新符号索引
3. **读索引了解结构** — 先读 `symbols.json` 的 `by_kind` 概览，找出涉及的关键模块
4. **基于索引做拆解决策** — 知道代码组织后再决定如何分解，不是盲目逐文件扫描
5. **只在需要时读具体文件** — 索引指明哪个文件包含什么，按需 `read_file`

这样就避免了 decompose 阶段 `find . -name "*.py"` + 逐文件 read 的重复扫描开销，也确保分解粒度基于真实的代码结构。

---

## 核心概念

### PCCycle 节点

树中的每个节点是一个 PDCA 循环：

```
PCCycle = { phase, mode, status, children, dependencies, decision_log }

phase: P | D | C | A           ← 当前阶段（灵活映射）
mode:  decompose | implement | review | ...  ← 具体工作模式
status: pending | running | done | failed | waiting_for_children
```

### Phase + Mode（灵活映射的核心）

不强制 4 步。每个阶段有多种 mode：

| Phase | 常用 Modes | 说明 |
|-------|-----------|------|
| **P** | `decompose` / `architecture` / `research` / `plan` / `spike` | 需求分解、架构设计、调研都是 P |
| **D** | `implement` / `design` / `configure` / `explore` / `synthesize` | 编码、设计、配置都是 D |
| **C** | `review` / `verify` / `evaluate` / `audit` | 审查、测试、评估都是 C |
| **A** | `standardize` / `merge` / `reflect` / `document` | 规范化、合并、复盘都是 A |

### 执行风格（Style）

每个节点可以显式声明执行风格，控制执行姿态。expand 时在 spec 中加 `style` 字段，或运行时用 `phase.set-style` 调整：

| Style | 行为 | 适用场景 |
|-------|------|---------|
| `divergent` | 尽量发散，探索更多可能 | 调研、创意思考、方案对比 |
| `convergent` | 收敛到精确结论 | 决策评审、方案定稿 |
| `strict` | 严格按规执行 | 编码实现、配置、验证 |
| `balanced`（默认） | 自行判断 | 大多数日常操作 |

### 递归展开

P 太大 → 展开为子 PDCA 树。每个子节点是独立的 PCCycle，可继续展开。

父节点进入 `waiting_for_children`，全部子节点完成后自动恢复。

### 波次调度（Wave Scheduling）

无依赖的兄弟节点**默认并行**执行。同一波次的节点可以用 `task` 工具并行派生子 Agent。

---

## 在 DeerFlow 中的使用

DeerFlow 中通过 `rpd` tool 操作状态机，通过 `task` tool 派生子 Agent 执行叶子节点：

### 核心流程

1. **初始化** — `rpd action=init params='{"slug":"my-task","goal":"...", ...}'`
2. **展开子节点** — `rpd action=tree.expand params='{"node_id":"<id>", "children":[...]}'`
3. **获取就绪节点** — `rpd action=tree.tick`
4. **执行叶子节点** — 对每个就绪节点：
   - `rpd action=tree.node-start params='{"node_id":"<id>"}'`
   - 用 `task` tool 派生子 Agent 执行，或自行处理
   - `rpd action=tree.node-done params='{"node_id":"<id>"}'`
5. **检查下一波** — `rpd action=tree.tick`
6. **阶段过渡** — `rpd action=phase.transition params='{"node_id":"<id>", "phase":"D"}'`
7. 重复直到根节点完成

### 节点执行规则

- 同一波次（same wave number）的节点可以并行执行
- 使用 `task` tool 的 `subagent_type="general-purpose"` 执行复杂节点
- 使用 `task` tool 的 `subagent_type="bash"` 执行纯命令行节点
- 也可以自己直接执行简单节点

### 示例：调研任务

```
1. 初始化: rpd action=init params='{"slug":"tech-research","goal":"调研 AI Agent 框架"}'

2. 展开: rpd action=tree.expand params='{
  "node_id":"<root-id>",
  "children":[
    {"phase":"P","mode":"research","title":"框架调研","style":"divergent"},
    {"phase":"D","mode":"synthesize","title":"对比分析"},
    {"phase":"C","mode":"review","title":"质量审查"},
    {"phase":"A","mode":"document","title":"输出报告"}
  ]
}'

3. Tick → 获取 Wave 1（框架调研）
4. 用 task 工具并行执行：
   - task("调研 LangGraph")     → node-done
   - task("调研 CrewAI")        → node-done
   - task("调研 AutoGen")       → node-done

5. Tick → 对比分析就绪 → 执行 → node-done
6. Tick → 质量审查就绪 → 执行 → node-done
7. phase.transition → 输出报告
8. 直到根节点完成
```

---

## Subagent Capability Profile（按需发现 Skill/Tool）

Subagent 执行节点时，不只靠 LLM 通才能力。DeerFlow 提供原生工具让 subagent 按需发现和加载能力。

### 原则

1. **按需加载，不是全量清单** — 不需要预加载所有 skill 的 SKILL.md 到 subagent 上下文
2. **subagent 自己判断用什么能力** — 不靠 RPD 父节点指定，不靠硬编码映射表
3. **有 hint 用 hint，没有就搜索** — 父节点可设 `skill_hint` 加速，但不是约束

### Capability API 映射

| 功能 | 在 DeerFlow 中的方式 |
|------|---------------------|
| 搜索匹配 skill | `describe_skill(search_term)` — 按名称/关键词发现的 skill 列表 |
| 加载 skill 内容 | `read_file(path=<skill_path>)` — 读取 SKILL.md |
| 按 phase 查询 | `describe_skill` + 关键词过滤 |
| 查询可用工具 | 工具已直接可用；需按需推广的 MCP 工具通过 `tool_search` 发现 |
| 查询系统工具 | `bash` tool 可执行任意 shell 命令 |

### Subagent 执行流程

当 subagent 收到一个节点时：

```
1. 看节点: phase=D, mode=implement, title="实现搜索功能"
2. 看是否有 skill_hint:
   - 有 → describe_skill + read_file 加载对应 skill
   - 无 → 自己判断是否需要 skill：
     a. 这个任务有专门的 skill 吗？
        → describe_skill("search") 搜索 → 找到就 read_file 加载
     b. 没有匹配 skill → 用 LLM 通才能力
3. 复杂任务 → 用 task tool 递归派发子 subagent
4. 执行完成后 → node-done
```

### 什么时候用 skill

| 场景 | 行为 |
|------|------|
| 有 `skill_hint` | 优先加载。hint 不存在时静默 fallback |
| 领域特定（API 集成、数据分析） | 先 `describe_skill` 看看 |
| 通用开发（CRUD、重构） | LLM 通才能力就够了，不查 skill |
| 审查/检查 | `describe_skill("review")` 查审查类 skill |
| 数据分析 | `describe_skill("analysis")` 或直接用 Python |

### skill_hint 配置（RPD 节点 spec 可选）

展开子节点时可指定 `skill_hint` 帮助 subagent 快速锁定技能：

```json
{
  "phase": "C",
  "mode": "review",
  "title": "审查搜索实现",
  "skill_hint": "bug-hunt",
  "dependencies": []
}
```

subagent 看到 `skill_hint` 后自动 `describe_skill("bug-hunt")` + `read_file`，按该技能规范执行。
没有 `skill_hint` 的节点不影响执行——subagent 自己决定要不要查。

---

## 异步 A 阶段（Async A）

A 阶段的某些操作（等外部结果、等审批）本质上是等待外部事件。异步支持让 A 节点不阻塞执行：

1. 执行 A 节点，判断需要外部等待
   → `rpd action=phase.set-async params='{"node_id":"<id>","reason":"等待 CI 通过"}'`
   → 节点标记为 async，状态 running

2. 后续通过 tree.check-async 解决：
   → `rpd action=tree.check-async params='{"node_id":"<id>","result":"done"}'`

### 规则

- **仅 A 阶段**节点可标记为 async
- async 节点在树状态中用 **↻** 标识
- 依赖关系不变：依赖 async 节点的其他节点仍然等待它完成

---

## 方法论引导

P 阶段可以选择方法论辅助决策：

```
rpd action=methodology.list params='{"phase":"P","mode":"architecture"}'
rpd action=methodology.get params='{"name":"adr-first"}'
```

| 场景 | 推荐方法论 |
|------|-----------|
| P+architecture | ADR-First |
| P+spike | Spike-and-Stabilize |
| D+implement | TDD 或 API-First |

方法论是参考，不是教条。你的判断力 > 方法论的规则。

---

## 决策日志

每个节点维护 `decision_log`。在关键时刻（为何选这个 mode/方法论、为何展开或停止展开、值得记录的技术决策）手动追加到节点的 decision_log 中。

决策日志不是日常操作日志——只记录**关于选择的元认知**。

---

## 边缘情况

- **全部节点 terminal 但还有工作**：root 还在 running，需要手动 phase transition 推进
- **子节点 failed**：决定重试（prune 后重新 expand）或接受
- **大波次**：多个节点就绪时，可以用 `task` 工具并行派生子 Agent
- **扇出限制**：单节点最多 50 个子节点

---

## `rpd` 工具参考

| 操作 | 作用 |
|------|------|
| `init` | 初始化新任务或恢复已有任务 |
| `tree.tick` | 获取当前波次所有就绪节点 |
| `tree.status` | 查看完整树状态 |
| `tree.expand` | 展开为子节点 |
| `tree.node-start/done/fail/skip` | 节点生命周期 |
| `tree.check-async` | 检查异步节点并完成 |
| `tree.prune` | 撤销展开 |
| `phase.transition` | 阶段过渡（P→D→C→A） |
| `phase.set-mode` | 设置工作模式 |
| `phase.set-style` | 设置执行风格 |
| `phase.set-async` | 标记异步节点 |
| `methodology.list/get` | 方法论管理 |
| `state` / `state.set` | 引擎状态查看/修改 |
| `save` | 强制持久化 |
