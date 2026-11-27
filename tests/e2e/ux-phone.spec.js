// The phone UX audit (audit/ux.md), one test per user-visible fix. Runs on the
// phone and desktop projects; the finding each one pins is in its title.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { go, signIn, onboard, newEmail, expectNoHorizontalOverflow } = require('./helpers');

const SHOTS = process.env.UX_SHOTS || path.resolve('test-results/ux-shots');
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name, ti) => page.screenshot({
  path: path.join(SHOTS, `${name}-${ti.project.name}.png`), fullPage: true, animations: 'disabled' });

test.beforeEach(({}, ti) => test.skip(ti.project.name === 'tablet', 'phone and desktop only'));

/** Up to the consent screen, without accepting it. */
async function toConsent(page, tag) {
  await page.goto('/app');
  await page.fill('#fEmail', newEmail(tag));
  await page.click('#authGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await page.fill('#fCode', code);
  await page.click('#authGo');
  await expect(page.locator('#vConsent')).toHaveClass(/on/);
}

async function toSettings(page) {
  await go(page, 'settings');
  await expect(page.locator('#vSettings')).toHaveClass(/on/);
}

// ------------------------------------------------------------------- High

test('H1: the AI disclosure reads as a sentence, not tracked capitals', async ({ page }, ti) => {
  await toConsent(page, 'ux-consent');
  const style = await page.locator('label.aiconsent').evaluate(el => {
    const s = getComputedStyle(el);
    return { tt: s.textTransform, ls: s.letterSpacing, fw: s.fontWeight };
  });
  expect(style.tt).toBe('none');
  expect(style.ls).toBe('normal');
  expect(Number(style.fw)).toBeLessThan(600);
  await shot(page, 'H1-consent', ti);
});

test.describe('H2/H3: crisis line follows where she lives', () => {
  test.use({ timezoneId: 'America/New_York' });

  test('born in Lagos, living in New York: 988, on the first screen', async ({ page }, ti) => {
    await signIn(page, 'ux-lagos');
    // The form says which country it thinks she lives in, before she builds.
    await expect(page.locator('#obHome')).toContainText('United States');
    await onboard(page, { city: 'lagos' });
    // H3: the reveal is the first screen with the footer; it was the
    // signed-out default ("findahelpline") until a later screen fixed it.
    await expect(page.locator('#foot')).toContainText('988');
    await shot(page, 'H3-reading-footer', ti);
    await toSettings(page);
    await expect(page.locator('#countrySub')).toContainText('United States');
    await expect(page.locator('#countryRow')).toContainText('Where you live now');
  });

  test('she can correct it on the form, from a list of names', async ({ page }) => {
    await signIn(page, 'ux-home-pick');
    await page.click('#obHomeBtn');
    await expect(page.locator('#sheetSelect')).toBeVisible();
    await page.selectOption('#sheetSelect', 'GB');
    await page.click('#sheetOk');
    await expect(page.locator('#obHome')).toContainText('United Kingdom');
    await onboard(page, { city: 'lagos' });
    await expect(page.locator('#foot')).not.toContainText('988');
    await toSettings(page);
    await expect(page.locator('#countrySub')).toContainText('United Kingdom');
  });
});

test('H4: Settings draws at once, with saved switches, while the reading is still being written', async ({ page }, ti) => {
  await signIn(page, 'ux-settings-fast');
  await onboard(page, { time: '' });
  await toSettings(page);
  await page.click('#pushPeopleBtn');
  await expect(page.locator('#pushPeopleBtn')).toHaveText('Off');
  // Now the reading takes forever, as a first one on a busy server can.
  await page.route('**/api/reading', () => {});
  await page.reload();
  await expect(page.locator('#vSettings')).toHaveClass(/on/);         // reload keeps the screen
  await expect(page.locator('#sBirth')).toContainText('birth time unknown', { timeout: 3000 });
  await expect(page.locator('#addTime')).toBeVisible();
  await expect(page.locator('#planSub')).not.toBeEmpty();
  await expect(page.locator('#pushPeopleBtn')).toHaveText('Off');
  await expect(page.locator('#pushPeopleBtn')).toHaveAttribute('aria-checked', 'false');
  await expect(page.locator('#shareMoodBtn')).toHaveText('On');
  await shot(page, 'H4-settings-no-reading', ti);
});

test.describe('H5: App Store wrapper readiness', () => {
  test('Export downloads a file and the app stays where it was', async ({ page }) => {
    await signIn(page, 'ux-export');
    await onboard(page);
    await toSettings(page);
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#exportBtn')]);
    expect(dl.suggestedFilename()).toMatch(/^my-sign-\d{4}-\d{2}-\d{2}\.json$/);
    await expect(page).toHaveURL(/\/app$/);
    await expect(page.locator('#vSettings')).toHaveClass(/on/);
    await expect(page.locator('#exportBtn')).toHaveText('Export');
  });

  test('Sign out stays in the app, on its own sign-in screen', async ({ page }) => {
    await signIn(page, 'ux-signout');
    await onboard(page);
    await toSettings(page);
    await page.click('#signOut');
    await expect(page).toHaveURL(/\/app$/);
    await expect(page.locator('#vAuth')).toHaveClass(/on/);
    await expect(page.locator('#authErr')).toHaveText("You're signed out.");
  });

  test('Compare plans opens and closes the table', async ({ page }) => {
    await signIn(page, 'ux-plans');
    await onboard(page);
    await toSettings(page);
    const table = page.locator('#planTable');
    await expect(table).toBeHidden();                       // closed until asked for
    await page.click('#planCompare');
    await expect(table).toBeVisible();
    await expect(page.locator('#planCompare')).toHaveText('Hide');
    await page.click('#planCompare');
    await expect(table).toBeHidden();
  });

  test('inside the app shell: no Safari instructions, no prices, nothing coming soon', async ({ page }, ti) => {
    await page.addInitScript(() => { window.Capacitor = { isNativePlatform: () => true }; });
    await signIn(page, 'ux-wrapped');
    await onboard(page);
    await toSettings(page);
    // Where the server can't send (no APNs key), the shell has no notification
    // rows at all (App Review 2.1). With it: tests/e2e/push.spec.js.
    await expect(page.locator('#pushRow')).toBeHidden();
    await expect(page.locator('#pushPeopleRow')).toBeHidden();
    await expect(page.locator('#nTime')).toBeHidden();
    // No tier it can't sell (3.1.1).
    await expect(page.locator('#planCompare')).toBeHidden();
    await expect(page.locator('#planTable')).toBeHidden();
    await expect(page.locator('#vSettings')).not.toContainText(/coming soon|app update/i);
    // Even once billing is wired, the shell shows no web prices: StoreKit only.
    const html = await page.evaluate(async () => {
      const prices = (await (await fetch('/api/state')).json()).plan.prices;
      return paywallHTML('Keep everyone.',
        'Your first five people are free. With Plus you can add everyone, and see who is easiest to be around three days ahead.',
        { ...prices, available: true }, 'people_cap');
    });
    expect(html).not.toContain('$');
    expect(html).not.toMatch(/coming soon|Plus|data-plan/i);
    expect(html).toContain('Your first five people are free.');
    await shot(page, 'H5-wrapped-settings', ti);
  });

  test('pages the app opens in its browser lead nowhere that sells (3.1.1)', async ({ page }) => {
    await page.goto('/support?app=1');
    await expect(page.locator('a.back')).toBeHidden();
    // The flag outlives the first page: the next one is still tidied.
    await page.locator('a[href*="/privacy"]').first().click();
    await expect(page).toHaveURL(/\/privacy\?app=1/);
    await expect(page.locator('a.back')).toBeHidden();
    await page.goto('/couples');
    await expect(page.locator('a[href="/app"], a[href="/"]').first()).toBeAttached();
    const shown = await page.evaluate(() => [...document.querySelectorAll('a[href]')]
      .filter(a => a.offsetParent !== null && new URL(a.href).host === location.host).map(a => new URL(a.href).pathname));
    expect(shown.filter(p => p === '/' || p.startsWith('/app'))).toEqual([]);
    await page.goto('/accuracy?app=1');
    await expect(page.locator('a.cta')).toBeHidden();
  });

  test('a paid report is not sold in the app\'s browser, and the gifts still are (3.1.1)', async ({ page }) => {
    // The shop list with one report added, as it will be once a report is published.
    await page.route('**/shop-catalog.js', async route => {
      const body = await (await route.fetch()).text();
      await route.fulfill({contentType: 'application/javascript', body: body +
        ';(() => { const p = JSON.parse(JSON.stringify(window.SHOP_CATALOG[0]));' +
        ' p.handle = "couple-compatibility-report"; p.title = "Couple Compatibility Report"; p.tags = ["report"];' +
        ' window.SHOP_CATALOG.push(p); })();'});
    });
    const report = '.card[data-h="couple-compatibility-report"]';
    await page.goto('/shop');
    await expect(page.locator(report).first()).toBeAttached();
    await page.goto('/shop?app=1');
    await expect(page.locator('.card').first()).toBeVisible();
    await expect(page.locator(report)).toHaveCount(0);
    // An offer drawn later, and a link to a report's page, are both dead here.
    await page.goto('/check?app=1');
    const seen = await page.evaluate(() => {
      const box = document.createElement('div');
      box.dataset.section = 'check_report_offer';
      box.innerHTML = '<a id="rGo" href="https://shop.mysignapp.com/products/couple-compatibility-report">Get the couple report</a>';
      const gift = document.createElement('a');
      gift.id = 'gGo'; gift.href = 'https://shop.mysignapp.com/products/zodiac-pillar-candle'; gift.textContent = 'A candle';
      document.body.append(box, gift);
      return new Promise(done => setTimeout(() => done({
        offer: getComputedStyle(box).display, link: document.getElementById('rGo').hidden, gift: gift.hidden}), 50));
    });
    expect(seen).toEqual({offer: 'none', link: true, gift: false});
  });

  test('the same pages in a normal browser keep their links', async ({ page }) => {
    await page.goto('/privacy');
    await expect(page.locator('a.back')).toBeVisible();
  });

  test('the paywall sells nothing while Plus cannot be bought', async ({ page }) => {
    await signIn(page, 'ux-paywall');
    await onboard(page);
    await go(page, 'chat');
    // Today draws its thread once the view is on screen; inject after that,
    // or the redraw clears what this test put there.
    await expect(page.locator('#thread .msg').first()).toBeVisible({ timeout: 15_000 });
    const r = await page.evaluate(async () => {
      const prices = (await (await fetch('/api/state')).json()).plan.prices;
      const box = document.createElement('div');
      box.className = 'msg paywall';
      box.innerHTML = paywallHTML("That's three for today.", "That's three for today. Tomorrow's yours.", prices, 'chat_cap');
      document.querySelector('#thread').appendChild(box);
      return { html: box.innerHTML, available: prices.available };
    });
    expect(r.available).toBe(false);
    expect(r.html).not.toMatch(/\$\d/);                          // no price
    expect(r.html).not.toContain('data-plan="annual"');
    // Low 6: the title isn't repeated as the first sentence.
    expect(r.html.match(/That's three for today\./g)).toHaveLength(1);
    const ask = page.locator('#thread [data-plan="waitlist"]');
    await ask.click();
    await expect(ask).toBeDisabled();
    await expect(page.locator('#thread .paywall small')).toContainText("on the list");
    // Low 7: a tapped, disabled button no longer looks tappable.
    expect(Number(await ask.evaluate(el => getComputedStyle(el).opacity))).toBeLessThan(1);
  });
});

// ----------------------------------------------------------------- Medium

test.describe('M1-M3: city picker', () => {
  test('M1: a slow answer for what she typed before never replaces the current one', async ({ page }, ti) => {
    await signIn(page, 'ux-race');
    await page.route(/\/api\/cities\?q=pari/, async route => {
      await new Promise(r => setTimeout(r, 1500));
      await route.continue();
    });
    await page.locator('#fCity').pressSequentially('pari', { delay: 30 });
    await page.waitForTimeout(250);                       // pari is in flight
    await page.fill('#fCity', 'lagos');
    await expect(page.locator('#ac button[data-id]').first()).toContainText('Lagos');
    await page.waitForTimeout(1700);                      // pari has landed
    await expect(page.locator('#ac')).not.toContainText('Paris');
    await expect(page.locator('#ac button[data-id]').first()).toContainText('Lagos');
    await shot(page, 'M1-city-race', ti);
  });

  test('M2: arrow keys and Enter pick a city; the button says what is missing', async ({ page }) => {
    await signIn(page, 'ux-arrows');
    await expect(page.locator('#goWhy')).toContainText('your name');
    await page.fill('#fName', 'Maya');
    await page.fill('#fDate', '1999-03-14');
    await page.fill('#fTime', '04:42');
    await expect(page.locator('#goWhy')).toHaveText('Still needed: your birth city.');
    await page.locator('#fCity').focus();
    await page.keyboard.type('chicago, il');
    await expect(page.locator('#ac button[data-id]').first()).toBeVisible();
    await page.keyboard.press('ArrowDown');
    await expect(page.locator('#ac button[data-id]').first()).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#fCity')).toHaveValue('Chicago, IL, US');
    await expect(page.locator('#ac')).toBeHidden();
    await expect(page.locator('#go')).toBeEnabled();
    await expect(page.locator('#goWhy')).toHaveText('');
  });

  test('M2: typed and moved on — the list closes; one exact name is taken, otherwise she is told', async ({ page }) => {
    await signIn(page, 'ux-blur');
    await page.fill('#fName', 'Maya');
    await page.fill('#fDate', '1999-03-14');
    await page.click('#noTime');
    await page.locator('#fCity').pressSequentially('springfield');
    await expect(page.locator('#ac button[data-id]').first()).toBeVisible();
    await page.locator('#fName').click();
    await expect(page.locator('#ac')).toBeHidden();
    await expect(page.locator('#obErr')).toContainText('Pick your city from the list');
    await expect(page.locator('#goWhy')).toContainText('picked from the list');

    await page.fill('#fCity', '');
    await page.locator('#fCity').pressSequentially('reykjavik');
    await expect(page.locator('#ac button[data-id]').first()).toBeVisible();
    await page.locator('#fName').click();
    await expect(page.locator('#fCity')).toHaveValue(/Reykjav/);
    await expect(page.locator('#go')).toBeEnabled();
  });

  test('M3: a typo gets "did you mean", not a dead end', async ({ page }, ti) => {
    await signIn(page, 'ux-typo');
    await page.locator('#fCity').pressSequentially('chcago');
    await expect(page.locator('#ac .achead')).toContainText('Did you mean');
    await expect(page.locator('#ac button[data-id]').first()).toContainText('Chicago, IL');
    await shot(page, 'M3-city-typo', ti);
    await page.fill('#fCity', 'zzzzqx');
    await expect(page.locator('#ac .acnone')).toContainText('Check the spelling');
  });
});

test('M4: a person can be added with a birthday only', async ({ page }, ti) => {
  await signIn(page, 'ux-nocity');
  await onboard(page);
  await go(page, 'people');
  await page.click('#cOpen');
  await page.fill('#cName', 'Jo');
  await page.fill('#cDate', '1996-04-20');
  await expect(page.locator('#cGo')).toBeEnabled();
  // A city typed but not picked is the one thing that still blocks, and says so.
  await page.fill('#cCity', 'chica');
  await expect(page.locator('#cGo')).toBeDisabled();
  await expect(page.locator('#cCityHint')).toContainText('Pick their city');
  await page.fill('#cCity', '');
  await page.click('#cGo');
  const card = page.locator('.conn').filter({ hasText: 'Jo' });
  await expect(card).toBeVisible({ timeout: 20000 });
  await shot(page, 'M4-person-no-city', ti);
});

test('M5: Tab stays inside an open sheet; the page behind is inert', async ({ page }) => {
  await signIn(page, 'ux-trap');
  await onboard(page);
  await toSettings(page);
  await page.click('#delAcct');
  await expect(page.locator('#sheet')).toBeVisible();
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('#sheet'))).toBe(true);
  }
  await page.keyboard.press('Shift+Tab');
  expect(await page.evaluate(() => !!document.activeElement.closest('#sheet'))).toBe(true);
  expect(await page.evaluate(() => document.querySelector('.app').inert)).toBe(true);
  await page.keyboard.press('Escape');
  expect(await page.evaluate(() => document.querySelector('.app').inert)).toBe(false);
});

test('M6: the person card is a header button beside its reading, not one big button', async ({ page }) => {
  await signIn(page, 'ux-nested');
  await onboard(page);
  await go(page, 'people');
  await page.click('#cOpen');
  await page.fill('#cName', 'Nest'); await page.fill('#cDate', '1997-06-02');
  await page.click('#cGo');
  const card = page.locator('.conn').first();
  await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'true', { timeout: 20000 });
  await expect(page.locator('.deep .score')).toBeVisible({ timeout: 20000 });
  expect(await page.locator('[role=button] select, [role=button] input, button button, button select').count()).toBe(0);
  await card.locator('.conn-hd').focus();
  await page.keyboard.press('Enter');
  await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'false');
  await expect(card.locator('.detail')).toHaveCount(0);
  await page.keyboard.press('Space');
  await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'true');
});

test('M7: mood buttons are at least 44px on a 320px phone', async ({ page }, ti) => {
  test.skip(ti.project.name !== 'mobile', 'a phone width');
  await page.setViewportSize({ width: 320, height: 568 });
  await signIn(page, 'ux-moods');
  await onboard(page);
  await go(page, 'chat');
  await expect(page.locator('#moods button').first()).toBeVisible();
  const sizes = await page.locator('#moods button').evaluateAll(bs => bs.map(b => {
    const r = b.getBoundingClientRect(); return [r.width, r.height];
  }));
  expect(sizes).toHaveLength(10);
  for (const [w, h] of sizes) { expect(w).toBeGreaterThanOrEqual(44); expect(h).toBeGreaterThanOrEqual(44); }
  await expectNoHorizontalOverflow(page);
  await page.locator('#checkin').screenshot({ path: path.join(SHOTS, 'M7-moods-320.png') });
});

test('M8: the tab bar steps aside while the keyboard is up', async ({ page }, ti) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await signIn(page, 'ux-kb');
  await onboard(page);
  await go(page, 'chat');
  await expect(page.locator('#tabbar')).toBeVisible();
  await page.waitForFunction(() => !document.documentElement.dataset.vt);   // the glide has landed
  await page.locator('#input').focus();
  await expect(page.locator('#tabbar')).toBeVisible();          // focus alone is not a keyboard
  await page.setViewportSize({ width: 360, height: 330 });      // the keyboard opens
  await expect(page.locator('#tabbar')).toBeHidden();
  await shot(page, 'M8-keyboard-up', ti);
  await page.setViewportSize({ width: 360, height: 640 });
  await page.locator('#input').blur();
  await expect(page.locator('#tabbar')).toBeVisible();
});

test('M9: Sign in says it is working', async ({ page }) => {
  await page.goto('/app');
  await page.fill('#fEmail', newEmail('ux-busy'));
  await page.click('#authGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await page.route('**/api/auth/verify', async route => {
    await new Promise(r => setTimeout(r, 1200)); await route.continue();
  });
  await page.fill('#fCode', code);
  await page.click('#authGo');
  await expect(page.locator('#authGo')).toHaveText('Signing in…');
  await expect(page.locator('#authGo')).toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('#vConsent')).toHaveClass(/on/);
});

test('M10: the first reading says how long it really takes', async ({ page }) => {
  await signIn(page, 'ux-wait');
  await page.route('**/api/reading', async route => {
    await new Promise(r => setTimeout(r, 2500)); await route.continue();
  });
  await page.fill('#fName', 'Maya'); await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
  await page.fill('#fCity', 'chicago, il');
  await page.locator('#ac button[data-id]').first().click();
  // Onboarding starts the reading and the reading view joins it: one request.
  const reqs = [];
  page.on('request', r => { if (r.url().endsWith('/api/reading')) reqs.push(r); });
  await page.click('#go');
  await expect(page.locator('#rdOpen')).toContainText('about 15 seconds');
  await expect(page.locator('#rdName')).toContainText('chart', { timeout: 20000 });
  expect(reqs.length).toBe(1);
});

test.describe('M15: Back and reload', () => {
  test('Settings → About you → Back is Settings, and Save returns there too', async ({ page }) => {
    await signIn(page, 'ux-back-about');
    await onboard(page);
    await toSettings(page);
    await page.click('#abE-hobbies');
    await expect(page.locator('#vAbout')).toHaveClass(/on/);
    await page.goBack();
    await expect(page.locator('#vSettings')).toHaveClass(/on/);
    await page.click('#abE-interests');
    await expect(page.locator('#vAbout')).toHaveClass(/on/);
    await page.click('#abNext');                              // Save
    await expect(page.locator('#vSettings')).toHaveClass(/on/);
    await page.goBack();                                      // not a second Settings
    await expect(page.locator('#vMain, #vReading').first()).toBeAttached();
    await expect(page.locator('#vSettings')).not.toHaveClass(/on/);
  });

  test('reload keeps the screen she was on, including an open placement', async ({ page }) => {
    await signIn(page, 'ux-reload');
    await onboard(page);
    await go(page, 'people');
    await expect(page.locator('#vPeople')).toHaveClass(/on/);
    await page.reload();
    await expect(page.locator('#vPeople')).toHaveClass(/on/);
    await go(page, 'chart');
    await page.locator('#rdBig3 [data-place="sun"]').click();
    await expect(page.locator('#vPlace')).toHaveClass(/on/);
    await page.reload();
    await expect(page.locator('#vPlace')).toHaveClass(/on/);
    await expect(page.locator('#plBody h2')).not.toBeEmpty();
    await page.goBack();
    await expect(page.locator('#vReading')).toHaveClass(/on/);
  });

  test('Back from the first screen is not a second Today', async ({ page }) => {
    await signIn(page, 'ux-dup');
    await onboard(page);
    await page.goto('about:blank');
    await page.goto('/app');
    await expect(page.locator('#vMe')).toHaveClass(/on/);
    await page.goBack();
    await expect(page).toHaveURL('about:blank');
  });
});

// -------------------------------------------------------------------- Low

test('Low 1-3: resend updates the code, waits 30s, and switching email clears the error', async ({ page }) => {
  await page.clock.install();
  await page.goto('/app');
  await page.fill('#fEmail', newEmail('ux-resend'));
  await page.click('#authGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const first = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await expect(page.locator('#reSend')).toBeDisabled();
  await expect(page.locator('#reSend')).toContainText('30s');
  await page.clock.runFor(31_000);
  await expect(page.locator('#reSend')).toBeEnabled();
  await page.click('#reSend');
  await expect(page.locator('#devCode')).not.toContainText(first);
  await page.fill('#fCode', first === '000000' ? '111111' : '000000');
  await page.click('#authGo');
  await expect(page.locator('#authErr')).not.toBeEmpty();
  await page.click('#reEmail');
  await expect(page.locator('#authErr')).toBeEmpty();
});

test('Low 14: "Skip it" no longer promises the Moon sign outright', async ({ page }) => {
  await signIn(page, 'ux-skip');
  await expect(page.locator('#vOnboard .hint').first()).toContainText('unless it changed sign that day');
});

test('Low 11: no glossary button inside an aria-hidden wrapper', async ({ page }) => {
  await signIn(page, 'ux-gloss');
  // onboard() ends on the reading. Tapping Chart from there goes to chat
  // (flow.spec), so this passed only when the check beat the transition.
  await onboard(page);
  await expect(page.locator('#rdBig3 .b3').first()).toBeVisible();
  expect(await page.locator('[aria-hidden="true"] button.gloss').count()).toBe(0);
});
