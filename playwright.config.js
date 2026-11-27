// @ts-check
const { defineConfig, devices } = require('@playwright/test');

/**
 * The app is Python; this exists because every change so far has been verified
 * by driving a browser by hand. Two people editing server.py, index.html and
 * test_app.py at once have already produced three merge conflicts and one
 * NameError that only showed up at runtime — the pytest suite cannot see any of
 * that, because none of it is reachable without a browser.
 *
 * Playwright starts the server itself, on its own port and its own data
 * directory, so a run never touches local development state.
 */
module.exports = defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,          // one server, one store; parallel runs collide
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  timeout: 30_000,
  expect: { timeout: 7_000 },

  use: {
    baseURL: 'http://127.0.0.1:8499',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  // Chromium everywhere. The iPhone and iPad descriptors default to WebKit,
  // which means a second browser download in CI for no gain here — these tests
  // check responsive layout and app flow, not Safari's rendering. Keep the
  // viewports and touch emulation, drop the engine.
  projects: [
    { name: 'mobile', use: { ...devices['iPhone 13'], browserName: 'chromium' } },
    { name: 'tablet', use: { ...devices['iPad Mini'], browserName: 'chromium' } },
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } } },
  ],

  webServer: {
    command: 'python -m uvicorn app.server:app --host 127.0.0.1 --port 8499',
    url: 'http://127.0.0.1:8499/api/state',
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      // Codes come back in the response instead of an inbox. app/auth.py
      // refuses this on Lambda regardless of what is set here.
      MYSIGN_DEV_AUTH: '1',
      MYSIGN_DATA: './tests/e2e/.data',
      PYTHONPATH: '.',
    },
  },
});
