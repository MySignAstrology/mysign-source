// Runs only the public-site spec. site.spec.js starts its own app and
// site/serve.py, so this config needs no webServer — handy when :8499 (the
// main config's server) is busy.  npx playwright test -c tests/e2e/site.config.js
const base = require('../../playwright.config.js');

module.exports = { ...base, testDir: '.', testMatch: 'site.spec.js', webServer: undefined,
  use: { ...base.use, baseURL: undefined } };
