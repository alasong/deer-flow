import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe.configure({ mode: "serial" });

const MOCK_OWNER_API_RESPONSES = {
  agents: {
    agents: [
      {
        agent_id: "a-1",
        name: "搜索小兵",
        capabilities: ["search", "web"],
        status: "active",
        registered_at: "2026-07-01T00:00:00Z",
      },
      {
        agent_id: "a-2",
        name: "代码工兵",
        capabilities: ["code", "review"],
        status: "idle",
        registered_at: "2026-07-01T00:00:00Z",
      },
    ],
  },
  queue: {
    pending: [
      {
        task_id: "t-001",
        capability: "search",
        status: "pending",
        description: "搜索竞品最新动态",
        priority: "high",
      },
    ],
    active: [
      {
        task_id: "t-002",
        capability: "code",
        status: "running",
        description: "重构用户模块接口",
        priority: "critical",
      },
    ],
  },
  board: {
    entries: [
      {
        key: "status.search-1",
        value: "running",
        updated_at: "2026-07-01T00:00:00Z",
        updated_by: "agent.search-1",
      },
    ],
  },
  approvals: {
    approvals: [
      {
        task_id: "t-001",
        reason: "搜索结果包含外部数据源，需确认合规",
        requested_by: "agent.search-1",
        status: "pending",
        created_at: "2026-07-01T00:00:00Z",
      },
    ],
  },
};

test("owner dashboard shows all four tabs with data", async ({ page }) => {
  // Mock owner API endpoints via route interception
  await page.route("**/api/owner/agents", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_OWNER_API_RESPONSES.agents),
    });
  });
  await page.route("**/api/owner/queue", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_OWNER_API_RESPONSES.queue),
    });
  });
  await page.route("**/api/owner/board", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_OWNER_API_RESPONSES.board),
    });
  });
  await page.route("**/api/owner/approvals", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_OWNER_API_RESPONSES.approvals),
    });
  });

  // Mock approve endpoint
  await page.route("**/api/owner/approvals/*/approve", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        approval: { task_id: "t-001", status: "approved" },
      }),
    });
  });
  // Mock reject endpoint
  await page.route("**/api/owner/approvals/*/reject", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        approval: { task_id: "t-001", status: "rejected" },
      }),
    });
  });

  // Navigate directly (sidebar link tested separately)
  await page.goto("/workspace/owner");
  await page.waitForURL("**/workspace/owner");

  // Verify page title and tabs exist
  await expect(page.locator("h1")).toContainText("Owner Dashboard");
  await expect(page.getByRole("tab", { name: "Agents" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Queue" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Board" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Approvals" })).toBeVisible();

  // Verify agents data renders (default tab)
  await expect(page.getByRole("cell", { name: "搜索小兵" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "代码工兵" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "search" })).toBeVisible();

  // Switch to Queue tab and verify data
  await page.getByRole("tab", { name: "Queue" }).click();
  await expect(page.getByRole("cell", { name: "t-001" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "t-002" })).toBeVisible();

  // Switch to Board tab
  await page.getByRole("tab", { name: "Board" }).click();
  await expect(
    page.getByRole("cell", { name: "status.search-1" }),
  ).toBeVisible();

  // Switch to Approvals tab
  await page.getByRole("tab", { name: "Approvals" }).click();
  await expect(page.getByRole("cell", { name: /搜索结果包含/ })).toBeVisible();
});

test("approve button calls API and refreshes list", async ({ page }) => {
  let approveCalled = false;
  let approvalsCallCount = 0;

  await page.route("**/api/owner/approvals", async (route) => {
    approvalsCallCount++;
    if (approveCalled) {
      // After approve, return empty list
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ approvals: [] }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_OWNER_API_RESPONSES.approvals),
      });
    }
  });
  await page.route("**/api/owner/approvals/*/approve", async (route) => {
    approveCalled = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        approval: { task_id: "t-001", status: "approved" },
      }),
    });
  });
  // Stub other owner endpoints
  for (const [path, data] of Object.entries({
    "/api/owner/agents": MOCK_OWNER_API_RESPONSES.agents,
    "/api/owner/queue": MOCK_OWNER_API_RESPONSES.queue,
    "/api/owner/board": MOCK_OWNER_API_RESPONSES.board,
  })) {
    await page.route(`**${path}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    });
  }

  await page.goto("/workspace/owner");
  await page.getByRole("tab", { name: "Approvals" }).click();

  // Click Approve button on the pending approval
  await page.getByRole("button", { name: "Approve" }).click();

  // After approve, the list should refresh (approvalsCallCount >= 2 means refetch happened)
  await expect(async () => {
    expect(approvalsCallCount).toBeGreaterThanOrEqual(2);
  }).toPass({ timeout: 5000 });

  // After approve, the status should show "Approved" or the empty state
  await expect(
    page.getByText("Approved").or(page.getByText("No approvals")),
  ).toBeVisible();
});

test("reject button calls API and refreshes list", async ({ page }) => {
  let rejectCalled = false;
  let approvalsCallCount = 0;

  await page.route("**/api/owner/approvals", async (route) => {
    approvalsCallCount++;
    if (rejectCalled) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ approvals: [] }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_OWNER_API_RESPONSES.approvals),
      });
    }
  });
  await page.route("**/api/owner/approvals/*/reject", async (route) => {
    rejectCalled = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        approval: { task_id: "t-001", status: "rejected" },
      }),
    });
  });
  for (const [path, data] of Object.entries({
    "/api/owner/agents": MOCK_OWNER_API_RESPONSES.agents,
    "/api/owner/queue": MOCK_OWNER_API_RESPONSES.queue,
    "/api/owner/board": MOCK_OWNER_API_RESPONSES.board,
  })) {
    await page.route(`**${path}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    });
  }

  await page.goto("/workspace/owner");
  await page.getByRole("tab", { name: "Approvals" }).click();

  await page.getByRole("button", { name: "Reject" }).click();

  await expect(async () => {
    expect(approvalsCallCount).toBeGreaterThanOrEqual(2);
  }).toPass({ timeout: 5000 });

  await expect(
    page.getByText("Rejected").or(page.getByText("No approvals")),
  ).toBeVisible();
});

test("sidebar contains Owner Dashboard link", async ({ page }) => {
  mockLangGraphAPI(page, { threads: [] });

  await page.goto("/workspace/chats/new");
  await expect(
    page.getByRole("link", { name: /owner dashboard/i }),
  ).toBeVisible();
});
