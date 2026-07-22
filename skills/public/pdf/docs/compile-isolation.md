# Compilation-Level Isolation — 编译级隔离

当前 workspace 目录级隔离在编译型大项目中不够——child team 缺少完整依赖树，无法编译验证。

## 两级隔离策略

| 项目类型 | 默认隔离 | 代价 |
|---------|---------|------|
| 编译型（Go/Rust/C++/Java） | **git worktree** | 10-60s checkout |
| 解释型（Python/JS/Ruby） | workspace 目录 | 0s |
| 超大仓库（>500MB） | 预编译 stubs | 一次构建成本 |

## Git Worktree 流程（编译型默认）

```
1. Parent 为 child 创建独立分支: git branch pdf-<slug>-<id>
2. git worktree add .fat/pdf/worktrees/<id> pdf-<slug>-<id>
3. Child 在 worktree 内运行完整 PDD（拥有完整编译环境）
4. Child contract 新增 compile_verification:
   - child 结束前必须: go build ./... 或等效命令通过
   - 编译失败 = P1，进入 child 自身修复循环
5. Parent 集成: git diff + apply patch → 编译 → 测试
```

## Contract 扩展

```yaml
child_teams:
  - id: child-auth
    isolation:
      mode: worktree          # worktree | workspace | stubs
      compile_check: "go build ./api/auth/..."
      stub_deps: []           # 仅 stubs 模式需要
```

## Meta-Check 扩展

新增两个集成维度：

| 维度 | 检查内容 |
|------|---------|
| compile_integration | 合并后的增量编译通过？跨 child 无 link error？ |
| interface_fidelity | 导出符号签名与 contract 声明一致？ |

## 向后兼容

- 非编译项目：保持 workspace 目录，无额外开销
- 配置开关：`.fat/pdf/config.yaml` 中 `isolation.default_mode: worktree|workspace`
