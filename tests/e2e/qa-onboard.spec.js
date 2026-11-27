// QA walk: onboarding and the first reading.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { go, signIn, onboard, skipAbout, expectNoHorizontalOverflow } = require('./helpers');

const SHOTS = process.env.QA_SHOTS || path.resolve('test-results/qa-shots');
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true, animations: 'disabled' });

test.beforeEach(({}, ti) => test.skip(ti.project.name !== 'mobile', 'QA walk runs once'));
test.use({ viewport: { width: 390, height: 844 } });

async function pickCity(page, q = 'chicago, il') {
  await page.fill('#fCity', q);
  const opt = page.locator('#ac button').first();
  await expect(opt).toBeVisible();
  await opt.click();
}
function yearsAgo(n, plusDays = 0) {
  const d = new Date(); d.setFullYear(d.getFullYear() - n); d.setDate(d.getDate() + plusDays);
  // The local date, not toISOString's UTC one: in the evening in the Americas
  // UTC is already tomorrow, and "exactly 18 today" became 17 for the server.
  const p = x => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

test.describe('onboarding', () => {
  test('empty form, then the full happy path at three sizes', async ({ page }) => {
    await signIn(page, 'qa-ob');
    await shot(page, '10-onboard-empty-390');
    await page.setViewportSize({ width: 375, height: 667 });
    await shot(page, '10-onboard-empty-se');
    await expect(page.locator('#go')).toBeDisabled();
    await page.fill('#fName', 'Maya');
    await page.fill('#fDate', '1999-03-14');
    await page.fill('#fTime', '04:42');
    await page.fill('#fCity', 'chic');
    await expect(page.locator('#ac button').first()).toBeVisible();
    await shot(page, '11-onboard-autocomplete-se');
    await page.locator('#ac button').first().click();
    await expect(page.locator('#go')).toBeEnabled();
    await expectNoHorizontalOverflow(page);
  });

  test('unknown birth time path', async ({ page }) => {
    await signIn(page, 'qa-notime');
    await page.click('#noTime');
    await expect(page.locator('#fTime')).toBeDisabled();
    await expect(page.locator('#noTime')).toHaveText('Actually, I know it');
    await shot(page, '12-onboard-skip-time');
    await page.click('#noTime');                    // helper toggles it again
    await onboard(page, { name: 'Nia', time: '' });
    await expect(page.locator('.b3').nth(2)).toHaveClass(/missing/);
    await shot(page, '13-reading-no-time');
  });

  test('17-year-old is refused with a clear message', async ({ page }) => {
    await signIn(page, 'qa-underage');
    await page.fill('#fName', 'Kid');
    await page.fill('#fDate', yearsAgo(17));
    await page.fill('#fTime', '10:00');
    await pickCity(page);
    await page.click('#go');
    await expect(page.locator('#obErr')).toHaveText('My Sign is for people 18 and over.');
    await expect(page.locator('#vOnboard')).toHaveClass(/on/);
    await shot(page, '14-onboard-underage');
  });

  test('exactly 18 today is let in', async ({ page }) => {
    await signIn(page, 'qa-18');
    await onboard(page, { name: 'Bday', date: yearsAgo(18), time: '10:00' });
  });

  // UX/BUG (MEDIUM): a city with no matches shows nothing at all — no "no
  // matches" row — and the button just stays disabled. Typing a valid city and
  // not tapping a suggestion looks identical. Nothing tells her why she's stuck.
  test('a city with no matches says so', async ({ page }) => {
    await signIn(page, 'qa-badcity');
    await page.fill('#fName', 'Maya'); await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
    await page.fill('#fCity', 'Qwxzzplk');
    await page.waitForTimeout(600);
    await shot(page, '15-onboard-bad-city');
    // No list match offers the wider search (#60) and the nearest-town hint.
    await expect(page.locator('#ac [data-more]')).toBeVisible({ timeout: 1500 });
    await expect(page.locator('#ac .achint')).toContainText('nearest town');
  });

  test('a typed (not picked) city leaves the button disabled', async ({ page }) => {
    await signIn(page, 'qa-typedcity');
    await page.fill('#fName', 'Maya'); await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
    await page.fill('#fCity', 'Chicago');
    await page.waitForTimeout(500);
    await page.locator('#fName').click();      // dismiss
    await expect(page.locator('#go')).toBeDisabled();
    // BUG (LOW): the suggestion list stays open after focus leaves the field.
    await shot(page, '16-onboard-typed-city-not-picked');
  });

  // BUG (HIGH): FastAPI validation errors (422) carry `detail` as an ARRAY.
  // api() does `new Error(body.detail)`, so the user sees "[object Object]".
  // index.html:900. Reachable from onboarding with a 61+ char name (the input
  // has no maxlength; server max_length=60), from check-in text >500 chars, and
  // from People names >60.
  test.fail('a 61-character name gets a human error, not [object Object]', async ({ page }) => {
    await signIn(page, 'qa-longname');
    await page.fill('#fName', 'A'.repeat(61));
    await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
    await pickCity(page);
    await page.click('#go');
    await expect(page.locator('#obErr')).not.toBeEmpty();
    await shot(page, '17-onboard-61-char-name');
    await expect(page.locator('#obErr')).not.toContainText('[object Object]');
  });

  test('garbage and markup in the name are rendered as text', async ({ page }) => {
    await signIn(page, 'qa-garbage');
    const evil = '<img src=x onerror=window.__pwned=1>🌙✨';
    await onboard(page, { name: evil });
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    await expect(page.locator('#rdName')).toContainText('<img');
  });

  // BUG (HIGH): any long unbroken token in the name (a hyphenated double-barrel
  // surname, an email pasted by mistake, emoji runs) overflows .rd-name, which
  // has no overflow-wrap. On a phone the layout viewport widens to ~520px, the
  // whole page zooms out, and the fixed tab bar ends up UNDER the reading cards
  // — Playwright reports `.b3 intercepts pointer events` on the tab.
  // index.html:351 (.rd-name) — add overflow-wrap:anywhere; same for .greet h1
  // (:235) and .conn .nm (:474).
  test('with an unbroken long name the tab bar is still tappable', async ({ page }) => {
    await signIn(page, 'qa-garbage2');
    await onboard(page, { name: '<img src=x onerror=window.__pwned=1>🌙✨' });
    await page.click('#tabChat', { timeout: 5000 });
    await expect(page.locator('#greet h1')).toContainText('🌙');
  });

  test('a whitespace-only name keeps the button disabled', async ({ page }) => {
    await signIn(page, 'qa-blankname');
    await page.fill('#fName', '    ');
    await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
    await pickCity(page);
    await expect(page.locator('#go')).toBeDisabled();
  });

  test('a birth year before 1900 is refused', async ({ page }) => {
    await signIn(page, 'qa-1899');
    await page.fill('#fName', 'Old'); await page.fill('#fDate', '1899-01-01'); await page.fill('#fTime', '10:00');
    await pickCity(page);
    await page.click('#go');
    await expect(page.locator('#obErr')).toHaveText("That birth year doesn't look right.");
  });

  // UX (MEDIUM): the date input accepts a future date with no min/max; the
  // server answers with the under-18 message, which is the wrong reason.
  test('a future birth date', async ({ page }) => {
    await signIn(page, 'qa-future');
    await page.fill('#fName', 'Future'); await page.fill('#fDate', `${new Date().getFullYear()}-12-31`);
    await page.fill('#fTime', '10:00');
    await pickCity(page);
    await page.click('#go');
    await expect(page.locator('#obErr')).not.toBeEmpty();
    await shot(page, '18-onboard-future-date');
  });

  // BUG (HIGH): see above — 60-char name scrolls sideways (524px > 375px).
  test('60-character name: reading header and greeting do not overflow', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await signIn(page, 'qa-60');
    const name = 'Maximiliana-Alexandrina Wolfeschlegelsteinhausenbergerdorff'.slice(0, 60);
    await onboard(page, { name });
    await expectNoHorizontalOverflow(page);
    await shot(page, '19-reading-60-char-name-se');
    await go(page, 'chat');
    await expectNoHorizontalOverflow(page);
    await shot(page, '20-today-60-char-name-se');
  });

  test('refresh mid-onboarding loses what was typed', async ({ page }) => {
    await signIn(page, 'qa-ob-refresh');
    await page.fill('#fName', 'Maya'); await page.fill('#fDate', '1999-03-14');
    await page.reload();
    await expect(page.locator('#vOnboard')).toHaveClass(/on/);
    // Documented, not failed: a 4-field form is cheap to retype.
    await expect(page.locator('#fName')).toHaveValue('');
  });
});

test.describe('first reading', () => {
  test('loading state is shown while the reading is computed', async ({ page }) => {
    await signIn(page, 'qa-loading');
    let release;
    const gate = new Promise(r => { release = r; });
    await page.route('**/api/reading', async route => { await gate; await route.continue(); });
    await page.fill('#fName', 'Maya'); await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
    await pickCity(page);
    await page.click('#go');
    await skipAbout(page);
    await expect(page.locator('#rdName')).toHaveText('Reading your chart…');
    await shot(page, '21-reading-loading');
    release();
    await expect(page.locator('#rdName')).toContainText("Maya's chart");
    await shot(page, '22-reading-content-390');
    await page.setViewportSize({ width: 375, height: 667 });
    await shot(page, '22-reading-content-se');
  });

  // BUG (HIGH): if /api/reading fails right after onboarding, showReading() has
  // already switched to the reading view; the error is written into #obErr on
  // the now-hidden onboarding view. The user is stuck on "Reading your chart…"
  // forever with no retry and no tab bar escape that re-fetches.
  // index.html:1095-1106 + 1337-1345.
  test('a failed first reading offers a retry instead of hanging', async ({ page }) => {
    await signIn(page, 'qa-readfail');
    await page.route('**/api/reading', route => route.fulfill({ status: 500, body: '{"detail":"boom"}', contentType: 'application/json' }));
    await page.fill('#fName', 'Maya'); await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
    await pickCity(page);
    await page.click('#go');
    await skipAbout(page);
    await page.waitForTimeout(1500);
    await shot(page, '23-reading-failed-stuck');
    await expect(page.getByRole('button', { name: /try again|retry/i })).toBeVisible({ timeout: 1000 });
  });
});
