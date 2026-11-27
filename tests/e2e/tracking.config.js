// Runs only the app-analytics spec. tracking.spec.js starts its own app on a
// spare port, so this needs no webServer.  npx playwright test -c tests/e2e/tracking.config.js
const base = require('../../playwright.config.js');

module.exports = { ...base, testDir: '.', testMatch: 'tracking.spec.js', webServer: undefined,
  projects: base.projects.filter(p => p.name === 'desktop'),
  use: { ...base.use, baseURL: undefined } };
