// Runs only the operator-console spec. ops.spec.js starts its own apps on spare
// ports, so this needs no webServer.  npx playwright test -c tests/e2e/ops.config.js
const base = require('../../playwright.config.js');

module.exports = { ...base, testDir: '.', testMatch: 'ops.spec.js', webServer: undefined, timeout: 90_000,
  projects: base.projects.filter(p => p.name === 'desktop'),
  use: { ...base.use, baseURL: undefined } };
