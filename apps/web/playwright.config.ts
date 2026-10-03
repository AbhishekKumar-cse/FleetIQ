import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  outputDir: "../../docs/exports/playwright/results",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:3011" },
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3011",
    url: "http://127.0.0.1:3011",
    reuseExistingServer: false,
    timeout: 180000,
  },
});
