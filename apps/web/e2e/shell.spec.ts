import { test, expect } from "@playwright/test";
test("App Router serves the development shell", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "FleetIQ", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Development shell", { exact: true }),
  ).toBeVisible();
});
