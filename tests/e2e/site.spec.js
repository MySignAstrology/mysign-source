// The public site (site/), driven through site/serve.py exactly as a visitor
// would: landing page → start sheet → emailed code → /app with a cookie.
//
// Self-contained: it starts its own app (dev auth, its own data dir) and its
// own serve.py on spare ports, so it does not depend on the config's
// webServer and never touches local development state.
const { test, expect } = require('@playwright/test');
const { spawn } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');
const { expectNoHorizontalOverflow } = require('./helpers');
let AxeBuilder = null;
try { AxeBuilder = require('@axe-core/playwright').default; } catch (e) { /* optional */ }

const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.PYTHON || 'python';
let app, site, SITE;

function freePort() {
  return new Promise((res, rej) => {
    const s = net.createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
    s.on('error', rej);
  });
}
async function waitFor(url, ms = 30_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { const r = await fetch(url); if (r.status < 500) return; } catch (e) { /* not up yet */ }
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error('timed out waiting for ' + url);
}

test.describe.configure({ mode: 'serial' });
// These tests are about what happens after a visitor says yes to the random id.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('ms_consent', 'yes'); } catch (e) {} });
});


test.beforeAll(async () => {
  test.setTimeout(90_000);
  const [ap, sp] = [await freePort(), await freePort()];
  const data = path.join(ROOT, 'tests/e2e/.data/site-' + ap);
  fs.mkdirSync(data, { recursive: true });
  app = spawn(PY, ['-m', 'uvicorn', 'app.server:app', '--host', '127.0.0.1', '--port', String(ap)],
    { cwd: ROOT, env: { ...process.env, MYSIGN_DEV_AUTH: '1', MYSIGN_DATA: data, PYTHONPATH: '.' }, stdio: 'ignore' });
  site = spawn(PY, ['site/serve.py', '--port', String(sp), '--upstream', `http://127.0.0.1:${ap}`],
    { cwd: ROOT, stdio: 'ignore' });
  SITE = `http://127.0.0.1:${sp}`;
  await waitFor(`http://127.0.0.1:${ap}/api/state`, 60_000);
  await waitFor(SITE + '/');
});
test.afterAll(() => { for (const p of [site, app]) if (p) p.kill(); });

const newEmail = tag => `${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.test`;

/** Collects what track.js sends to /e (the collector isn't deployed here). */
async function collect(context) {
  const events = [];
  await context.route('**/e', async route => {
    try { events.push(...JSON.parse(route.request().postData() || '{}').events); } catch (e) { /* ignore */ }
    await route.fulfill({ status: 204 });
  });
  return events;
}

test('start → email → code → /app, signed in', async ({ page, context }) => {
  const events = await collect(context);
  const email = newEmail('site');
  await page.route('**/api/auth/channels', r => r.fulfill({ json: { email: true, sms: false } }));
  await page.goto(SITE + '/?utm_source=tiktok&invite=abcdefghijkl');
  // The page is up, not what the headline says: copy passes (#112) kept
  // rewording it, and this test is about the journey after it.
  await expect(page.locator('#h-hero')).toBeVisible();
  await expect(page.locator('#h-hero')).not.toBeEmpty();

  await page.locator('.herosec [data-open="start"]').click();
  const sheet = page.locator('dialog#start');
  await expect(sheet).toBeVisible();
  await expect(page.locator('#sId')).toBeFocused();
  // Email only, whatever this machine's SMS settings are.
  await expect(page.locator('#sIdLabel')).toHaveText('Email');
  await expect(page.locator('#sGo')).toBeDisabled();

  await page.fill('#sId', email);
  await page.click('#sGo');
  await expect(page.locator('#devCode')).toBeVisible();
  await expect(page.locator('#sSent')).toContainText(email);
  await expect(page.locator('#sResend')).toBeDisabled();
  const pending = await page.evaluate(() => JSON.parse(sessionStorage.getItem('ms_code_for')));
  expect(pending).toEqual({ kind: 'email', value: email });

  // A wrong code is refused in words, and the sheet stays put.
  await page.fill('#sCode', '000000');
  await expect(page.locator('#sErr')).not.toBeEmpty();

  const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  // The app takes the invite out of the address bar as it boots, so the query
  // is checked on the navigation itself.
  const toApp = page.waitForRequest(r => r.isNavigationRequest() && new URL(r.url()).pathname === '/app');
  await page.fill('#sCode', code);   // six digits submit on their own
  expect(new URL((await toApp).url()).search).toBe('?utm_source=tiktok&invite=abcdefghijkl');
  await page.waitForURL(u => new URL(u).pathname === '/app');

  const state = await page.request.get(SITE + '/api/state');
  expect((await state.json()).signed_in).toBe(true);
  expect(await page.evaluate(() => sessionStorage.getItem('ms_code_for'))).toBeNull();

  await expect.poll(() => events.map(e => e.name)).toEqual(expect.arrayContaining(
    ['home', 'start_open', 'start_submit', 'start_code_sent', 'start_verified']));
  const req = events.find(e => e.name === 'start_code_sent');
  expect(req).toMatchObject({ source: 'site', type: 'tap', screen: 'home', props: { channel: 'email' },
    ctx: { utm_source: 'tiktok', landing_path: '/' } });
  expect(req.anon_id).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
  expect(req.session_id).toBeTruthy();
  for (const e of events) expect(e.name).toMatch(/^[a-z0-9][a-z0-9_.:-]{0,59}$/);
});

test('auth bodies carry anon_id; a refresh resumes the code step', async ({ page }) => {
  const bodies = [];
  page.on('request', r => { if (r.url().includes('/api/auth/')) bodies.push(r.postDataJSON()); });
  const email = newEmail('resume');
  await page.goto(SITE + '/');
  await page.click('header [data-open="signin"]');
  await expect(page.locator('#sTitle')).toHaveText('Welcome back.');
  await page.fill('#sId', email);
  await page.click('#sGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const anon = await page.evaluate(() => localStorage.getItem('ms_anon'));
  expect(bodies.at(-1)).toEqual({ email, anon_id: anon });

  await page.reload();
  await page.click('header [data-open="signin"]');
  await expect(page.locator('#sStep2')).toBeVisible();
  await expect(page.locator('#sSent')).toContainText(email);
  await expect(page.locator('#sCode')).toBeFocused();

  await page.click('#sChange');
  await expect(page.locator('#sStep1')).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('ms_code_for'))).toBeNull();
});

test('digits without SMS ask for an email; Escape closes and returns focus', async ({ page }) => {
  await page.route('**/api/auth/channels', r => r.fulfill({ json: { email: true, sms: false } }));
  await page.goto(SITE + '/');
  const opener = page.locator('.herosec [data-open="start"]');
  await opener.click();
  await page.fill('#sId', '5551234567');
  await expect(page.locator('#sIdHint')).toContainText('email');
  await expect(page.locator('#sGo')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('dialog#start')).toBeHidden();
  await expect(opener).toBeFocused();
});

test('with SMS on, digits become a US phone in E.164', async ({ page }) => {
  // The server has no SMS yet; stand in for the routes another branch adds.
  let sent = null;
  await page.route('**/api/auth/channels', r => r.fulfill({ json: { email: true, sms: true } }));
  await page.route('**/api/auth/request', r => { sent = r.request().postDataJSON(); return r.fulfill({ json: { sent: true, channel: 'sms' } }); });
  await page.route('**/api/auth/verify', r => r.fulfill({ status: 400, json: { detail: 'That code didn’t match.' } }));
  await page.goto(SITE + '/');
  await page.locator('.herosec [data-open="start"]').click();
  await expect(page.locator('#sIdLabel')).toHaveText('Phone or email');
  await page.fill('#sId', '1 (555) 234-5678');
  await expect(page.locator('#sIdHint')).toContainText('(555) 234-5678');
  await page.click('#sGo');
  await expect(page.locator('#sSent')).toContainText('(555) 234-5678');
  expect(sent).toMatchObject({ phone: '+15552345678' });
  await expect(page.locator('#devCode')).toBeHidden();
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('ms_code_for'))))
    .toEqual({ kind: 'phone', value: '+15552345678' });
  await page.fill('#sCode', '123456');
  await expect(page.locator('#sErr')).toHaveText('That code didn’t match.');
});

test('global privacy control: no ids are kept or sent', async ({ browser }) => {
  const context = await browser.newContext();
  await context.addInitScript(() => Object.defineProperty(navigator, 'globalPrivacyControl', { get: () => true }));
  const events = await collect(context);
  const page = await context.newPage();
  await page.goto(SITE + '/');
  await page.locator('.herosec [data-open="start"]').click();
  await page.evaluate(() => window.MsTrack.flush());
  await expect.poll(() => events.length).toBeGreaterThan(0);
  for (const e of events) { expect(e.anon_id).toBeNull(); expect(e.session_id).toBeNull(); }
  expect(await page.evaluate(() => localStorage.getItem('ms_anon'))).toBeNull();
  await context.close();
});

for (const scheme of ['light', 'dark']) {
  test(`axe @${scheme}: page and sheet`, async ({ page }, ti) => {
    test.skip(!AxeBuilder, '@axe-core/playwright not installed');
    test.skip(ti.project.name === 'tablet', 'mobile and desktop cover it');
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await page.goto(SITE + '/');
    await page.evaluate(() => document.querySelectorAll('.rv').forEach(e => e.classList.add('in')));
    await page.waitForTimeout(600);
    const run = async () => (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze())
      .violations.map(v => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map(n => n.target.join(' ')).join(' | ')}`);
    expect(await run()).toEqual([]);
    await page.locator('.herosec [data-open="start"]').click();
    await page.waitForTimeout(400);
    expect(await run()).toEqual([]);
  });
}

test('the free reading works with no account, then carries into sign-up', async ({ page }) => {
  await page.goto(SITE + '/');
  await page.fill('#frDate', '1996-07-22');
  await page.click('#frGo');
  await expect(page.locator('#frMore')).toBeVisible();
  await page.fill('#frTime', '18:40');
  await page.locator('#frCity').pressSequentially('Austin, T');
  await page.locator('#frAc button').first().click();
  await page.click('#frDeep');
  await expect(page.locator('#freeRes')).toBeVisible();
  await expect(page.locator('#frList li')).toHaveCount(3);
  await expect(page.locator('#frSigns')).toContainText('Leo Sun');
  const state = await page.request.get(SITE + '/api/state');
  expect((await state.json()).signed_in).toBeFalsy();

  await page.click('#frSave');
  await expect(page.locator('#sTitle')).toContainText('full reading');
  await page.fill('#sId', `free-${Date.now()}@example.com`);
  await page.click('#sGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await page.fill('#sCode', code);
  await page.waitForURL(u => new URL(u).pathname === '/app');
  // Past consent, the app's form is already filled in.
  await expect.poll(() => page.evaluate(() => document.getElementById('fDate')?.value)).toBe('1996-07-22');
  expect(await page.inputValue('#fTime')).toBe('18:40');
  expect(await page.inputValue('#fCity')).toContain('Austin');
  await expect(page.locator('#obFromFree')).toBeAttached();
});

async function freeReading(page) {
  await page.goto(SITE + '/');
  await page.fill('#frDate', '1996-07-22');
  await page.click('#frGo');
  await expect(page.locator('#frMore')).toBeVisible();
  await page.fill('#frTime', '18:40');
  await page.locator('#frCity').pressSequentially('Austin, T');
  await page.locator('#frAc button').first().click();
  await page.click('#frDeep');
  await expect(page.locator('#frList li')).toHaveCount(3);
  await expect(page.locator('#frList .frcard button[data-ready]')).toHaveCount(3, { timeout: 15000 });
}
const CARD = '#frList .frcard button';
// Mouse or touch, whatever the test project's device says.
const pointer = (page, coarse) => page.addInitScript(c => {
  const real = window.matchMedia.bind(window);
  window.matchMedia = q => q === '(pointer: coarse)' ? { matches: c } : real(q);
}, coarse);

test('with a mouse, a card downloads as a Story-sized image and tells the server nothing', async ({ page }) => {
  await pointer(page, false);
  await page.addInitScript(() => { navigator.canShare = () => false; });
  const sent = [];
  page.on('request', r => {
    const p = new URL(r.url()).pathname;
    if (r.method() === 'POST' && p !== '/api/free-reading' && p !== '/api/free-glimpse' && p !== '/e') sent.push(p);
  });
  await freeReading(page);
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator(CARD).first().click()]);
  expect(download.suggestedFilename()).toBe('my-sign-toxic-trait.png');
  const png = fs.readFileSync(await download.path());
  expect(png.toString('latin1', 1, 4)).toBe('PNG');
  expect(png.readUInt32BE(16)).toBe(1080);
  expect(png.readUInt32BE(20)).toBe(1920);
  await expect(page.locator('#frList .frcard .hint').first()).toContainText('downloads');
  expect(sent).toEqual([]);
});

test('on a phone the card opens the share sheet, as a file', async ({ page }) => {
  await page.addInitScript(() => {
    window.__shared = [];
    navigator.canShare = () => true;
    navigator.share = d => { window.__shared.push(d.files.map(f => [f.name, f.type, f.size])); return Promise.resolve(); };
  });
  await freeReading(page);
  await page.locator(CARD).first().click();
  const shared = await page.evaluate(() => window.__shared);
  expect(shared[0][0][0]).toBe('my-sign-toxic-trait.png');
  expect(shared[0][0][1]).toBe('image/png');
  expect(shared[0][0][2]).toBeGreaterThan(10000);
  await expect(page.locator('.frcardimg')).toHaveCount(0);
});

test('in an in-app browser that cannot share, the image itself is there to save', async ({ page }) => {
  await pointer(page, true);
  await page.addInitScript(() => { delete Navigator.prototype.share; delete Navigator.prototype.canShare; });
  await freeReading(page);
  let downloaded = false;
  page.on('download', () => { downloaded = true; });
  await page.locator(CARD).first().click();
  const img = page.locator('#frList li').first().locator('.frcardimg');
  await expect(img).toBeVisible();
  await expect.poll(() => img.evaluate(i => i.naturalWidth)).toBe(1080);
  await expect(page.locator('#frList .frcard .hint').first()).toContainText('Press and hold');
  expect(downloaded).toBe(false);
  await expectNoHorizontalOverflow(page);
});

test('a share sheet that fails falls back to the image, and cancelling does not', async ({ page }) => {
  await pointer(page, true);
  await page.addInitScript(() => {
    window.__err = 'AbortError';
    navigator.canShare = () => true;
    navigator.share = () => Promise.reject(new DOMException('no', window.__err));
  });
  await freeReading(page);
  await page.locator(CARD).first().click();
  await expect(page.locator('.frcardimg')).toHaveCount(0);
  await page.evaluate(() => { window.__err = 'NotAllowedError'; });
  await page.locator(CARD).first().click();
  await expect(page.locator('.frcardimg')).toHaveCount(1);
});

test('a font that never loads does not hold up the card', async ({ page }) => {
  await page.addInitScript(() => { document.fonts.load = () => new Promise(() => {}); });
  await freeReading(page);
});

test('the card speaks for whose chart it is, and carries no birth details', async ({ page }) => {
  await page.addInitScript(() => {
    window.__drawn = [];
    const real = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function(t, ...rest) { window.__drawn.push(String(t)); return real.call(this, t, ...rest); };
  });
  await freeReading(page);
  let drawn = (await page.evaluate(() => window.__drawn)).join(' | ');
  expect(drawn).toContain('MY TOXIC TRAIT');
  expect(drawn).toContain('Leo Sun');
  expect(drawn).not.toMatch(/1996|18:40|Austin/);

  await page.click('#frAgain');
  await page.evaluate(() => { window.__drawn = []; });
  await page.fill('#frDate', '1996-07-22');
  await page.click('#frGo');
  await expect(page.locator('#frMore')).toBeVisible();
  await page.fill('#frTime', '18:40');  // the Sun changes sign that day, so a time is what keeps the line
  await page.locator('#frCity').pressSequentially('Austin, T');
  await page.locator('#frAc button').first().click();
  await page.click('#frDeep');
  // The first reading's buttons are still in the list until this one replaces them.
  await expect.poll(() => page.evaluate(() => window.__drawn.join(' | ')), { timeout: 15000 }).toContain('YOUR TOXIC TRAIT');
  drawn = (await page.evaluate(() => window.__drawn)).join(' | ');
  expect(drawn).not.toContain('MY TOXIC TRAIT');
});

test('/ig: the free reading, then sign-up, keeps the Instagram tag', async ({ page }) => {
  await page.goto(SITE + '/ig.html');
  await expect(page).toHaveURL(/utm_source=instagram/);
  await page.fill('#frDate', '1996-07-22');
  await page.click('#frGo');
  await expect(page.locator('#frMore')).toBeVisible();
  await page.fill('#frTime', '18:40');
  await page.locator('#frCity').pressSequentially('Austin, T');
  await page.locator('#frAc button').first().click();
  await page.click('#frDeep');
  await expect(page.locator('#frList li')).toHaveCount(3);

  await page.click('#frSave');
  await page.fill('#sId', newEmail('ig'));
  await page.click('#sGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await page.fill('#sCode', code);
  await page.waitForURL(u => new URL(u).pathname === '/app');
  expect(new URL(page.url()).searchParams.get('utm_source')).toBe('instagram');
  await expect.poll(() => page.evaluate(() => document.getElementById('fDate')?.value)).toBe('1996-07-22');
});

test('a chart read for someone else never fills in her own sign-up', async ({ page }) => {
  await page.goto(SITE + '/');
  const read = async (date) => {
    await page.fill('#frDate', date);
    await page.click('#frGo');
    await expect(page.locator('#frMore')).toBeVisible();
    await page.locator('#frCity').pressSequentially('Austin, T');
    await page.locator('#frAc button').first().click();
    await page.click('#frDeep');
    await expect(page.locator('#freeRes')).toBeVisible();
  };
  await read('1996-07-22');
  expect(await page.evaluate(() => sessionStorage.getItem('ms_free_read'))).toContain('1996-07-22');
  await page.click('#frAgain');
  expect(await page.evaluate(() => sessionStorage.getItem('ms_free_read'))).toBeNull();
  await read('1990-03-03');
  expect(await page.evaluate(() => sessionStorage.getItem('ms_free_read'))).toBeNull();
  await expect(page.locator('#frSave')).toHaveText('Make my own chart');
});

test('every row of the free reading city list can be tapped', async ({ page }) => {
  await page.goto(SITE + '/');
  await page.fill('#frDate', '1996-07-22');
  await page.click('#frGo');
  await expect(page.locator('#frMore')).toBeVisible();
  await page.locator('#frCity').pressSequentially('San');
  const rows = page.locator('#frAc button');
  await expect(rows.nth(3)).toBeVisible();
  for (let i = 0; i < await rows.count(); i++) {
    const on = await rows.nth(i).evaluate(el => {
      // The page scrolls smoothly; instant, or the position below is stale.
      el.scrollIntoView({ block: 'center', behavior: 'instant' });
      const s = el.getBoundingClientRect();
      return document.elementFromPoint(s.left + s.width / 2, s.top + s.height / 2) === el;
    });
    expect(on, `row ${i} is covered`).toBe(true);
  }
});

test('the landing page is one question, and it fits a phone', async ({ page }) => {
  await page.goto(SITE + '/');
  await expectNoHorizontalOverflow(page);
  await expect(page.locator('#frDate')).toBeVisible();
  await expect(page.locator('#frGo')).toBeInViewport();
  await expect(page.locator('.herosec [data-open="start"]')).toBeVisible();
  for (const gone of ['#you', '.final', '#faq', '.note']) await expect(page.locator(gone)).toHaveCount(0);
  await expect(page.locator('footer nav a[href="/privacy"]')).toBeVisible();
});
