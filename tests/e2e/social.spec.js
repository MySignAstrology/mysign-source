const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, expectNoHorizontalOverflow, checkIn } = require('./helpers');

/**
 * team2/social: "Before you date" from Home, then a circle in People.
 * The quick check must leave nothing behind unless she saves; the circle view
 * must fit a phone with no sideways scroll.
 */
test('before you date, then a circle of her people', async ({ page }) => {
  await signIn(page, 'social');
  await onboard(page);

  // --- Home offers the quick check while she has nobody: after the check-in,
  // and once the "about you" card (shown first, one card at a time) is waved off.
  await checkIn(page);
  await expect(page.locator('#bydHome')).toBeHidden();
  await go(page, 'me');
  await page.click('#abNudgeNo');
  await go(page, 'people');                  // the quick check sits on People now
  await expect(page.locator('#bydHome')).toBeVisible();
  await page.click('#bydHomeGo');
  await expect(page.locator('#vPeople')).toHaveClass(/on/);
  await expect(page.locator('#bydBox')).toBeVisible();

  await page.fill('#bydName', 'Jake');
  await page.fill('#bydDate', '1997-11-20');
  await page.click('#bydGo');
  await expect(page.locator('#bydOut .score')).toHaveText(/\d+/);
  await expect(page.locator('#bydOut')).toContainText('Strongest links between your charts');
  await expect(page.locator('#bydOut')).toContainText('birthday-only read');
  await expectNoHorizontalOverflow(page);

  await page.click('#bydDiscard');
  await expect(page.locator('#toast')).toContainText('Nothing was saved');
  const count = await page.evaluate(() => fetch('/api/connections').then(r => r.json()).then(j => j.count));
  expect(count).toBe(0);

  // --- With a city, Save to People makes an ordinary person
  await page.click('#bydOpen');
  await page.fill('#bydName', 'Jake');
  await page.fill('#bydDate', '1997-11-20');
  // A typed name is not enough — London is several places — so it is picked.
  await page.fill('#bydPlace', 'London');
  await page.locator('#bydAc button[data-id]', { hasText: 'London, England' }).click();
  await page.click('#bydGo');
  await expect(page.locator('#bydSave')).toBeVisible();
  await page.click('#bydSave');
  await expect(page.locator('#toast')).toContainText('Jake is in your People');
  await expect(page.locator('.conn', { hasText: 'Jake' })).toBeVisible();

  // --- A second person, then a circle of the two
  await page.evaluate(() => fetch('/api/connections', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'Sam', date: '1997-11-20', time: '', city: 'Chicago IL' }) }));
  await go(page, 'chat');
  await go(page, 'people');
  await page.click('[data-circle-new]');
  await page.fill('#circleName', 'the girls');
  const picks = page.locator('[data-circle-pick]');
  await expect(picks).toHaveCount(2);
  await picks.nth(0).check();
  await picks.nth(1).check();
  await page.click('#circleMake');

  const view = page.locator('#circleView');
  await expect(view.locator('h3')).toHaveText('the girls');
  await expect(view.locator('ol li')).toHaveCount(2);
  await expect(view).toContainText(/Moon in \w+/);
  await expect(view.locator('.sup').first()).toContainText('Warmest this week');
  // Every pair, the two of them with each other included: 2 with her + 1.
  await expect(view.locator('#circlePairs summary')).toContainText('Every pair today · 3');
  await view.locator('#circlePairs summary').click();
  await expect(view.locator('#circlePairs li')).toHaveCount(3);
  await expect(view.locator('#circlePairs')).toContainText(/(Jake & Sam|Sam & Jake)/);
  await expect(view.locator('#circlePairs .sup')).toContainText('Most in sync pair');
  await expect(page.locator('[data-circle][aria-pressed=true]')).toContainText('the girls');
  await expectNoHorizontalOverflow(page);

  // The card is served as SVG, first names only.
  const svg = await page.evaluate(async () => {
    const c = (await (await fetch('/api/circles')).json()).circles[0];
    return (await fetch(`/api/card/circle/${c.id}.svg?initials=1`)).text();
  });
  expect(svg).toContain('the girls');
  expect(svg).not.toContain('Jake');
  expect(svg).toContain('EVERY PAIR, TODAY');

  // --- Home stops pushing the quick check once she has people
  await go(page, 'chat');
  await expect(page.locator('#bydHome')).toBeHidden();
});
