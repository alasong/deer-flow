# PDF 强引擎改造 — 差距分析与 TODO

## 当前状态

**已完成：**
- 引擎基础设施（dag/hsm/runner/rollback/constraints）
- 蓝图拓扑 + 约束规则（blueprint.yaml / constraints.yaml）
- 旧 LLM 驱动代码（cmd_stage/cmd_agent_spawn/cmd_agent_done/cmd_exit_check/cmd_gate）已删除
- **SKILL.md 已重写** — 从 PDE 拷贝改为蓝图驱动执行模型
  - pipeline_tick → execute → node-done 循环
  - node type 定义（engine_exec / llm_spawn / llm_converge / llm_merge / repair_gate / manual_checkpoint）
  - HSM event 判定标准 + manual_checkpoint 处理流程
  - 旧命令引用已修复（exit-check → constraint verify, stage → hsm event 等）
  - 旧 PDE 流程已删除（P-1 → P3、N+M spawn 模板、MADE、sfagent 等）

---

## P1 — 重要

- [x] **验证 N/M/通道在蓝图驱动模式下的映射**：当前的 P1_design/P2_review 参考了 old N/M，确认 blueprint 是否需要调整
- [x] **节点级 retry 自动执行**：blueprint 定义了 retry 次数，引擎自动处理而非 LLM 手动 node-fail → tick（当前 node-fail 不会 auto-tick）

## P2 — 锦上添花

- [x] **引擎 auto-tick on node-fail**：node-fail 后自动 tick 检查 retry readiness，减少 LLM 手动调用
- [x] **约束门禁 auto-check**：HSM fire_event() 内部调用 verify_constraints，不再 LLM 手动
- [x] **pipeline state 与 session/resume 集成**：resume 时恢复 pipeline 节点状态而非旧的 phase checkpoint

## 已关闭

- ~~重写 SKILL.md 为蓝图驱动执行模型~~ ✅
- ~~定义 node type LLM 执行指南~~ ✅
- ~~写入 pipeline 执行循环~~ ✅
- ~~写入 HSM event 判定标准~~ ✅
- ~~写入 manual_checkpoint 处理流程~~ ✅
- ~~修复引用旧命令的全部位置~~ ✅
- ~~删除 SKILL.md 中旧的 PDE 流程~~ ✅

---

## P0 — 分片化改造（消除串行瓶颈）

最大收益：删除 D1_merge，doer 输出独立碎片，Act 阶段工具合并。预计节省 ~13min。

### Do 阶段

- [x] **删除 D1_merge 节点** — `blueprint.full.yaml`，doer 输出独立 section 文件，不合并
- [x] **D1_doer 输出加 frontmatter** — `blueprint.full.yaml` + SKILL.md，每个 do_output 带 `---` frontmatter（id, deps, confidence, lens）
- [x] **D2_review 改并行分片审** — `blueprint.full.yaml`，deps: [D1_doer]，每个 section 独立审查并行
- [x] **D3_fix 改分片决策** — `blueprint.full.yaml`，每个 section 的 review 独立路由 fix/clean

### Check 阶段

- [x] **C1_check 改分片并行** — `blueprint.full.yaml`，N 个 checker 各自审自己的 section，+1 全局 checker
- [x] **C2_repair_gate 改分片路由** — `blueprint.full.yaml`，分片独立路由，clean 放行

### Plan 阶段

- [x] **P3_converge 分片化** — `blueprint.full.yaml` + SKILL.md，designer 输出 frontmatter 引擎合并 + LLM 只写 plan.md 正文

### Act 阶段

- [x] **新增引擎 converge 命令** — `pdf-engine.py converge`，机械合并所有 section → merged.md，<1s
- [x] **A1_standardize 分片汇总** — `blueprint.full.yaml` + SKILL.md，section 自检报告→引擎汇总表格，LLM 只写综述

## P1 — 补齐引擎命令（blueprint.full.yaml 已加节点，缺命令实现）

blueprint 已新增 11 个 Plan 节点，以下 8 个引擎命令待实现：

- [x] `pdf-engine.py sanitize run` — 输入清洗（去偏置词），参考 PDE P-0.25（已存在）
- [x] `pdf-engine.py archive context-inject` — 历史 archive 注入，参考 PDE P0.42（已存在）
- [x] `pdf-engine.py knowledge search` — 技术栈语义检索，参考 PDE P0.45（已存在）
- [x] `pdf-engine.py intel inject` — WebSearch 情报注入，参考 PDE P0.5
- [x] `pdf-engine.py factor analyze` — 内置因子匹配引擎（6 因子），参考 PDE P0.54
- [x] `pdf-engine.py meta review` — 框架偏误审计，参考 PDE P0.56
- [x] `pdf-engine.py decisions merge` — frontmatter 引擎合并，参考 PDE P1（已存在）
- [x] `pdf-engine.py density-check` — 信息密度检查，参考 PDE P1

### 边界问题

- [x] 分片化后 entry_gate 约束改为检查多个 section 文件而非单个 merged.md
- [x] Do → Check 过渡 entry_gate `artifacts: [merged.md]` → `artifacts: [do_output_*.md]`
- [x] Check → Act 过渡 entry_gate `artifacts: [check_report.md]` → `artifacts: [check_report_*.md]`

## P2 — 其他优化方向

| # | 方向 | 做法 | 收益预期 | 复杂度 |
|---|------|------|----------|--------|
| - [ ] | **增量执行** | DAG 按输入 hash 跳过已完成的节点 | N 越大越明显 | 高 |
| - [ ] | **模型分级精选** | task_type=analysis 用 sonnet 替代 opus（仅在事实校验升 opus） | 成本降 30-50% | 低 |

