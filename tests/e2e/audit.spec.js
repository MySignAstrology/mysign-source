// Fixes from the November 2026 audit, for issues already on main.
const { test, expect } = require('@playwright/test');
const { go, signIn, onboard } = require('./helpers');

test('tapping Today mid-reply keeps her message and the reply', async ({ page }) => {
  await signIn(page, 'audit-midreply');
  await onboard(page);
  await go(page, 'chat');
  await page.route('**/api/message', async route => {
    await new Promise(r => setTimeout(r, 2500));
    await route.continue();
  });
  await page.fill('#input', 'should i answer him');
  await page.click('#send');
  await go(page, 'chat');                        // used to wipe the thread
  await expect(page.locator('.msg.user').last()).toContainText('should i answer him');
  await expect(page.locator('.msg.bot').last()).not.toBeEmpty({ timeout: 20_000 });
  await expect(page.locator('.msg.bot').last()).not.toContainText('Tap a number', { timeout: 1000 });
});

test('the mood dial is one Tab stop and arrow keys choose', async ({ page }) => {
  await signIn(page, 'audit-radio');
  await onboard(page);
  await go(page, 'chat');
  const first = page.locator('#moods button[data-m="1"]');
  await expect(first).toHaveAttribute('tabindex', '0');
  await expect(page.locator('#moods button[data-m="2"]')).toHaveAttribute('tabindex', '-1');
  await first.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#moods button[data-m="2"]')).toBeFocused();
  await expect(page.locator('#moods button[data-m="2"]')).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('#ciGo')).toBeEnabled();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');                 // wraps to 10
  await expect(page.locator('#moods button[data-m="10"]')).toHaveAttribute('aria-checked', 'true');
});

test('the add-to-Home-Screen offer waits for a check-in, not the first load', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'phones only');
  await signIn(page, 'audit-install', { installOffer: true });
  await onboard(page);
  await go(page, 'chat');
  await page.reload();
  await expect(page.locator('#greet')).not.toBeEmpty({ timeout: 15_000 });
  await expect(page.locator('#sheet')).toBeHidden();
  await go(page, 'chat');
  await page.click('#moods button[data-m="6"]');
  await page.click('#ciGo');
  await expect(page.locator('#sheetTitle')).toContainText('morning note');
});
