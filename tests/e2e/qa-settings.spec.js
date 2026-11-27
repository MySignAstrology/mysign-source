// QA walk: Memory, Settings, and the session (back button, refresh, tabs, expiry).
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { go, signIn, onboard, newEmail, signedOutReloaded } = require('./helpers');

const SHOTS = process.env.QA_SHOTS || path.resolve('test-results/qa-shots');
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name, full = true) => page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: full, animations: 'disabled' });

test.beforeEach(({}, ti) => test.skip(ti.project.name !== 'mobile', 'QA walk runs once'));
test.use({ viewport: { width: 390, height: 844 } });

async function toSettings(page, tag, ob = {}) {
  await signIn(page, tag);
  await onboard(page, ob);
  await go(page, 'settings');
  await expect(page.locator('#vSettings')).toHaveClass(/on/);
  await expect(page.locator('#sEmail')).not.toBeEmpty();
}

// Signed out, the app stays the app: its own sign-in screen at /app, in a
// browser tab and the App Store shell alike (UX audit H5).
async function onWebsite(page) {
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.locator('#vAuth')).toHaveClass(/on/);
  await signedOutReloaded(page);
}

test.describe('memory', () => {
  test('empty state for a brand-new account', async ({ page }) => {
    await signIn(page, 'qa-mem');
    await onboard(page);
    await go(page, 'patterns');
    await expect(page.locator('#mSub')).toContainText('Nothing yet');
    await expect(page.locator('#mBody')).toContainText('Days show up here after they end.');
    await shot(page, '61-memory-empty');
  });
});

test.describe('settings', () => {
  test('renders, with the plan and the iPhone not-installed push path', async ({ page }) => {
    await toSettings(page, 'qa-set');
    await expect(page.locator('#planSub')).toContainText('0 of 3');
    // The mobile project emulates an iPhone UA in a browser tab (not installed).
    await expect(page.locator('#pushBtn')).toHaveText('Add to Home Screen');
    // A tappable explanation, not a dead button (main's help path).
    await expect(page.locator('#pushBtn')).toBeEnabled();
    await expect(page.locator('#pushSub')).toContainText('Home Screen');
    await shot(page, '62-settings-390');
    await page.setViewportSize({ width: 375, height: 667 });
    await shot(page, '62-settings-se');
  });

  // FEATURE GAP (HIGH for the brief): /api/tradition exists server-side
  // (house system + rulerships) but Settings has no control for it.
  test('tradition can be changed from Settings', async ({ page }) => {
    await toSettings(page, 'qa-trad');
    await expect(page.locator('#vSettings').getByText(/tradition|house system|whole sign|placidus/i).first())
      .toBeVisible({ timeout: 1500 });
  });

  test('tradition API works (no UI)', async ({ page }) => {
    await toSettings(page, 'qa-trad-api');
    const r = await page.request.get('/api/tradition');
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    const sys = Object.keys(j.options)[1] || Object.keys(j.options)[0];
    const w = await page.request.post('/api/tradition', { data: { houses: sys } });
    expect(w.ok()).toBeTruthy();
  });

  test('add birth time from Settings (unknown-time account)', async ({ page }) => {
    await toSettings(page, 'qa-set-time', { name: 'Nia', time: '' });
    await expect(page.locator('#addTime')).toBeVisible();
    await expect(page.locator('#rectRow')).toBeVisible();
    await page.click('#addTime');
    await page.click('#sheetOk');            // empty
    await expect(page.locator('#sheetErr')).toHaveText('Pick a time first.');
    await shot(page, '63-settings-addtime-sheet', false);
    await page.fill('#sheetInput', '04:42');
    await page.click('#sheetOk');
    await expect(page.locator('#vReading')).toHaveClass(/on/, { timeout: 20000 });
    await expect(page.locator('.b3').nth(2)).not.toHaveClass(/missing/);
  });

  test('add a phone number: two sheets, then it shows masked', async ({ page }) => {
    await page.setExtraHTTPHeaders({ 'cloudfront-viewer-address': `198.51.100.${Math.floor(Math.random() * 250)}:443` });
    await toSettings(page, 'qa-phone');
    const last4 = String(Math.floor(Math.random() * 1e4)).padStart(4, '0');
    await expect(page.locator('#phoneRow')).toBeVisible();
    await page.click('#phoneBtn');
    await page.fill('#sheetInput', '555-12');
    await page.click('#sheetOk');
    await expect(page.locator('#sheetErr')).toHaveText('Enter ten digits, including the area code.');
    await page.fill('#sheetInput', `(312) 555-${last4}`);
    await page.click('#sheetOk');
    await expect(page.locator('#sheetBody')).toContainText('your code is');
    const code = (await page.locator('#sheetBody').innerText()).match(/(\d{6})/)[1];
    await page.fill('#sheetInput', code);
    await page.click('#sheetOk');
    await expect(page.locator('#phoneSub')).toContainText(`•••-${last4}`);
    await expect(page.locator('#phoneBtn')).toHaveText('Change');
  });

  test('country: invalid then valid', async ({ page }) => {
    await toSettings(page, 'qa-country');
    // A picker of names: there is no invalid code left to type.
    await page.click('#countryBtn');
    await expect(page.locator('#sheetSelect')).toBeVisible();
    await expect(page.locator('#sheetInput')).toBeHidden();
    await shot(page, '64-settings-country-picker', false);
    await page.selectOption('#sheetSelect', 'GB');
    await page.click('#sheetOk');
    await expect(page.locator('#countrySub')).toContainText('United Kingdom');
    // The footer's crisis line should follow the country she just set.
    const foot = await page.locator('#foot').innerText();
    test.info().annotations.push({ type: 'footer-after-GB', description: foot });
  });

  // BUG (MEDIUM): changing country updates Settings but the crisis footer keeps
  // the old country's line until a full reload — boot() is the only writer of
  // #foot. For a safety surface, that's the one that matters.
  test('crisis footer follows the new country without a reload', async ({ page }) => {
    await toSettings(page, 'qa-country-foot');
    const before = await page.locator('#foot').innerText();
    await page.click('#countryBtn');
    await page.selectOption('#sheetSelect', 'GB');
    await page.click('#sheetOk');
    await expect(page.locator('#countrySub')).toContainText('United Kingdom');
    await expect(page.locator('#foot')).not.toHaveText(before, { timeout: 2000 });
  });

  test('export downloads a JSON file with her data', async ({ page }) => {
    await toSettings(page, 'qa-export');
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#exportBtn')]);
    expect(dl.suggestedFilename()).toMatch(/^my-sign-\d{4}-\d{2}-\d{2}\.json$/);
    const p = await dl.path();
    const j = JSON.parse(fs.readFileSync(p, 'utf8'));
    expect(j.profile.name).toBe('Maya');
    expect(j.account.email).toContain('@');
  });

  test('delete account: two confirmations, wrong word refused, then gone', async ({ page }) => {
    const email = await (async () => { await toSettings(page, 'qa-del'); return page.locator('#sEmail').innerText(); })();
    await page.click('#delAcct');
    await expect(page.locator('#sheetTitle')).toHaveText('Delete everything?');
    await shot(page, '65-settings-delete-1', false);
    await page.click('#sheetOk');
    await expect(page.locator('#sheetTitle')).toHaveText('Really, all of it?');
    await page.fill('#sheetInput', 'delete');
    await page.click('#sheetOk');
    await expect(page.locator('#sheetErr')).toHaveText('Type DELETE in capitals.');
    await shot(page, '66-settings-delete-2', false);
    await page.fill('#sheetInput', 'DELETE');
    await page.click('#sheetOk');
    await onWebsite(page);
    await page.goto('/app');
    await expect(page.locator('#vAuth')).toHaveClass(/on/, { timeout: 10000 });
    // Signing in again with the same address is a brand-new account.
    await page.fill('#fEmail', email);
    await page.click('#authGo');
    await expect(page.locator('#devCode')).toBeVisible();
    const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
    await page.fill('#fCode', code);
    await page.click('#authGo');
    await expect(page.locator('#vConsent')).toHaveClass(/on/);
  });

  test('sign out returns to the website and the old cookie is dead', async ({ page, context }) => {
    await toSettings(page, 'qa-signout');
    const cookies = await context.cookies();
    await page.click('#signOut');
    await onWebsite(page);
    // Replay the old cookie: it must not work (sessions are revoked).
    await context.addCookies(cookies);
    const r = await page.request.get('/api/state');
    expect((await r.json()).signed_in).toBe(false);
  });

  // UX (LOW): "Sign out" has no confirmation and sits first in Settings, right
  // where a thumb lands. Signing back in costs an email round trip.
  test('sign out has no confirm (documented)', async ({ page }) => {
    await toSettings(page, 'qa-signout-confirm');
    await page.click('#signOut');
    await onWebsite(page);
  });
});

test.describe('crisis footer', () => {
  // BUG (HIGH — safety surface): #foot is only written by boot(). A brand-new
  // user's last boot() runs at consent, BEFORE onboarding sets her country, so
  // a US user sees "find a line at findahelpline.com" instead of "call or text
  // 988" for her whole first session (until a reload). index.html boot() and
  // the #go handler; fix: re-read /api/state (or the footer) after onboarding
  // and after a country change.
  test('a new US user sees 988 in the footer right after onboarding', async ({ page }) => {
    await signIn(page, 'qa-foot');
    await onboard(page);           // Chicago -> US
    await go(page, 'chat');
    await expect(page.locator('#foot')).toContainText('988', { timeout: 2000 });
  });

  test('after a reload the US footer is right', async ({ page }) => {
    await signIn(page, 'qa-foot2');
    await onboard(page);
    await page.reload();
    await expect(page.locator('#foot')).toContainText('988');
  });
});

test.describe('session and navigation', () => {
  // BUG (HIGH for an iOS wrapper / Android): the app never touches history.
  // The hardware/gesture Back from any tab leaves the app entirely (to
  // about:blank here), instead of returning to the previous tab.
  test('browser Back goes to the previous tab, not out of the app', async ({ page }) => {
    await signIn(page, 'qa-back');
    await onboard(page);
    await go(page, 'people');
    await expect(page.locator('#vPeople')).toHaveClass(/on/);   // a person waits for the slide
    await go(page, 'settings');
    await expect(page.locator('#vSettings')).toHaveClass(/on/);
    // Settings is reached through Me, so Back retraces that: Me, then People.
    await page.goBack().catch(() => {});
    await expect(page.locator('#vMe')).toHaveClass(/on/, { timeout: 2000 });
    await page.goBack().catch(() => {});
    await expect(page.locator('#vPeople')).toHaveClass(/on/, { timeout: 2000 });
  });

  // Was UX (MEDIUM): refresh always landed on Today, whichever tab you were on.
  test('refresh keeps the current tab', async ({ page }) => {
    await signIn(page, 'qa-refresh-tab');
    await onboard(page);
    await go(page, 'people');
    await page.reload();
    await expect(page.locator('#vPeople')).toHaveClass(/on/, { timeout: 3000 });
  });

  test('refresh on consent stays on consent', async ({ page }) => {
    await page.goto('/app');
    const email = newEmail('qa-refresh-consent');
    await page.fill('#fEmail', email);
    await page.click('#authGo');
    await expect(page.locator('#devCode')).toBeVisible();
    const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
    await page.fill('#fCode', code);
    await page.click('#authGo');
    await expect(page.locator('#vConsent')).toHaveClass(/on/);
    await page.reload();
    await expect(page.locator('#vConsent')).toHaveClass(/on/);
  });

  test('two tabs: sign out in one, the other recovers to sign-in on next action', async ({ page, context }) => {
    await signIn(page, 'qa-tabs');
    await onboard(page);
    const other = await context.newPage();
    await other.goto('/app');
    await expect(other.locator('#vMe')).toHaveClass(/on/);
    await go(page, 'settings');
    await page.click('#signOut');
    await onWebsite(page);
    // Tab two: navigate to People -> 401 -> should put sign-in in front.
    await go(other, 'people');
    await expect(other.locator('#vAuth')).toHaveClass(/on/);
    await expect(other.locator('#authErr')).toContainText('signed out');
    await shot(other, '67-session-expired-other-tab', false);
  });

  // The cookie disappears mid-conversation (expiry, revoked elsewhere).
  test('expired session mid-chat', async ({ page, context }) => {
    await signIn(page, 'qa-expiry');
    await onboard(page);
    await go(page, 'chat');
    await expect(page.locator('#checkin #moods')).toBeVisible();
    await page.waitForLoadState('networkidle');
    await context.clearCookies();
    await page.fill('#input', 'still there?');
    await page.click('#send');
    await expect(page.locator('#thread .msg').last()).toContainText('signed out', { timeout: 5000 });
    await shot(page, '68-session-expired-chat', false);
    // send() reloads after 2.5s; sign-in should be in front after that.
    await expect(page.locator('#vAuth')).toHaveClass(/on/, { timeout: 8000 });
  });
});
