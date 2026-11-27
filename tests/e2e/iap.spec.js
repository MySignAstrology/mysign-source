// @ts-check
// Plus inside the iPhone app: sold through the App Store and nothing else.
// The shell is imitated the way ux-phone.spec.js does it (a Capacitor global),
// plus the MySignStore that native/bridge.js provides there, faked: StoreKit
// itself is tested in native/ios/App/AppTests, the server in tests/test_apple.py.
const { test, expect } = require('@playwright/test');
const { go, signIn, onboard } = require('./helpers');

const APPLE_TERMS = 'Payment will be charged to your Apple Account. Subscription renews automatically ' +
  'unless cancelled at least 24 hours before the end of the period. Manage in Settings → Apple Account → Subscriptions.';

async function toSettings(page) {
  await go(page, 'settings');
  await expect(page.locator('#vSettings')).toHaveClass(/on/);
}

/** The shell with the App Store open. `eligible` is whether the trial is offered. */
async function shell(page, { eligible = true, restore = { ok: false, found: 0, message: '' } } = {}) {
  await page.addInitScript(({ eligible, restore }) => {
    window.Capacitor = { isNativePlatform: () => true };
    const w = /** @type {any} */ (window);
    w.__iap = [];
    const trial = { unit: 'week', value: 1, days: 7, eligible };
    w.MySignStore = {
      ready: () => !w.__plus,
      open: () => true,
      products: () => [
        { id: 'mysign_plus_annual', price: '$59.99', perMonth: '$5.00', period: { unit: 'year', value: 1, label: 'year' }, trial },
        { id: 'mysign_plus_monthly', price: '$9.99', period: { unit: 'month', value: 1, label: 'month' }, trial },
      ],
      buy: async id => { w.__iap.push(['buy', id]); if (w.__outcome) return { ok: false, outcome: w.__outcome }; w.__plus = true; return { ok: true, outcome: 'purchased' }; },
      restore: async () => { w.__iap.push(['restore']); return restore; },
      manage: async () => { w.__iap.push(['manage']); },
    };
  }, { eligible, restore });
}

test.describe('Plus in the iPhone app', () => {
  test('the paywall shows both plans, the trial, Apple’s terms, and Restore', async ({ page }) => {
    await shell(page);
    await signIn(page, 'iap-paywall');
    await onboard(page);
    await toSettings(page);
    const box = page.locator('#planStore');
    await expect(box).toBeVisible();
    await expect(box.locator('[data-iap="mysign_plus_annual"]')).toHaveText('Annual · $59.99/year ($5.00/month)');
    await expect(box.locator('[data-iap="mysign_plus_monthly"]')).toHaveText('Monthly · $9.99/month');
    await expect(box).toContainText('The first 7 days are free. After the trial it is $59.99 per year or $9.99 per month');
    await expect(box).toContainText(APPLE_TERMS);
    await expect(box.locator('a[href="/terms"]')).toBeVisible();
    await expect(box.locator('a[href="/privacy"]')).toBeVisible();
    await expect(box.locator('[data-iap-restore]')).toHaveText('Restore purchases');
    // Nothing that sells or steers anywhere but the App Store (3.1.1), and no rounded-up saving.
    const text = await box.innerText();
    expect(text).not.toMatch(/save \d+%|mysignapp\.com|checkout|email you|on the web/i);
    await expect(box.locator('[data-plan]')).toHaveCount(0);
    await expect(page.locator('#planManage')).toBeHidden();

    // The same block is what the caps show.
    const html = await page.evaluate(async () => {
      const prices = (await (await fetch('/api/state')).json()).plan.prices;
      // @ts-ignore
      return paywallHTML('Keep everyone.', 'Your first five people are free.', prices, 'people_cap');
    });
    expect(html).toContain('data-iap="mysign_plus_annual"');
    expect(html).toContain('Restore purchases');
    expect(html).not.toMatch(/data-plan=|coming soon/);
  });

  test('buying goes to the App Store and says so when it is done', async ({ page }) => {
    await shell(page);
    await signIn(page, 'iap-buy');
    await onboard(page);
    await toSettings(page);
    await page.click('#planStore [data-iap="mysign_plus_annual"]');
    await expect(page.locator('#planStore')).toContainText('You’re on Plus.');
    expect(await page.evaluate(() => /** @type {any} */ (window).__iap)).toEqual([['buy', 'mysign_plus_annual']]);
  });

  test('closing Apple’s sheet leaves the paywall as it was', async ({ page }) => {
    await shell(page);
    await signIn(page, 'iap-cancel');
    await onboard(page);
    await toSettings(page);
    await page.evaluate(() => { /** @type {any} */ (window).__outcome = 'cancelled'; });
    await page.click('#planStore [data-iap="mysign_plus_monthly"]');
    await expect(page.locator('#planStore [data-iap="mysign_plus_monthly"]')).toBeEnabled();
    await expect(page.locator('#planStore')).toContainText(APPLE_TERMS);
    await page.evaluate(() => { /** @type {any} */ (window).__outcome = 'pending'; });
    await page.click('#planStore [data-iap="mysign_plus_monthly"]');
    await expect(page.locator('#planStore')).toContainText('Waiting for approval');
  });

  test('someone who has used the trial is told the price starts now', async ({ page }) => {
    await shell(page, { eligible: false });
    await signIn(page, 'iap-notrial');
    await onboard(page);
    await toSettings(page);
    await expect(page.locator('#planStore')).toContainText('$59.99 per year or $9.99 per month, charged when you confirm.');
    await expect(page.locator('#planStore small')).not.toContainText(/free|trial/);
  });

  test('Restore purchases answers plainly when there is nothing to restore', async ({ page }) => {
    await shell(page);
    await signIn(page, 'iap-restore');
    await onboard(page);
    await toSettings(page);
    await page.click('#planStore [data-iap-restore]');
    await expect(page.locator('#planStore')).toContainText('There is no Plus subscription to restore on this Apple Account.');
    expect(await page.evaluate(() => /** @type {any} */ (window).__iap)).toEqual([['restore']]);
  });

  test('a subscriber is offered nothing more, only Manage and Restore', async ({ page }) => {
    await shell(page);
    await page.route('**/api/state', async route => {
      const r = await route.fetch();
      const s = await r.json();
      if (s.plan) Object.assign(s.plan, { tier: 'plus', source: 'apple', apple_sub: { status: 'active', product: 'mysign_plus_annual', trial: true, renews: true } });
      await route.fulfill({ response: r, json: s });
    });
    await page.addInitScript(() => { /** @type {any} */ (window).__plus = true; });
    await signIn(page, 'iap-subscriber');
    await onboard(page);
    await toSettings(page);
    await expect(page.locator('#planSub')).toHaveText('Plus (free trial), through the App Store. You can chat as much as you like.');
    await expect(page.locator('#planStore [data-iap]')).toHaveCount(0);
    await expect(page.locator('#planStore [data-iap-manage]')).toBeVisible();
    await expect(page.locator('#planStore [data-iap-restore]')).toBeVisible();
  });
});

test.describe('Deleting an account that has an Apple subscription', () => {
  test('she is told Apple keeps billing, before the final confirmation', async ({ page }) => {
    await page.route('**/api/state', async route => {
      const r = await route.fetch();
      const s = await r.json();
      if (s.plan) Object.assign(s.plan, { tier: 'plus', source: 'apple', apple_sub: { status: 'active', product: 'mysign_plus_monthly', trial: false, renews: true } });
      await route.fulfill({ response: r, json: s });
    });
    let deleted = false;
    await page.route('**/api/account', async route => {
      if (route.request().method() === 'DELETE') deleted = true;
      await route.continue();
    });
    await signIn(page, 'iap-delete');           // a browser: the warning is not only for the app
    await onboard(page);
    await toSettings(page);
    await page.click('#delAcct');
    await expect(page.locator('#sheetTitle')).toHaveText('Delete everything?');
    await page.click('#sheetOk');
    await expect(page.locator('#sheetTitle')).toHaveText('Cancel your Apple subscription too');
    await expect(page.locator('#sheetBody')).toContainText('Deleting your account does not cancel it');
    await expect(page.locator('#sheetBody')).toContainText('Settings, tap your name, then Subscriptions');
    await page.click('#sheetOk');
    await expect(page.locator('#sheetTitle')).toHaveText('Really, all of it?');   // the final confirmation comes after
    await page.click('#sheetCancel');
    expect(deleted).toBe(false);
  });

  test('without one, deleting asks what it always asked', async ({ page }) => {
    await signIn(page, 'iap-delete-plain');
    await onboard(page);
    await toSettings(page);
    await page.click('#delAcct');
    await page.click('#sheetOk');
    await expect(page.locator('#sheetTitle')).toHaveText('Really, all of it?');
    await page.click('#sheetCancel');
  });
});

test('in a browser there is no App Store block, and the paywall is the web one', async ({ page }) => {
  await signIn(page, 'iap-web');
  await onboard(page);
  await toSettings(page);
  await expect(page.locator('#planStore')).toBeHidden();
  const html = await page.evaluate(async () => {
    const prices = (await (await fetch('/api/state')).json()).plan.prices;
    // @ts-ignore
    return paywallHTML('Keep everyone.', 'x', prices, 'people_cap');
  });
  expect(html).not.toContain('data-iap');
  expect(html).toContain('data-plan="waitlist"');
});
