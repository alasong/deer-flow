---
name: "Finishing a Development Branch"
applies_to:
  phase: "A"
  modes: ["standardize", "merge"]
authority: "guidance"
---

# 完成开发分支（Finishing a Development Branch）

## 核心流程

```
Verify Tests → Detect Environment → Determine Base Branch
    → Present 4 Options → Execute Choice → Cleanup
```

## 适用场景

- 功能开发完成，所有测试通过，准备整合代码
- 需要决定 merge / PR / keep / discard 四种路线之一
- Act 阶段收尾，将成果写回主分支

## 不适用场景

- 功能尚未完成（此时应继续开发而非收尾）
- 测试未通过（必须先修测试）
- 不需要版本控制的探索性工作

## 流程详解

### Step 1: 验证测试（Test Verification Gate）

**在展示任何选项之前，先确保测试全部通过：**

```bash
# 运行项目测试套件
npm test / pytest / cargo test / go test ./...
```

**如果测试失败：**
```
测试未通过（N 个失败）。必须先修测试才能继续：

[展示失败详情]

merge/PR 流程不能继续，直到测试全部通过。
```

**停止。不要进入 Step 2。**

**如果测试通过：** 继续下一步。

### Step 2: 检测环境（Detect Environment）

确定当前工作区状态：

- 是普通仓库（normal repo）还是 worktree？
- 当前分支是 named branch 还是 detached HEAD？
- 是否有未提交的改动？

```bash
# 检测当前分支
git branch --show-current

# 检测是否为 worktree
git rev-parse --is-inside-work-tree

# 检查是否有未提交改动
git status --porcelain
```

环境检测决定了两件事：
1. **选项菜单** — named branch 展示 4 个选项，detached HEAD 展示 3 个
2. **清理策略** — 普通仓库无需清理 worktree，worktree 环境需要特定清理步骤

### Step 3: 确定基准分支（Determine Base Branch）

```bash
# 尝试常见的基准分支
git merge-base HEAD main 2>/dev/null || git merge-base HEAD master 2>/dev/null
```

或询问："这个分支是从 main 分出来的，是否正确？"

### Step 4: 展示选项（Present Options）

**Normal repo 和 named-branch worktree — 展示以下 4 个选项：**

```
实现已完成。请选择下一步操作：

1. Merge 回 <base-branch>（本地合并）
2. Push 并创建 Pull Request
3. Keep — 保留当前状态，后续处理
4. Discard — 丢弃本次工作

请选择？
```

**Detached HEAD — 展示以下 3 个选项：**

```
实现已完成。当前处于 detached HEAD（外部管理工作区）。

1. Push 为新分支并创建 Pull Request
2. Keep — 保留当前状态
3. Discard — 丢弃本次工作

请选择？
```

### Step 5: 执行选择（Execute Choice）

#### 选项 1：Merge 回基准分支

```bash
# 切回基准分支
git checkout <base-branch>
git pull

# 合并特性分支
git merge <feature-branch>

# 验证合并后的测试
<test command>
```

合并成功后：执行清理（Step 6），然后删除特性分支：

```bash
git branch -d <feature-branch>
```

#### 选项 2：Push 并创建 PR

```bash
# 推送分支
git push -u origin <feature-branch>

# 创建 PR
gh pr create --title "<标题>" --body "描述"
```

**不要清理 worktree** — 用户需要 worktree 来响应 PR 反馈。

#### 选项 3：Keep — 保留分支

报告当前状态："保持分支 <name>。工作区保留在 <path>。"

**不要清理工作区。**

#### 选项 4：Discard — 丢弃分支

**先确认：**
```
此操作将永久删除：
- 分支 <name>
- 所有 commits：<commit-list>
- 工作区 <path>

请键入 'discard' 确认。
```

等待精确的 `discard` 输入。确认后：

```bash
# 先切换到其他目录或分支
git checkout <base-branch>

# 强制删除分支
git branch -D <feature-branch>
```

### Step 6: 清理工作区（Cleanup）

**仅对选项 1（Merge）和选项 4（Discard）执行清理。** 选项 2 和 3 始终保留工作区。

- 普通仓库：无需额外清理，分支删除后即完成
- Worktree 环境：先切出 worktree，再执行 `git worktree remove <path>`，然后 `git worktree prune`
- 始终先确认 merge/删除成功，再清理 worktree
- 清理 worktree 前先 cd 到主仓库根目录

## 快速参考

| 选项 | Merge | Push | 保留工作区 | 删除分支 |
|------|-------|------|------------|----------|
| 1. Merge 本地 | 是 | - | - | 是 |
| 2. 创建 PR | - | 是 | 是 | - |
| 3. Keep 保留 | - | - | 是 | - |
| 4. Discard | - | - | - | 是（强制） |

## 常见错误

| 错误 | 问题 | 解决方案 |
|------|------|----------|
| 跳过测试验证 | merge 了有问题的代码，或创建了失败的 PR | 始终在提供选项前验证测试 |
| 为 PR 选项清理 worktree | 删除了用户迭代 PR 反馈所需的工作区 | 仅对选项 1 和 4 清理 worktree |
| 在删除分支前移除 worktree | `git branch -d` 会失败，因为 worktree 仍引用该分支 | 先 merge/discard，再移除 worktree，最后删分支 |
| 从 worktree 内部执行 `git worktree remove` | 命令静默失败 | 始终先 cd 到主仓库根目录 |
| 删除前未确认 | 意外丢失工作 | Discard 必须等待用户键入 "discard" 确认 |
| 未验证合并后的测试 | 合并引入新问题 | 合并后重新运行测试，验证通过才收尾 |
| 未指定基准分支就 merge | merge 到错误的分支 | 执行 merge 前明确确认 base branch |

## 注意事项

- **测试是第一道门** — 测试未通过前，不提供任何收尾选项
- **不开放式提问** — 不要说"接下来怎么办"，而是展示精确的 4 选项（或 3 选项）菜单
- **Discard 不可逆** — 必须要求 typed confirmation，不能接受 "yes" 或 "y"
- **分支删除顺序** — 先合并/确认，再清理 worktree，最后删分支。顺序错误会导致失败
- **PR 迭代期间保留 worktree** — 用户需要在本地响应 review 反馈
- **不要 force push** — 除非用户明确要求
