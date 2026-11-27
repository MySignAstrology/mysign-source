// The app's own analytics (the Track block in app/static/index.html): the
// device id shared with the website, the anonymous sign-in screen, time in
// app, and a push being opened.
//
// Self-contained like site.spec.js: its own app on a spare port, with
// MYSIGN_COLLECTOR set so the sign-in screen counts. /e is intercepted —
// there is no collector here.  npx playwright test -c tests/e2e/tracking.config.js
const { test, expect } = require('@playwright/test');
const { spawn } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.PYTHON || 'python';
const ULID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
let app, BASE;

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

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  test.setTimeout(90_000);
  const port = await freePort();
  const data = path.join(ROOT, 'tests/e2e/.data/tracking-' + port);
  fs.mkdirSync(data, { recursive: true });
  app = spawn(PY, ['-m', 'uvicorn', 'app.server:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: ROOT, stdio: 'ignore',
      env: { ...process.env, MYSIGN_DEV_AUTH: '1', MYSIGN_DATA: data, MYSIGN_COLLECTOR: '1', PYTHONPATH: '.' } });
  BASE = `http://127.0.0.1:${port}`;
  await waitFor(BASE + '/api/state');
});
test.afterAll(() => { if (app) app.kill(); });

const newEmail = tag => `${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.test`;

/** Everything the page sends: /e batches, /api/activity batches, auth bodies. */
async function wiretap(context) {
  const got = { e: [], activity: [], auth: [] };
  await context.route('**/e', async route => {
    got.e.push(JSON.parse(route.request().postData() || '{}'));
    await route.fulfill({ status: 204 });
  });
  context.on('request', r => {
    const u = new URL(r.url());
    if (u.pathname === '/api/activity') got.activity.push(r.postDataJSON());
    if (u.pathname.startsWith('/api/auth/')) got.auth.push({ path: u.pathname, body: r.postDataJSON() });
  });
  return got;
}

async function signedIn(context, tag) {
  const email = newEmail(tag);
  const req = context.request;
  const code = (await (await req.post(BASE + '/api/auth/request', { data: { email } })).json()).dev_code;
  await req.post(BASE + '/api/auth/verify', { data: { email, code } });
  await req.post(BASE + '/api/consent', { data: { ai: true } });
  await req.post(BASE + '/api/onboard', { data: { name: 'Maya', date: '1999-03-14', time: '04:42', city: 'Chicago IL' } });
}

const events = batches => batches.flatMap(b => b.events || []);

// These tests are about what happens after a visitor says yes to the random id.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('ms_consent', 'yes'); } catch (e) {} });
});

test('the sign-in screen counts anonymously and the device id joins the sign-up', async ({ page, context }) => {
  const got = await wiretap(context);
  await page.goto(BASE + '/app');
  await expect(page.locator('#vAuth')).toHaveClass(/on/);
  await page.fill('#fEmail', newEmail('trk'));
  await page.click('#authGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const anon = await page.evaluate(() => localStorage.getItem('ms_anon'));
  expect(anon).toMatch(ULID);
  const code = (await page.locator('#devCode').textContent()).match(/\d{6}/)[0];
  await page.fill('#fCode', code);
  await page.click('#authGo');
  await expect(page.locator('#vConsent')).toHaveClass(/on/);

  // What she did signed out went to the collector, as the app, with the id.
  await expect.poll(() => events(got.e).map(e => e.name)).toEqual(
    expect.arrayContaining(['sign_in', 'auth_go']));
  for (const b of got.e) {
    expect(b.source).toBe('app');
    expect(b.anon_id).toBe(anon);
    expect(b.session).toBeTruthy();
    expect(b.ctx.landing).toBe('/app');
  }
  for (const e of events(got.e)) expect(e.at).toBeGreaterThan(0);
  // Never anything typed.
  expect(JSON.stringify(got.e)).not.toContain('@example.test');

  const bodies = got.auth.filter(a => /request|verify/.test(a.path)).map(a => a.body);
  expect(bodies.length).toBe(2);
  for (const b of bodies) expect(b.anon_id).toBe(anon);

  // Not tracked between sign-in and consent; then under her account.
  const before = got.e.length;
  await page.check('#aiOk');
  await page.click('#consentGo');
  await expect(page.locator('#vOnboard')).toHaveClass(/on/);
  await page.evaluate(() => Track.flush());
  await expect.poll(() => got.activity.length).toBeGreaterThan(0);
  expect(got.activity[0].anon_id).toBe(anon);
  expect(events(got.activity).map(e => e.name)).toContain('onboard');
  expect(got.e.length).toBe(before);
});

test('time in app: a heartbeat every 30s, sent in a batch about once a minute', async ({ page, context }) => {
  await signedIn(context, 'beat');
  const got = await wiretap(context);
  // Me's crystal ball draws every frame; under a fake clock ten minutes is
  // 36,000 draws. It is not what this test is about: leave the plain ball.
  await context.route('**/static/orb.js', route => route.abort());
  await page.clock.install();
  await page.goto(BASE + '/app');
  await expect(page.locator('#vMe')).toHaveClass(/on/);
  await page.mouse.move(5, 5);
  await page.mouse.down(); await page.mouse.up();
  await page.evaluate(() => Track.flush());
  await expect.poll(() => got.activity.length).toBeGreaterThan(0);
  const sent = got.activity.length;
  await page.clock.runFor(31_000);
  expect(got.activity.length).toBe(sent);            // a lone beat waits for company
  await page.clock.runFor(30_000);
  await expect.poll(() => events(got.activity).filter(e => e.type === 'heartbeat').length)
    .toBeGreaterThanOrEqual(1);
  expect(got.activity.length).toBe(sent + 1);
  const beat = events(got.activity).find(e => e.type === 'heartbeat');
  expect(beat).toMatchObject({ name: 'app', screen: 'me' });

  // Nobody touching it: the beats stop.
  const n = events(got.activity).filter(e => e.type === 'heartbeat').length;
  await page.clock.runFor(10 * 60_000);
  await page.evaluate(() => Track.flush());
  const later = events(got.activity).filter(e => e.type === 'heartbeat').length;
  expect(later - n).toBeLessThanOrEqual(10);         // ~5 minutes' worth, not 20
});

test('a push opened is counted once and its marker leaves the address bar', async ({ page, context }) => {
  await signedIn(context, 'push');
  const got = await wiretap(context);
  await page.goto(BASE + '/?from=push&pk=morning');
  await expect(page.locator('#vMain')).toHaveClass(/on/);
  expect(new URL(page.url()).search).toBe('');
  await page.evaluate(() => Track.flush());
  await expect.poll(() => events(got.activity).filter(e => e.name === 'push_opened'))
    .toEqual([expect.objectContaining({ type: 'open', props: { kind: 'morning' } })]);
});

test('with Global Privacy Control no id is made or sent', async ({ page, context }) => {
  await context.addInitScript(() =>
    Object.defineProperty(Navigator.prototype, 'globalPrivacyControl', { get: () => true }));
  const got = await wiretap(context);
  await page.goto(BASE + '/app');
  await expect(page.locator('#vAuth')).toHaveClass(/on/);
  await page.fill('#fEmail', newEmail('gpc'));
  await page.click('#authGo');
  await expect(page.locator('#devCode')).toBeVisible();
  await page.evaluate(() => Track.flush());
  await expect.poll(() => got.e.length).toBeGreaterThan(0);
  for (const b of got.e) { expect(b.anon_id).toBeNull(); expect(b.session).toBeNull(); }
  expect(got.auth.find(a => a.path === '/api/auth/request').body).not.toHaveProperty('anon_id');
  expect(await page.evaluate(() => localStorage.getItem('ms_anon'))).toBeNull();
});
