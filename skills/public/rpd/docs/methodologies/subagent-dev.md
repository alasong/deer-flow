---
name: "Subagent-Driven Development"
applies_to:
  phase: "D"
  modes: ["implement", "design"]
authority: "guidance"
---

# Subagent-Driven Development（子代理驱动开发）

## 核心原则

将实现计划拆解为独立任务，为每个任务派发全新的 subagent，每个任务完成后进行**两阶段审查**（spec compliance review → code quality review）。

```
Fresh subagent per task + Two-stage review (spec → quality) = 高质量、快迭代
```

**为什么用 subagent：** 每个 subagent 拥有独立的、精心构造的 context，不会被 controller 的对话历史污染。Controller 在派发前提取所有任务文本和上下文，subagent 拿到完整信息即可全速前进。

## 核心流程

```
Read plan → 提取所有任务（含完整文本和上下文）→ 创建 TodoWrite
  └→ 对每个任务：
       1. Dispatch implementer subagent（含完整任务文本 + context）
       2. Implementer 可提问，回答后继续
       3. Implementer 实现、测试、self-review
       4. Dispatch spec compliance reviewer subagent
       5. 未通过 → Implementer 修复 → 回到 4（re-review）
       6. Dispatch code quality reviewer subagent
       7. 未通过 → Implementer 修复 → 回到 6（re-review）
       8. 标记任务完成 → 继续下一个任务
  └→ 所有任务完成后，dispatch final reviewer 进行全局审查
```

### 关键规则

- **连续执行（Continuous execution）：** 不要在任务之间停下来询问人类"是否继续"。除非遇到 BLOCKED 状态无法解决、关键歧义、或所有任务完成，否则一直执行。进度汇报和"Should I continue?"对用户来说是噪声。
- **审查顺序不可颠倒：** spec compliance review 必须在 code quality review 之前。Spec 不通过谈 code quality 没有意义。
- **绝不跳过审查（Never skip reviews）：** 任何审查步骤都不能跳过。两者都通过后才算任务完成。
- **审查闭环：** reviewer 发现问题 → implementer 修复 → reviewer 再次审查 → 直到 approved。不能跳 re-review。
- **Implementer self-review 不能替代正式审查：** self-review 是质量内建的一环，但外部 reviewer 的双重审查不可省略。

## Model Selection（模型选择）

遵循"用最便宜能用的模型"原则，根据任务复杂度分级：

| 复杂度 | 描述 | 模型建议 |
|--------|------|----------|
| 机械实现 | 单文件、1-2 文件、spec 明确、纯实现 | 快速低成本模型 |
| 集成任务 | 多文件协作、模式匹配、调试 | 标准模型 |
| 架构/设计/审查 | 需设计判断或广泛代码库理解 | 最强可用模型 |

**复杂度信号：**
- 涉及 1-2 文件 + 完整 spec → cheap model
- 多文件 + 集成关注点 → standard model
- 需设计判断或广域代码理解 → 最强大模型

## Subagent Status 处理

Implementer subagent 返回四种 status，需要正确应对：

**DONE：** 正常完成，进入 spec compliance review。

**DONE_WITH_CONCERNS：** 完成但有疑虑。先阅读 concerns 再决定是否继续。如果 concerns 是关于正确性或 scope，先解决再 review。如果只是观察性备注（如"这个文件变大了"），记下即可继续。

**NEEDS_CONTEXT：** 缺少信息。提供缺失的 context 后重新派发同一 subagent。

**BLOCKED：** 无法完成。评估阻塞原因：
1. Context 问题 → 补充 context，同一模型重新派发
2. 需要更强推理 → 换更强大的模型重新派发
3. 任务太大 → 拆分为更小的子任务
4. 计划本身有问题 → 上报给人类

**绝对不要**忽略 escalation，或在没有任何改变的情况下让同一模型重试。Implementer 说卡住了，说明需要改变。

## Subagent 交互规范

**如果 subagent 提问：**
- 清晰完整地回答
- 如有必要补充额外 context
- 不要催促其进入实现

**如果 reviewer 发现问题：**
- 同一 implementer subagent 修复
- Reviewer 再次审查
- 重复直到 approved
- 不要跳过 re-review

**如果 subagent 失败：**
- 派发 fix subagent 并附上具体修复指令
- 不要手动修复（会导致 context pollution）

## 注意事项

- Subagent 不应读取 plan 文件——controller 应提供完整文本
- 不要忽略 subagent 的提问——回答完再让其继续
- 不要在 spec 不通过时接受"差不多就行"
- 不要让 implementer 的 self-review 替代外部 review
- 不要在任一 review 有 open issues 时进入下一个任务
- 不要同时派发多个 implementer subagent（会导致冲突）
- Subagent 需要场景上下文（scene-setting context）——让它理解当前任务在整个计划中的位置
- 这是**指南**不是**教条**，根据实际情况调整
