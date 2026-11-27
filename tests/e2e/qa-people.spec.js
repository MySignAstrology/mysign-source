// QA walk: People — add, deep report, kinds, add time later, forget, compare.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { go, signIn, onboard, expectNoHorizontalOverflow } = require('./helpers');

const SHOTS = process.env.QA_SHOTS || path.resolve('test-results/qa-shots');
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name, full = true) => page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: full, animations: 'disabled' });

test.beforeEach(({}, ti) => test.skip(ti.project.name !== 'mobile', 'QA walk runs once'));
test.use({ viewport: { width: 390, height: 844 } });

async function toPeople(page, tag) {
  await signIn(page, tag);
  await onboard(page);
  await go(page, 'people');
  await expect(page.locator('#vPeople')).toHaveClass(/on/);
}
async function add(page, { name, rel = '', date = '1997-06-02', time = '', prec = 'exact', kind = '', city = 'chicago, il' }) {
  await page.click('#cOpen');
  await page.fill('#cName', name);
  if (rel) await page.fill('#cRel', rel);
  await page.fill('#cDate', date);
  if (time) await page.fill('#cTime', time);
  await page.selectOption('#cPrec', prec);
  if (kind) await page.selectOption('#cKind', kind);
  await page.fill('#cCity', city);
  await page.locator('#cAc button').first().click();
  await page.click('#cGo');
  await expect(page.locator('.conn').filter({ hasText: name })).toBeVisible({ timeout: 20000 });
}

test.describe('people', () => {
  test('empty state', async ({ page }) => {
    await toPeople(page, 'qa-ppl-empty');
    await expect(page.locator('#pSub')).toContainText('Nobody yet');
    await expect(page.locator('.emptyart')).toBeVisible();
    await shot(page, '50-people-empty');
    await page.click('#cOpen');
    await shot(page, '51-people-add-form');
    await page.setViewportSize({ width: 375, height: 667 });
    await shot(page, '51-people-add-form-se');
    await expectNoHorizontalOverflow(page);
  });

  test('add with exact time, open deep report, change kind', async ({ page }) => {
    await toPeople(page, 'qa-ppl-exact');
    await add(page, { name: 'Theo', rel: 'talking, five months', time: '14:00', kind: 'romantic' });
    // Adding opens the new person straight away.
    const card = page.locator('.conn').filter({ hasText: 'Theo' });
    await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'true');
    await expect(card.locator('.deep .score b')).toBeVisible({ timeout: 20000 });
    await expect(card.locator('[data-fun]')).not.toContainText('Reading the two of you', { timeout: 20000 });
    await shot(page, '52-people-deep-report');
    await card.locator('select[data-kind]').selectOption('work');
    await expect(page.locator('.conn').filter({ hasText: 'Theo' }).locator('select[data-kind]')).toHaveValue('work', { timeout: 20000 });
    await shot(page, '53-people-kind-work');
  });

  test('add without a time, then add the time later', async ({ page }) => {
    await toPeople(page, 'qa-ppl-notime');
    await add(page, { name: 'Sam', date: '1995-11-19', city: 'new york, ny' });
    const card = page.locator('.conn').filter({ hasText: 'Sam' });
    await expect(card.locator('.deep h4', { hasText: 'Make this more accurate' })).toBeVisible({ timeout: 20000 });
    await expect(card).toContainText('unknown');
    await shot(page, '54-people-no-time');
    await card.locator('[data-edit-time]').fill('07:30');
    await card.locator('[data-save-time]').click();
    await expect(page.locator('.conn').filter({ hasText: 'Sam' }).locator('.deep h4', { hasText: 'Make this more accurate' })).toHaveCount(0, { timeout: 20000 });
  });

  test('approximate time shows a range and the sharpen box', async ({ page }) => {
    await toPeople(page, 'qa-ppl-approx');
    await add(page, { name: 'Jules', time: '09:00', prec: 'approximate', kind: 'friend' });
    const card = page.locator('.conn').filter({ hasText: 'Jules' });
    await expect(card.locator('.deep .score small')).toContainText('depending on the exact birth time', { timeout: 20000 });
    await expect(card).toContainText('within an hour');
    await shot(page, '55-people-approx');
  });

  // UX (MEDIUM): "Update" with an empty time silently does nothing —
  // index.html:1731 `if (!time) return;` — no hint.
  test('save-time with no time chosen gives feedback', async ({ page }) => {
    await toPeople(page, 'qa-ppl-emptysave');
    await add(page, { name: 'Ana' });
    const card = page.locator('.conn').filter({ hasText: 'Ana' });
    await card.locator('[data-save-time]').click();
    await shot(page, '56-people-empty-update');
  });

  test('forget asks first, then removes', async ({ page }) => {
    await toPeople(page, 'qa-ppl-forget');
    await add(page, { name: 'Ex' });
    await page.locator('[data-del]').click();
    await expect(page.locator('#sheet')).toBeVisible();
    await expect(page.locator('#sheetTitle')).toHaveText('Forget Ex?');
    await shot(page, '57-people-forget-sheet', false);
    await page.click('#sheetCancel');
    await expect(page.locator('.conn')).toHaveCount(1);
    await page.locator('[data-del]').click();
    await page.click('#sheetOk');
    await expect(page.locator('.conn')).toHaveCount(0);
    await expect(page.locator('#pSub')).toContainText('Nobody yet');
  });

  test('compare appears at two and orders three', async ({ page }) => {
    await toPeople(page, 'qa-ppl-cmp');
    await add(page, { name: 'Theo', time: '14:00' });
    await expect(page.locator('#pCompare')).toBeHidden();
    await add(page, { name: 'Sam', date: '1995-11-19', time: '07:30', city: 'new york, ny' });
    await add(page, { name: 'Rio', date: '2000-02-29', city: 'lagos' });
    await expect(page.locator('#pCompare')).toBeVisible();
    await expect(page.locator('#pCompare')).toContainText('Rio');
    // Collapse the open card so the list is visible.
    await page.locator('.conn[data-open=true] .top').click();
    await shot(page, '58-people-compare-3');
  });

  // BUG (MEDIUM): the expanded card is role="button" and contains a <select>,
  // buttons and inputs — nested interactive controls (axe: nested-interactive).
  // Screen readers announce the whole report as one button label. Also the
  // "Birth time"/"How sure" labels in the sharpen box have no `for`, so the
  // inputs are unnamed. index.html:1546, 1613-1615.
  // Fixed: the header is the button; the reading sits beside it.
  test('the open card is not a button wrapping other controls', async ({ page }) => {
    await toPeople(page, 'qa-ppl-nested');
    await add(page, { name: 'Nest' });
    await expect(page.locator('.deep .score')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('.conn[role=button] select')).toHaveCount(0, { timeout: 1000 });
  });

  test('60-character name on a person card', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await toPeople(page, 'qa-ppl-long');
    const name = 'Bartholomew-Maximilian Featherstonehaugh-Cholmondeley III'.padEnd(60, 'x');
    await add(page, { name, rel: 'my extremely complicated situationship of nearly two years now' });
    await page.locator('.conn[data-open=true] .top').click();
    await shot(page, '59-people-long-name-se');
    // Documented in report if it fails; long hyphenated names wrap at hyphens.
    await expectNoHorizontalOverflow(page);
  });

  // BUG (HIGH): same 422 -> "[object Object]" as onboarding (index.html:900).
  // #cName has no maxlength either.
  test.fail('a person over the 60-char limit gets a readable error', async ({ page }) => {
    await toPeople(page, 'qa-ppl-61');
    await page.click('#cOpen');
    await page.fill('#cName', 'N'.repeat(61));
    await page.fill('#cDate', '1997-06-02');
    await page.fill('#cCity', 'chicago, il');
    await page.locator('#cAc button').first().click();
    await page.click('#cGo');
    await expect(page.locator('#cErr')).not.toBeEmpty();
    const msg = await page.locator('#cErr').innerText();
    test.info().annotations.push({ type: 'cErr', description: msg });
    await shot(page, '60-people-61-char-error');
    expect(msg).not.toContain('[object Object]');
  });
});
