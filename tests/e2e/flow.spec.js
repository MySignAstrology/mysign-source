const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, expectNoHorizontalOverflow } = require('./helpers');

test.describe('the whole path', () => {
  test('account, chart, question, memory', async ({ page }) => {
    await signIn(page, 'flow');
    await onboard(page);

    // The reveal — the payoff for handing over a birth time.
    await expect(page.locator('#rdName')).toContainText("Maya's chart");
    await expect(page.locator('.b3')).toHaveCount(3);
    await expect(page.locator('.b3').nth(0)).toContainText('Pisces');
    await expect(page.locator('.b3').nth(1)).toContainText('Aquarius');
    await expect(page.locator('.b3').nth(2)).toContainText('Aquarius');
    await expect(page.locator('#rdStandout .item').first()).toBeVisible();
    await expect(page.locator('.wire').first()).toBeVisible();

    // Openers are drawn from this chart, not a generic list.
    const starters = page.locator('#rdStarters button');
    await expect(starters).not.toHaveCount(0);
    await expect(starters.first()).toBeVisible();

    // Asking one moves into the chat and gets answered.
    const asked = await starters.first().innerText();
    await starters.first().click();
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    await expect(page.locator('.msg.user')).toContainText(asked);
    await expect(page.locator('.msg.bot').last()).not.toBeEmpty();

    // And it is remembered. Days are written up after they end, so a fresh
    // account shows the question count rather than an episode.
    await go(page, 'patterns');
    await expect(page.locator('#vMemory')).toHaveClass(/on/);
    await expect(page.locator('#mSub')).toContainText('1 question');
  });

  test('the reading is reachable again from the header', async ({ page }) => {
    await signIn(page, 'nav');
    await onboard(page);
    await go(page, 'chat');             // reading -> chat
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    await go(page, 'chart');            // chat -> Me -> reading
    await expect(page.locator('#vReading')).toHaveClass(/on/);
    await expect(page.locator('.b3')).toHaveCount(3);
  });

  test('a check-in is recorded and shown back', async ({ page }) => {
    await signIn(page, 'checkin');
    await onboard(page);
    await go(page, 'chat');
    await page.click('#moods button[data-m="4"]');
    await page.fill('#ciText', "didn't sleep");
    await page.click('#ciGo');
    await expect(page.locator('#checkin')).toContainText('4/10');
    await expect(page.locator('#checkin')).toContainText("didn't sleep");
  });
});

test.describe('without a birth time', () => {
  test('omits the rising sign instead of inventing one', async ({ page }) => {
    await signIn(page, 'notime');
    await onboard(page, { name: 'Nia', time: '' });

    const rising = page.locator('.b3').nth(2);
    await expect(rising).toHaveClass(/missing/);
    await expect(rising).toContainText('Unknown');

    // No house is *claimed*. The word itself appears legitimately — the Unknown
    // card explains that adding a time fills in "every house placement" — so
    // assert on the ordinal, which is what a claim looks like.
    await expect(page.locator('#rdBig3')).not.toContainText(/\d+(st|nd|rd|th) house/);
    await expect(page.locator('#rdStandout')).toContainText("What I can't tell you yet");
  });

  test('adding the time later fills the chart in', async ({ page }) => {
    await signIn(page, 'addtime');
    await onboard(page, { name: 'Nia', time: '' });
    await expect(page.locator('.b3').nth(2)).toContainText('Unknown');

    await go(page, 'settings');
    await expect(page.locator('#addTime')).toBeVisible();
    await page.click('#addTime');
    // The app asks in its own sheet, not the browser's prompt().
    await expect(page.locator('#sheet')).toBeVisible();
    await page.fill('#sheetInput', '04:42');
    await page.click('#sheetOk');

    await expect(page.locator('#vReading')).toHaveClass(/on/, { timeout: 20_000 });
    await expect(page.locator('.b3').nth(2)).toContainText('Aquarius');
    await expect(page.locator('#rdBig3')).toContainText('house');
  });
});

test.describe('safety', () => {
  test('a crisis message drops the persona and carries no reading', async ({ page }) => {
    await signIn(page, 'crisis');
    await onboard(page);
    await go(page, 'chat');

    await page.fill('#input', "honestly i don't want to be here anymore");
    await page.click('#send');

    const reply = page.locator('.msg.crisis');
    await expect(reply).toBeVisible({ timeout: 20_000 });
    await expect(reply).toContainText('988');
    await expect(reply).toContainText('hurting yourself');

    // It must look different, not just read differently.
    await expect(page.locator('.msg.bot.quiet')).toHaveCount(0);
    const text = await reply.innerText();
    for (const word of ['Neptune', 'Pluto', 'retrograde', 'orb', 'natal', '12th']) {
      expect(text, `astrology leaked into a crisis reply: ${word}`)
        .not.toContain(word);
    }
  });
});

test.describe('signed out', () => {
  test('lands on sign-in and shows nothing personal', async ({ page }) => {
    await page.goto('/app');
    await expect(page.locator('#vAuth')).toHaveClass(/on/);
    await expect(page.locator('#tabMe')).toBeHidden();
    await expect(page.locator('#composer')).toBeHidden();
  });
});

test.describe('comparing people', () => {
  async function addPerson(page, {name, date, time, city}) {
    await page.click('#cOpen');
    await page.fill('#cName', name);
    await page.fill('#cDate', date);
    await page.fill('#cTime', time);
    await page.fill('#cCity', city);
    const opt = page.locator('#cAc button').first();
    await expect(opt).toBeVisible();
    await opt.click();
    await page.click('#cGo');
    await expect(page.locator('.conn').filter({hasText: name}))
      .toBeVisible({ timeout: 20_000 });
  }

  test('one person is not a comparison; two are', async ({ page }) => {
    await signIn(page, 'compare');
    await onboard(page);
    await go(page, 'people');

    await addPerson(page, {name:'Theo', date:'1997-06-02', time:'14:00', city:'chicago, il'});
    // Nothing to compare against yet — the panel should stay away.
    await expect(page.locator('#pCompare')).toBeHidden();

    await addPerson(page, {name:'Sam', date:'1995-11-19', time:'07:30', city:'new york, ny'});
    const cmp = page.locator('#pCompare');
    await expect(cmp).toBeVisible({ timeout: 20_000 });
    await expect(cmp.locator('.verdict')).not.toBeEmpty();
    await expect(cmp.locator('.grp')).not.toHaveCount(0);
    await expect(cmp).toContainText('Theo');
    await expect(cmp).toContainText('Sam');
    await expect(cmp.locator('.dims .dim')).toHaveCount(3);
  });

  test('it explains why close scores are shown as level', async ({ page }) => {
    await signIn(page, 'comparelevel');
    await onboard(page);
    await go(page, 'people');
    await addPerson(page, {name:'Theo', date:'1997-06-02', time:'14:00', city:'chicago, il'});
    await addPerson(page, {name:'Sam', date:'1995-11-19', time:'07:30', city:'new york, ny'});

    const note = page.locator('#pCompare .note');
    await expect(note).toBeVisible();
    await expect(note).toContainText('single link');
  });
});
