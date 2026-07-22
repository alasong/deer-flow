# Risk Profile — M 确定

> 维度定义见 `docs/dimensions.yaml`（单一事实来源）。此文件为 M 速查和触发条件参考。

## 8 维度

| 维度 | 触发条件 | 关注点 |
|------|---------|--------|
| **correctness** | 始终 | Plan合规、逻辑bug、边界处理、控制流 |
| security | 鉴权/输入/敏感数据/权限/文件路径/注入面 | OWASP Top10、校验完备、日志泄漏、硬编码密钥 |
| performance | 循环内IO/新DB查询/缓存/热路径/大数据/并发 | N+1查询、无界循环、缺索引、内存分配、阻塞IO |
| api_design | 公开API/breaking change/SDK/RPC/schema | 命名一致性、错误格式、向后兼容、版本策略、分页 |
| test_quality | 复杂逻辑/多分支/已知边缘/覆盖率缺口 | 缺失路径、断言充分性、边缘用例、mock合理性 |
| maintainability | 公共库/shared module/长期维护/抽象设计 | DRY、耦合、命名、函数长度、单一职责 |
| reliability | 关键路径/消息队列/外部调用/持久化 | 错误完备性(无裸panic)、重试退避、超时context、可观测性、幂等 |
| data_privacy | PII收集/用户数据/合规要求/第三方共享 | GDPR/CCPA/HIPAA、脱敏、留存策略、数据分类、用户权利 |

## M 速查

```
M=1 correctness only              内部工具/log格式
M=2 +security                     用户数据+鉴权
M=3 +api_design                   公开API+性能关键
M=4 +test_quality                 高复杂度+多边缘
M=5-7 全维度（不含data_privacy）    认证系统重写
M=8 +data_privacy                 个人数据/合规（含PII时可在任意M条件激活）
```

M≥1，correctness=baseline。M>4 去重开销大。含 PII 时 data_privacy 可在任意 M 条件激活，M=8 自动包含。

## 维度重叠

多 reviewer 同根因问题 → 按根因去重，保留高严重性（P1>P2>P3），标注双 reviewer 为共同发现者。
