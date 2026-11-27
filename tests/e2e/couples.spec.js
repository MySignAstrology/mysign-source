// @ts-check
/**
 * /couples, as a visitor uses it: signed out, browse, filter, open a couple,
 * read why, come back, and find the way into the app.
 */
const { test, expect } = require('@playwright/test');
const { expectNoHorizontalOverflow } = require('./helpers');

test('browse, filter and open a couple without signing in', async ({ page }) => {
  await page.goto('/couples');
  await expect(page.locator('h1')).toContainText('Famous couples');
  const cards = page.locator('a.couple');
  expect(await cards.count()).toBeGreaterThanOrEqual(30);
  await expectNoHorizontalOverflow(page);

  // The Kardashians filter shows only them.
  await page.click('.chips button[data-g="kardashians"]');
  const names = await page.locator('a.couple .nm').allInnerTexts();
  expect(names.length).toBeGreaterThan(5);
  for (const n of names) expect(n).toMatch(/Kardashian|Jenner/);

  // Highest scores: sorted, descending.
  await page.click('.chips button[data-g="top"]');
  const scores = (await page.locator('a.couple .score b').allInnerTexts()).map(Number);
  expect(scores).toEqual([...scores].sort((a, b) => b - a));

  // Open the Obamas.
  await page.click('.chips button[data-g="all"]');
  await page.click('a.couple[data-slug="barack-obama-and-michelle-obama"]');
  await expect(page).toHaveURL(/\/couples\/barack-obama-and-michelle-obama$/);
  await expect(page.locator('h1')).toContainText('Barack Obama');
  await expect(page.locator('.bigscore b')).toHaveText(/^\d+$/);
  await expect(page.locator('.status')).toContainText('Married since 1992');
  // Barack's birth time is public (his birth certificate); Michelle's isn't.
  await expect(page.locator('.person').first()).toContainText('Rising');
  await expect(page.locator('.person').nth(1)).toContainText('Birth time isn’t public');
  await expect(page.locator('.theme').first()).toBeVisible();
  // Their story: a public timeline, every line linked to where it was reported.
  await expect(page.locator('.story li').first()).toContainText('Met');
  await expect(page.locator('.story li', { hasText: 'Married' })).toContainText('1992');
  await expect(page.locator('.story li a').first()).toHaveAttribute('href', /^https:\/\//);
  await expect(page.locator('.how')).toContainText('same compatibility engine');
  await expectNoHorizontalOverflow(page);

  // The way in, and the way back.
  await expect(page.locator('.endcta a.primary')).toHaveAttribute('href', '/app');
  await page.click('a.back');
  await expect(page).toHaveURL(/\/couples$/);
});

test('an unknown couple is a 404 that still shows the list', async ({ page }) => {
  const res = await page.goto('/couples/nobody-and-noone');
  expect(res && res.status()).toBe(404);
  await expect(page.locator('a.couple').first()).toBeVisible();
});
