const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, expectNoHorizontalOverflow } = require('./helpers');

/**
 * team2/habit: "ask your mom" end to end, in two browsers. She has no birth
 * time; the Rising card offers the link; whoever opens it has no account, sees
 * only her first name and birth date, types the time; her chart updates and
 * she is told her rising sign. Plus the "mention my people" switch.
 */
test('ask your mom: link, answer without an account, rising sign arrives', async ({ browser }) => {
  const ctx = await browser.newContext();
  // No share sheet in headless Chromium: capture what would be copied instead.
  await ctx.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, 'share', { value: undefined, configurable: true });
    Object.defineProperty(Navigator.prototype, 'clipboard', { configurable: true,
      get: () => ({ writeText: async t => { window.__copied = t; } }) });
  });
  const her = await ctx.newPage();
  await signIn(her, 'askmom');
  await onboard(her, { name: 'Sam Rivera', date: '1997-11-20', time: '' });

  const pill = her.locator('#rdBig3 [data-askmom]').first();
  await expect(pill).toBeVisible();
  await pill.click();
  await expect(her.locator('#sheetTitle')).toHaveText('Ask your mom');
  await her.click('#sheetOk');
  await expect.poll(() => her.evaluate(() => window.__copied || '')).toContain('/bt/');
  const text = await her.evaluate(() => window.__copied);
  expect(text).toContain('what time was I born?');
  const path = text.match(/\/bt\/[A-Za-z0-9_-]+/)[0];

  // --- the parent: a separate browser, no account
  const mom = await (await browser.newContext()).newPage();
  await mom.goto(path);
  await expect(mom.locator('#open h1')).toContainText('Sam');
  await expect(mom.locator('body')).toContainText('November 20, 1997');
  await expect(mom.locator('body')).not.toContainText('Rivera');
  await expectNoHorizontalOverflow(mom);
  await mom.fill('#t', '23:10');
  await mom.locator('[data-v=certificate]').click();
  await mom.click('#go');
  await expect(mom.locator('#done')).toBeVisible();

  // Spent: a second visit gets the warm "done its job" page.
  await mom.goto(path);
  await expect(mom.locator('#gone')).toBeVisible();

  // --- her side: the toast names the rising sign, and the ask row is gone
  await go(her, 'settings');
  await expect(her.locator('#toast')).toContainText(/Your rising sign is \w+\. Your houses/);
  await expect(her.locator('#askMomRow')).toBeHidden();
  await expect(her.locator('#addTime')).toBeHidden();
  await ctx.close();
});

test('ask your mom: "within an hour" is saved as approximate, not exact', async ({ browser }) => {
  const ctx = await browser.newContext();
  await ctx.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, 'share', { value: undefined, configurable: true });
    Object.defineProperty(Navigator.prototype, 'clipboard', { configurable: true,
      get: () => ({ writeText: async t => { window.__copied = t; } }) });
  });
  const her = await ctx.newPage();
  await signIn(her, 'askmomrough');
  await onboard(her, { name: 'Sam Rivera', date: '1997-11-20', time: '' });
  await her.locator('#rdBig3 [data-askmom]').first().click();
  await her.click('#sheetOk');
  await expect.poll(() => her.evaluate(() => window.__copied || '')).toContain('/bt/');
  const path = (await her.evaluate(() => window.__copied)).match(/\/bt\/[A-Za-z0-9_-]+/)[0];

  const mom = await (await browser.newContext()).newPage();
  await mom.goto(path);
  await expect(mom.locator('#roughTip')).toBeHidden();
  await mom.fill('#t', '15:20');
  await mom.locator('[data-v=roughly]').click();
  await expect(mom.locator('#roughTip')).toBeVisible();
  await expect(mom.locator('#roughTip')).toContainText('approximate, not exact');
  await expectNoHorizontalOverflow(mom);
  await mom.click('#go');
  await expect(mom.locator('#done')).toBeVisible();
  // Honest either way: pinned down only if stable, otherwise "hidden rather than guessed".
  await expect(mom.locator('#doneMsg')).toContainText(/Sam’s rising sign|saved as approximate/);
  await expect(mom.locator('#doneMsg')).toContainText(/exact minute/);

  // Her side: still no exact time, so the ask row stays and says what she has.
  await go(her, 'settings');
  await expect(her.locator('#toast')).toContainText('give or take an hour');
  await expect(her.locator('#askMomRow')).toBeVisible();
  await expect(her.locator('#askMomSub')).toContainText('about 15:20, give or take an hour');
  await ctx.close();
});

test('mention my people switch flips and sticks', async ({ page }) => {
  await signIn(page, 'pushppl');
  await onboard(page);
  await go(page, 'settings');
  const btn = page.locator('#pushPeopleBtn');
  await expect(btn).toHaveAttribute('aria-checked', 'true');
  await btn.click();
  await expect(btn).toHaveAttribute('aria-checked', 'false');
  await expect(page.locator('#pushPeopleSub')).toContainText('only about your own sky');
  await page.reload();
  await expect(page.locator('#vSettings')).toHaveClass(/on/);   // a reload keeps the tab
  await expect(page.locator('#pushPeopleBtn')).toHaveAttribute('aria-checked', 'false');
  await expectNoHorizontalOverflow(page);
});
