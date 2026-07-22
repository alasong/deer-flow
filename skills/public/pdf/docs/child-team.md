# Child Team — 递归分解

任务超单 PDCA 能力时，Parent 分解为独立 sub-task，spawn K 个 child team（各自 PDCA），Parent 集成验证。

## Depth-N 递归（硬上限=4）

```
L0: Parent     架构师+集成者，不写代码
L1: Team ×K   各自 PDCA（如 scope>5K LOC → 继续分解为 L2）
L2: Squad ×K  各自 PDCA（如 scope>5K LOC → 继续分解为 L3）
L3: Doer ×K   各自 PDCA（最细粒度，不再分解）
```

**收敛条件：** child scope <5K LOC 则停止分解，作为 leaf 执行正常 PDCA。
**硬上限：** max depth=4。达到 depth=4 后无论 scope 多大都不再分解。
**子级不能再分 →** 向上报告，parent 重做 Decompose 拆分 scope。

## 触发（Plan 判定）

两 planner 都判 T1-T6→分解。一方→走 Plan 收敛→Round2→仲裁。

| # | 条件 | 说明 |
|---|------|------|
| T1 | doer scope>1K LOC | 该doer工作量值独立PDCA |
| T2 | 有传递依赖 | 不可并行，child team可串行 |
| T3 | 总改动>3K LOC | 单parent合并O(N²)过高 |
| T4 | 跨服务/仓库 | 各服务一个child |
| T5 | 风险剖面差异大 | auth需security，logging需maintainability |
| T6 | 组件间接口不确定 | 接口自身需PDCA稳定 |

不触发：N≤3且scope<500 LOC；紧耦合单体；紧急修复。

## Parent 阶段

| 阶段 | 做 | 不做 |
|------|----|------|
| Meta-Plan | 标准Plan+分解+child识别+scope分配 | 不写代码 |
| Decompose | 细化scope+产出contract+设workspace | 不写代码 |
| ChildExec | spawn child+监控+处理失败 | 不写代码 |
| Meta-Check | 接口合规+集成正确性+无回归+无重叠 | 不重检child代码 |
| Meta-Act | 跨child标准化+清理+合并 | — |

## 契约

`.fat/pdf/contracts/child_contract.yaml`，child启动前锁定：

```yaml
shared_interfaces:
  - name: AuthAPI
    owned_by: child-auth
    file: api/auth/types.go
    consumed_by:
      - { child: child-billing, symbols: [ValidateToken, GetUserID] }

child_teams:
  - id: child-auth
    owns: [api/auth/, internal/auth/]
    reads: [api/shared/types.go]
    must_not_modify: [api/billing/, internal/billing/]
    provides: [AuthAPI]
    depends_on: []
    risk_profile: { dimensions: [correctness, security], m: 2 }
    task_spec: "JWT鉴权，暴露ValidateToken/GetUserID"

  - id: child-billing
    owns: [api/billing/, internal/billing/]
    reads: [api/auth/types.go, api/shared/types.go]
    must_not_modify: [api/auth/, internal/auth/]
    depends_on: [child-auth]
    risk_profile: { dimensions: [correctness], m: 1 }
    task_spec: "账单查询+支付回调"
```

## 隔离

```
.fat/pdf/work/
  child-auth/
    api/auth/types.go     ← owned源文件
    .fat/pdf/              ← child自己的过程文件
  child-billing/
    .fat/pdf/              ← 隔离
```

默认 workspace 目录；编译型项目可选 git worktree。

## Meta-Check

只验集成属性（不重检child代码质量）：

| 维度 | 检查 |
|------|------|
| interface_compliance | 导出所有承诺符号？ |
| contract_faithfulness | 只改owned？consumed未变？ |
| integration_correctness | 编译/链接？跨child调用？ |
| no_regression | 已有测试通过？ |
| overlap_detection | 无>1 child改同文件？ |

M_meta=min(M_parent, child_count)，通常2-3，sonnet。

## 生成

```
1. 按contract建workspace → owned文件 + .fat/pdf/ + 共享接口(只读)
2. 并行spawn child: "在workspace内跑完整PDD，过程文件→.fat/pdf/"
3. 依赖DAG: 无依赖并行，有依赖等上游done
4. Parent轮询 child-x/.fat/pdf/.pdf_state.json → 全done→Meta-Check
   超时: 30min警告, 45min简化重试, 60min标记failed
```

## 错误

| 级别 | Parent动作 |
|------|-----------|
| Child内部(P1/plan冲突/doer失败) | Child自行处理 |
| P1跨child(contract违反/接口缺失/重叠) | Respawn该child(≤2次/child/cycle) |
| P1设计(contract歧义) | 回Decompose修订(≤2次) |
| P0(集成不可能) | 暂停，用户决定 |

## 状态

```json
{
  "parent_mode": true,
  "decomposition": {
    "contract_path": ".fat/pdf/contracts/child_contract.yaml",
    "child_teams": {
      "child-auth": {"status":"done","workspace":".fat/pdf/work/child-auth/","state_file":"..."},
      "child-billing": {"status":"running","workspace":".fat/pdf/work/child-billing/","state_file":"..."}
    },
    "meta_check": {"status":"pending","p1_count":0},
    "child_dependency_order": [["child-auth"],["child-billing"]]
  }
}
```
新增 stage：`decompose`, `child_exec`, `meta_check`。
