# Symbol/Dependency Graph — 符号/依赖图

百万级代码仓中，Plan 阶段 agent 无法全量读取。符号图为 planner 提供精确的文件定位能力。

## 方式：JIT Grep 扫描（无需外部工具）

Plan 阶段在 planner spawn 之前，主 agent 做三遍扫描：

```
Pass 1 — 入口定位:
  grep -r "函数名\|类名\|接口名" --include="*.go" -l
  → 产出: 入口文件列表

Pass 2 — 类型/接口定义:
  grep -r "type\|interface" --include="*.go" -l 入口文件所在包
  → 产出: 依赖类型定义文件

Pass 3 — Import 闭包:
  解析入口文件的 import 语句 → 递归找到所有传递依赖
  → 产出: 完整受影响文件列表（通常 ~50-200 个，而非 ~10000 个）
```

**产物：** `.fat/pdf/impact_files.txt`（planner 只读这个列表里的文件）

## 高级模式：预建索引缓存

频繁使用的包/模块，主 agent 会将 Pass 2-3 的结果缓存到知识库：

```json
// .fat/pdf/knowledge/symbol-cache.json
{
  "module-name": {
    "entry_points": ["file:line"],
    "dependency_closure": ["file1", "file2", ...],
    "cached_at": "2026-06-01",
    "ttl": "90d"
  }
}
```

下次 PDD 任务触及同一模块时，直接读缓存跳过扫描（~2s 替代 ~30s）。

## 跨能力联动

- **增量测试（#4）：** impact_files.txt 反向解析 → 哪些测试 import 了这些文件 → 只跑这些测试
- **知识库（#5）：** 缓存命中率、依赖图变更检测 → 触发知识过期
- **Depth-N（#3）：** impact_files.txt 按包分组 → 识别子任务边界 → 递归分解
