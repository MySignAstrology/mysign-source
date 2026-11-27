// @ts-check
// The chart read one chapter at a time: opened from the Chart tab, one planet
// lit on the wheel, the rest of the tab put away until "Back to chart".
const { test, expect } = require('@playwright/test');
const { signIn, onboard } = require('./helpers');

test.describe('the chapter reading', () => {
  test('opens from the chart, walks every chapter, and comes back', async ({ page }) => {
    await signIn(page, 'chap-walk');
    await onboard(page);
    await page.click('#rdChapGo');
    await expect(page.locator('#rdChap')).toBeVisible();
    await expect(page.locator('#rdBig3')).toBeHidden();
    await expect(page.locator('#chCount')).toHaveText('1 of 9');
    await expect(page.locator('#chBody h2')).toHaveText('The shape of you.');
    await expect(page.locator('#chPrev')).toBeDisabled();

    await page.click('#chNext');
    await expect(page.locator('#chCount')).toHaveText('2 of 9');
    await expect(page.locator('#chBody h2')).toHaveText('Sun in Pisces.');
    await expect(page.locator('#chBody .role')).toHaveText('who you are');
    await expect(page.locator('#chBody .pos')).toContainText(/\d+° Pisces · \d+(st|nd|rd|th) house/);
    await expect(page.locator('#chWheel .wh-p.on')).toHaveCount(1);
    await expect(page.locator('#chWheel .wh-mk')).toHaveCount(1);
    await expect(page.locator('#chNav button[aria-current]')).toHaveAttribute('aria-label', 'Sun');
    await expect(page.locator('#chAskIn')).toHaveAttribute('placeholder', 'Ask about your Sun…');

    // Every chapter has words, and none pushes the page sideways.
    const n = await page.locator('#chNav button').count();
    for (let i = 0; i < n; i++) {
      await page.locator('#chNav button').nth(i).click();
      await expect(page.locator('#chBody .t').first()).not.toBeEmpty();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    await expect(page.locator('#chNext')).toBeDisabled();
    await expect(page.locator('#chBody h2')).toContainText('Saturn in');

    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#chCount')).toHaveText('8 of 9');
    await page.click('#chBack');
    await expect(page.locator('#rdChap')).toBeHidden();
    await expect(page.locator('#rdBig3')).toBeVisible();
  });

  test('a row in the planet table opens that planet’s chapter', async ({ page }) => {
    await signIn(page, 'chap-row');
    await onboard(page);
    await page.locator('#rdPlanets [data-chap="Venus"]').click();
    await expect(page.locator('#chBody h2')).toContainText('Venus in');
    await expect(page.locator('#chBody .role')).toHaveText('how you love');
    // The outer planets are in the table but have no chapter.
    await page.click('#chBack');
    await expect(page.locator('#rdPlanets [data-chap="Pluto"]')).toHaveCount(0);
  });

  test('without a birth time there is no rising chapter and no houses', async ({ page }) => {
    await signIn(page, 'chap-notime');
    await onboard(page, { time: '' });
    await page.click('#rdChapGo');
    await expect(page.locator('#chNav [data-k="Ascendant"]')).toHaveCount(0);
    await page.click('#chNav [data-k="Sun"]');
    await expect(page.locator('#chBody .pos')).not.toContainText('house');
  });

  test('the ask box carries the question into the chat', async ({ page }) => {
    await signIn(page, 'chap-ask');
    await onboard(page);
    await page.click('#rdChapGo');
    await page.click('#chNav [data-k="Moon"]');
    await page.fill('#chAskIn', 'Why do I need so much quiet?');
    await page.press('#chAskIn', 'Enter');
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    await expect(page.locator('.msg.me, .msg.user').last()).toContainText('Why do I need so much quiet?');
  });
});
