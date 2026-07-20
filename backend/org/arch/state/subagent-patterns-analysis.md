# 临时分析笔记

## 背景

从 subagents 系统识别出 6 个值得复制的设计模式，评估将其落入 DeerFlow 其他模块的可行性。

## 模式清单与落地方案

### P1. 纯函数数据塑形层（step_events.py）

**现状**：多个模块混合 IO + 数据转换（task_tool.py 的结果格式化、middleware 的数据处理）。

**落地方案**：不以"提取公共库"的形式落地，而是作为**编码规范**：
- 新模块的数据转换逻辑必须写成纯函数（无状态、无 IO、无 import side effect）
- 现有模块重构时优先分离 IO 层和塑形层

**是否产生重复机制**：否。这是 refactoring 模式，不是新机制。

### P2. 跨语言契约 + 生产者边界校验（status_contract.py）

**现状**：contracts/ 目录存在，但并非所有 `additional_kwargs` 字段都有 `make_*` 函数做边界校验。

**落地方案**：推进到 AGENTS.md 开发规范：
- 每个持久化的 `additional_kwargs` 键必须有对应的 `make_*` 函数（含类型校验 + ValueError on invalid）
- `test_*_contract.py` 锁死前后端一致性
- 大文本截断统一用 `_bound_metadata_text` 的 head+...+tail 策略

**是否产生重复机制**：否。标准化已有实践，减少未来重复风险。

### P3. Additive 字段代替新枚举值

**现状**：非正式存在于多个模块中。

**落地方案**：写入 AGENTS.md 的 API 演进指南：
- 优先新增可选字段，不扩展现有枚举
- 保持 `NotRequired` + reader 端 `legacy_normalization` 映射

**是否产生重复机制**：否。这是设计原则，不是运行时机制。

### P4. 隔离事件循环（executor.py）

**现状**：仅 subagents 使用。其他模块没有类似需求（主图在单线程跑）。

**落地方案**：保持现状。如果有新模块需要类似隔离，复用同一模式而非重写。

**是否产生重复机制**：否。唯一实现。

### P5. LLM-as-Judge 模式（goal.py）

**现状**：`goal.py::evaluate_goal_completion` — 评估 goal 完成度。subagent 结果质量评估已移除。

**重复风险评估**：当前无重复（唯一实现）。

### P6. 增量捕获 + 历史收缩耐受（capture_new_step_messages）

**现状**：仅 step_events 使用。

**落地方案**：保持模块私有。如有其他 stream replay 场景需要相同模式，再提取。

**是否产生重复机制**：否。唯一实现。

---

## 核心结论

**6 个模式中，5 个不会产生重复机制，1 个（LLM-as-Judge）是潜在风险但当前不值得合并。**

真正需要落地到 DeerFlow 的不是具体代码，而是三条规范化约束：

1. 新模块的数据转换逻辑必须写成纯函数（P1）
2. 每个 `additional_kwargs` / 跨语言数据边界必须有 `make_*` 校验函数（P2）
3. API 演进优先加字段，不扩展枚举（P3）

这三条写入 AGENTS.md 的 **Development Guidelines**，比提取任何公共库都更值。

> **观察点**：如果出现第三个 LLM 结构化评估场景，触发 P5 合并（提取共享 `structured_llm_evaluator` 工具模块）。
