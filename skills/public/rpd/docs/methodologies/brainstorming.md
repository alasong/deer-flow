---
name: "Brainstorming / Design First"
applies_to:
  phase: "P"
  modes: ["research", "architecture"]
authority: "guidance"
---

# Brainstorming（设计先行）

## 核心流程

```
Socratic Questioning → 2-3 Approaches 带 trade-offs
    → Present Design（分节展示，逐个确认）
    → Spec Self-Review（placeholders / contradictions / ambiguity）
    → User Approval Gate（用户审核 spec 后方可进入 implementation）
```

**HARD-GATE**：在 design 被用户批准之前，不得写任何代码、scaffold 项目、或者调用任何实现类技能。这条规则适用于所有场景，无论看起来多么简单。

## 适用场景

- 需求不清晰，需要探索用户意图和约束
- 有新功能或行为变更需要设计决策
- 多方案需要对比 trade-offs
- 架构或 research 阶段需要对齐设计方向后再开始实施

## 不适用场景

- 已有明确 spec 的纯实现任务（直接用 TDD）
- 紧急 hotfix（时间窗口不允许做设计）
- 用户已经给出完整设计文档的情况（直接进入 review 和 implementation）

## Step-by-Step

### Step 1: 理解当前上下文

- 检查相关文件、文档、最近 commit，建立 baseline
- 如果需求描述了多个独立子系统（如"做一个包含聊天、文件存储、计费和分析的平台"），立即标记：先帮助用户分解为子项目，明确顺序和依赖，再对第一个子项目走设计流程

### Step 2: Socratic Questioning（逐个提问）

- **一次只问一个问题**。如果需要探索多个维度，拆成多个消息
- 偏好选择题（"A 还是 B？"），但 open-ended 也 OK
- 聚焦于：**purpose**（为什么做）、**constraints**（约束条件）、**success criteria**（怎样算做完）

### Step 3: 提出 2-3 个方案

- 每个方案带 trade-offs，给出你的推荐和理由
- 推荐方案放前面，解释为什么它更合适
- 禁止只给一个方案——没有对比就没有 informed decision

### Step 4: 分节展示设计

- 每节按复杂度控制篇幅：简单的几句话，复杂的 200-300 词
- 每节都要问用户"这部分看起来对吗？"——**incremental validation**
- 覆盖：architecture、components、data flow、error handling、testing
- 准备好随时退回去澄清

### Step 5: Design for Isolation

- 把系统拆成小的、职责单一的单元，每个单元通过 well-defined interface 通信
- 每个单元要能回答：它做什么？怎么用？依赖什么？
- 原则：不读内部实现也能理解一个单元是做什么的；不改接口也能改内部实现
- 文件变大往往是"做得太多"的信号——保持专注

### Step 6: 产出设计文档

- 使用 RPD artifact 路径保存 spec：通过 `rpd-engine.py artifact add <path> --role design-spec` 注册
- 文档命名：`YYYY-MM-DD-<topic>-design.md`
- 如果可用，运行 clarity review 确保文档清晰简洁

### Step 7: Spec Self-Review

写完 design doc 后，重新审视：

1. **Placeholder scan**：有没有"TBD"、"TODO"、未完成章节、模糊需求？立即修复
2. **Internal consistency**：章节之间有没有矛盾？architecture 和 feature 描述是否匹配？
3. **Scope check**：这个 spec 是否足够聚焦为一个 implementation plan？还是需要进一步分解？
4. **Ambiguity check**：有没有一个需求可以被两种不同方式理解？如果有，选一种并显式说明

**不需要再评审，直接 fix inline 然后继续。**

### Step 8: User Approval Gate

将 spec path 和完整内容给用户审核：

> "Spec 已保存并注册为 RPD artifact：[path]。请 review 一下，有需要修改的地方吗？确认后我们可以进入 implementation。"

- 如果用户要求改，改完重新跑 self-review
- **只有用户明确 approve 后，才能进入 implementation phase**

## 在 RPD 中的应用

当你为一个 P 阶段节点选择 Brainstorming 方法论时：

1. 先确认当前没有任何实现代码（HARD-GATE 检查）
2. 执行 Step 1-4（Socratic questioning → 2-3 approaches → 逐节 design approval）
3. 产出 design doc，注册为 RPD artifact：
   ```
   rpd-engine.py artifact add docs/designs/YYYY-MM-DD-<topic>-design.md --role design-spec
   ```
4. 执行 self-review（Step 7）并等待用户批准（Step 8）
5. 调用 `rpd-engine.py tree node-done <id>` 完成 P 阶段节点
6. 子节点可以从该 artifact 读取设计约束进行实现

## Anti-Patterns & Red Flags

### "这太简单了，不需要设计" （🚩 RED FLAG）
- 恰恰是"简单"任务里，未检查的假设导致最多的返工
- 设计可以很短（简单场景几句话就够了），但必须展示并获得批准
- **防御措施**：保持 lightweight，但不跳过 gate

### 一次性扔出所有问题 （🚩 RED FLAG）
- "我有 10 个问题..." —— 用户不可能一次回答完
- **防御措施**：强制一次一个问题，拆成多个 turn
- 一个问题 = 一个 LLM turn，不要在一个 message 里堆砌多个问题

### 只给一个方案（"这是唯一方案"）
- 没有对比就没有 informed decision
- 你可能没意识到的 trade-off，用户可能能看到
- **防御措施**：强制 2-3 个方案，即使有一个明显更优

### 在设计中混入实现细节/代码
- 设计阶段谈论代码是危险的信号——说明已经开始实现思维
- **防御措施**：停留在 abstraction 层面——components、interfaces、data flow
- 具体代码留到 implementation phase 的 TDD 去做

### 跳过用户审核 Gate
- "用户看起来同意设计了，直接写代码吧" —— No. 必须等用户说"可以"
- **防御措施**：HARD-GATE 是程序性的，不是可有可无的
- 如果用户回复不明确，主动问："这是 approve 的意思吗？还是你有想改的地方？"

### YAGNI 违反（过度设计）
- 设计中包含"以后可能会用到"的特性
- **防御措施**：每个功能点都要问——"现在没有它行不行？" 如果行，砍掉
- 更聚焦的 design = 更快的 implementation = 更少的 review 轮次

### 在不理解问题域就开始写 spec
- 跳过 questioning 直接写设计文档
- **防御措施**：Socratic questioning 不是可选的——它是最重要的步骤
- 如果你不能清晰地用一两句话回答"用户到底要建什么"，说明 questioning 不够

## 关键原则

- **HARD-GATE**：没有 design approval 就没有 implementation
- **One question at a time**：不 overload 用户
- **Multiple choice preferred**：选择题比开放题更容易回答
- **2-3 approaches**：永远提供方案对比
- **Incremental validation**：每节确认，而不是一次性全展示
- **YAGNI ruthlessly**：从现在不需要的特性中脱身
- **Design for isolation**：小单元、清晰接口、独立可测
