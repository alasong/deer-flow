---
name: "Architecture Decision Record First"
applies_to:
  phase: "P"
  modes: ["architecture"]
authority: "guidance"
---

# ADR-First（架构决策优先）

## 核心思想

在进入实施前，先记录关键的架构决策。每个 ADR 包含：

```
# ADR-N: <决策标题>

## 上下文
问题描述、约束条件、相关背景。

## 决策
具体的选择。

## 理由
为什么选这个方案。

## 后果
这个决策带来的正面和负面影响。

## 备选方案
考虑了哪些其他方案，为什么拒绝了。

## 状态
accepted | proposed | deprecated | superseded
```

## 适用场景

- 有长期影响的架构决策
- 多方案需要对比的场景
- 团队成员需要对齐认知

## 不适用场景

- 临时/可逆的实现选择（如"用哪个变量名"）
- 框架强制的常规模式

## ADR 在 RPD 中的应用

当你的 P+architecture 节点选择了 ADR-First 方法论：

1. 识别 1-3 个需要做决策的架构维度
2. 为每个维度写 ADR（产出文件）
3. 注册为 artifact：`rpd-engine.py artifact add <path> --role decision`
4. 调用 `rpd-engine.py tree node-done <id>`
5. 子节点引用这些 ADR 作为设计约束

## 注意事项

- 不需要对所有决策写 ADR，只写关键/有争议的
- ADR 不是一成不变的，可以用 `superseded` 状态来迭代
- ADR 不是权威，是记录——它记录"当时为什么这么选"
