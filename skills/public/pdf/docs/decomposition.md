# Decomposition Analysis — N 确定

## Can-Split Test

```
1. 接口耦合: A和B共享接口/签名/类型？→ YES不可拆分（互编译失败）→ NO进下一步
2. 文件耦合: A和B修改同一文件？→ YES不可拆分（合并冲突）→ NO可拆分
3. 传递依赖: B依赖A的输出（tests/lib调用）？→ 不可并行，可顺序（Primary+Tests）
```

## N 选择

| N | 场景 | 示例 |
|---|------|------|
| 1 | 耦合，1-2文件同包（默认） | <500 LOC 修改 |
| 2 | 两解耦 pkg，无共享接口 | 前端+后端 |
| 3 | 三独立模块 | API + config + migration |
| 4-7 | 多服务/大规模 | monorepo 多服务 |

N>4：合并开销 O(N²)。

## 不拆

- 共享接口变更（两 doer 改同 interface → 必冲突）
- 级联变更（doer-1 改签名，doer-2 调它 → 顺序依赖）
- 测试+实现（应走 P2 review，不并行）
