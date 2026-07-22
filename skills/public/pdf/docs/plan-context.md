# plan_context.yaml Schema

当前周期有效，不持久化。Plan 阶段收敛后由主 agent 从 `plan_decisions.yaml` 生成，注入所有 reviewer spawn prompt。

## Schema

```yaml
# 任务类型（来自 Plan 收敛结论）
task_type: "<type>: <description>"

# 波及模块列表
affected_modules:
  - "<module/path>"

# Plan 阶段做出的关键设计决策
key_decisions:
  - "<decision description>"

# 各维度领域审查关注点（每维度 2-5 条）
dimension_concerns:
  <dimension>:
    - "<concern description>"

# 代码启发式模式（grep 搜索模式 + 风险说明）
code_heuristics:
  - pattern: "<grep regex>"
    risk: "<risk description>"

# 关联历史发现（cycle-log, convention-patterns 等）
cross_references:
  - "<reference>"
```

## 维度列表

来自 `docs/risk-profile.md`：correctness, security, performance, api_design, test_quality, maintainability, reliability, data_privacy

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `task_type` | 是 | 简短分类，如 `bug_fix: double-charge on payment retry` |
| `affected_modules` | 是 | 列出本次改动波及的全部模块路径 |
| `key_decisions` | 否 | Plan 阶段的关键设计决策，0-5 条 |
| `dimension_concerns` | 是 | 每个维度 2-5 条。仅包含当前 M 选择的维度；`correctness` 始终包含 |
| `code_heuristics` | 否 | grep 搜索模式 + 风险，0-5 条 |
| `cross_references` | 否 | 关联 cycle-log 或 convention-patterns 中的历史发现，0-3 条 |

## 示例

取自 `plan_decisions.yaml` 中的权威决策：

```yaml
# Plan 阶段生成，当前周期有效，不持久化
task_type: "bug_fix: double-charge on payment retry"
affected_modules: ["payment/gateway", "payment/webhook"]
key_decisions:
  - "retry uses exponential backoff with idempotency key"
  - "webhook dedup via Stripe Idempotency-Key header"
dimension_concerns:
  correctness:
    - "idempotency: payment intent must be processable at most once"
    - "concurrent retry + webhook callback: verify no race on charge state"
    - "partial refund calculation: rounding mode consistent with charge?"
  reliability:
    - "gateway timeout: does timeout leave transaction ambiguous?"
    - "dead letter queue for failed webhook delivery"
  security:
    - "Stripe API key rotation: hardcoded keys in test fixtures?"
    - "webhook signature verification: verify Stripe-Signature header check"
  data_privacy:
    - "PII in webhook logs? Strip card fingerprint from debug output"
    - "PCI-DSS scope: payment data must not be stored in application database"
code_heuristics:
  - pattern: "float64.*amount|float32.*price"
    risk: "monetary values in float"
  - pattern: "\\.Save\\(\\)|\\.Update\\(\\)"
    risk: "check for missing idempotency guard before write"
cross_references:
  - "cycle-log-2026-05-15: auth changes broke billing notification — verify webhook auth"
```

## 与 reviewer 的交互

每个 reviewer spawn prompt 变为：

```
你是 {维度} reviewer。检查清单：[维度 checklist]。

## 当前任务背景
{plan_context 全文}

## 你的维度关注点
{plan_context.dimension_concerns.{维度} 的摘取项}

## 审查目标
{do_output.md 或当前阶段的产物}
```

维度 checklist 提供通用覆盖，plan_context 提供领域深度。两者互补。

## 生命周期

```
Plan 收敛
  ↓
主 agent 从 plan_decisions.yaml 生成 plan_context.yaml（1 次 LLM 调用）
  ↓
plan_context 注入到所有 reviewer 的 spawn prompt
  ↓
每个 reviewer 收到: [维度 checklist] + [plan_context（维度相关部分高亮）]
  ↓
reviewer 完成审查 → plan_context 随周期结束丢弃
  ↓
有价值的发现 → Act 阶段提升到现有 KB（cycle-log, convention-patterns）
```

- **创建时机**: Plan 阶段收敛后、reviewer spawn 前
- **消费时机**: 本周期所有 reviewer spawn 时
- **销毁时机**: 本周期结束（下周期重新生成）
- **持久化**: 不持久化。有价值发现通过 Act 阶段进入 cycle-log / convention-patterns
