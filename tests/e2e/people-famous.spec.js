// @ts-check
const { test, expect } = require('@playwright/test');
const { go, newEmail, expectNoHorizontalOverflow } = require('./helpers');

/**
 * An empty People tab isn't a dead end: famous couples, scored by the same
 * engine, are there to read, and tapping one opens its /couples page.
 */
test('an empty People tab shows famous couples to read', async ({ page }) => {
  const email = newEmail('famous');
  const code = (await (await page.request.post('/api/auth/request', { data: { email } })).json()).dev_code;
  await page.request.post('/api/auth/verify', { data: { email, code } });
  await page.request.post('/api/consent', { data: { ai: true } });
  const city = (await (await page.request.get('/api/cities?q=Chicago')).json())[0].id;
  expect((await page.request.post('/api/onboard',
    { data: { name: 'Noor', date: '1997-09-03', time: '08:10', city } })).ok()).toBeTruthy();

  await page.goto('/app');
  await go(page, 'people');
  const famous = page.locator('#pFamous');
  await expect(famous).toBeVisible();
  await expect(famous).toContainText('While your list is empty');
  await expect(famous.locator('li a')).toHaveCount(5);
  await expect(famous.locator('li a').first()).toContainText('Taylor Swift');
  await expect(famous.locator('.more')).toHaveAttribute('href', '/couples');
  await expectNoHorizontalOverflow(page);

  await famous.locator('li a').first().click();
  await expect(page).toHaveURL(/\/couples\/taylor-swift-and-travis-kelce$/);
  await expect(page.locator('h1')).toContainText('Taylor Swift');
});
