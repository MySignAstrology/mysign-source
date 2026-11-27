// team2/sky: the Home banner and the Chart's events list, end to end.
const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, expectNoHorizontalOverflow } = require('./helpers');

test('sky events: banner on Home opens the events list on Chart', async ({ page }) => {
  await signIn(page, 'sky');
  await onboard(page);
  // Onboarding lands on the reading, which now carries the events section.
  const head = page.locator('#skyEventsHead');
  await expect(head).toBeVisible({ timeout: 20_000 });
  const items = page.locator('#rdEvents .sev');
  await expect(items.first()).toBeVisible();
  expect(await items.count()).toBeGreaterThan(2);
  // Every countdown is ticking text, never empty.
  await expect(page.locator('#rdEvents [data-cd]').first()).toHaveText(/^(in \d|in about|tomorrow|any minute|already passed)/);
  await expect(page.locator('#rdEvents')).toContainText(/house/);
  await expectNoHorizontalOverflow(page);

  // Home: the banner names what's under way or what's next, with a countdown.
  await go(page, 'today');
  const banner = page.locator('#skyBanner');
  await expect(banner).toBeVisible({ timeout: 15_000 });
  await expect(banner).toContainText(/Next:|retrograde|shadow|today/);
  await expectNoHorizontalOverflow(page);
  await banner.click();
  await expect(page.locator('#vReading')).toHaveClass(/on/);
  await expect(head).toBeVisible();
});

test('sky events: the API answers with ephemeris times', async ({ page }) => {
  await signIn(page, 'skyapi');
  await onboard(page, { time: '' });
  const d = await page.evaluate(async () => (await fetch('/api/sky/events')).json());
  expect(d.time_known).toBe(false);
  expect(d.events.length).toBeGreaterThan(2);
  for (const e of d.events) {
    expect(e.utc).toMatch(/Z$/);
    expect(e.her_house).toBeNull();
  }
});
