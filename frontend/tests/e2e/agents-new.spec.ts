import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

const MOCK_AGENTS = [
  {
    name: "existing-agent",
    description: "An existing agent",
    system_prompt: "You are an existing agent.",
  },
];

test.describe("Create new agent", () => {
  test("page renders with name input and continue button", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/new");
    await page.waitForURL("**/workspace/agents/new");

    // Page title
    await expect(page.locator("h1")).toBeVisible();
    // Name input
    await expect(page.getByPlaceholder(/name/i).first()).toBeVisible();
    // Continue button
    await expect(page.getByRole("button", { name: /continue|下一步/i })).toBeVisible();
  });

  test("continue button is disabled when name is empty", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/new");
    await page.waitForURL("**/workspace/agents/new");

    await expect(page.getByRole("button", { name: /continue|下一步/i })).toBeDisabled();
  });

  test("shows error for invalid agent name characters", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/new");

    // Type a name with spaces (invalid)
    await page.getByPlaceholder(/name/i).first().fill("invalid name");
    await page.getByRole("button", { name: /continue|下一步/i }).click();

    // Should show an error message about invalid characters
    await expect(page.getByRole("button", { name: /continue|下一步/i })).toBeEnabled();
    await expect(page.locator("p.text-destructive")).toBeVisible();
  });

  test("shows error when agent name already exists", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    // Mock name check to return "not available"
    await page.route("**/api/agents/check?name=*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ available: false }),
      });
    });

    await page.goto("/workspace/agents/new");

    await page.getByPlaceholder(/name/i).first().fill("existing-agent");
    await page.getByRole("button", { name: /continue|下一步/i }).click();

    // Should show "already exists" error
    await expect(page.locator("p.text-destructive")).toBeVisible();
  });

  test("confirms name and transitions to chat step", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    // Mock name check to return "available"
    await page.route("**/api/agents/check?name=*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ available: true }),
      });
    });

    await page.goto("/workspace/agents/new");

    await page.getByPlaceholder(/name/i).first().fill("my-new-agent");
    await page.getByRole("button", { name: /continue|下一步/i }).click();

    // After confirming name, should transition to chat step (PromptInput textarea)
    await expect(page.getByPlaceholder(/describe|instruct|描述|指令|能力/i).first()).toBeVisible({ timeout: 10_000 });
  });

  test("back button returns to agents gallery", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/new");
    await page.waitForURL("**/workspace/agents/new");

    // Click the back/arrow button in the header
    await page.locator("header button").first().click();

    // Should navigate to /workspace/agents
    await page.waitForURL("**/workspace/agents");
  });
});
