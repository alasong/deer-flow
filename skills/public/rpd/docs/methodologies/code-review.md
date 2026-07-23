---
name: "Code Review"
applies_to:
  phase: "C"
  modes: ["review"]
authority: "guidance"
---

# Code Review（代码审查）

## 核心原则

```
Review early, review often.
```

尽早审查、频繁审查，在问题级联放大之前通过 reviewer subagent 捕获它们。reviewer 获得的是**精心构造的工作产物上下文**，而非你的会话历史，这能让 reviewer 聚焦于评估代码本身。

## 何时请求 Review

**Mandatory（必须）：**
- 每个 subagent 驱动的任务完成后
- 完成主要功能（major feature）后
- 合并到 main 分支前

**Optional（可选但推荐）：**
- 卡住时（fresh perspective — 换个视角）
- 重构前（baseline check — 基线检查）
- 修复复杂 bug 后

## 如何调度 Reviewer

### 1. 获取 Git SHA

```bash
BASE_SHA=$(git rev-parse HEAD~1)   # 或 origin/main
HEAD_SHA=$(git rev-parse HEAD)
```

### 2. 派发 Reviewer Subagent

使用 Task 工具（type: general-purpose），提供以下占位信息：

- `{DESCRIPTION}` — 你构建内容的简要总结
- `{PLAN_OR_REQUIREMENTS}` — 应满足的需求或计划
- `{BASE_SHA}` — 起始 commit
- `{HEAD_SHA}` — 结束 commit

**关键约束：** reviewer 收到的是 clean context，不携带你的会话历史。这保证了 reviewer 专注于工作产物本身，同时保留你的上下文继续工作。

### 3. 处理反馈

| 严重级别 | 处理方式 |
|----------|----------|
| **Critical** | 立即修复（fix immediately），不可忽略 |
| **Important** | 继续前进前必须修复（fix before proceeding） |
| **Minor** | 记录下来稍后处理（note for later） |

**如果 reviewer 错了：**
- 用技术理由反驳（push back with technical reasoning）
- 展示代码/测试证明其正确性
- 请求 reviewer 澄清（request clarification）

## 与 Subagent 工作流的集成

**Subagent-Driven Development：**
- 每个任务（each task）完成后 review
- 在问题累积前捕获并修复
- 修复后再进入下一个任务

**执行计划（Executing Plans）：**
- 每个任务后或自然的 checkpoints 处 review
- 获得反馈 → 应用修正 → 继续执行

**Ad-Hoc 开发：**
- Merge 前 review
- 卡住时 review

## 注意事项

- 永远不要说 "这个改动很简单" 而跳过 review
- 永远不要忽略 Critical 级别的 issue
- 永远不要在 Important 级别的 issue 未修复的情况下继续
- 不要与有效的技术反馈争论
- 这是**指南**不是**教条**，根据实际情况调整
