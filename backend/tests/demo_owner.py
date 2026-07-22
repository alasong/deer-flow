"""Owner Dashboard 演示脚本 — 通过 HTTP API 注入数据。

用法::
    cd backend && uv run python tests/demo_owner.py

前置条件：Gateway 已在 localhost:8001 运行。
"""

from __future__ import annotations

import requests

BASE = "http://localhost:8001/api/owner"


def ok(label: str) -> None:
    print(f"  ✅ {label}")


def fail(label: str, detail: str) -> None:
    print(f"  ❌ {label}: {detail}")


def main() -> None:
    # 1. 注册 Agent
    print("\n" + "=" * 50)
    print("  注册 Agents")
    print("=" * 50)
    agents = [
        ("agent.search-1", "搜索小兵", ["search", "web"]),
        ("agent.code-1", "代码工兵", ["code", "review"]),
        ("agent.data-1", "数据分析师", ["data", "viz"]),
        ("agent.op-1", "运维助手", ["ops", "deploy"]),
    ]
    for agent_id, name, caps in agents:
        resp = requests.post(
            f"{BASE}/agents/register",
            json={"agent_id": agent_id, "name": name, "capabilities": caps},
        )
        if resp.status_code in (200, 409):
            ok(f"{name} ({agent_id})  capabilities={caps}")
        else:
            fail(name, str(resp.json()))

    # 2. 队列任务
    print("\n" + "=" * 50)
    print("  注入队列任务")
    print("=" * 50)
    tasks = [
        ("t-001", "search", "搜索竞品最新动态", "high"),
        ("t-002", "code", "重构用户模块接口", "critical"),
        ("t-003", "data", "导出 Q2 销售报表", "normal"),
        ("t-004", "search", "收集技术选型资料", "low"),
        ("t-005", "code", "修复登录页 CSS 问题", "normal"),
    ]
    for task_id, capability, desc, priority in tasks:
        resp = requests.post(
            f"{BASE}/queue/enqueue",
            json={
                "task_id": task_id,
                "capability": capability,
                "description": desc,
                "priority": priority,
            },
        )
        if resp.status_code == 200:
            ok(f"{task_id} — {desc}")
        else:
            fail(task_id, str(resp.json()))

    # 领两个任务
    for agent in ["agent.search-1", "agent.code-1"]:
        resp = requests.post(f"{BASE}/queue/claim?agent_id={agent}")
        if resp.status_code == 200:
            ok(f"{agent} 领了任务")
        else:
            fail(agent, str(resp.json()))

    # 3. Board 状态
    print("\n" + "=" * 50)
    print("  写入 Board")
    print("=" * 50)
    entries = [
        ("status.search-1", "running", "agent.search-1"),
        ("status.code-1", "running", "agent.code-1"),
        ("status.data-1", "idle", "agent.data-1"),
        ("progress.search-1", '{"progress":0.6,"current":"爬取中"}', "agent.search-1"),
        ("progress.code-1", '{"progress":0.3,"current":"分析依赖"}', "agent.code-1"),
        ("alert.system", "磁盘使用率 85%", "monitor"),
    ]
    for key, value, by in entries:
        resp = requests.post(
            f"{BASE}/board/post",
            json={"key": key, "value": value, "updated_by": by},
        )
        if resp.status_code == 200:
            ok(f"{key} = {value[:20]}…")
        else:
            fail(key, str(resp.json()))

    # 4. 审批请求
    print("\n" + "=" * 50)
    print("  创建审批请求")
    print("=" * 50)
    approvals = [
        ("t-001", "搜索结果包含外部数据源，需确认合规", "agent.search-1"),
        ("t-002", "重构涉及数据库 schema 变更，需审批", "agent.code-1"),
        ("t-003", "报表数据需发送给客户，需批准", "agent.data-1"),
    ]
    for task_id, reason, requested_by in approvals:
        resp = requests.post(
            f"{BASE}/approvals/new",
            json={"task_id": task_id, "reason": reason, "requested_by": requested_by},
        )
        if resp.status_code == 200:
            ok(f"{task_id}: {reason[:30]}…")
        else:
            fail(task_id, str(resp.json()))

    # 5. 验证
    print("\n" + "=" * 50)
    print("  验证 API")
    print("=" * 50)
    for path in ["/agents", "/queue", "/board", "/approvals"]:
        resp = requests.get(f"{BASE}{path}")
        data = resp.json()
        if path == "/queue":
            ok(f"GET {path} → {len(data['pending'])} pending, {len(data['active'])} active")
        elif path == "/agents":
            ok(f"GET {path} → {len(data['agents'])} agents")
        elif path == "/board":
            ok(f"GET {path} → {len(data['entries'])} entries")
        elif path == "/approvals":
            ok(f"GET {path} → {len(data['approvals'])} approvals")

    print(f"\n{'=' * 50}")
    print("  ✅ 演示完成！打开前端 → http://localhost:3000/workspace/owner")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
