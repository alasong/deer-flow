# Persistent Architecture Knowledge Base — 持久化架构知识库

每个 PDD 周期从 Plan 冷启动。知识库跨周期复用架构洞察。

## 存储

`.fat/pdf/knowledge/` — 项目级持久化，不提交到 VCS（已在 `.gitignore`）。

## 五类条目

### module-boundaries.json

```json
{
  "api/auth": {
    "responsibility": "JWT 鉴权，token 管理",
    "allowed_deps": ["api/shared", "internal/crypto"],
    "forbidden_deps": ["api/billing", "internal/notification"],
    "stability": "stable",        // stable | evolving | volatile
    "last_verified": "2026-06-01"
  }
}
```

### dependency-rules.json

```json
{
  "rules": [
    { "from": "internal/*", "to": "api/*", "verdict": "deny", "reason": "internal 不能依赖 api" },
    { "from": "api/*", "to": "internal/*", "verdict": "allow", "reason": "api 可以依赖 internal" }
  ]
}
```

### convention-patterns.json

```json
{
  "patterns": [
    { "name": "error-handling", "rule": "所有公开函数返回 (T, error)", "scope": "api/*" },
    { "name": "logging", "rule": "使用 logrus.WithContext，不直接 fmt.Print", "scope": "global" }
  ]
}
```

### cycle-log.json

```json
{
  "cycles": [
    {
      "task_slug": "auth-refactor",
      "completed": "2026-06-01",
      "n": 2, "m": 2, "p1_found": 0,
      "lessons": "auth 模块改动总是触发 billing notification 联动测试",
      "n_m_accuracy": "accurate"   // accurate | over | under
    }
  ]
}
```

### dimension-weights.json

```json
{
  "module": "api/auth",
  "dimension_effectiveness": {
    "security": 0.9,
    "correctness": 0.7,
    "test_quality": 0.3
  }
}
```

## 跨项目知识共享

`~/.fat/pdf/knowledge/` 作为全局共享只读层，跨项目复用架构洞察。

### 读取优先级

```
Plan 阶段:
1. 读 .fat/pdf/knowledge/（项目级本地 KB）— 优先
2. 读 ~/.fat/pdf/knowledge/（全局共享 KB）— fallback

合并规则:
- 同 key 条目：项目级覆盖全局
- 项目级无此 key：使用全局
```

### 权限

| 操作 | 项目级 KB | 全局共享 KB |
|------|-----------|------------|
| Plan 读取 | 允许 | 允许（只读） |
| Act 写入 | 允许 | 不允许（只读） |
| 升级到全局 | N/A | 由 Act 阶段 prompt 判断 |

### 升级流程

Act 阶段合并 `act_report.md` 后，prompt 判断当前周期产出的模式是否有跨项目价值：

```
1. 该模式/规则是否通用？  （不依赖本项目特有 API、模块命名）
2. 是否持续触发 P1？      （同一维度多次命中）
3. 是否可独立验证？      （不依赖本项目的测试环境）

全部是 → 建议手动 promote 到 ~/.fat/pdf/knowledge/
任一否 → 保留项目级即可
```

### 文件结构

```
~/.fat/pdf/knowledge/
├── module-boundaries.json     # 通用模块边界
├── dependency-rules.json      # 通用依赖规则
├── convention-patterns.json   # 跨项目约定
├── cycle-log.json             # （无，项目级仅记录本项目周期）
└── dimension-weights.json     # （无，维度权重因项目而异）
```

## 生命周期

```
Plan: 主 agent 读知识库 → planner 获得历史上下文
Act:  主 agent 写知识库 ← RCA 反馈 + 新发现

TTL:
  90d 基础有效期
  每次 PDD 验证同一事实 → +30d
  硬上限 365d
  超期自动标记 expired → 下个 Plan 忽略
```

## Plan 阶段的 KB 注入

```
主 agent 在 spawn planner 之前:
1. 读 knowledge/module-boundaries.json → 注入到 planner prompt:
   "已知模块 AuthAPI 位于 api/auth/，禁止依赖 api/billing/"
2. 读 knowledge/dependency-rules.json → 注入:
   "已知规则: internal/* 不能 import api/*"
3. 读 knowledge/cycle-log.json → 注入:
   "上次 auth 修改影响了 billing notification 测试，请注意"
4. 读 knowledge/dimension-weights.json → 注入:
   "api/auth 模块 security 维度历史命中率 0.9，建议保留"
```

## Act 阶段的 KB 更新

```
主 agent 在合并 act_report.md 之后:
1. 更新 module-boundaries（新发现的边界，或验证已有边界）
2. 更新 dependency-rules（新发现的规则违规模式）
3. 添加 cycle-log 条目（本次周期摘要 + n_m_accuracy）
4. 更新 dimension-weights（基于本次 Check 发现，调整维度权重）
5. 更新 convention-patterns（本次 Act 产出的新约定）
```
