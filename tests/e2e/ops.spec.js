// The operator console (app/static/ops.html): every report tab against the
// live-counter fallback and against seeded rollups, the range picker, compare,
// CSV export, role-gated tabs and paging.
//
// Self-contained like tracking.spec.js: two apps of its own on spare ports, one
// with no rollups (live fallback) and one seeded by seed_reports.py.
//   npx playwright test -c tests/e2e/ops.config.js
// OPS_SHOTS=<dir> also writes screenshots there.
const { test, expect } = require('@playwright/test');
const { spawn, execFileSync } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.PYTHON || 'python';
const SHOTS = process.env.OPS_SHOTS;
const ADMIN = 'ops-admin@example.test';
const apps = [];

function freePort() {
  return new Promise((res, rej) => {
    const s = net.createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
    s.on('error', rej);
  });
}
async function waitFor(url, ms = 60_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { const r = await fetch(url); if (r.status < 500) return; } catch (e) { /* not up yet */ }
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error('timed out waiting for ' + url);
}
async function boot(tag, seed) {
  const port = await freePort();
  const data = path.join(ROOT, `tests/e2e/.data/ops-${tag}-${port}`);
  fs.rmSync(data, { recursive: true, force: true });
  fs.mkdirSync(data, { recursive: true });
  const env = { ...process.env, MYSIGN_DEV_AUTH: '1', MYSIGN_DATA: data, MYSIGN_OPS_ADMINS: ADMIN, PYTHONPATH: '.' };
  for (const k of ['AWS_LAMBDA_FUNCTION_NAME', 'MYSIGN_FN_NAME', 'MYSIGN_OPS_TOKEN']) delete env[k];
  if (seed) execFileSync(PY, ['tests/e2e/seed_reports.py', '120'], { cwd: ROOT, env, stdio: 'ignore' });
  const app = spawn(PY, ['-m', 'uvicorn', 'app.server:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: ROOT, stdio: 'ignore', env });
  apps.push(app);
  const base = `http://127.0.0.1:${port}`;
  await waitFor(base + '/api/state');
  return base;
}
let LIVE, ROLLUP;

test.describe.configure({ mode: 'serial' });
test.beforeAll(async ({}, info) => {
  // A desk tool: run once, on the desktop project (the mobile test sets its own viewport).
  test.skip(info.project.name !== 'desktop', 'desktop only');
  test.setTimeout(120_000);
  [LIVE, ROLLUP] = await Promise.all([boot('live'), boot('rollup', true)]);
});
test.afterAll(() => { for (const a of apps) a.kill(); });

async function signIn(context, base, email) {
  const req = context.request;
  const code = (await (await req.post(base + '/api/auth/request', { data: { email } })).json()).dev_code;
  const r = await req.post(base + '/api/auth/verify', { data: { email, code } });
  expect(r.ok()).toBeTruthy();
}
// Sign-in codes are rate-limited per address: sign the admin in once per app, reuse the cookie.
const STATES = {};
async function asAdmin(context, base) {
  if (!STATES[base]) {
    await signIn(context, base, ADMIN);
    STATES[base] = (await context.storageState()).cookies;
  } else await context.addCookies(STATES[base]);
}
async function person(base, browser, tag) {
  const ctx = await browser.newContext();
  await signIn(ctx, base, `${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.test`);
  await ctx.request.post(base + '/api/consent', { data: { ai: true } });
  await ctx.close();
}

/** Console errors and failed same-origin requests, minus the web fonts (no network here). */
function watch(page) {
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !/fonts\.(googleapis|gstatic)/.test(m.text())) errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));
  page.on('requestfailed', r => { if (!/fonts\.(googleapis|gstatic)/.test(r.url())) errors.push('failed ' + r.url()); });
  return errors;
}
async function clean(page) {
  await expect(page.locator('#view .skel')).toHaveCount(0);
  const text = await page.locator('#app').innerText();
  expect(text).not.toMatch(/\bNaN\b|\bundefined\b|\bnull\b|Infinity|\[object/);
  await expect(page.locator('#view .err:not(:empty)')).toHaveCount(0);
}
async function shot(page, name, full = true) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: full });
}
const TABS = ['overview', 'acquisition', 'funnel', 'engagement', 'retention', 'notifications', 'users', 'safety', 'health', 'admin'];
async function tour(page, prefix) {
  for (const t of TABS) {
    await page.click(`nav.tabs a[data-tab="${t}"]`);
    await expect(page.locator(`nav.tabs a[data-tab="${t}"]`)).toHaveAttribute('aria-current', 'page');
    await clean(page);
    if (prefix) await shot(page, `${prefix}-${t}`);
  }
}

test('live fallback: every tab renders, badges say so, unavailable is a placeholder', async ({ page, context, browser }) => {
  await person(LIVE, browser, 'p1');
  await person(LIVE, browser, 'p2');
  const errors = watch(page);
  await asAdmin(context, LIVE);
  await page.goto(LIVE + '/ops');
  await expect(page.locator('#app')).toBeVisible();
  await clean(page);
  await expect(page.locator('.badge', { hasText: 'Live counters' }).first()).toBeVisible();
  await expect(page.locator('.kpi', { hasText: 'Visitors' }).locator('.v')).toHaveText('—');
  await tour(page, SHOTS ? 'live' : '');
  await page.click('nav.tabs a[data-tab="retention"]');
  await expect(page.locator('.na', { hasText: 'Not available until analytics is deployed' })).toBeVisible();
  await page.click('nav.tabs a[data-tab="health"]');
  await expect(page.locator('.na', { hasText: 'Not available here' })).toBeVisible();
  // Users: both people are listed (the index is written on sign-up), newest first.
  await page.click('nav.tabs a[data-tab="users"]');
  await expect(page.locator('#rows tr[data-uid]')).not.toHaveCount(0);
  const uid = await page.locator('#rows tr[data-uid]').first().getAttribute('data-uid');
  await page.locator('#rows tr[data-uid]').first().click();
  await expect(page.locator('.stepper')).toBeVisible();
  await clean(page);
  // An unflagged conversation is refused by the server, and the page says so calmly.
  await page.goto(LIVE + '/ops#/safety/' + uid);
  await expect(page.locator('#view')).toContainText('Can’t open this conversation');
  // Old links still land.
  await page.goto(LIVE + '/ops#/people');
  await expect(page.locator('nav.tabs a[data-tab="users"]')).toHaveAttribute('aria-current', 'page');
  expect(errors.filter(e => !/status of 403/.test(e))).toEqual([]);
});

test('rollups: range presets, custom range, compare deltas, CSV, retention groupings', async ({ page, context }) => {
  const errors = watch(page);
  await asAdmin(context, ROLLUP);
  await page.goto(ROLLUP + '/ops');
  await clean(page);
  await expect(page.locator('.badge', { hasText: 'Rollups' }).first()).toBeVisible();
  await expect(page.locator('.kpi', { hasText: 'Visitors' }).locator('.v')).not.toHaveText('—');
  await shot(page, 'rollup-overview-28d');

  for (const p of ['today', '7d', '90d', '28d']) {
    const resp = page.waitForResponse(r => r.url().includes('/api/ops/report/visits'));
    await page.click(`.chip[data-preset="${p}"]`);
    await resp;
    await expect(page.locator(`.chip[data-preset="${p}"]`)).toHaveAttribute('aria-pressed', 'true');
    await clean(page);
  }

  await page.check('#compare');
  await clean(page);
  await expect(page.locator('.kpi .d').first()).toContainText(/vs previous/);
  await expect(page.locator('#rangetxt')).toContainText('vs');

  await page.click('.chip[data-preset="custom"]');
  await expect(page.locator('#custom')).toBeVisible();
  const today = new Date().toISOString().slice(0, 10);
  const back = d => new Date(Date.now() - d * 864e5).toISOString().slice(0, 10);
  await page.fill('#from', back(5));
  await page.fill('#to', back(10));
  await page.click('#custom button[type=submit]');
  await expect(page.locator('#rangeErr')).toContainText('on or before');
  await page.fill('#from', back(13));
  await page.fill('#to', today);
  const resp = page.waitForResponse(r => r.url().includes(`from=${back(13)}`) && r.url().includes(`to=${today}`));
  await page.click('#custom button[type=submit]');
  await resp;
  await clean(page);

  // The export link carries the same range and works with the session cookie.
  const href = await page.locator('a[data-csv="visits"]').getAttribute('href');
  expect(href).toContain(`from=${back(13)}`);
  const csv = await context.request.get(ROLLUP + href);
  expect(csv.status()).toBe(200);
  expect((await csv.text()).split('\n')[0]).toContain('date,section,key,metric,value');

  // What the old standalone dashboard showed lives here now.
  await page.click('nav.tabs a[data-tab="overview"]');
  await page.click('#rest > summary');
  await expect(page.locator('.kpi', { hasText: 'App opens' })).toBeVisible();
  await clean(page);
  // Each headline number puts its days in the chart; the choice and the fold survive a reload of the data.
  await expect(page.locator('#focus figcaption')).toContainText('Visitors per day');
  await page.click('button[data-focus="signups"]');
  await expect(page.locator('button[data-focus="signups"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#focus figcaption')).toContainText('New sign-ups per day');
  await page.locator('#focus svg').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#focus figcaption span')).toHaveText(/^[A-Z][a-z]{2} \d+: \d/);
  await page.click('#refresh');
  await clean(page);
  await expect(page.locator('#focus figcaption')).toContainText('New sign-ups per day');
  await expect(page.locator('#rest')).toHaveAttribute('open', '');
  await expect(page.locator('#standout')).toContainText('Every paid visitor stopped at the hero');
  await expect(page.locator('#standout')).toContainText('Busiest day');
  await expect(page.locator('.kpi', { hasText: 'App opens' }).locator('.v')).not.toHaveText('—');
  await page.click('nav.tabs a[data-tab="acquisition"]');
  for (const h of ['Channel quality', 'Page sections viewed', 'Landing page actions', 'Visit length', 'Operating system', 'Browser'])
    await expect(page.locator('h2', { hasText: h }).first()).toBeVisible();
  await expect(page.locator('td.k', { hasText: 'Paid ad' })).toBeVisible();
  await page.click('nav.tabs a[data-tab="engagement"]');
  await expect(page.locator('figcaption', { hasText: 'App events per day' })).toBeVisible();
  for (const h of ['Events by hour', 'Events by weekday', 'Server events'])
    await expect(page.locator('h2', { hasText: h }).first()).toBeVisible();
  await clean(page);

  await page.click('.chip[data-preset="90d"]');
  await tour(page, SHOTS ? 'rollup' : '');
  await page.click('nav.tabs a[data-tab="retention"]');
  await expect(page.locator('.heat td.c').first()).toBeVisible();
  for (const d of ['by_channel', 'by_utm_source', 'cohorts']) {
    await page.click(`.chip[data-ret="${d}"]`);
    await expect(page.locator(`.chip[data-ret="${d}"]`)).toHaveAttribute('aria-pressed', 'true');
    await clean(page);
  }
  expect(errors).toEqual([]);
});

test('system health renders CloudWatch numbers (stubbed)', async ({ page, context }) => {
  const errors = watch(page);
  const t0 = Date.parse(new Date().toISOString().slice(0, 10) + 'T00:00:00Z') - 27 * 864e5;
  const series = (f) => Array.from({ length: 28 }, (_, i) => ({ t: new Date(t0 + i * 864e5).toISOString().replace('.000', ''), ...f(i) }));
  const fn = (name, k) => ({ name, totals: { invocations: 41000 * k, errors: 12 * k, throttles: 0, duration_p95_ms: 820 / k, concurrency_max: 9 * k, error_rate: 0.0003 },
    series: series(i => ({ invocations: 1400 * k + i * 20, errors: i % 9 === 0 ? 3 : 0, throttles: 0, duration_p95_ms: 700 + (i % 5) * 60, concurrency_max: 4 + (i % 4) })) });
  const body = { from: '', to: '', period: 86400, generated_at: new Date().toISOString(), cached: false,
    functions: { app: fn('mysign-App', 1), cron: fn('mysign-Cron', 0.1) },
    app: { namespace: 'MySign', totals: { messages: 5120, quota_refused: 3, red_tier: 1, sms_cap: 0, app_errors: 4, alert_unrouted: 0, auth_unavailable: 0 }, series: [] },
    email: { totals: { send: 812, delivery: 801, bounce: 6, complaint: 0, bounce_rate: 0.007, complaint_rate: 0 }, series: [] },
    edge: { status: 'complete', totals: { requests: 98765, bytes: 5.2e8, rate_4xx: 0.021, rate_5xx: 0.0004 }, series: series(i => ({ requests: 3000 + i * 10 })) },
    cold_starts: { status: 'complete', total: 233, init_ms_avg: 1480.2, init_ms_max: 2950, series: series(i => ({ cold: 5 + (i % 6) * 2, init_ms: 1400 })) } };
  await page.route('**/api/ops/health?**', r => r.fulfill({ json: body }));
  await asAdmin(context, ROLLUP);
  await page.goto(ROLLUP + '/ops#/health');
  await clean(page);
  await expect(page.locator('.stat', { hasText: 'Cold starts' }).locator('.v')).toHaveText('233');
  await expect(page.locator('.stat', { hasText: 'Duration p95' }).first().locator('.v')).toHaveText('820 ms');
  await expect(page.locator('.stat', { hasText: 'Requests' }).locator('.v')).toHaveText('98,765');
  await shot(page, 'rollup-health');
  expect(errors).toEqual([]);
});

test('an analyst sees reports only; safety pages are refused gracefully', async ({ page, context, browser }) => {
  const email = `analyst-${Date.now()}@example.test`;
  const ctx = await browser.newContext();
  await signIn(ctx, ROLLUP, email);               // must have an account first
  await ctx.close();
  await asAdmin(context, ROLLUP);
  const add = await context.request.post(ROLLUP + '/api/ops/operators',
    { data: { email, role: 'analyst' }, headers: { 'x-ops-request': '1' } });
  expect(add.ok()).toBeTruthy();
  await context.clearCookies();
  await signIn(context, ROLLUP, email);
  const errors = watch(page);
  await page.goto(ROLLUP + '/ops#/users');
  await clean(page);
  // Unknown or forbidden pages fall back to the overview; the tabs aren't offered.
  await expect(page.locator('nav.tabs a[data-tab="overview"]')).toHaveAttribute('aria-current', 'page');
  for (const t of ['users', 'safety', 'admin']) await expect(page.locator(`nav.tabs a[data-tab="${t}"]`)).toBeHidden();
  await expect(page.locator('#whoami')).toContainText('analyst');
  expect((await context.request.get(ROLLUP + '/api/ops/roster')).status()).toBe(403);
  expect(errors).toEqual([]);
});

test('mobile and dark layouts hold up', async ({ browser }) => {
  test.skip(!SHOTS, 'screenshots only');
  for (const [name, opts] of [['mobile-light', { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'light' }],
    ['desktop-dark', { viewport: { width: 1280, height: 900 }, colorScheme: 'dark' }],
    ['mobile-dark', { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' }]]) {
    const ctx = await browser.newContext(opts);
    await asAdmin(ctx, ROLLUP);
    const page = await ctx.newPage();
    for (const t of ['overview', 'funnel', 'retention', 'engagement']) {
      await page.goto(ROLLUP + '/ops#/' + t);
      await clean(page);
      await shot(page, `${name}-${t}`);
    }
    await ctx.close();
  }
});
