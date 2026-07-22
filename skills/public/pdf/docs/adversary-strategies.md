# Adversary Strategies — 对抗 Agent 攻击策略

## 激活规则

- M>=3 时自动激活
- M=1-2 的低风险任务不激活
- 每个 Check 阶段 spawn 1 个 check-adversary (sonnet)

## 输入

1. plan.md — 了解任务意图
2. do_output.md — 检查实现
3. review_do.md — 了解已知问题
4. cycle-log — 历史失败模式（重点：当前任务涉及的模块）
5. plan_context.yaml — 领域上下文

## 攻击策略分类

### 1. 历史弱点重放
从 cycle-log 提取当前模块的历史 P1 发现，验证同类问题是否再次出现。
如果历史显示 "api/auth 在边界条件上被攻破 2 次"，则专门构造新的边界条件攻击。

### 2. 跨维度交互攻击
寻找维度交叉地带的漏洞 — 安全检查通过 + 性能检查通过 = 组合起来是否有问题。
例如：重试逻辑（reliability） + 支付网关（correctness）= 双重扣款。

### 3. "看起来对但实际错" 攻击
寻找合法但不正确的结果：正确的状态码（200）但错误的语义。
例如：删除操作返回 200 但数据实际未被删除；空列表返回 200 而非 404。

### 4. 并发/竞态攻击
如果涉及任何状态变更，尝试构造并发场景。
例如：同时创建同名资源、同时更新同一条记录、操作途中撤销权限。

### 5. 输入边界攻击
构造输入的边缘情况：空值、零值、超长、非ASCII、特殊字符、注入payload。
针对具体模块调整 — api/auth 关注 token格式，internal/crypto 关注缓冲区。

## 输出格式

单一文件 `attack_results.md`：

```yaml
attacks:
  - strategy: "<攻击类型>: <具体策略>"
    result: SUCCESS|FAIL
    evidence: "<SUCCESS时：具体的失败证据>"
    severity: P1|P2  # SUCCESS时标记严重度
  - strategy: "..."
    result: FAIL
    evidence: "<为什么攻击未奏效>"

deprecated:
  - strategy: "<连续3次FAIL的策略名称>"

next_cycle:
  - recommendation: "<下周期建议的新攻击方向>"
```

## 跨周期进化

对抗 Agent 的攻击结果写入 cycle-log：
- 成功的攻击 → `adversary_success: true`，告知下周期 "这个方向可继续深入"
- 连续 3 次失败的同方向攻击 → 标记为 deprecated，下周期不再使用
- Act 阶段反馈：对抗 Agent 是否发现了 reviewer 未发现的 P1？→ 写入 RCA
- `next_cycle.recommendation` → 下周期 Agent spawn 时注入 prompt，指导攻击方向

## Spawn prompt 模板 (b)

```
你是 PDD Check 阶段的对抗 Agent。

你的目标不是审查 — 是证明这个产出有错。

## 历史弱点（从 cycle-log）
<读 cycle-log，筛选当前任务涉及的模块>
<若 cycle-log 为空（首次运行），跳过此步，专注通用攻击>
- 模块 X: N 次 P1（维度分布）
- 上次成功的攻击策略: <列出>
- 上次 next_cycle.recommendation: <读取>

## 攻击策略（从以下 5 类中选择 3-5 个）
- 历史弱点重放: 验证同类问题是否再次出现
- 跨维度交互攻击: 寻找维度交叉地带的漏洞
- "看起来对但实际错"攻击: 合法但不正确的结果
- 并发/竞态攻击: 构造并发场景
- 输入边界攻击: 空值/超长/非ASCII/注入payload

1. [策略名]（类别: XX）— 针对 <弱点> 的 <攻击>
   - 为什么选这个方向: <证据>
   - 怎么执行: <操作>
2. ...

## 执行攻击
对每个策略：分析产出 → 构造场景 → 判断 SUCCESS/FAIL

## 输出
attack_results.md（格式见输出格式章节）
末尾附 Decisions YAML。
```

## Plan 阶段对抗 Agent

### 激活条件

Plan 阶段 M>=3 且任务涉及安全或可靠性关键路径时 spawn 1 个 plan-adversary (haiku,bg)。

### 与 Check 阶段对抗 Agent 的区别

| 维度 | Check 阶段 | Plan 阶段 |
|------|-----------|-----------|
| 目标 | 证明实现有错 | 标注设计假设风险 |
| 输出 | attack_results.md (SUCCESS/FAIL) | plan_adversary_concerns.md (假设标注) |
| 严重度 | P1/P2（基于发现） | 标注假设的可信度（high/medium/low）|
| 修复成本 | 高（代码已写）| 低（设计阶段发现）|

### Spawn prompt 模板

```
你是 PDD Plan 阶段的对抗 Agent。

你的目标不是审查 plan 的正确性 — 是找出设计中隐含的假设，并在这些假设不成立时标记风险。

## 历史弱点（从 cycle-log）
<读 cycle-log，筛选当前任务涉及的模块>

## 方法论
对于 plan.md 中的每个关键设计决策，问：
1. 这个设计依赖什么外部条件？（第三方服务可用性、数据一致性、用户行为）
2. 如果这些条件不成立，系统会怎样？
3. 有没有更安全的设计可以消除这些假设？
4. 这个假设的失效概率和影响范围是多少？

## 输出
plan_adversary_concerns.md（每个假设风险评估格式如下）

```yaml
assumptions:
  - decision: "<关联的 design decision key>"
    assumption: "<具体假设>"
    if_false: "<假设不成立时的后果>"
    likelihood: high|medium|low
    impact: high|medium|low
    recommendation: "<可选的替代方案或缓解措施>"
```

末尾附 Decisions YAML。
```

```yaml
## Decisions
decisions:
  - key: attacks_attempted
    value: <number>
  - key: attacks_succeeded
    value: <number>
  - key: strategies_deprecated
    value: <list>
```
