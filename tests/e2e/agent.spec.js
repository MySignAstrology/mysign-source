const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, dayAhead } = require('./helpers');

// A day seven weeks out: outside the week the model is handed, so it has to
// look it up, and an exact date the demo looks up too. "Thursday" alone was
// within the week, and a real model sometimes answered it without the tool.
const said = ({ weekday, day }) => new RegExp(`${weekday}|\\b${day}(st|nd|rd|th)?\\b`);

test.describe('looking things up', () => {
  test('a question about another day shows the lookup and answers about it',
    async ({ page }) => {
    test.slow();                                   // a real model and a lookup
    const day = dayAhead('Thursday');
    await signIn(page, 'tool');
    await onboard(page);
    await go(page, 'chat');

    await page.fill('#input', `is ${day.iso} a bad day to talk to him`);
    await page.click('#send');

    // The pause is the point — it should be visible and named, not a spinner.
    const step = page.locator('.work .step').first();
    await expect(step).toBeVisible({ timeout: 45_000 });
    await expect(step).toContainText('looking up the sky');
    await expect(step).toHaveClass(/done/, { timeout: 45_000 });
    await expect(step).toContainText('Thursday');

    await expect(page.locator('.msg.bot').last()).toContainText(said(day),
      { timeout: 60_000 });
  });

  test('the work stays on the message after a reload', async ({ page }) => {
    test.slow();
    const day = dayAhead('Friday');
    await signIn(page, 'toolpersist');
    await onboard(page);
    await go(page, 'chat');
    await page.fill('#input', `what about ${day.iso}`);
    await page.click('#send');
    await expect(page.locator('.msg.bot .report')).toHaveCount(1, { timeout: 60_000 });
    await expect(page.locator('.msg.bot').last()).toContainText(said(day));

    await page.reload();
    await expect(page.locator('.msg.bot .tools').last())
      .toContainText('looked up', { timeout: 20_000 });
  });

  test('an ordinary question does not invent a lookup', async ({ page }) => {
    await signIn(page, 'notool');
    await onboard(page);
    await go(page, 'chat');
    await page.fill('#input', 'why do i feel like this');
    await page.click('#send');
    await expect(page.locator('.msg.bot').last()).not.toBeEmpty({ timeout: 20_000 });
    await expect(page.locator('.work .step')).toHaveCount(0);
  });
});

test.describe('reachable', () => {
  test('the conversation is a live region', async ({ page }) => {
    await signIn(page, 'a11y');
    await onboard(page);
    await go(page, 'chat');
    const thread = page.locator('#thread');
    await expect(thread).toHaveAttribute('aria-live', 'polite');
    await expect(thread).toHaveAttribute('role', 'log');
  });

  test('the mood dial is a labelled radiogroup', async ({ page }) => {
    await signIn(page, 'a11ymood');
    await onboard(page);
    await go(page, 'chat');
    const group = page.locator('#moods');
    await expect(group).toHaveAttribute('role', 'radiogroup');
    const four = group.locator('button[data-m="4"]');
    await expect(four).toHaveAttribute('aria-label', '4 out of 10');
    await four.click();
    await expect(four).toHaveAttribute('aria-checked', 'true');
  });

  test('every control a screen reader meets has a name', async ({ page }) => {
    await signIn(page, 'a11ynames');
    await onboard(page);
    await go(page, 'chat');
    const unnamed = await page.evaluate(() =>
      [...document.querySelectorAll('button:not([hidden]), textarea, input')]
        .filter(el => el.offsetParent !== null)
        .filter(el => !(el.getAttribute('aria-label') || el.textContent.trim()
                        || el.getAttribute('placeholder')
                        || (el.labels && el.labels.length)))
        .map(el => el.outerHTML.slice(0, 60)));
    expect(unnamed, `unnamed controls: ${unnamed.join(' | ')}`).toEqual([]);
  });
});

test.describe('your data', () => {
  test('export downloads a file with the conversation in it', async ({ page }) => {
    await signIn(page, 'export');
    await onboard(page);
    await go(page, 'chat');
    await page.fill('#input', 'should i answer him');
    await page.click('#send');
    await expect(page.locator('.msg.bot').last()).not.toBeEmpty({ timeout: 20_000 });

    await go(page, 'settings');
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.click('#exportBtn'),
    ]);
    expect(download.suggestedFilename()).toMatch(/^my-sign-\d{4}-\d{2}-\d{2}\.json$/);

    const stream = await download.createReadStream();
    const chunks = [];
    for await (const c of stream) chunks.push(c);
    const data = JSON.parse(Buffer.concat(chunks).toString());
    expect(data.thread.length).toBeGreaterThanOrEqual(2);
    expect(data.profile.name).toBe('Maya');
  });
});

test.describe('offline', () => {
  test('says so instead of showing a blank page', async ({ page, context }) => {
    await signIn(page, 'offline');
    await onboard(page);
    await expect(page.locator('#offline')).toBeHidden();

    await context.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    await expect(page.locator('#offline')).toBeVisible();
    await expect(page.locator('#offline')).toContainText("offline");

    await context.setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await expect(page.locator('#offline')).toBeHidden();
  });
});
