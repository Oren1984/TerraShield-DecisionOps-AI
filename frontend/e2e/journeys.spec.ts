import { expect, test, type Page } from "@playwright/test";

const OWNER = process.env.TS_E2E_OWNER ?? "harbormaster";
const OWNER_PW = process.env.TS_E2E_OWNER_PASSWORD ?? "e2e-tidewater-passphrase-42";
const OPERATOR = "e2e-operator";
const OPERATOR_PW = "e2e-riverbank-passphrase-17";

async function ownerSession(page: Page) {
  await page.goto("/");
  const setup = page.getByRole("link", { name: "Set up the owner account" });
  const signIn = page.getByRole("link", { name: "Owner sign in" });
  await expect(setup.or(signIn)).toBeVisible();
  if (await setup.isVisible()) {
    await setup.click();
    await page.getByLabel("Owner username").fill(OWNER);
    await page.getByLabel("Password", { exact: true }).fill(OWNER_PW);
    await page.getByLabel("Confirm password").fill(OWNER_PW);
    await page.getByRole("button", { name: "Create owner and continue" }).click();
  } else {
    await signIn.click();
    await page.getByLabel("Username").fill(OWNER);
    await page.getByLabel("Password").fill(OWNER_PW);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page.getByTestId("whoami")).toContainText(`${OWNER} · owner`);
}

async function startMockRun(page: Page, slug: string) {
  await page.goto("/app");
  await page.getByTestId(`run-mock-${slug}`).click();
  await expect(page).toHaveURL(/\/app\/runs\//);
}

async function waitStatus(page: Page, status: string) {
  await expect(page.getByTestId("run-status").first()).toHaveAttribute("data-status", status, {
    timeout: 60_000,
  });
}

test.describe.configure({ mode: "serial" });

test("owner: setup, run, approve, simulate, complete and export", async ({ page }) => {
  await ownerSession(page);
  await startMockRun(page, "cascading-failure");
  await waitStatus(page, "awaiting_review");
  await page.getByRole("tab", { name: "Plan comparison" }).click();
  await expect(page.getByTestId("plan-table")).toContainText("verified");
  await page.getByRole("tab", { name: "Owner review" }).click();
  await page.getByLabel(/Reason/).fill("Protect the substation before its flood hour");
  await page.getByRole("button", { name: "Approve selected plan" }).click();
  await waitStatus(page, "approved");
  await page.getByTestId("simulate").click();
  await waitStatus(page, "completed");
  await page.getByRole("tab", { name: "Completion" }).click();
  await expect(page.getByTestId("outcome-banner")).toContainText("Completed");
  const download = page.waitForEvent("download");
  await page.getByTestId("download-report").click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^terrashield-report-.*\.md$/);
  await page.getByRole("tab", { name: "Operations" }).click();
  for (const role of ["water", "air", "ground", "coordinator"]) {
    await expect(page.getByTestId(`agent-${role}`)).toContainText("validated");
  }
  await expect(page.getByTestId("trace")).toContainText("Simulation completed");
});

test("owner: reject a plan", async ({ page }) => {
  await ownerSession(page);
  await startMockRun(page, "coastal-storm");
  await waitStatus(page, "awaiting_review");
  await page.getByRole("tab", { name: "Owner review" }).click();
  await page.getByLabel(/Reason/).fill("Not acceptable for this exercise");
  await page.getByRole("button", { name: "Reject" }).click();
  await waitStatus(page, "rejected");
  await expect(page.getByTestId("decisions")).toContainText("rejected");
});

test("owner: editing the scenario invalidates an approval", async ({ page }) => {
  await ownerSession(page);
  await startMockRun(page, "coastal-storm");
  await waitStatus(page, "awaiting_review");
  const runUrl = page.url();
  await page.getByRole("tab", { name: "Owner review" }).click();
  await page.getByLabel(/Reason/).fill("Approve before the edit");
  await page.getByRole("button", { name: "Approve selected plan" }).click();
  await waitStatus(page, "approved");
  await page.getByRole("link", { name: "Coastal storm" }).click();
  const peak = page.getByLabel("Peak (additional) (mm/h)");
  await peak.fill(String(Number(await peak.inputValue()) + 1));
  await page.getByLabel("Change note").fill("e2e edit");
  await page.getByRole("button", { name: /Save as version/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "no longer valid" })).toBeVisible();
  await page.goto(runUrl);
  await waitStatus(page, "blocked");
  await page.getByRole("tab", { name: "Owner review" }).click();
  await expect(page.getByTestId("decisions")).toContainText("invalidated");
  await expect(page.getByTestId("simulate")).toHaveCount(0);
});

test("owner: cancel a run awaiting review", async ({ page }) => {
  await ownerSession(page);
  await startMockRun(page, "wind-saturated-ground");
  await waitStatus(page, "awaiting_review");
  await page.getByRole("button", { name: "Cancel run" }).click();
  await waitStatus(page, "cancelled");
  await page.getByRole("tab", { name: "Completion" }).click();
  await expect(page.getByTestId("outcome-banner")).toContainText("Cancelled");
});

test("blocked scenario shows an honest blocked result", async ({ page }) => {
  await ownerSession(page);
  await startMockRun(page, "conflict-insufficient-resources");
  await waitStatus(page, "blocked");
  await expect(page.getByTestId("blocked-reason")).toContainText("A2");
  await page.getByRole("tab", { name: "Operations" }).click();
  await expect(page.getByTestId("conflicts")).toContainText("route");
});

test("operator can run but cannot approve", async ({ page, browser }) => {
  await ownerSession(page);
  await page.goto("/app/users");
  if (!(await page.getByRole("cell", { name: OPERATOR, exact: true }).isVisible())) {
    await page.getByLabel("Username").fill(OPERATOR);
    await page.getByLabel(/Initial password/).fill(OPERATOR_PW);
    await page.getByRole("checkbox", { name: "Harbor Vale Operations" }).check();
    await page.getByRole("button", { name: "Create user" }).click();
    await expect(page.getByRole("cell", { name: OPERATOR, exact: true })).toBeVisible();
  }
  const ctx = await browser.newContext();
  const op = await ctx.newPage();
  await op.goto("/login");
  await op.getByLabel("Username").fill(OPERATOR);
  await op.getByLabel("Password").fill(OPERATOR_PW);
  await op.getByRole("button", { name: "Sign in" }).click();
  await expect(op.getByTestId("whoami")).toContainText("operator");
  await expect(op.getByRole("link", { name: "Users" })).toHaveCount(0);
  await startMockRun(op, "coastal-storm");
  await waitStatus(op, "awaiting_review");
  await op.getByRole("tab", { name: "Owner review" }).click();
  await expect(op.getByText("Only the owner can approve")).toBeVisible();
  await expect(op.getByRole("button", { name: "Approve selected plan" })).toHaveCount(0);
  // Direct navigation to an owner page is refused.
  await op.goto("/app/users");
  await expect(op).toHaveURL(/\/app$/);
  await ctx.close();
});

test("failed run state is shown as failed (UI state with an intercepted API response)", async ({ page }) => {
  // The only test that mocks the network: it checks how the UI renders a failed
  // local-model run. Real failure handling is covered by backend tests.
  await ownerSession(page);
  await startMockRun(page, "coastal-storm");
  await waitStatus(page, "awaiting_review");
  const id = page.url().split("/runs/")[1].split("?")[0];
  await page.route(`**/api/runs/${id}`, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({
      response: res,
      json: {
        ...body,
        status: "failed",
        mode: "local",
        failure_code: "model_timeout",
        failure_detail: "water assess: no response within 180s",
      },
    });
  });
  await page.reload();
  await page.getByRole("tab", { name: "Completion" }).click();
  await expect(page.getByTestId("outcome-banner")).toContainText("Failed");
  await expect(page.getByTestId("outcome-banner")).toContainText("model_timeout");
  await expect(page.getByRole("button", { name: "Retry with local model" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start a new Mock run" })).toBeVisible();
});

test("keyboard: tabs, help and focus", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Explore the demo/ }).click();
  await page
    .getByRole("link", { name: /Open run Coastal storm/ })
    .first()
    .click();
  const tab = page.getByRole("tab", { name: "Completion" });
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await tab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Operations" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "Operations" })).toHaveAttribute("aria-selected", "true");
  await page.locator("body").press("?");
  await expect(page.getByRole("dialog", { name: "Help" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Help" })).toHaveCount(0);
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
});

test("demo viewer is read-only @responsive", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Explore the demo/ }).click();
  await expect(page.getByTestId("whoami")).toContainText("Demo viewer");
  await expect(page.getByRole("button", { name: "Run · Mock" })).toHaveCount(0);
  await expect(page.getByTestId("readiness")).toContainText("Not checked");
  await page.getByRole("link", { name: /Open run Conflict and insufficient resources/ }).click();
  await waitStatus(page, "blocked");
  await page.getByRole("tab", { name: "Owner review" }).click();
  await expect(page.getByText("Only the owner can approve")).toBeVisible();
  // No horizontal page scroll at this width, on any run view.
  for (const name of ["Operations", "Plan comparison", "Owner review", "Simulation", "Completion"]) {
    await page.getByRole("tab", { name }).click();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `horizontal overflow on ${name}`).toBeLessThanOrEqual(1);
  }
  // Server-side refusal, independent of the UI.
  const res = await page.request.post("/api/workspaces", {
    data: { name: "demo-attempt" },
    headers: { "X-Requested-With": "terrashield" },
  });
  expect(res.status()).toBe(403);
});
