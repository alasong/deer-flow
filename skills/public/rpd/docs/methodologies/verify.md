---
name: "Verification Before Completion"
applies_to:
  phase: "C"
  modes: ["verify"]
authority: "guidance"
---

# Verification Before Completion（完成前验证）

声称工作已完成却不验证，是对事实的不诚实，而非高效率。

**核心原则：Evidence before claims — 先有证据，后下结论。**
**违背字面规则即违背规则精神。**

## Iron Law（铁律）

```
无新鲜验证证据，不得声称完成
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

如果**本条消息中**尚未运行验证命令，就不能宣称任何内容通过。

## Gate Function（门函数）

在声称任何**完成状态**或表达**满意**之前，必须通过此门：

```
1. IDENTIFY — 什么命令能证明这个声称？
2. RUN      — 执行完整的命令（新鲜的、完整的）
3. READ     — 读取全部输出，检查退出码，统计失败数
4. VERIFY   — 输出是否确认了声称？
   - 否 → 用证据陈述实际状态
   - 是 → 附上证据之后才做声称
5. ONLY THEN — 仅在此时做出声称

跳过任何一步 = 说谎，不是验证
```

## Common Failures（常见失败）

| 声称 | 需要什么证据 | 什么不算数 |
|------|-------------|-----------|
| 测试通过 | 测试命令输出：0 failures | 上次运行结果、"应该能过" |
| Linter 干净 | Linter 输出：0 errors | 部分检查、推测其他模块也ok |
| 构建成功 | 构建命令：exit 0 | Linter 通过了、日志看起来还行 |
| Bug 修复 | 验证原始症状：通过 | 代码改了、"应该修好了" |
| 回归测试有效 | Red-Green 循环验证 | 测试通过过一次 |
| Agent 完成任务 | VCS diff 展示变更 | Agent 口头报告 "success" |
| 需求全部满足 | 逐项核对 checklist | 测试通过了 |

## Red Flags — 立即 STOP

- 使用 "应该"、"可能"、"看起来"、**"should"、"probably"、"seems to"**
- 在验证之前表达满意（"很好！"、"完美！"、"搞定！"、"Great!"、"Perfect!"、"Done!"）
- 即将 commit/push/PR 但还没验证
- 信任 agent 的 success 报告而不独立检查
- 依赖部分验证（"只检查了一个文件"）
- 心想 "就这一次例外"
- 累了想赶紧结束
- **任何暗示成功但没有运行验证的措辞**

## Rationalization Prevention（借口预防）

| 借口 | 现实 |
|------|------|
| "应该能工作了" | RUN 验证命令 |
| "我有信心" | 信心 ≠ 证据 |
| "就这一次例外" | 无例外 |
| "Linter 过了" | Linter ≠ 编译器 |
| "Agent 说成功了" | 独立验证 |
| "我太累了" | 累了不是借口 |
| "部分检查就够了" | 部分 = 没证明 |
| "换个说法就不算违反规则了" | 精神优先于字面 |

## Verification Patterns（验证模式）

**Tests（测试）：**
```
✅ [运行测试命令] [看到 34/34 pass] → "所有测试通过"
❌ "应该能过了" / "看起来正确"
```

**Regression Tests / TDD Red-Green（回归测试）：**
```
✅ 编写 → 运行（通过） → 撤销修复 → 运行（必须失败） → 恢复 → 运行（通过）
❌ "我写了回归测试"（未做 Red-Green 验证）
```

**Build（构建）：**
```
✅ [运行构建命令] [看到 exit 0] → "构建通过"
❌ "Linter 过了"（Linter 不检查编译错误）
```

**Requirements Check（需求检查）：**
```
✅ 重读计划 → 创建 checklist → 逐项验证 → 报告差距或完成
❌ "测试都过了，阶段完成了"
```

**Agent Delegation（Agent 委托）：**
```
✅ Agent 报告成功 → 检查 VCS diff → 验证变更 → 报告实际状态
❌ 盲目信任 Agent 的报告
```

## 在 RPD 中的应用

当 C+verify 节点选择了此方法论：

1. 明确要验证的 claim 是什么
2. 确定验证命令（IDENTIFY）
3. 在当前消息中执行验证命令（RUN）
4. 读取完整输出（READ）
5. 判断是否通过：是→附证据声称完成；否→如实报告失败
6. 完成验证后调用 `rpd-engine.py tree node-done <id>`

**关键规则：** RUN 必须在**当前消息**中执行——用上一条消息的输出来声称本条消息的完成是作弊。

## 注意事项

- 这不是额外步骤，这是**验收标准**的一部分
- 验证命令要**完整**执行，不是子集
- 退出码（exit code）比日志文本更可靠
- 如果验证发现失败，不要掩盖——如实记录问题，这才是验证的价值
