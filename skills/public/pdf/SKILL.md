---
name: pdf
description: "强引擎蓝图驱动 PDCA 流水线：固定拓扑 + HSM 状态机 + Pipeline 调度 + 约束门禁"
argument-hint: "<任务描述> [--plan-only | --resume <task_slug> | --new] [--session-slug <slug>] [--append] [--no-detect-overlap]"
---

# /pdf — 强引擎蓝图驱动 PDCA 流水线

PDF 使用 **固定拓扑蓝图驱动** 执行模型。所有 PDCA 节点（包括每个节点的类型、依赖关系、重试策略、节点角色）在 `docs/topology/` 下的蓝图文件中一次性声明。引擎负责调度和状态管理，LLM 负责执行引擎派发的具体节点任务。

**引擎与 LLM 的职责分离：** 引擎（`pdf-engine.py`）决定"什么节点该运行了"（Make 风格：扫描 DAG → 找出所有依赖已满足的就绪节点 → 批量返回）。LLM 不再决定"下一步做什么"，改为按引擎派发的节点列表执行：对每个 `engine_exec` 节点运行对应的 CLI 命令，对每个 `llm_spawn` 节点 spawn 子 Agent 完成工作。执行完毕后通知引擎，引擎自动 tick 发现下一批就绪节点。

**固定拓扑的优势：** 声明式的蓝图文件使 PDCA 流程透明可审查，每个阶段的出入口、过渡事件、回滚路径一目了然。`manual_checkpoint` 节点在关键决策点插入人工屏障，确保高风险操作不自动通过。HSM 状态机带有 `max_loops` 计数器，防止死循环或失控回滚。

---

## 五通道独立蓝图拓扑

PDF 按 channel（lite/standard/full/analysis/planning）使用 **五个独立蓝图文件**。引擎在 `channel select` 阶段自动选择对应蓝图并保存到 state，后续 pipeline 命令从 state.blueprint 自动读取。

| 通道 | 蓝图文件 | 拓扑特征 | N/M 执行 |
|------|---------|---------|---------|
| **full**（默认） | `blueprint.full.yaml` | 全量多 Agent 拓扑：所有 llm_spawn 节点、对抗审查、完整回滚链路 | **严格执行** — N 个 designer/doer/checker 必须 spawn，M 个 reviewer 必须 spawn |
| **standard** | `blueprint.std.yaml` | 固定拓扑：P2_review/D2_review 始终执行，无对抗审查 | N 个 doer spawn，M reviewer 按拓扑固定执行 |
| **lite** | `blueprint.lite.yaml` | 最小单 Agent：无 llm_spawn 节点，全 llm_converge/engine_exec | 无 spawn，主 LLM 直接产出。N=1 M=0 |
| **analysis** | `blueprint.analysis.yaml` | 纯分析模式：零 P0 前置节点，全 llm_converge，auto-approve，Check 有三路路由 | 无 spawn，全收敛执行。N=1 M=0 |
| **planning** | `blueprint.planning.yaml` | 规划模式：项目需求到迭代规划，全 llm_converge，P2 架构多方案+用户确认，Check 五路路由 | 无 spawn，全收敛执行。N=1 M=0 |

> **analysis/planning 通道说明：** 全 `llm_converge` 节点，无 llm_spawn。因此 P2_review/D2_review/C1_check 等 spawn 类节点不存在，下面的通道感知表不适用于 analysis/planning 通道。

**蓝图选择流程（自动通道选择）：**
```
channel auto → 引擎自动分类 output_kind + 检测因子 + 多模块分析
           ↓
           自动保存 state.channel + state.blueprint
           ↓
           stage 自动推进到 plan
           ↓
pipeline tick → 从 state.blueprint 自动加载对应拓扑
           ↓
所有 pipeline 命令（node-done/node-fail/pass-checkpoint 等）自动使用正确蓝图
```

**引擎自动路由规则（pdf-engine.py channel auto）：**

```
1. LLM override 检查                   → 最高优先级，跳过所有自动判断
2. Factor force_channel（security → full 等）→ 因子级别强制
3. 关键词分类 output_kind              → 从 task_slug + description 自动匹配
4. output_kind → channel               → analysis/planning/full/standard/lite 映射
5. multi_module 检测（src/下 ≥3 模块）  → lite→standard→full 升级
```

LLM 不再需要手动分类 output_kind/task_type/factors。如有需要可显式覆盖：

```
python3 skills/pdf/tools/pdf-engine.py channel set-override <channel> "<reason>"
```

也可在入口直接指定蓝图（最高优先级）：
```
/pdf "task" --blueprint lite
python3 skills/pdf/tools/pdf-engine.py blueprint load <name>
```

---

## Node Type Reference (strict execution contract)

每个蓝图节点有一个 `type` 字段，**LLM 必须按类型严格执行，不得跳过或代劳**：

| Node Type | LLM 行为 | 完成后调用 |
|-----------|----------|-----------|
| `engine_exec` | 从节点 `command` 字段读取准确的 CLI 命令并执行。检查退出码。成功→`node-done`，失败→`node-fail`（引擎自动 retry）。 | `pipeline node-done\|fail <stage> <ref>` |
| `llm_spawn` | **必须 spawn 子 Agent！** 从节点 `spawn_role` 字段读取角色（如 `designer`、`reviewer`、`checker`）。参考 `role-packs/<role>.md` 构造 agent prompt，通过 `context bundle build <stage> --role <spawn_role>` 构建上下文包后再 spawn 子 Agent。**不论 N=1 或 N>1，均需 spawn，主 LLM 不得代劳。** 收到子 Agent 输出后→`node-done`。 | `pipeline node-done <stage> <ref>` |
| `llm_converge` | 读取同一阶段中前序节点的所有产出，合并/收拢为一份连贯的文档。**输出文件必须写入 session 目录**：先通过 `artifact resolve-path <filename>` 获取完整路径，写入后调用 `artifact add --move <filename> <role>` 注册并移动到 session artifacts 目录。→`node-done`。 | `pipeline node-done <stage> <ref>` |
| `llm_merge` | 读取多个 doer 的输出，将它们合并为一个统一的输出。路径规则同 `llm_converge`。→`node-done`。 | `pipeline node-done <stage> <ref>` |
| `repair_gate` | 评估修复决策。读取修复报告，决定：**fix** → 重新运行 doer 节点；**rollback** → 触发 HSM 回滚事件；**act** → 放行通过。→`node-done`。 | `pipeline node-done <stage> <ref>` + 可选 `hsm event` |
| `goal_doer` | **Spawn Goal-mode agent for autonomous iteration.** Read `spawn_role`, `max_continuations`, `evaluator_prompt`, and `artifacts` from the node definition. Build context bundle with `context bundle build <stage> --role <spawn_role>`. Spawn a subagent whose prompt embeds the goal objective + evaluator_prompt as the completion criterion. After the spawned agent returns, evaluate the output against evaluator_prompt. If criteria are met → call `node-done`; otherwise verify if artifact files exist (check via `artifact list` or `ls`). This is an **autonomous iteration** node: the LLM may re-spawn with updated context if the previous attempt was incomplete. Suitable for nodes that require exploration, iterative refinement, or open-ended problem-solving where Goal-style autonomous continuation is beneficial. | `pipeline node-done <stage> <ref>` |
| `manual_checkpoint` | **阻塞节点，两步流程：** (a) LLM 先调用 `node-done <stage> <ref>` → 引擎返回 `checkpoint_blocked` 状态，不自动 tick。(b) 常规模式：向用户展示确认 prompt；**auto 模式**（state.auto_mode=true 且节点 auto_approve=true）：LLM 自动评估通过条件后直接调用 `pass-checkpoint`，无需询问用户。 | 先 `pipeline node-done <stage> <ref>`，再根据模式调用 `pipeline pass-checkpoint\|reject-checkpoint <stage> <ref>` |

---

## Artifact Path Rules

所有 `llm_converge` / `llm_merge` / `llm_spawn` 节点产生的输出文件**必须写入 session-scoped 目录**，严禁直接写入 CWD。三条规则：

1. **写前查路径**：用 `artifact resolve-path <filename>` 获取完整写入路径（返回 `<project>/.fat/pdf/sessions/<session_id>/artifacts/<filename>`）
2. **写后注册**：用 `artifact add --move <filename> <role>` 注册到 state 并将文件移动到 session artifacts 目录。`--move` 自动从当前位置搬移到正确位置。
3. **不跳步骤**：即使用 `artifact add <filename>`（无 `--move`）也会重写注册路径到 session 目录，但必须确保文件实际存在于此路径。

**示例：**
```bash
# 获取路径
python3 skills/pdf/tools/pdf-engine.py artifact resolve-path plan_decisions.yaml
# → /Users/.../.fat/pdf/sessions/<id>/artifacts/plan_decisions.yaml

# 将文件写入该路径后注册
python3 skills/pdf/tools/pdf-engine.py artifact add --move plan_decisions.yaml decisions
```

**禁止操作：** 将文件直接写入 CWD（项目根目录）而不移动或注册。

---

## Pipeline Execution Model

PDF 使用 Make 风格的调度循环。LLM 和引擎交替工作:

```
1. 运行 `pdf-engine.py pipeline tick`
   → 引擎扫描当前 stage 的所有节点，找出所有依赖已满足的就绪节点
   → 返回节点列表和状态的 action

2. (可选) 对每个就绪节点运行 `pipeline node-start <stage> <ref>`，标记为"运行中"
   → 防止并行调度冲突，推荐在蓝图存在并行节点时使用

3. 对每个就绪节点，LLM 根据其 type 执行:
   - engine_exec: 运行节点中定义的 CLI command
   - llm_spawn: spawn 子 Agent 完成工作（参考 role-packs + context bundle build）
   - llm_converge / llm_merge: 读前序产出 → 合并收敛
   - goal_doer: spawn Goal-mode agent with evaluator_prompt, autonomously iterate until criteria met (max_continuations cap)。子 agent 返回后 LLM 自行判断是否完成，未完成则再次 spawn 直至 evaluator_prompt 条件满足。产出物通过 artifact add 注册。
   - repair_gate: 读修复报告 → 决策修复/回滚/放行
   - manual_checkpoint: 两步流程（见 Node Type Reference）

4. 节点执行完毕后，调用对应的引擎命令:
   - `node-done <stage> <ref>` → 普通节点标记 done 并自动 tick；
     manual_checkpoint 节点返回 checkpoint_blocked，**不自动 tick**
   - `node-fail <stage> <ref>` → 标记 failed，**不自动 tick**，
     LLM 需手动再运行 `pipeline tick` 检查重试就绪
   - `node-skip <stage> <ref>` → 标记 skipped，自动 tick
   - 仅 manual_checkpoint（用户确认后）:
     `pass-checkpoint <stage> <ref>` → 标记 done，自动 tick；
     `reject-checkpoint <stage> <ref>` → 重置上游节点，自动 tick

5. 重复步骤 1-4，直到当前 stage 的所有节点完成

6. 运行 `pdf-engine.py pipeline status` 确认 stage 状态
   → 如果 action 为 "stage_done"，进入 HSM 决策阶段

7. (可选预检) LLM 可提前运行 `pdf-engine.py constraint verify [--stage <stage>] [--phase <phase>]`
   → 检查 entry_gate 约束是否满足
   → 引擎在 HSM event 触发时也会自动执行此检查（双保险）

8. 约束通过后，LLM 评估执行结果，选择合适的 HSM 事件:
   - 成功 → `hsm event done` (do/check/act) 或 `hsm event design_approved` (plan)
   - 发现问题 → 触发相应的回滚事件

9. HSM 执行 transition，更新 stage 和 hsm_path
   → 在 transition 过程中自动 reset 回滚范围内的节点
   → 然后重新调用 `pipeline tick` 发现新 stage 的就绪节点
```

---

## HSM Transition Flow

HSM（分层状态机）管理 stage 之间的转换。当当前 stage 所有节点完成时，LLM 需要评估结果并决定要触发的事件：

| Event | 来源 Stage | 目标 | 含义 |
|-------|-----------|------|------|
| `design_approved` | plan | exit (进入 do) | Plan 获批，推进到 Do |
| `done` | do / check / act | exit (下一 stage) | 阶段完成自动推进 |
| `bug` | check | do (rollback) | 实现 bug → 回滚到 Do 从头修复 |
| `bug` | do | D1_doer (rollback) | 审查发现 bug → 回滚到 D1 重新实现 |
| `design_flaw` | plan | P1_design (rollback) | 设计缺陷 → 回滚到 P1 重新设计 |
| `design_flaw` | check | plan (rollback) | 设计缺陷 → 回滚到 Plan |
| `design_flaw_detected` | do | plan (rollback) | Do 阶段发现设计缺陷 → 回滚 Plan |
| `clean` | check | exit (进入 act) | 全部通过 → 进入 Act 标准化 |

**流程：**
1. LLM 确认 stage 所有节点完成
2. LLM 运行 `python3 skills/pdf/tools/pdf-engine.py constraint verify [--stage <stage>] [--phase <phase>]` 检查 entry_gate 约束是否满足
3. LLM 评估结果质量，决定使用哪个事件
4. 运行 `python3 skills/pdf/tools/pdf-engine.py hsm event <event_name>`
5. 引擎检查 transition 的 `max_loops` —— 超限则进入 paused 状态，等待人工介入
6. 引擎执行 reset（回滚范围内的节点标记为 pending）
7. 引擎更新 stage 和 hsm_path
8. LLM 运行 `python3 skills/pdf/tools/pdf-engine.py pipeline tick` 发现新 stage 的就绪节点
9. 重复执行循环

**约束门禁（双保险）：** 引擎在每个 HSM transition 前自动执行约束检查。LLM 也可提前运行 `constraint verify [--stage <stage>] [--phase <phase>]` 作为预检。

---

## Engine Commands Quick Reference

| Command | Purpose |
|---------|---------|
| `pipeline tick` | 扫描当前 stage 的 DAG，返回所有就绪节点 |
| `pipeline node-start <stage> <ref>` | (可选) 标记节点为"运行中"，防止并行调度冲突 |
| `pipeline node-done\|fail\|skip <stage> <ref>` | 上报节点结果。node-done 和 node-skip 自动 tick；node-fail **不自动 tick**，LLM 需手动 `pipeline tick` |
| `pipeline pass-checkpoint\|reject-checkpoint <stage> <ref>` | 人工确认/驳回 checkpoint 节点（需先调用 node-done） |
| `pipeline status` | 查看所有 stage 的 DAG 节点状态统计 |
| `pipeline summary` | 一行摘要：当前 stage + 完成比例 |
| `hsm status` | 显示当前 HSM 路径 + 可用 events + loop 计数 |
| `hsm event <name>` | 触发 stage transition 事件 |
| `hsm goto <path>` | 手动设置 HSM 路径（调试/干预），例如 `hsm goto plan.P1_design` |
| `hsm unpause` | 从 paused 状态恢复 |
| `hsm reset-loops [<event>]` | 重置 loop 计数器 |
| `rollback check` | 检查回滚就绪状态 |
| `rollback exec <target_stage> <event>` | 执行回滚（重置目标 stage 内所有节点，附带事件原因） |
| `rollback cancel` | 取消待处理的回滚 |
| `constraint verify [--stage <stage>] [--phase <phase>]` | 约束门禁检查 — 验证指定阶段满足通过条件（位置参数，非 flag） |
| `blueprint list` | 列出所有可用蓝图 |
| `blueprint load <name>` | 加载指定蓝图到 state |
| `blueprint validate` | 验证当前蓝图的完整性 |
| `artifact add <path> [role]` | 注册产出物到工单追踪 |
| `artifact list` | 列出所有已注册产出物 |
| `agent pending` | 列出待执行的 agent 任务 |
| `agent status` | 列出所有 agent 及其状态 |
| `state [key]` | 查看引擎状态（可指定 key） |
| `channel auto` | 引擎自动通道选择（从 task_slug 关键词分类 + 因子检测 + 多模块分析） |
| `channel select <json>` | （手动模式，自动路由不理想时备用）基于任务属性计算通道选择 |
| `decisions-diff <f1> <f2>` | 对比两个 decisions YAML 文件 |
| `precheck run <task>` | 执行前置兼容性检查 |
| `scope classify <task>` | 任务上下文范围分类 |
| `sanitize run <task>` | 输入清洗与校验 |
| `knowledge seed` | 注入知识库/技术栈种子条目 |
| `history query` | 查询 Cycle-History 自适应记录 |

## Plan 阶段 — Blueprint 驱动执行

Plan 阶段由蓝图（`docs/topology/blueprint.yaml`）中的 `plan` 节点拓扑驱动。蓝图负责 DAG 节点依赖解析、重试、检查点阻塞和阶段间事件路由。LLM 角色是**执行每个蓝图节点的具体动作**：调用引擎命令、解析结果、做出判断决策。

Plan 阶段入口门（entry_gate）：无前置产物要求，空数组，直接进入。

```
Blueprint plan topology:
  P0_precheck → P0_scope → P0_kb(opt) → P1_design → P2_review → P3_converge → PLAN_APPROVE
                                                                                      │
                                              ┌──────────────────────────────────────┤
                                              ▼                                      ▼
                                      design_flaw (rollback P1)           design_approved (→ Do)
```

若 Plan 阶段因 HSM 事件 `design_flaw` 回滚，重置范围为 `P1_design` 到 `PLAN_APPROVE`，P0 节点保持完成状态不重置。最大回滚循环次数：2。

---

### P0_precheck（engine_exec）

**命令：**
```
python3 skills/pdf/tools/pdf-engine.py precheck run "$RAW_INPUT" --domain <domain> --task-type <type>
```

向引擎提供 domain 和 task_type 参数。若未确定 domain，先用 general 执行第一轮预检。

**结果三档处理：**

| 结果 | 行为 |
|------|------|
| `warn` | 记录警告到 state，标记预检完成，继续下一个节点 |
| `ask_user` | **暂停**（engine_exec 节点自动触发 HSM pause）。向用户输出 engine 生成的反问模板，等待回复。用户回复后 unpause，用新信息重新执行 precheck（重试上限 2 次） |
| `block` | **阻止继续**。停止 Plan 阶段，输出 block 原因，等待人工介入。调用 `python3 skills/pdf/tools/pdf-engine.py hsm unpause` 后可选择重新执行 precheck 或手动 override |

**重试策略：** 蓝图配置 retry=2。任意档失败后重试，最多 2 次。超出后节点标记 failed，触发 HSM pause。

预检完成后调用 checkpoint 更新：
```
python3 skills/pdf/tools/pdf-engine.py checkpoint update --phase precheck
```

---

### 前置检查 — LLM 判别（原则4 先说不）

此步骤不是蓝图节点，是 engine_exec 之后、继续执行前的 LLM 判断环节。

引擎预检通过后，LLM 必须再执行一次深度判别：用户请求的前提是否合理？是否存在未被引擎识别的根本问题？是否应建议更好的替代方向？

**判断框架：**
1. 用户请求的任务是否有隐含前提（如"把 X 改为 Y"而 X 当前不存在）？
2. 是否有更直接的方法（如用已有工具代替手动实现）？
3. 请求的路线是否高效（如绕弯实现本可一跳完成的功能）？
4. 是否存在安全、兼容性或数据完整性的未言明风险？

若前置有问题，**明确说出不做什么**（"先说不"），给出理由和替代方案，向用户确认后再继续。用户确认后，在 state 中记录排除项：
```
python3 skills/pdf/tools/pdf-engine.py state set rejected_premises=<json>
```

若前置合理，输出确认语简略标注后继续到下一个蓝图节点。

---

### P0_scope（engine_exec）

**命令：**
```
python3 skills/pdf/tools/pdf-engine.py scope classify "$RAW_INPUT"
```

引擎自动执行三级降级：sfagent → 内联分类器 → 默认值。

**scope 结果及上下文注入策略：**

| scope 值 | 策略 |
|----------|------|
| `within_project` | 保留项目上下文（当前目录、相关源文件），继续正常 Plan 流程 |
| `independent` | **生成隔离子任务上下文**：spawn clean-slate agent，仅传递 `$TASK` 和全局规则。不影响当前项目文件的注入 |
| `hybrid` | **过滤注入**：只注入 `filter_context()` 过滤后的相关文件。scope 结果记入 state，下游 context bundle builder 自动识别 |

scope 结果自动写入 state，后续 `context bundle build` 命令读取 scope 值调整注入策略。

```
python3 skills/pdf/tools/pdf-engine.py checkpoint update --phase scope
```

---

### P0_kb（engine_exec，可选）

**命令：**
```
python3 skills/pdf/tools/pdf-engine.py knowledge seed
```

引擎执行知识库种子注入——根据当前 domain 配置生成 seed 条目到 knowledge 索引。

**处理规则：**
- 成功 → checkpoint 更新，继续
- 失败（如 knowledge 索引未初始化、命令不存在）→ 记录 warn，标记节点为 failed，引擎自动 retry
- retry 用尽后节点标记 failed，触发 retries_exhausted 处理

```
python3 skills/pdf/tools/pdf-engine.py checkpoint update --phase knowledge_seed
```

---

### 领域匹配 + 自动通道选择（引擎自动，LLM 可覆盖）

`P0_kb` 完成后、`P1_design` 前，LLM 执行领域匹配，引擎自动做通道选择。

**1. 领域匹配：**
- 读 `docs/domain/index.yaml` → 关键词匹配 `$TASK` → 首个命中者为 domain
- 无一命中 → domain=general
- 写入 state：
  ```
  python3 skills/pdf/tools/pdf-engine.py state set domain=<domain>
  ```

**2. 引擎自动通道选择（无需 LLM 手动分类）：**
- 调用引擎自动确定通道：
  ```
  python3 skills/pdf/tools/pdf-engine.py channel auto
  ```
- 引擎从 state 自动读取以下信号，不再需要 LLM 提供 JSON：
  - `state.task_slug` / `state.task_description` → 关键词匹配自动分类 output_kind
  - `state.domain` → P0_scope 已设置
  - `FACTOR_TAXONOMY` → 内置因子规则（security_audit → force full 等）
  - 项目文件树 → 自动检测 multi_module（src 下 ≥3 个模块 → 升级到 full）
- 引擎输出示例：
  ```
  channel=full output_kind=feature
  channel=planning output_kind=planning
  channel=analysis output_kind=analysis factor=security_audit
  ```

**3. LLM 覆盖（如有需要）：**
若自动选择结果不理想，LLM 可显式覆盖：
```
python3 skills/pdf/tools/pdf-engine.py channel set-override <channel> "<reason>"
```

通道选择后，LLM 有义务为后续 spawn 节点确定 N（designer 数）和 M（reviewer 数）：

- **N**：由 LLM 根据任务复杂度判断。通常拆解为不重叠的设计片段，片段数即为 N。共享接口/类型/文件 → N=1，解耦 → N≥2。
- **M**：由触发的维度确定。correctness 为基线维度（M≥1）。每触发一个额外维度 M+1。

写入 state 供后续 spawn 节点读取：
```
python3 skills/pdf/tools/pdf-engine.py state set plan.N=<N>
python3 skills/pdf/tools/pdf-engine.py state set plan.M=<M>
```

```
python3 skills/pdf/tools/pdf-engine.py checkpoint update --phase channel_select
```

---

### P1_design（llm_spawn）

**蓝图配置：** `type: llm_spawn`, `spawn_role: designer`, `deps: [P0_scope, P0_kb]`, `retry: 3`, `artifacts: [plan_decisions.yaml]`

Spawn N 个 designer agent（N 在通道选择阶段确定，从 state.plan.N 读取）。每个 designer 负责一个不重叠的设计片段。

**Spawn 操作：**
```
# 为每个 designer 获取模型
python3 skills/pdf/tools/pdf-engine.py config get-model plan.design --domain <domain>

# 注册 agent（引擎自动管理生命周期）
python3 skills/pdf/tools/pdf-engine.py pipeline node-start plan P1_design

# 为每个 designer 构建上下文包
python3 skills/pdf/tools/pdf-engine.py context bundle build plan --role designer
```

每个 designer 输出必须包含 `## Decisions` YAML block，结构遵循 ADR 格式（key / decision / rationale）。设计片段通过 `artifact resolve-path design_output_<n>.md` 获取路径后写入。

**输入上下文：**
- 从 `context bundle build` 获取的上下文包（task、scope 结果、domain、channel 配置）
- `P0_kb` 注入的技术栈知识（若有）
- 领域配置中的设计约束（从 `docs/domain/<domain>.yaml` 读取）

**冲突规则：**
- 不同 designer 对同一接口的不同签名 → 标注 `CRITICAL CONFLICT`
- 不同 designer 对同一模块的不同实现路径 → 标注 `MODERATE CONFLICT`
- trivial 措辞差异 → engine 自动合并

所有 designer 完成后按注册顺序合并产物：
```
python3 skills/pdf/tools/pdf-engine.py artifact add --move design_output_1.md designer
...
python3 skills/pdf/tools/pdf-engine.py pipeline node-done plan P1_design
```

---

### P2_review（llm_spawn）

**蓝图配置：** `type: llm_spawn`, `spawn_role: planner_reviewer`, `deps: [P1_design]`, `retry: 3`, `artifacts: [plan_review_*.md]`

Spawn M 个 reviewer agent（M 在通道选择阶段确定，从 state.plan.M 读取）。每个 reviewer 覆盖一个维度，correctness 为基线维度。

**Spawn 操作：**
```
python3 skills/pdf/tools/pdf-engine.py config get-model plan.p2 --domain <domain>
python3 skills/pdf/tools/pdf-engine.py pipeline node-start plan P2_review
```

**Reviewer 输入：**
- 所有 designer 输出（`design_output_<n>.md`）
- 对应维度的结构化 checklist（`docs/decision-engineering/checklists/<dimension>.yaml`）
- domain review_extensions（`docs/domain/<domain>.yaml` 的 `review_extensions.<dimension>.extra_checks`）

每个 reviewer 输出通过 `artifact resolve-path plan_review_<dimension>.md` 获取路径后写入，包含：
- 维度关键发现
- checklist 逐项 ✅/❌/N/A 标记
- 每项发现标注：blocker / warning / info
- 建议改进项

**通道感知的 review 强度：**

| 通道 | M 范围 | 备注 |
|------|--------|------|
| lite | M=0 | 拓扑无 review 节点，跳过 |
| standard | M≥1（仅 correctness） | 固定执行，不追加维度 |
| full | M≥1 | 完整 M 维度。M≥3 升级 opus correctness+security reviewer，额外 spawn plan-adversary |

M≥3 时额外 spawn 1 个 plan-adversary agent（角色为对抗审查，模型为 opus），输出通过 `artifact resolve-path plan_review_adversary.md` 获取路径后写入，聚焦设计假设的隐含风险。详见 `docs/adversary-strategies.md`。

每个 reviewer 输出后注册：
```bash
# 注册每个 reviewer 的产出（--move 自动移动到 session artifacts 目录）
python3 skills/pdf/tools/pdf-engine.py artifact add --move plan_review_correctness.md correctness
python3 skills/pdf/tools/pdf-engine.py artifact add --move plan_review_security.md security
# ... 每个维度一个
python3 skills/pdf/tools/pdf-engine.py pipeline node-done plan P2_review
```

---

### P3_converge（llm_converge）

**蓝图配置：** `type: llm_converge`, `deps: [P2_review]`, `retry: 3`, `artifacts: [plan.md]`

主 agent 执行收敛合并，注入所有 designer 输出 + reviewer findings + adversary 输出（若有）作为合并输入源。

```
python3 skills/pdf/tools/pdf-engine.py pipeline node-start plan P3_converge
```

**输入材料完整列表：**
1. 所有 `design_output_<n>.md`（N 份设计片段）
2. 所有 `plan_review_<dimension>.md`（M 份审查报告）
3. `plan_review_adversary.md`（若有）

**收敛步骤：**
1. 对比所有 designer 设计片段的 `## Decisions` block，用引擎核对冲突：
   ```
   for each pair (i, j):
     python3 skills/pdf/tools/pdf-engine.py decisions-diff design_output_<i>.md design_output_<j>.md
   ```
2. 处理 reviewer blocker 发现：每项 blocker 必须回答（接受/拒绝/修改）
3. 生成 `plan_decisions.yaml`——结构化记录所有最终决策（每个决策含 id、decision、rationale、rejected_alternatives）
4. 生成 `plan.md`——人类可读的完整方案文档

**信息密度规则：** 收敛 agent 必须直接引用原始 designer 和 reviewer 产出的具体段落，而非仅读上层摘要。

```
python3 skills/pdf/tools/pdf-engine.py artifact add --move plan_decisions.yaml decisions
python3 skills/pdf/tools/pdf-engine.py artifact add --move plan.md plan
python3 skills/pdf/tools/pdf-engine.py pipeline node-done plan P3_converge
```

---

### PLAN_APPROVE（manual_checkpoint）

**蓝图配置：** `type: manual_checkpoint`, `deps: [P3_converge]`, `retry: 0`, `auto_approve: <bool>`

manual_checkpoint 节点有**两种执行模式**，由蓝图节点的 `auto_approve` 字段决定：

| 模式 | auto_approve | 行为 | 典型通道 |
|------|-------------|------|---------|
| **人工确认** | `false`（默认） | 向用户展示确认 prompt，**等待人工回复**后才放行 | full, standard |
| **自动通过** | `true` | LLM 自动评估 checkpoint 标准，直接调用 `pass-checkpoint` | lite（纯自动） |

#### 模式一：人工确认（auto_approve: false）

向用户输出以下确认 prompt，然后**等待人工确认**。引擎在 manual_checkpoint 节点阻塞所有下游，直至用户明确回复。

**用户 prompt 模板：**
```
## Plan 完成 — 人工确认

Plan 阶段已完成并生成以下产物：
- `plan_decisions.yaml` — N 个设计决策
- `plan.md` — 完整方案文档
- `plan_review_*.md` — M 份审查报告

请确认：
- **通过**：进入 Do 阶段 → 触发 `design_approved` 事件
- **驳回**：回滚到 P1_design 重新设计 → 触发 `design_flaw` 事件
- **手动修改 plan.md**：修改后输入 `r` 重新触发本确认
```

**用户操作映射：**
| 用户输入 | 动作 |
|----------|------|
| `y` / `yes` / `通过` / `approve` | 引擎执行 `pipeline pass-checkpoint plan PLAN_APPROVE` → HSM 事件 `design_approved` |
| `n` / `no` / `驳回` / `reject` | 引擎执行 `pipeline reject-checkpoint plan PLAN_APPROVE` → HSM 事件 `design_flaw`，回滚到 `P1_design` |
| `r` / `retry` / `修改后重审` | 用户已手动修改 `plan.md` → 重新执行确认 prompt |
| 其他自由文本 | 视为附加说明，继续阻塞等待明确 y/n |

#### 模式二：自动通过（auto_approve: true）

蓝图节点标记 `auto_approve: true` 后，LLM 无需等待用户确认：

1. 调用 `pipeline node-done plan PLAN_APPROVE` → 引擎返回 `checkpoint_blocked`
2. LLM 自动评估 checkpoint 通过标准：
   - `plan_decisions.yaml` 是否存在且非空
   - `plan.md` 是否存在且完整
   - 所有 reviewer blocker 是否已处理
3. 评估通过 → 直接调用 `pipeline pass-checkpoint plan PLAN_APPROVE`
4. 引擎自动触发 HSM 事件推进到 Do 阶段

此模式适用于 lite 通道或经过测试验证的全自动流水线。

#### 启用 auto mode（全局开关）

Auto mode 是运行时开关，与蓝图 `auto_approve` 配合：
- `state.auto_mode=true` + `节点 auto_approve=true` → 引擎在 runner 层**自动执行** pass-checkpoint，LLM 甚至无需手动调 `pass-checkpoint`
- 引擎在 `node_complete()` 中检测 manual_checkpoint 时，先检查 `auto_mode && auto_approve` → 是则直接 auto-pass

```bash
# 启用 auto mode
python3 skills/pdf/tools/pdf-engine.py state set auto_mode=true

# 关闭 auto mode
python3 skills/pdf/tools/pdf-engine.py state set auto_mode=false
```

#### 通用规则

**回滚循环限制：** 蓝图配置 `design_flaw` 事件的最大循环次数 max_loops（full=2, lite=1）。达到上限后 HSM 自动进入 pause 状态，等待人工介入。调用 `python3 skills/pdf/tools/pdf-engine.py hsm unpause` 后继续。

```
python3 skills/pdf/tools/pdf-engine.py pipeline pass-checkpoint plan PLAN_APPROVE  # 或 reject-checkpoint
```

---

### 约束检查（Plan 过渡前）

Plan 阶段过渡到下一阶段前，引擎自动执行约束门验证。LLM 也可手动触发验证确认完整性：

```
python3 skills/pdf/tools/pdf-engine.py constraint verify --stage plan
```

**计划阶段约束（来自 `docs/topology/constraints.yaml`）：**

| 约束名 | 类型 | 目标 | 失败行为 |
|--------|------|------|---------|
| `plan_decisions_exist` | file_exists | `plan_decisions.yaml` | block（阻止过渡） |
| `plan_md_exists` | file_exists | `plan.md` | block（阻止过渡） |
| `plan_decisions_consistency` | m_value_consistency | — | warn（记录警告，不阻断） |

约束未通过（block）→ 引擎标记当前阶段失败，触发 HSM pause。LLM 需修复缺失产物后重新执行 `constraint verify`。

---

### Plan 阶段过渡

Plan 阶段完成后评估结果：

**1. design_approved（进入 Do 阶段）：**
- 所有约束通过
- 用户确认通过
- 引擎执行 HSM 事件：
  ```
  python3 skills/pdf/tools/pdf-engine.py hsm event design_approved
  ```
- 蓝图引擎自动推进到 Do 阶段
- Plan 产物（`plan_decisions.yaml`、`plan.md`）被 Do 阶段 entry_gate 消费为前置条件

**2. design_flaw（回滚到 P1_design）：**
- 用户驳回或 reviewer 发现 blocker
- 引擎执行 HSM 事件：
  ```
  python3 skills/pdf/tools/pdf-engine.py hsm event design_flaw
  ```
- 引擎自动重置 `[plan.P1_design, plan.P2_review, plan.P3_converge, plan.PLAN_APPROVE]` 节点状态
- P0 节点保持完成状态（不重置）
- 循环计数 +1。若已达 max_loops=2 上限 → HSM pause

**产物清理：** Plan 过渡后，`plan_decisions.yaml` 和 `plan.md` 被 Do 阶段 entry_gate 消费为入口条件。Plan artifacts 在 Do 阶段结束时由引擎自动归档压缩：
```
python3 skills/pdf/tools/pdf-engine.py artifact compress
```

## Do 阶段

> **拓扑依赖：** Do 阶段由 `blueprint.yaml` 中的 `do.stage` 固定定义，执行流程为：
> `D0_prepare → D1_doer → D2_review → D3_fix → transition (done / bug / design_flaw_detected)`

**入口条件：** Plan PLAN_APPROVE checkpoint 通过 + HSM `design_approved` 事件触发 → 引擎自动执行 transition + 重置 DAG → `pipeline tick` 返回 `do` 阶段的就绪节点。

**入口栅栏（entry_gate）：** 前置依赖：确认 `plan.md` 存在。Do 阶段入口门禁要求 `plan.md` 就绪。引擎自动验证 `plan.md` 存在，缺失则 `block`。

**整体执行流程（Make-style）：**

```
1. pipeline tick → 返回就绪节点列表
2. LLM 执行每个就绪节点（spawn agent / run engine command / write artifact）
3. LLM 通过 pipeline node-done|fail|skip 上报结果
4. 引擎自动 re-tick → 下一批就绪节点
5. 所有节点 done → pipeline tick 返回 action=stage_done
6. LLM 评估结果后调 hsm event → 引擎执行 transition
7. 引擎 pipeline tick → Do 退出（或 rollback）
```

### D0_prepare — Context Bundle（engine_exec）

**节点类型：** `engine_exec`

**命令：**
```bash
python3 skills/pdf/tools/pdf-engine.py pipeline node-start do D0_prepare
python3 skills/pdf/tools/pdf-engine.py context bundle build do --role doer
python3 skills/pdf/tools/pdf-engine.py pipeline node-done do D0_prepare
```

**说明：** 引擎自动组装标准化上下文包（≤12K chars；中文约 4:1 换算 token，代码约 1:1），包含 `task` / `plan_key_decisions` / `made_findings_summary` / `triggered_factors` / `active_domain` / `mde_state`。输出写入 session 目录，供后续 D1_doer 使用。

**重试策略：** 配置 `retry: 2`，失败自动重试；超限后暂停，需人工介入。

### D1_doer — Spawn N 实现者（llm_spawn）

**节点类型：** `llm_spawn`

**流程：**
1. LLM 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-start do D1_doer`
2. 从 state.plan.N 读取（通道选择阶段已确定）
3. Spawn N 个 doer（模型从引擎配置读取：`python3 skills/pdf/tools/pdf-engine.py config get-model do.doer --domain <domain>`，bg），每人负责不重叠的产出
4. 每个 doer 输出 `do_output_<n>.md`（n = 1..N）
5. 全部完成后调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-done do D1_doer`

**强制 TDD：** 所有 doer 必须先写测试再实现。无 direct 模式。详见 `role-packs/doer.md`。

**Doer Prompt 抑制路径预设（分级规则）：**

- **ALLOWED：** 项目约定提示（错误处理模式、命名规范、模块布局）
- **ALLOWED：** 已知陷阱和反模式（避免踩坑的提示）
- **ALLOWED：** 输入输出接口契约（上游 API 签名、返回类型、异常约定）
- **ALLOWED：** 评估标准（正确性/性能/安全的通过条件，如"API 响应 < 200ms"）
- **FORBIDDEN：** 具体实施步骤或算法选择
- **FORBIDDEN：** 单行级实现指令

Doer prompt 可以给**结构边界**（接口契约、评估标准），但**实现路径**不给（具体的代码行、算法选择、步骤序列）。

**代码产出质量继承（当 doer 生成代码时）：**

非 trivial 输出必须携带质量元数据：
- 非显而易见逻辑 → 注释 `# confidence: medium` / `# assumption: ...`
- 外部来源 → `# source: <文件>:<行号>` 或 `# source: <链接>`
- 禁止裸决策 → 每个非 trivial 的配置/分支/公式必须有 why 注释

**重试策略：** 配置 `retry: 3`。单个 doer 失败重试；全部失败 → `repair_gate` 触发回滚。

**do_output frontmatter 规范：** 每个 `do_output_<n>.md` 文件必须包含 YAML frontmatter：

```yaml
---
id: do_output_<n>
deps: [<依赖的组件/接口>]
confidence: high|medium|low
lens: <视角分类，如 backend/api/frontend/db>
---
```

frontmatter 使下游 reviewer 明确知晓 section 来源视角和依赖范围，支撑并发独立审查。

### D2_review — Spawn M 审查者（llm_spawn）

**节点类型：** `llm_spawn`

**流程：**
1. LLM 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-start do D2_review`
2. 从 state.plan.M 读取（通道选择阶段已确定）
3. 每个 reviewer 读对应 do_output_<n>.md（按 section 独立审查），不同 reviewer 可并行审查不同 section
4. Spawn M 个 reviewer（模型从引擎配置读取），每人覆盖一个触发维度
5. 每个 reviewer 输出 `review_do_<section_id>.md`（含 ✅/❌/N/A checklist + 对应 section id）
6. 所有 reviewer 完成后，LLM 汇总所有 `review_do_*.md`（与 blueprint 声明的产物名一致）
7. 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-done do D2_review`

**通道感知的 reviewer 规则：**

| 通道 | reviewer | reviewer 模型 |
|------|----------|-------------|
| lite | 拓扑无 review 节点，跳过 | — |
| standard | 固定执行 M reviewer | 从引擎配置读取 |
| full | 完整 M reviewer | 从引擎配置读取；M≥5 升级规则由 spawn-config 控制 |

**重试策略：** 配置 `retry: 3`。

### D3_fix — 修复门（repair_gate）

**节点类型：** `repair_gate`

**流程：**
1. LLM 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-start do D3_fix`
2. 收集 D2_review 所有 reviewer 输出
3. 按优先级评估 P1 类型：

| P1 类型 | 优先级 | 处理方式 |
|---------|--------|----------|
| `design_flaw` | 最高 | 设计缺陷 → 归入 design_flaw_detected |
| `bug` | 高 | 实现 bug → 归入 bug |
| `flaky_test` | 中 | 不稳定的测试 → 记录，继续 |
| `false_positive` | 低 | 误报 → 记录，继续 |
| `clean` | — | 无问题 → 归入 done |

4. 输出 `repair_decisions.yaml`，包含分类结果和路由建议
5. 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-done do D3_fix`

**通道感知的修复策略：**

| 通道 | 修复行为 |
|------|---------|
| lite | 跳过修复，P1 记录到结论 |
| standard | 仅处理 bug/design_flaw（1 轮） |
| full | 全功能修复门（含 adversary、flaky_test 重试） |

**重试策略：** 配置 `retry: 3`。

### Do 阶段完成判定

D3_fix 完成后，所有 Do 节点均已 done → `pipeline tick` 返回 `action=stage_done`。

LLM 执行以下评估：

1. **约束检查**（见 `## 约束检查` 章节。引擎自动执行，LLM 也可提前预检）：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py constraint verify --stage do
   ```
   验证 `do_output_*.md` section 文件存在性等约束。

2. **过渡事件选择**：

| 条件 | 事件 | 目标 |
|------|------|------|
| 全部 reviewer clean，无 P1 | `done` | Check 阶段 |
| 存在 design_flaw 类型 P1 | `design_flaw_detected` | 回滚到 Plan 阶段 |
| 存在 bug 类型 P1 | `bug` | 回滚到 D1_doer |

3. **触发事件**：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py hsm event <event_name>
   ```

4. 引擎执行 transition → DAG reset → `pipeline tick` 返回下一阶段就绪节点。

---

## Check 阶段

> **拓扑依赖：** Check 阶段由 `blueprint.yaml` 中的 `check.stage` 固定定义，执行流程为：
> `C1_check → C2_repair_gate → transition (clean / bug / design_flaw)`

**入口条件：** Do 阶段 `done` 事件触发 → 引擎 transition 到 check → `pipeline tick` 返回 check 就绪节点。

**入口栅栏（entry_gate）：** 引擎自动验证 `do_output_*.md` section 文件存在。

### C1_check — Spawn N 校验者（llm_spawn）

**节点类型：** `llm_spawn`

**流程：**
1. LLM 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-start check C1_check`
2. 从 state.plan.N_check 读取（引擎根据 channel-rules.yaml 自动计算，参见 `docs/channel-rules.yaml` 的 n_check 字段）
3. Spawn N 个 checker（模型从引擎配置读取，bg），每人审查对应 section 的 `do_output_<n>.md`
4. 额外 spawn 1 个 global checker，审查跨 section 的切面问题（接口兼容性、整体一致性）
5. 每个 checker 输出 `check_report_<n>.md`，global checker 输出 `check_report_global.md`
6. 全部完成后调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-done check C1_check`

**通道感知的 Check 规则：**

| 通道 | checker | 模型 |
|------|---------|------|
| lite | haiku，1 个 | 从引擎配置读取 |
| standard | haiku，N 个 | 从引擎配置读取 |
| full | sonnet，N 个 | 从引擎配置读取；M≥3→opus |

**重试策略：** 配置 `retry: 3`。

### C2_repair_gate — 修复门（repair_gate）

**节点类型：** `repair_gate`

**流程：**
1. LLM 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-start check C2_repair_gate`
2. 收集所有 `check_report_*.md`，分类 P1
3. 输出 `repair_decisions.yaml`
4. 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-done check C2_repair_gate`

**路由判定（四路路由）：**

| P1 类型 | 事件 | 目标 | 说明 |
|---------|------|------|------|
| 无 P1（clean） | `clean` | Act 阶段 | 全部通过 |
| bug 类型缺陷 | `bug` | Do 阶段（rollback） | 回滚到整个 Do 阶段 |
| design_flaw 类型缺陷 | `design_flaw` | Plan 阶段（rollback） | 回滚到 Plan 阶段 |
| flaky_test | 不触发事件 | 重跑（≤3 次） | 超限后标记 escalated，进入 Act |

**重试策略：** 配置 `retry: 2`。

### Check 阶段完成判定

C2_repair_gate 完成后，所有 Check 节点 done → `pipeline tick` 返回 `action=stage_done`。

LLM 执行以下步骤：

1. **约束检查**（引擎自动执行，LLM 也可提前预检）：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py constraint verify --stage check
   ```

2. **过渡事件选择**（与 C2_repair_gate 结果一致）：

| 条件 | 事件 | 目标 |
|------|------|------|
| 全部 clean | `clean` | Act 阶段 |
| 存在 bug P1 | `bug` | 回滚到 Do 阶段（重置 do.* 所有节点） |
| 存在 design_flaw P1 | `design_flaw` | 回滚到 Plan 阶段（重置 plan.* 所有节点） |

3. **触发事件**：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py hsm event <event_name>
   ```

**max_loops 限制：**
- `bug` → 3 次（do 回滚上限）
- `design_flaw` → 2 次（plan 回滚上限）
- 超限后引擎自动 pause，需人工介入。

---

## Act 阶段

> **拓扑依赖：** Act 阶段由 `blueprint.yaml` 中的 `act.stage` 固定定义，执行流程为：
> `A0_converge → A1_standardize → A2_evolve → transition done → __final__`

**入口条件：** Check 阶段 `clean` 事件触发 → 引擎 transition 到 act → `pipeline tick` 返回 act 就绪节点。

**入口栅栏（entry_gate）：** `check_report_*.md` 不存在 → `warn` 级别提示（不阻断）。

### A1_standardize — 标准化输出（llm_converge）

**节点类型：** `llm_converge`

**流程：**
1. LLM 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-start act A1_standardize`
2. 各 section 自检报告由引擎聚合为表格（汇总各 section 的置信度、未解决矛盾、假设清单）
3. LLM 仅写合成部分：读取引擎聚合表格，编写 `act_report.md` 的概述、综合分析和 Next Cycle Entry 部分
4. 生成 `act_report.md`，内含以下结构：

```markdown
## 执行概要
## 产出记录
## 未解决矛盾（即使空列表也要注明"无矛盾"）
## 置信度标注（每个关键结论标注: 确定/不确定/需验证）
## 量级标注（无裸模糊词，替换为具体数值或锚定）
## 假设清单（至少 1 条显式假设）
## 来源验证（Check 阶段标记的 unsourced claims 已处理）
## Next Cycle Entry
```

5. 自检清单（finalize `act_report.md` 前逐项通过）：

| # | 检查项 | 过关标准 |
|---|--------|----------|
| 1 | 矛盾保留 | `act_report.md` 包含"未解决矛盾"节（即使空列表注明"无矛盾"） |
| 2 | 置信度透明 | 每个关键结论/断言标注了置信度（确定/不确定/需验证） |
| 3 | 量级标注 | 全文无裸模糊词（很多/很快/大量/显著），全部替换为具体数值或锚定 |
| 4 | 假设清单 | 结论中列出显式假设清单（至少 1 条） |
| 5 | 来源验证 | Check 阶段标记的 unsourced claims 已处理（补充来源或降置信度） |

6. 调用 `python3 skills/pdf/tools/pdf-engine.py pipeline node-done act A1_standardize`

**重试策略：** 配置 `retry: 3`。

### A2_evolve — 技术栈经验沉淀（engine_exec）

**节点类型：** `engine_exec`

**命令：**
```bash
python3 skills/pdf/tools/pdf-engine.py pipeline node-start act A2_evolve
# 判断是否有值得沉淀的经验
python3 skills/pdf/tools/pdf-engine.py knowledge append-ts <name> --content "- <经验内容>"
python3 skills/pdf/tools/pdf-engine.py pipeline node-done act A2_evolve
```

**执行条件（LLM 判断）：**
- 本次 Do/Check 阶段是否发现技术栈特定反模式或陷阱？
- 是否有值得跨项目共享的框架使用经验？
- 有 → 执行 `knowledge append-ts` 写入经验
- 无 → 执行 `knowledge append-ts` 写入空记录（标注 "no findings this cycle"）

**自动过程：**
1. `append-ts` 追加到 `~/.fat/pdf/knowledge/tech-stack/<name>.md`（含时间戳和来源项目）
2. 增量更新 FAISS 索引（若 `.rag-index/index.faiss` 存在）

**重试策略：** 配置 `retry: 2`。

### Act 阶段完成判定

A2_evolve done 后 → `pipeline tick` 返回 `action=stage_done`。

1. **约束检查**（引擎自动执行，LLM 也可提前预检）：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py constraint verify --stage act
   ```

2. **过渡事件**：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py hsm event done
   ```

3. 引擎执行 transition → `__final__` → `stage=done` → cycle 完成。

**Cycle 结束清理：**
- 写入 RCA 反馈：本次 N/M 分解准确？维度遗漏？→ 写入记忆
- 对抗结果回灌：成功攻击策略写入 cycle-log
- **领域演化：** 若同一 domain + 同一 P1 类型在 cycle-log ≥3 次 → 生成 `domain_evolution_proposal.yaml`

---

## HSM Event 判定标准

HSM 事件驱动阶段间 transition。引擎在每个 HSM transition 前自动执行 `constraint verify`（见 `## 约束检查` 章节），LLM 也可提前预检。仅当所有 block 级约束通过后才执行 transition。

### 事件总表

| 事件 | 源阶段 | 触发条件 | 目标 | max_loops | 说明 |
|------|--------|---------|------|-----------|------|
| `design_approved` | plan | 所有 Plan 节点 done，`plan.md` 质量可接受，PLAN_APPROVE 人工确认通过 | Plan→Do | 1 | Plan 获批，进入 Do |
| `design_flaw` | plan | Plan review 发现设计缺陷，或 Do/Check 回滚至此 | P1_design 节点 | 2 | 回滚到 P1 重新设计 |
| `done` | do | 全部 Do 节点 done，review 无 P1（clean） | Do→Check | 1 | Do 完成，进入 Check |
| `bug` | do | Do review 发现实现 bug（已分类到 bug 类型） | D1_doer 节点 | 3 | 回滚到 D1 重新实现 |
| `design_flaw_detected` | do | Do review 发现设计级缺陷 | Plan 阶段 | 2 | 回滚到 Plan 重做方案 |
| `clean` | check | Check 全部通过，无 P1 或仅 flaky_test | Check→Act | 1 | 进入 Act 标准化 |
| `bug` | check | Check 发现实现 bug | Do 阶段 | 3 | 回滚到 Do 重新实现 |
| `design_flaw` | check | Check 发现设计缺陷 | Plan 阶段 | 2 | 回滚到 Plan 重新设计 |
| `done` | act | Act 全部节点 done | `__final__` | 1 | Cycle 完成 |

### 判定细则

#### design_approved（Plan→Do）

**必须满足全部条件：**
- [ ] `plan.md` 已生成，非空
- [ ] `plan_decisions.yaml` 已生成，完整性通过引擎验证
- [ ] Channel 已确定（lite/standard/full/analysis/planning）
- [ ] N 和 M 已计算
- [ ] PLAN_APPROVE manual_checkpoint 已通过人工确认（pass-checkpoint）
- [ ] `constraint verify --stage plan` 全部 block 约束通过
- [ ] 无 design_flaw 循环标记（≤1 次重设计）

**当上述条件不满足时：**
- `plan.md` 不完整 → 不触发事件，继续完善
- 人工驳回 checkpoint → `reject-checkpoint` → 自动重置上游节点，重新执行
- 达到 `max_loops` 上限 → 引擎 pause，需人工决定：重置 loops 继续 或 放弃

#### done（Do→Check, Check→Act, Act→\__final__）

**Do→Check（事件 `done`）：**
- [ ] D0-D3 全部标记 done/skipped
- [ ] D3_fix 的 `repair_decisions.yaml` 中所有 P1 为 false_positive 或已修复
- [ ] `do_output_*.md` section 文件存在，且通过约束检查
- [ ] `constraint verify --stage do` 全部 block 约束通过

**Check→Act（事件 `clean`）：**
- [ ] C1-C2 全部标记 done/skipped
- [ ] C2_repair_gate 路由结果为 clean（无 bug/design_flaw P1）
- [ ] `check_report_*.md` section 文件存在
- [ ] `constraint verify --stage check` 全部 block 约束通过

**Act→\__final__（事件 `done`）：**
- [ ] A1-A4 （或 skip）全部标记 done
- [ ] `act_report.md` 存在并包含全部要求的节
- [ ] `constraint verify --stage act` 通过（仅 warn 级别）
- [ ] 领域演化 proposal 已生成（若触发条件满足）

#### bug / design_flaw / design_flaw_detected（回滚事件）

**`bug`（Do→D1_doer / Check→Do）：**
- [ ] D2_review 或 C1_check 明确标注了 `type: bug` 的 P1 项
- [ ] P1 严重程度 ≥ minor
- [ ] 非 design 层面的问题（如实现不符合 spec、边界条件未处理、性能未达标）
- [ ] 如果当前 loops 已 >= max_loops → 引擎自动 pause，不能自动触发

**`design_flaw`（Plan→P1_design）/ `design_flaw_detected`（Do→Plan）：**
- [ ] D2_review 或 C1_check 明确标注了 `type: design_flaw` 的 P1 项
- [ ] 涉及架构决策、接口契约、模块拆分等设计层面问题
- [ ] 需回滚到 Plan 重新做设计
- [ ] 如果当前 loops 已 >= max_loops → 引擎自动 pause，不能自动触发

### max_loops 超限处理

**当引擎返回 `hsm_paused=true`：**

引擎自动将状态置为 `hsm_paused = true`，`pipeline tick` 返回 `action=paused`。

LLM 收到后：
1. 向用户报告暂停原因（哪个 event、哪个 stage、已使用/最大 loops）
2. 等待用户决策：
   - 手动重置 loops：`python3 skills/pdf/tools/pdf-engine.py hsm reset-loops [--event <event>]`
   - 手动 goto 继续：`python3 skills/pdf/tools/pdf-engine.py hsm goto <path>`
   - 或放弃当前 cycle
3. 用户操作后 → `python3 skills/pdf/tools/pdf-engine.py hsm unpause` → 继续执行

---

## Manual Checkpoint 处理流程

蓝图中的 `manual_checkpoint` 节点（如 Plan 阶段的 PLAN_APPROVE）执行后自动阻塞下游，等待人工确认或驳回。

### 执行步骤

**Step 1：引擎将节点标记为 `checkpoint_blocked`**

当 pipeline tick 检测到 checkpoint 节点满足 deps 条件时，引擎返回节点为 ready。LLM 执行该节点时不需要 spawn 子 agent，而是编写确认内容。

**Step 2：LLM 写入确认提示并等待用户**

LLM 向用户展示确认信息，包括：
- 当前阶段完成情况摘要
- 通过了哪些自动检查
- 确认通过会触发什么（下一阶段/什么事件）
- 确认驳回会导致什么（重置哪些节点，触发什么事件）

示例输出：
```
=== 人工确认：PLAN_APPROVE ===
Plan 阶段已完成。
- plan.md 已生成
- plan_decisions.yaml 已验证
- 通道：full，N=2, M=3
- 无设计缺陷

确认通过 → 触发 design_approved 事件，进入 Do 阶段
确认驳回 → 重置上游 P3_converge 节点，重新收敛
请输入 'y' 确认、'n' 驳回：
```

**Step 3：用户确认通过**

用户确认后，LLM 调用：
```bash
python3 skills/pdf/tools/pdf-engine.py pipeline pass-checkpoint <stage> <ref>
```

引擎执行：
- 将 checkpoint 节点标记为 done
- unblock 下游依赖
- 触发 re-tick → 下游节点变为就绪

**Step 4：用户驳回**

用户驳回后，LLM 调用：
```bash
python3 skills/pdf/tools/pdf-engine.py pipeline reject-checkpoint <stage> <ref>
```

引擎执行：
- 重置 checkpoint 节点和全部上游节点为 pending
- 触发 re-tick → 上游节点重新执行
- 如果用户希望触发特定 HSM 事件（如重做设计），LLM 在 reject 后手动触发：
  ```bash
  python3 skills/pdf/tools/pdf-engine.py hsm event design_flaw
  ```

### 多个 checkpoint 的处理

- 一个阶段可以有多个 checkpoint 节点
- 每个 checkpoint 独立阻塞各自的下游
- 一个 checkpoint 的 reject 不会影响同一阶段其他已通过的 checkpoint
- 所有 checkpoint 必须通过才能进入 stage_done

---

## 约束检查

约束系统定义在 `docs/topology/constraints.yaml`，引擎在执行 HSM transition 前自动执行。

### 约束列表

| 约束名 | 类型 | 阶段 | 触发时机 | on_fail |
|--------|------|------|---------|---------|
| `plan_decisions_exist` | file_exists | plan | plan_exit 前 | block |
| `plan_md_exists` | file_exists | plan | plan_exit 前 | block |
| `do_sections_exist` | file_exists | do | do_exit 前 | block |
| `check_reports_exist` | file_exists | check | check_exit 前 | block |
| `act_report_exists` | file_exists | act | act_exit 前 | warn |
| `plan_decisions_consistency` | m_value_consistency | plan | plan_exit 前 | warn |

### 手动检查（可选预检）

引擎自动执行约束检查。LLM 也可提前运行作为预检：

```bash
python3 skills/pdf/tools/pdf-engine.py constraint verify --stage <stage>
```

输出包含三元组：
```
all_pass: true/false
violations: [{name, pass, detail, on_fail} ...]
blocking: [{name, detail} ...]  (on_fail=block 的 violations)
```

**决策规则：**
- 存在 `blocking` violations → 不得触发 HSM 事件，必须先修复问题
- 仅有 warn/info violations → 可以触发事件，但 violations 应注入下一阶段 prompt 作为上下文
- `avoid` on_fail → 到达 max_loops 上限后触发 pause

**重复 violation 升级：** 同一约束在 1 小时内连续失败 ≥3 次 → 引擎自动触发 `pause_and_notify`，需人工介入。

---

## 状态与切换

引擎状态读写通过 `python3 skills/pdf/tools/pdf-engine.py state` 命令组管理。

### 状态查看

```bash
# 查看完整状态
python3 skills/pdf/tools/pdf-engine.py state

# 查看特定 key
python3 skills/pdf/tools/pdf-engine.py state get <key>

# 写入 key-value
python3 skills/pdf/tools/pdf-engine.py state set <key>=<value>

# 查看当前 HSM 位置
python3 skills/pdf/tools/pdf-engine.py hsm status

# 查看 DAG 节点进度
python3 skills/pdf/tools/pdf-engine.py pipeline status

# 查看可用 HSM 事件
python3 skills/pdf/tools/pdf-engine.py hsm status  # 返回 available_events 列表
```

### 阶段切换（正常流程）

```
Plan ──design_approved──→ Do ──done──→ Check ──clean──→ Act ──done──→ __final__
```

### 回滚切换

```
Do  ──design_flaw_detected──→ Plan  (重置 plan.*)
Do  ──bug──────────────────→ D1_doer  (重置 do.D1_doer 及下游)
Check ──bug──────────────────→ Do  (重置 do.*)
Check ──design_flaw─────────→ Plan  (重置 plan.*)
```

### DAG 回滚

回滚事件触发后，引擎自动执行 `reset` 逻辑：
- 若 target 为 stage 级（如 `plan` 或 `do`）：重置该 stage 下所有节点为 `pending`
- 若 target 为节点级（如 `do.D1_doer`）：仅重置该节点及下游依赖为 `pending`

引擎在 transition 前执行 `rollback check`：
```bash
python3 skills/pdf/tools/pdf-engine.py rollback check
```

确认后可执行：
```bash
python3 skills/pdf/tools/pdf-engine.py hsm event <event_name>
```

如需手动覆盖（调试/异常恢复）：
```bash
python3 skills/pdf/tools/pdf-engine.py hsm goto <path>       # 强制设置 HSM 路径
python3 skills/pdf/tools/pdf-engine.py hsm unpause           # 清除 pause 状态
python3 skills/pdf/tools/pdf-engine.py hsm reset-loops       # 重置 loops 计数器
```

### HSM Pause 状态的切换

当引擎返回 `paused` 状态时，LLM 必须：
1. 向用户报告 pause 原因
2. 提供操作选项（goto / reset-loops / unpause）
3. 等待用户指令后执行 `python3 skills/pdf/tools/pdf-engine.py hsm unpause`

---

## --resume

**最高优先级入口：** 如果调用包含 `--resume <task_slug>`，直接进入恢复流程，跳过所有自动检测。

### 恢复流程

1. 引擎自动定位 session 目录：
   ```bash
   # 引擎自动从 task_slug 匹配 session
   python3 skills/pdf/tools/pdf-engine.py state
   ```

2. 读取状态：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py pipeline status
   ```
   输出当前 HSM path、stage、各节点完成状态。

3. 注入恢复上下文：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py state get hsm_path
   python3 skills/pdf/tools/pdf-engine.py pipeline status
   ```
   将输出注入 prompt 作为 "session 恢复上下文"（包含当前 stage、已完成节点、pending 节点、产物状态）。

4. 执行 `pipeline tick` 恢复执行：
   ```bash
   python3 skills/pdf/tools/pdf-engine.py pipeline tick
   ```
   - 返回 `nodes_ready` → 从就绪节点继续
   - 返回 `stage_done` → 评估结果后触发 HSM event 继续
   - 返回 `paused` → 通知用户 pause 原因，等待指令

### 恢复场景

| 场景 | 恢复行为 |
|------|---------|
| 恢复时处于 stage 中间（有 pending 节点） | `pipeline tick` 返回就绪节点，从断点继续 |
| 恢复时处于 stage_done 状态 | 需要 LLM 重新评估结果后触发 HSM event |
| 恢复时处于 hsm_paused 状态 | 通知用户，等待手动 unpause |
| 所有节点全部 done，stage=done | 输出最新 `act_report.md`，提示 cycle 已完成 |
| 无 session 状态文件 | 提示"无 checkpoint 可恢复"，退出 |

### 自动恢复（无 `--resume`）

当不使用 `--resume` 但检测到活跃 session 时，按以下强度判定：

| 强度 | 条件 | 行为 |
|------|------|------|
| 强相关 | 同一主题/模块/问题域，task 字段高度重叠 | 自动 `--resume`，输出确认 |
| 弱相关 | 同一语言/大领域但不同具体问题 | 询问用户确认 |
| 无关 | 完全不同话题 | 正常进入新 session |

---

## 使用

### 基础用法

```
/pdf "添加用户登录功能"                         # 完整 PDCA（自动检测或创建 session）
/pdf "分析当前代码库安全缺陷" --plan-only        # 仅 Plan（只读分析）
/pdf "重构支付模块" --session-slug payment-refactor  # 指定自定义 session slug
/pdf --resume payment-refactor                  # 从 checkpoint 恢复
/pdf "新功能" --new                              # 强制新任务，跳过活跃 session 检测
/pdf "追加需求" --session-slug big --append      # 追加新任务到已有 plan（v5.0）
/pdf "追加" --session-slug big --append --no-detect-overlap  # 跳过文件重叠检测
/pdf "分析日志" --blueprint lite                 # 使用指定蓝图（覆盖 auto-select）
/pdf "重构核心" --blueprint full                  # 直接指定 full 蓝图
```

### 引擎命令速查

**Pipeline 执行：**
```bash
python3 skills/pdf/tools/pdf-engine.py pipeline tick                    # 获取就绪节点列表
python3 skills/pdf/tools/pdf-engine.py pipeline node-start <stage> <ref>  # 标记节点开始执行
python3 skills/pdf/tools/pdf-engine.py pipeline node-done <stage> <ref>   # 标记节点完成
python3 skills/pdf/tools/pdf-engine.py pipeline node-fail <stage> <ref>   # 标记节点失败
python3 skills/pdf/tools/pdf-engine.py pipeline node-skip <stage> <ref>   # 标记节点跳过（引擎自动错误恢复时使用）
python3 skills/pdf/tools/pdf-engine.py pipeline status                   # 查看全部阶段各节点状态
python3 skills/pdf/tools/pdf-engine.py pipeline summary                  # 一行摘要
```

**HSM 状态管理：**
```bash
python3 skills/pdf/tools/pdf-engine.py hsm status              # 查看 HSM 路径 + 可用 events + loop counts
python3 skills/pdf/tools/pdf-engine.py hsm event <name>        # 触发 transition 事件
python3 skills/pdf/tools/pdf-engine.py hsm goto <path>         # (DEBUG) 强制跳转到指定 HSM 路径
python3 skills/pdf/tools/pdf-engine.py hsm unpause             # 清除 pause 状态
python3 skills/pdf/tools/pdf-engine.py hsm reset-loops         # 重置 loops 计数器
```

**Checkpoint：**
```bash
python3 skills/pdf/tools/pdf-engine.py pipeline pass-checkpoint <stage> <ref>    # 人工确认通过
python3 skills/pdf/tools/pdf-engine.py pipeline reject-checkpoint <stage> <ref>   # 人工驳回重做
```

**约束检查：**
```bash
python3 skills/pdf/tools/pdf-engine.py constraint verify [--stage <stage>]   # 约束完整性检查
python3 skills/pdf/tools/pdf-engine.py blueprint validate                     # 蓝图完整性验证
```

**回滚：**
```bash
python3 skills/pdf/tools/pdf-engine.py rollback check     # 回滚前预检（查看影响范围）
python3 skills/pdf/tools/pdf-engine.py rollback exec      # 执行回滚（调试/手动模式）
python3 skills/pdf/tools/pdf-engine.py rollback cancel    # 取消回滚
```

---

## Session 管理命令

```bash
python3 skills/pdf/tools/pdf-engine.py session list                 # 列出所有 session
python3 skills/pdf/tools/pdf-engine.py session current              # 当前 session 信息
python3 skills/pdf/tools/pdf-engine.py session switch <id>          # 切换活跃 session
python3 skills/pdf/tools/pdf-engine.py session create <slug>        # 手动创建 session
python3 skills/pdf/tools/pdf-engine.py session delete <id>          # 删除 session
```

## Project 管理命令（v5.0）

```bash
python3 skills/pdf/tools/pdf-engine.py project init [--name <name>] [--force]    # 初始化项目状态
python3 skills/pdf/tools/pdf-engine.py project status                            # 项目级概览
python3 skills/pdf/tools/pdf-engine.py project add-task <slug> --desc "..."      # 注册新任务
python3 skills/pdf/tools/pdf-engine.py project close-task <slug>                 # 关闭任务
python3 skills/pdf/tools/pdf-engine.py project rebuild                           # 从 session 重建项目状态
python3 skills/pdf/tools/pdf-engine.py project verify                            # 一致性校验
python3 skills/pdf/tools/pdf-engine.py project archive                           # 归档已完成任务
```

## Plan / DAG 命令

```bash
# Plan 命令（v5.0）
python3 skills/pdf/tools/pdf-engine.py plan append --slug <slug> --new-task "..."  # 追加任务到 plan
python3 skills/pdf/tools/pdf-engine.py plan list <slug>                            # 列出所有 plan 版本
python3 skills/pdf/tools/pdf-engine.py plan current <slug>                         # 当前 plan 版本
python3 skills/pdf/tools/pdf-engine.py plan diff --v1 N --v2 M                     # 比较 plan 版本

# DAG 基础（v5.0）
python3 skills/pdf/tools/pdf-engine.py dag build <plan.md>                         # 构建 DAG
python3 skills/pdf/tools/pdf-engine.py dag status [--stub-states <json>]           # DAG 状态（+stub 列）
python3 skills/pdf/tools/pdf-engine.py dag check <plan.md> [--stub-states <json>]  # 校验 DAG 完整性
python3 skills/pdf/tools/pdf-engine.py dag next                                    # 下一可执行层级
python3 skills/pdf/tools/pdf-engine.py dag visualize <plan.md>                     # ASCII 拓扑图

# DAG Stub 命令组（v5.1）
python3 skills/pdf/tools/pdf-engine.py dag stub inject --plan <plan.md>             # 注入上游 stub
python3 skills/pdf/tools/pdf-engine.py dag stub status                             # stub 状态表
python3 skills/pdf/tools/pdf-engine.py dag stub show <task-slug>                   # 查看单个 stub
python3 skills/pdf/tools/pdf-engine.py dag stub replace <task-slug>                # 替换为真实实现
python3 skills/pdf/tools/pdf-engine.py dag stub notify [task-slug]                 # 通知管理
python3 skills/pdf/tools/pdf-engine.py dag stub mapping [--rebuild]                # 映射表
python3 skills/pdf/tools/pdf-engine.py dag stub cleanup [--dry-run]                # 清理过期 stub
python3 skills/pdf/tools/pdf-engine.py dag stub config <task-slug>                 # 通知配置
```

## Memory 命令

```bash
python3 skills/pdf/tools/pdf-engine.py memory inspect [--project <path>]       # 记忆文件详情
python3 skills/pdf/tools/pdf-engine.py memory list [--project <path>]          # 列出可注入条目（v5.0）
python3 skills/pdf/tools/pdf-engine.py memory inject [--project <path>]        # 手动注入记忆（v5.0）
python3 skills/pdf/tools/pdf-engine.py memory ignore add <key>                 # 添加忽略条目（v5.0）
python3 skills/pdf/tools/pdf-engine.py memory ignore remove <id|key>           # 移除忽略条目（v5.0）
python3 skills/pdf/tools/pdf-engine.py memory ignore list                      # 列出忽略列表（v5.0）
python3 skills/pdf/tools/pdf-engine.py memory update <key> "<value>"           # 写入/更新记忆条目（v5.1）
python3 skills/pdf/tools/pdf-engine.py memory update <key> "<value>" --yes     # 跳过确认直接写入（v5.1）
python3 skills/pdf/tools/pdf-engine.py memory update <key> "<value>" --dry-run # 预览不写入（v5.1）
```

---

## 旧命令迁移对照

以下旧命令已在新引擎中删除或替换。撰写 SKILL.md 或与引擎交互时使用新命令：

| 旧命令 | 新命令 | 说明 |
|--------|--------|------|
| `exit-check <stage>` | `constraint verify --stage <stage>` | 约束检查替代退出清单 |
| `stage next` | `hsm event done` | 直接用 HSM 事件推进 |
| `stage back` | `hsm event bug` / `hsm event design_flaw` | 回滚也通过 HSM 事件驱动 |
| `gate check` | — | 删除，由 HSM max_loops 自动处理 |
| `gate inc` | — | 删除，回滚 loop 由引擎自动计数 |
| `agent spawn <id> <role>` | `pipeline node-start <stage> <ref>` | 节点开始由 pipeline 管理 |
| `agent done <id> <artifact>` | `pipeline node-done <stage> <ref>` | 节点完成上报 |
| `gate forgery check` | 内置 `pipeline node-done` | 引擎自动检测 <2s 快速 done |
| `checkpoint update --phase <phase>` | 仅 Plan 阶段使用 | Plan 阶段进度标记（Do/Check/Act 由 pipeline_tick + node-done 自动追踪） |
| `decisions-diff <f1> <f2>` | 引擎内置命令 | 在 P3_converge 中用 `decisions-diff` 手动对比；`llm_merge` 节点自动合并时无需手动调用 |

---

## 边缘情况

### N=1, M=1 — 最小模式

所有阶段走简化路径：D0→D1_doer(1) → D2_review(1) → D3_fix → Check C1→C2 → Act A0_converge→A1_standardize→A2_evolve。

### 过拆分检测

>1 doer 改同文件 → 冲突由各 section 的 reviewer 标注，Check 阶段 global checker 仲裁版本。

### 维度重叠

多 reviewer 报告同问题 → 按根因去重，保留高严重性。

### Agent 故障

- 1 个 agent 失败 → 重试（按 blueprint 配置的 retry 次数）
- 重试超限 → 标记节点为 `failed`，`pipeline tick` 返回 `nodes_failed`
- 全部 agent 失败 → 退化为 PLL（Plan-LLM 直接输出），report 用户

### 中期升级

Do 阶段发现 Plan>2K LOC → 评估是否需要递归分解。

### 递归分解

详见 `docs/child-team.md`。depth-N child 隔离执行，contract violation → respawn。

### 领域冷启动

新领域先用 general 跑 3-5 周期 → Act 分析 → 生成 v0.1 domain 文件。
