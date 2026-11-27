const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, checkIn, expectNoHorizontalOverflow } = require('./helpers');

test.describe('mood', () => {
  test('My Sign speaks first, the check-in names a feeling, and the reply lands in the chat', async ({ page }) => {
    await signIn(page, 'mood-flow');
    await onboard(page);
    await go(page, 'chat');

    // An empty conversation opens with My Sign, not a blank box.
    await expect(page.locator('#thread .msg.opener')).toContainText('How\'s today, really?');

    // One tap at a time: number, energy, a word with its meaning, a tag.
    await expect(page.locator('#ciEnergy')).toBeHidden();
    await expect(page.locator('#ciGo')).toBeDisabled();
    await page.click('#moods button[data-m="3"]');
    await page.click('#ciEnergy button[data-e="low"]');
    await page.click('#ciWordList button[data-w="drained"]');
    await expect(page.locator('#ciMeans')).toContainText('running on empty');
    await page.click('#ciTagList button[data-t="work"]');
    await expectNoHorizontalOverflow(page);
    await page.click('#ciGo');

    await expect(page.locator('#checkin')).toContainText('3/10');
    await expect(page.locator('#checkin .feel')).toContainText('drained');
    await expect(page.locator('#thread .msg.opener')).toHaveCount(0);
    await expect(page.locator('.msg.user').last()).toContainText('Checking in: 3/10 — drained. It\'s about work.');
    await expect(page.locator('.msg.bot').last()).toContainText('?', { timeout: 20_000 });

    // The Mood tab shows it back.
    await go(page, 'patterns');
    await expect(page.locator('#moodBody .mood-cal i.on')).toHaveCount(1);
    await expect(page.locator('#moodBody .mood-words')).toContainText('drained');
    await expectNoHorizontalOverflow(page);
  });
});

test.describe('mood, the next batch', () => {
  test('the line takes a one-tap reaction into the chat, once', async ({ page }) => {
    await signIn(page, 'mood-line');
    await onboard(page);
    await go(page, 'chat');
    const react = page.locator('#oneline .lnreact');
    await expect(react).toBeVisible();
    await react.locator('button[data-r="sort"]').click();
    await expect(react).toHaveCount(0);
    await expect(page.locator('.msg.user').last()).toContainText("About today's line");
    await expect(page.locator('.msg.user').last()).toContainText('sort of.');
    await expect(page.locator('.msg.bot').last()).toContainText('?', { timeout: 20_000 });
    await page.reload();
    await expect(page.locator('#greet')).not.toBeEmpty({ timeout: 15_000 });
    await expect(page.locator('#oneline .lnreact')).toHaveCount(0);
  });

  test('promo cards wait for the check-in, and only one shows', async ({ page }) => {
    await signIn(page, 'mood-promo');
    await onboard(page);
    await go(page, 'chat');
    await expect(page.locator('#abNudge')).toBeHidden();
    await expect(page.locator('#bydHome')).toBeHidden();
    await checkIn(page, 7);
    await go(page, 'me');                    // the card sits on Me now
    await expect(page.locator('#abNudge')).toBeVisible();
    await expect(page.locator('#bydHome')).toBeHidden();
  });

  test('the chat box suggests one of her chart\'s own questions', async ({ page }) => {
    await signIn(page, 'mood-ph');
    await onboard(page);
    await go(page, 'chat');
    // One of her own questions when one fits the box; otherwise the default.
    await expect(page.locator('#input')).toHaveAttribute('placeholder', /^(Try: .{1,42}|Ask me anything…)$/, { timeout: 20_000 });
  });
});

test('the add-to-Home-Screen offer waits for the check-in reply', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'phones only');
  await signIn(page, 'mood-install', { installOffer: true });
  await onboard(page);
  await go(page, 'chat');
  await expect(page.locator('#sheet')).toBeHidden();               // not on first load
  await page.click('#moods button[data-m="6"]');
  await page.click('#ciGo');
  await expect(page.locator('.msg.bot').last()).toContainText('?', { timeout: 20_000 });
  await expect(page.locator('#sheetTitle')).toContainText('morning note');
  await page.click('#sheetCancel');
  await page.reload();
  await expect(page.locator('#greet')).not.toBeEmpty({ timeout: 15_000 });
  await expect(page.locator('#sheet')).toBeHidden();               // once
});

test('the reading opens folded below the big three, and stays open once unfolded', async ({ page }) => {
  await signIn(page, 'rd-fold');
  await onboard(page);
  await expect(page.locator('#rdMore')).toHaveClass(/fold/);
  await expect(page.locator('#rdQuick')).toBeVisible();
  await page.click('#rdMoreGo');
  await expect(page.locator('#rdMore')).not.toHaveClass(/fold/);
  await expect(page.locator('#rdMoreGo')).toBeHidden();
  await page.reload();
  await expect(page.locator('#rdName')).toContainText('chart', { timeout: 20_000 });
  await expect(page.locator('#rdMore')).not.toHaveClass(/fold/);
});

test.describe('mood, fourth batch', () => {
  test('the morning push lands on the line, with "does this land?" ready', async ({ page }) => {
    await signIn(page, 'land-line');
    await onboard(page);
    await page.goto('/app?from=push&pk=morning');
    await expect(page.locator('#vMain')).toHaveClass(/on/, { timeout: 15_000 });
    await expect(page.locator('#oneline .lnreact button').first()).toBeFocused({ timeout: 10_000 });
    expect(page.url()).not.toContain('from=push');
  });

  test('a good day can leave a note for a harder one', async ({ page }) => {
    await signIn(page, 'note-good');
    await onboard(page);
    await checkIn(page, 8);
    await page.click('#ciWrite');
    await page.fill('#sheetInput', 'You always come back from this.');
    await page.click('#sheetOk');
    await expect(page.locator('#ciNote')).toContainText('Kept for a harder day');
  });

  test('a person card asks My Sign about them', async ({ page }) => {
    await signIn(page, 'ask-person');
    await onboard(page);
    await go(page, 'people');
    await page.click('#cOpen');
    await page.fill('#cName', 'Theo');
    await page.fill('#cDate', '2000-11-02');
    await page.fill('#cCity', 'Chicago');
    await page.locator('#cAc button').first().click();
    await page.click('#cGo');
    const ask = page.locator('[data-ask-person="Theo"]');
    await expect(ask).toBeVisible({ timeout: 20_000 });
    await ask.click();
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    await expect(page.locator('.msg.user').last()).toContainText("What's going on with me and Theo today?");
    await expect(page.locator('.msg.bot').last()).not.toBeEmpty({ timeout: 20_000 });
  });
});
