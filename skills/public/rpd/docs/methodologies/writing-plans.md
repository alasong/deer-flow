---
name: "Writing Plans"
applies_to:
  phase: "P"
  modes: ["decompose", "plan"]
authority: "guidance"
---

# Writing Plans（编写实施方案）

## 核心思想

在触达代码之前，先编写一份完整的实施方案。假设执行者对你的 codebase 和工具链一无所知——你需要把每个细节都写清楚：哪些文件要改、怎么写、怎么测试。把整个计划拆成 bite-sized tasks（2-5 分钟一个），每个 step 包含完整的代码和命令。

```
Scope Check → File Structure Mapping → Task Decomposition → Self-Review
```

## 适用场景

- 有多步操作的 feature 实现
- 涉及多个文件新增/修改的任务
- 需要 TDD 驱动的工作

## 不适用场景

- 单步快速修复（直接改即可）
- 纯探索/原型工作（spike 模式更合适）

## 范围检查（Scope Check）

如果 spec 涉及多个独立的子系统，先建议拆分为独立的 plan——每个 plan 产出可独立工作、可测试的软件。

## 文件结构映射（File Structure Mapping）

在定义 task 之前，先映射出哪些文件将被创建或修改，以及每个文件的职责。这里是分解决策落定的地方：

- 每个文件有明确的单一职责
- 倾向于小文件 > 大文件。文件越小，你的编辑越可靠
- 一起变化的文件应该放在一起。按职责拆分，而不是按技术层拆分
- 在现有 codebase 中，遵循既有模式。如果正在修改的文件已经变得臃肿，可以在 plan 中包含拆分

这个结构决定了 task 分解。每个 task 应该产出独立且自洽的变更。

## Bite-Sized Task Granularity

**每个 step 是一个动作（2-5 分钟）：**

```
- "Write the failing test" — step
- "Run it to make sure it fails" — step
- "Implement the minimal code to make the test pass" — step
- "Run the tests and make sure they pass" — step
- "Commit" — step
```

## Plan Document Header

每个 plan 必须以这个 header 开头：

```markdown
# [Feature Name] Implementation Plan

**Goal:** [一句话描述这个计划要构建什么]

**Architecture:** [2-3 句描述技术方案]

**Tech Stack:** [关键技术/库]

---
```

## Task Structure

每个 task 按以下格式编写：

````markdown
### Task N: [Component Name]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

- [ ] **Step 1: 写失败测试**

```python
def test_specific_behavior():
    result = function(input)
    assert result == expected
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/path/test.py::test_name -v`
Expected: FAIL with "function not defined"

- [ ] **Step 3: 实现最简代码**

```python
def function(input):
    return expected
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/path/test.py::test_name -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/path/test.py src/path/file.py
git commit -m "feat: add specific feature"
```
````

## No Placeholders（无占位符规则）

每个 step 必须包含执行者实际需要的具体内容。以下都是 **plan 失败**——永远不要这样写：

- `"TBD"`、`"TODO"`、`"implement later"`、`"fill in details"`
- `"Add appropriate error handling"` / `"add validation"` / `"handle edge cases"`（没有具体代码）
- `"Write tests for the above"`（没有实际测试代码）
- `"Similar to Task N"`（必须重复代码——执行者可能乱序阅读 task）
- 只描述做什么但没展示怎么做的步骤（代码步骤必须有 code block）
- 引用了未在任何 task 中定义的类型、函数或方法

## 注意事项

- 所有文件路径必须是精确的绝对路径或相对路径
- 每个改动代码的 step 都要展示完整的代码
- 命令必须精确，并给出预期输出
- 遵循 DRY、YAGNI、TDD、频繁提交原则

## 自审查清单（Self-Review）

写完完整的 plan 后，以全新视角对照 spec 检查 plan。这是你自行运行的清单：

**1. Spec 覆盖度：** 快速浏览 spec 的每个章节/需求。能指出哪个 task 实现了它吗？列出任何缺口。

**2. 占位符扫描：** 搜索 plan 中的红线——上面"No Placeholders"节中的任何模式。修复它们。

**3. 类型一致性：** 后置 task 中使用的类型、方法签名、属性名是否与前置 task 中定义的匹配？Task 3 中叫 `clearLayers()` 但 Task 7 中叫 `clearFullLayers()` 是 bug。

发现 issues 就直接修复，无需重新审查。如果发现 spec 需求没有对应的 task，添加该 task。

## Writing Plans 在 RPD 中的应用

当 P+plan 或 P+decompose 节点选择了 Writing Plans 方法论：

1. 先做 Scope Check，确认 spec 范围是否合理
2. 做 File Structure Mapping，确定文件职责分解
3. 按 Task Structure 格式逐 task 编写
4. 每个 task 遵循 No Placeholders 规则
5. 完成后运行 Self-Review
6. 将 plan 文件注册为 artifact：`rpd-engine.py artifact add <path> --role plan`
7. 调用 `rpd-engine.py tree node-done <id>`
8. 子节点（D 阶段）直接引用 plan 中的 task 作为执行依据
