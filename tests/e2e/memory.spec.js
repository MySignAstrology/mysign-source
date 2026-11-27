// team2/memory: the pattern reveal shows once, and chat about a saved person
// quotes what she said about them, with the date, next to a computed transit.
const { test, expect } = require('@playwright/test');
const { execFileSync } = require('child_process');
const path = require('path');
const { go, signIn, onboard } = require('./helpers');

// CI has no .venv: it installs into the runner's own python.
const VENV = path.resolve(__dirname, '../../.venv/bin/python');
const PY = process.env.MYSIGN_PY || (require('fs').existsSync(VENV) ? VENV : 'python');

function plant(email, person) {
  execFileSync(PY, [path.join(__dirname, 'plant_memory.py'), email, person], {
    env: { ...process.env, MYSIGN_DATA: process.env.MYSIGN_E2E_DATA || './tests/e2e/.data' },
  });
}

test.describe('memory that earns its claims', () => {
  test('a verified pattern unlocks once, and chat remembers a person', async ({ page }) => {
    test.slow();                                   // a real model writes the reply
    const email = await signIn(page, 'unlock');
    await onboard(page);
    const r = await page.request.post('/api/connections', { data: {
      name: 'Jake', relation: 'talking', date: '1997-11-03', time: '08:15', city: 'Chicago IL' } });
    expect(r.ok()).toBeTruthy();
    plant(email, 'Jake');

    // Chat about him: her own words, dated, and the computed day between them.
    await go(page, 'chat');
    await page.fill('#input', 'how are things with jake');
    await page.click('#send');
    // What it must carry, not one model's wording: her note (he went quiet
    // about the weekend) and how today looks between them. The demo quotes the
    // note word for word; a real model says it in its own words.
    const reply = page.locator('.msg.bot').last();
    await expect(page.locator('.msg.bot .report')).toHaveCount(1, { timeout: 60_000 });
    await expect(reply).toContainText(/went quiet/i);
    await expect(reply).toContainText(/weekend/i);
    await expect(reply).toContainText(/\btoday\b/i);

    // The reveal: numbers from verify(), once.
    await go(page, 'patterns');
    const box = page.locator('#unlock');
    await expect(box).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('#ulN')).toContainText('/');
    await expect(page.locator('#ulT')).toContainText('you logged');
    await expect(page.locator('#ulT')).toContainText('Scorpio');
    const noScroll = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
    expect(noScroll).toBeTruthy();
    await page.click('#ulOk');
    await expect(box).toBeHidden();

    // What it remembers about Jake, deletable.
    const mem = page.locator('#tmPeople');
    await expect(mem).toContainText('went quiet');
    await mem.locator('.forget').first().click();
    await page.click('#sheetOk');
    await expect(page.locator('#tmPeople')).toHaveCount(0, { timeout: 10_000 });

    // Seen means seen: back to Memory, no reveal.
    await go(page, 'patterns');
    await go(page, 'patterns');
    await expect(page.locator('#mBody')).toBeVisible();
    await page.waitForTimeout(800);
    await expect(box).toBeHidden();
  });
});
