import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:15173',
    launchOptions: { executablePath: process.env.TRAG_TEST_BROWSER || undefined }
  },
  webServer: {
    command: 'npm run dev -- --port 15173 --strictPort',
    url: 'http://127.0.0.1:15173',
    timeout: 30000,
    reuseExistingServer: false
  }
})
