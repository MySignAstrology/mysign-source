const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, checkIn, openAboutFromHome, expectNoHorizontalOverflow } = require('./helpers');

// Sign-up goes straight to the reading now; the "about you" deck is offered
// on Home, once.
async function toAbout(page, tag) {
  await signIn(page, tag);
  await onboard(page);
  await openAboutFromHome(page);
}

test.describe('about you', () => {
  test('swipe, pick, add your own, see the mix, then the chart', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 740 });
    await toAbout(page, 'about-flow');
    await expect(page.locator('#abTitle')).toContainText('actually');
    await expectNoHorizontalOverflow(page);

    // The top card says which part of her it speaks to, then the placement.
    const top = page.locator('#abDeck .abcard.top');
    await expect(top.locator('h3')).toHaveText('Hiking');
    await expect(top).toContainText('It speaks to your hopes · Jupiter in');

    await page.click('#abYes');                                  // Hiking
    await expect(top.locator('h3')).toHaveText('Running');
    await page.click('#abNo');                                   // Running
    await page.click('#abUndo');                                 // back to Running
    await expect(top.locator('h3')).toHaveText('Running');
    await page.keyboard.press('ArrowRight');                     // Running, by key
    await expect(top.locator('h3')).toHaveText('Lifting & the gym');

    // A drag far enough to the right keeps it.
    const box = await top.boundingBox();
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + box.width / 2, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 60, y, { steps: 4 });
    await page.mouse.move(box.x + box.width / 2 + 180, y, { steps: 4 });
    await page.mouse.up();
    await expect(top.locator('h3')).toHaveText('Yoga & pilates');
    await expect(page.locator('.abcount')).toContainText('3 picked');

    // Interests: chips, capped.
    await page.click('#abNext');
    await expect(page.locator('#abTitle')).toContainText('for hours');
    await page.locator('#abChips button', { hasText: 'Psychology' }).click();
    await expect(page.locator('#abChips button', { hasText: 'Psychology' })).toHaveAttribute('aria-pressed', 'true');

    // Skills: her own words, through the sheet.
    await page.click('#abNext');
    await page.click('#abAdd');
    await page.fill('#sheetInput', 'Fixing bikes');
    await page.click('#sheetOk');
    await expect(page.locator('#abChips button', { hasText: 'Fixing bikes' })).toHaveAttribute('aria-pressed', 'true');

    // Passions: three at most, and the optional line.
    await page.click('#abNext');
    const chips = page.locator('#abChips button:not(.add)');
    for (let i = 0; i < 3; i++) await chips.nth(i).click();
    await chips.nth(3).click();
    await expect(page.locator('#toast')).toContainText('up to 3');
    await expect(chips.nth(3)).toHaveAttribute('aria-pressed', 'false');
    await page.fill('#abDream', 'Teach kids to swim');

    await page.click('#abNext');
    await expect(page.locator('#abTitle')).toContainText('elemental mix');
    await expect(page.locator('.abmix .row')).toHaveCount(4);
    await expect(page.locator('.abtext')).toContainText('Your chart leans');
    await expectNoHorizontalOverflow(page);

    await expect(page.locator('#abNext')).toHaveText('Back to Me');
    await page.click('#abNext');
    await expect(page.locator('#vMe')).toHaveClass(/on/, { timeout: 20_000 });

    // All of it lives in Settings, editable.
    await go(page, 'settings');
    await expect(page.locator('#abS-hobbies')).toHaveText('Hiking, Running, Lifting & the gym');
    await expect(page.locator('#abS-skills')).toContainText('Fixing bikes');
    await expect(page.locator('#abS-passions')).toContainText('Teach kids to swim');
    await page.click('#abE-interests');
    await expect(page.locator('#vAbout')).toHaveClass(/on/);
    await expect(page.locator('#abNext')).toHaveText('Save');
    await page.locator('#abChips button', { hasText: 'Psychology' }).click();
    await page.locator('#abChips button', { hasText: 'History' }).click();
    await page.click('#abNext');
    await expect(page.locator('#vSettings')).toHaveClass(/on/);
    await expect(page.locator('#abS-interests')).toHaveText('History');

    // Done once, so Home doesn't ask.
    await go(page, 'chat');
    await expect(page.locator('#greet')).not.toBeEmpty();
    await expect(page.locator('#abNudge')).toBeHidden();
  });

  test('not at sign-up, asked once on Home, and "Not now" sticks', async ({ page }) => {
    await signIn(page, 'about-skip');
    await onboard(page);                                           // straight to the reading
    await go(page, 'chat');
    await expect(page.locator('#abNudge')).toBeHidden();           // not before the check-in
    await checkIn(page);
    await go(page, 'me');
    await expect(page.locator('#abNudge')).toBeVisible({ timeout: 10_000 });

    await page.click('#abNudgeGo');
    await expect(page.locator('#vAbout')).toHaveClass(/on/);
    await page.click('#abSkip');                                   // from Me: stop asking
    await expect(page.locator('#vMe')).toHaveClass(/on/);
    await expect(page.locator('#abNudge')).toBeHidden();
    // Skipping goes back in history, which settles a beat later; a reload
    // before then would restore the deck rather than Me.
    await page.waitForFunction(() => history.state && history.state.v === 'vMe');
    await page.reload();
    await expect(page.locator('#mePath button').first()).toBeVisible({ timeout: 15_000 });
    await go(page, 'chat');                                        // Today is drawn again: still not asked
    await expect(page.locator('#greet')).not.toBeEmpty({ timeout: 15_000 });
    await go(page, 'me');
    await expect(page.locator('#abNudge')).toBeHidden();
  });

  test('the Home card fills in what you love and ends back on Home', async ({ page }) => {
    await toAbout(page, 'about-nudge');
    await page.click('#abYes');
    await page.click('#abNext');            // straight past the rest of the deck
    await page.click('#abNext');
    await page.click('#abNext');
    await page.click('#abNext');            // passions -> mix
    await expect(page.locator('#abNext')).toHaveText('Back to Me');
    await page.click('#abNext');
    await expect(page.locator('#vMe')).toHaveClass(/on/);
    await expect(page.locator('#abNudge')).toBeHidden();
  });
});
