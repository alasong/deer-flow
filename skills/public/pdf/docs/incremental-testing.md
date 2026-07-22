# Incremental Testing — 增量测试

百万级代码全量测试可能 >30 分钟。Check 阶段只跑受影响的测试，全量回归推到 Act。

## 测试波及分析

Plan 阶段产出的 `impact_files.txt` → Check 阶段反向解析：

```
1. 从 impact_files.txt 列出所有修改的文件
2. 对每个修改文件，查找 import 它的测试文件:
   grep -r "import.*<modified-package>" --include="*_test.go" -l
3. 传递依赖: 如果 pkg A 改了，pkg B import A，pkg C import B
   → 同时包含 pkg C 的测试（保守策略，宁多勿漏）
4. 产出: .fat/pdf/affected_tests.txt
```

## Check 阶段测试选择

```
跑: affected_tests.txt 中的全部测试 (~2-5 min)
   + 10% 随机采样非波及测试（安全网，防漏检）
总时间: ~3-7 min vs 全量 30+ min
```

## 安全网机制

| 层级 | 时机 | 动作 |
|------|------|------|
| 随机采样 | Check 同步 | 10% 非波及包测试，防假阴性 |
| 全量回归 | Act 后台 | `go test ./...` 非阻塞，失败进入 cycle-log |
| 智能降级 | 波及比 >30% | 自动切全量（增量选择本身开销超过收益） |

## 测试映射缓存

频繁命中的测试映射缓存到知识库：

```json
// .fat/pdf/knowledge/test-mappings.json
{
  "pkg/api/auth/types.go": {
    "affected_tests": ["pkg/api/auth/auth_test.go", "pkg/middleware/auth_test.go"],
    "cached_at": "2026-06-01",
    "ttl": "90d"
  }
}
```

## 配置

`.fat/pdf/config.yaml`:
```yaml
testing:
  incremental: true           # 默认启用
  sampling_ratio: 0.1         # 随机采样比例
  auto_full_threshold: 0.3    # 波及>此比例自动切全量
  full_regression: act_async  # 全量回归在 Act 后台跑
```
