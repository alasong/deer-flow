---
name: "Test-Driven Development"
applies_to:
  phase: "D"
  modes: ["implement"]
authority: "guidance"
---

# TDD（测试驱动开发）

## 核心循环

```
Red — 写一个失败的测试（定义你想要的）
Green — 实现最少代码让测试通过
Refactor — 保持测试绿色，优化代码
Repeat — 直到功能完整
```

## 适用场景

- 有明确输入/输出的功能实现
- 需要高覆盖率的模块开发
- 修复 bug 时（先写回归测试）

## 不适用场景

- 探索性/原型工作（此时 spike 模式更合适）
- UI/视觉设计
- 复杂系统集成（此时 API-first + TDD 组合更好）

## TDD 在 RPD 中的应用

当你为一个子节点选择 TDD 方法论时：

1. 创建测试文件作为第一个 artifact
2. 运行 `rpd-engine.py tree node-start <id>` 开始工作
3. 遵循 Red→Green→Refactor 循环
4. 每完成一个循环，可以记录一条 decision_log
5. 完成所有功能后，运行 `rpd-engine.py tree node-done <id>`

## 注意事项

- 不是说测试覆盖率不重要，而是**先写什么测试**是关键
- 从最核心的业务逻辑开始，而不是基础设施
- 单元测试优先，集成测试覆盖关键路径
- 这是**指南**不是**教条**，根据实际情况调整
