const { test, expect } = require('@playwright/test');
const { go, newEmail, signIn, onboard, signedOutReloaded } = require('./helpers');

/**
 * Every control in the app, pressed, with the thing it is supposed to do
 * checked. A button that throws, does nothing, or leaves the screen in a
 * half-state fails here — the flow specs only walk the happy path.
 *
 * Any uncaught page error fails the test it happened in.
 */
test.beforeEach(async ({ page }) => {
  page.on('pageerror', e => { throw new Error('page error: ' + e.message); });
  // The app turns stray rejections into a toast so a person isn't left with a
  // dead tap. That must not hide them from this suite: record every one that
  // isn't the expected signed-out 401.
  await page.addInitScript(() => {
    window.__rejections = [];
    addEventListener('unhandledrejection', e => {
      if (!(e.reason && e.reason.status === 401))
        window.__rejections.push(String(e.reason && e.reason.message || e.reason));
    });
  });
});
test.afterEach(async ({ page }) => {
  const r = await page.evaluate(() => window.__rejections || []).catch(() => []);
  expect(r, 'a control raised an unhandled error').toEqual([]);
});

const tab = (page, id) => page.click(id);
const on = (page, view) => expect(page.locator(view)).toHaveClass(/on/);

test.describe('greeting follows the clock', () => {
  const cases = [
    ['03:00', 'Still up'], ['08:00', 'Good morning'], ['11:59', 'Good morning'],
    ['12:00', 'Good afternoon'], ['16:59', 'Good afternoon'], ['17:00', 'Good evening'],
    ['21:30', 'Good evening'], ['22:00', 'Late night'], ['23:59', 'Late night'],
  ];
  for (const [hm, want] of cases) {
    test(`${hm} → ${want}`, async ({ page }) => {
      await page.clock.setFixedTime(new Date(`2026-10-27T${hm}:00`));
      await signIn(page, 'greet');
      await onboard(page);
      await go(page, 'chat');
      await expect(page.locator('#greet h1')).toContainText(want);
    });
  }

  const skies = [['04:59', 'night'], ['05:00', 'dawn'], ['08:59', 'dawn'], ['09:00', 'day'],
                 ['16:59', 'day'], ['17:00', 'dusk'], ['20:59', 'dusk'], ['21:00', 'night']];
  for (const [hm, want] of skies) {
    test(`the light at ${hm} is ${want}`, async ({ page }) => {
      await page.clock.setFixedTime(new Date(`2026-10-27T${hm}:00`));
      await page.goto('/app');                              // set before first paint, even signed out
      await expect(page.locator('html')).toHaveAttribute('data-sky', want);
    });
  }

  test('the whole sky changes through the day, in both themes', async ({ page }) => {
    await page.goto('/app');
    for (const scheme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: scheme });
      const tops = await page.evaluate(() =>
        ['03:00', '07:00', '12:30', '18:00', '20:00'].map(hm => {
          MySky.apply(new Date(`2026-10-27T${hm}:00`));
          return getComputedStyle(document.documentElement).getPropertyValue('--s1').trim();
        }));
      expect(new Set(tops).size, `${scheme}: five times of day, five skies`).toBe(5);
    }
  });

  test('the sky blends minute to minute, with no visible jump', async ({ page }) => {
    await page.goto('/app');
    const jump = await page.evaluate(() => {
      const rgb = s => s.match(/\d+/g).map(Number);
      let worst = 0;
      for (let m = 0; m < 24 * 60; m += 1) {
        const a = MySky.at(new Date(2026, 9, 27, 0, m), true)[1];
        const b = MySky.at(new Date(2026, 9, 27, 0, m + 1), true)[1];
        const d = rgb(a).map((v, i) => Math.abs(v - rgb(b)[i]));
        worst = Math.max(worst, ...d);
      }
      return worst;
    });
    expect(jump, 'one minute never moves a channel by more than 2').toBeLessThanOrEqual(2);
  });

  test('the sun and moon never sit behind the content', async ({ page }) => {
    await page.goto('/app');
    const lows = await page.evaluate(() => {
      let lowest = 0;
      for (let m = 0; m < 24 * 60; m += 15) {
        MySky.apply(new Date(2026, 9, 27, 0, m));
        lowest = Math.max(lowest, parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--orb-y')));
      }
      return lowest;
    });
    expect(lows, 'orb stays in the top band').toBeLessThanOrEqual(22);
  });

  test('the light moves while the app stays open', async ({ page }) => {
    await page.clock.install({ time: new Date('2026-10-27T16:30:00') });
    await page.goto('/app');
    await expect(page.locator('html')).toHaveAttribute('data-sky', 'day');
    await page.clock.fastForward('01:00:00');           // 17:30
    await expect(page.locator('html')).toHaveAttribute('data-sky', 'dusk');
  });

  test('an app left open does not stay on the morning greeting', async ({ page }) => {
    await page.clock.install({ time: new Date('2026-10-27T09:00:00') });
    await signIn(page, 'greetlive');
    await onboard(page);
    await go(page, 'chat');
    await expect(page.locator('#greet h1')).toContainText('Good morning');
    await page.clock.fastForward('10:00:00');           // 19:00
    await expect(page.locator('#greet h1')).toContainText('Good evening');
  });
});

test.describe('sign-in and onboarding controls', () => {
  test('send code, change email, sign in, consent', async ({ page }) => {
    // Fresh addresses every run: the server allows only a few codes per
    // address per hour, and a fixed one trips that on the third run.
    const email = newEmail('btn-auth');
    const first = newEmail('btn-first');
    await page.goto('/app');
    await expect(page.locator('#authGo')).toBeDisabled();
    await page.fill('#fEmail', first);
    await page.click('#authGo');
    await expect(page.locator('#sentTo')).toContainText(first);
    await expect(page.locator('#fEmail')).toHaveJSProperty('readOnly', true);

    await page.click('#reEmail');                       // use a different email
    await expect(page.locator('#codeField')).toBeHidden();
    await expect(page.locator('#authGo')).toHaveText('Send me a code');
    await page.fill('#fEmail', email);
    await page.press('#fEmail', 'Enter');               // Enter submits too
    await expect(page.locator('#devCode')).toBeVisible();
    const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
    await page.fill('#fCode', code);
    await page.click('#authGo');
    await on(page, '#vConsent');
    await expect(page.locator('#consentGo')).toBeDisabled();   // AI consent comes first
    await page.check('#aiOk');
    await page.click('#consentGo');
    await on(page, '#vOnboard');
  });

  test('skip-time toggles both ways and the build button tracks the form', async ({ page }) => {
    await signIn(page, 'btn-onb');
    await expect(page.locator('#go')).toBeDisabled();
    await page.click('#noTime');
    await expect(page.locator('#fTime')).toBeDisabled();
    await expect(page.locator('#noTime')).toHaveText('Actually, I know it');
    await page.click('#noTime');
    await expect(page.locator('#fTime')).toBeEnabled();
    await expect(page.locator('#noTime')).toHaveText('Skip it');
    await onboard(page);                                 // picks a city from #ac, presses #go
  });
});

test.describe('today', () => {
  test('the first thing she reads is a line, not a table', async ({ page }) => {
    await signIn(page, 'btn-line');
    await onboard(page);
    await go(page, 'chat');
    const line = page.locator('#oneline blockquote');
    await expect(line).toBeVisible();
    await expect(line).not.toBeEmpty();
    await expect(page.locator('#oneline figcaption')).toContainText(/Because|quiet/);
    // Order on the page: greeting, the line, the check-in, then the facts.
    const y = async sel => (await page.locator(sel).boundingBox()).y;
    expect(await y('#oneline')).toBeLessThan(await y('#checkin'));
    // The facts have their own step on Me.
    await go(page, 'today');
    await expect(page.locator('#today')).toContainText('The sky behind it');
    await expect(page.locator('#today .intensity')).toHaveText(/^(an intense|a lively|a quiet) day$/);
    // "See the sky behind it" brings the facts up.
    const why = page.locator('#skyWhy');
    if (await why.count()) {
      await go(page, 'chat');
      await why.click();
      await on(page, '#vToday');
      await expect(page.locator('#today')).toBeInViewport();
    }
  });

  test('mood dots, check-in, starters, composer', async ({ page }) => {
    await signIn(page, 'btn-today');
    await onboard(page);
    await go(page, 'chat');
    await on(page, '#vMain');

    // A new account is offered questions; tapping one sends it.
    const starter = page.locator('#todayStart .starters button').first();
    await expect(starter).toBeVisible();
    const asked = await starter.innerText();
    await starter.click();
    await expect(page.locator('.msg.user').last()).toContainText(asked);
    await expect(page.locator('#todayStart')).toHaveCount(0);
    await expect(page.locator('.msg.bot').last()).not.toBeEmpty({ timeout: 20_000 });

    // Every mood dot selects exactly itself.
    await expect(page.locator('#ciGo')).toBeDisabled();
    for (let m = 1; m <= 10; m++) {
      await page.click(`#moods button[data-m="${m}"]`);
      await expect(page.locator('#moods [aria-checked=true]')).toHaveCount(1);
      await expect(page.locator(`#moods button[data-m="${m}"]`)).toHaveAttribute('aria-checked', 'true');
    }
    await expect(page.locator('#ciGo')).toBeEnabled();
    await page.fill('#ciText', 'fine');
    await page.click('#ciGo');
    await expect(page.locator('#checkin')).toContainText('10/10');

    // Send is disabled on empty, enabled on text, Enter sends, Shift+Enter doesn't.
    await expect(page.locator('#send')).toBeDisabled();
    await page.fill('#input', 'line one');
    await expect(page.locator('#send')).toBeEnabled();
    await page.press('#input', 'Shift+Enter');
    await expect(page.locator('#input')).toHaveValue(/line one\n/);
    const before = await page.locator('.msg.user').count();
    await page.press('#input', 'Enter');
    await expect(page.locator('.msg.user')).toHaveCount(before + 1);
  });
});

test.describe('the reading', () => {
  test('starters, ask-my-own, share buttons', async ({ page }) => {
    await signIn(page, 'btn-read');
    await onboard(page);

    // Share: no OS share sheet in a test browser, so the card downloads — as a
    // PNG, which is what Instagram and Messages can actually use.
    for (const id of ['#shareChart', '#shareToday']) {
      const [dl] = await Promise.all([page.waitForEvent('download'), page.click(id)]);
      expect(dl.suggestedFilename()).toMatch(/\.png$/);
    }

    await page.click('#rdOwn');
    await on(page, '#vMain');
    await expect(page.locator('#input')).toBeFocused();

    await go(page, 'chart');
    const q = page.locator('#rdStarters button').first();
    const asked = await q.innerText();
    await q.click();
    await on(page, '#vMain');
    await expect(page.locator('.msg.user').last()).toContainText(asked);
  });

  test('a slow share says it is working, and a second tap opens no second sheet', async ({ page }) => {
    // On a phone the card takes seconds before the sheet appears; she tapped
    // twice and the second tap landed inside the sheet.
    await page.addInitScript(() => {
      window.__shares = 0;
      navigator.canShare = () => true;
      navigator.share = () => { window.__shares++; return new Promise(ok => setTimeout(ok, 1500)); };
    });
    await signIn(page, 'btn-share-busy');
    await onboard(page);
    const b = page.locator('#shareChart');
    await b.click();
    await expect(b).toHaveAttribute('aria-busy', 'true');
    await expect(b).toBeDisabled();
    await b.click({ force: true });                                // the impatient second tap
    await expect.poll(() => page.evaluate(() => window.__shares)).toBe(1);
    await expect(b).not.toHaveAttribute('aria-busy', /.*/, { timeout: 5000 });
    await expect(b).toBeEnabled();
    expect(await page.evaluate(() => window.__shares)).toBe(1);
  });

  test('the Add-my-birth-time button on the unknown Rising card', async ({ page }) => {
    await signIn(page, 'btn-rising');
    await onboard(page, { time: '' });
    await page.click('#rdBig3 [data-addtime]');
    await expect(page.locator('#sheet')).toBeVisible();
    await page.click('#sheetOk');                        // empty: refused, stays open
    await expect(page.locator('#sheetErr')).toContainText('Pick a time');
    await page.fill('#sheetInput', '04:42');
    await page.click('#sheetOk');
    await expect(page.locator('.b3').nth(2)).not.toContainText('Unknown', { timeout: 20_000 });
  });
});

test.describe('navigation', () => {
  test('every tab opens its view and marks itself current; Me\'s rows open theirs', async ({ page }) => {
    await signIn(page, 'btn-nav');
    await onboard(page);
    const tabs = [['#tabChat', '#vMain'], ['#tabPpl', '#vPeople'], ['#tabMe', '#vMe']];
    for (const [btn, view] of tabs) {
      await page.click(btn);
      await on(page, view);
      await expect(page.locator(btn)).toHaveAttribute('aria-current', 'page');
      await expect(page.locator('.navbtn[aria-current]')).toHaveCount(1);
      await expect(page.locator('#composer')).toBeVisible({ visible: view === '#vMain' });
    }
    // The chart, today, her patterns and settings are steps on Me: each opens
    // its screen, keeps Me lit, and has a way back.
    for (const [where, view] of [['chart', '#vReading'], ['today', '#vToday'], ['patterns', '#vMemory'], ['settings', '#vSettings']]) {
      await go(page, where); await on(page, view);
      await expect(page.locator('#tabMe')).toHaveAttribute('aria-current', 'page');
      await page.click(`${view} .me-back`); await on(page, '#vMe');
    }
  });
});

test.describe('people', () => {
  test('open, cancel, add, expand by click and keyboard, forget', async ({ page }) => {
    await signIn(page, 'btn-ppl');
    await onboard(page);
    await go(page, 'people');
    await expect(page.locator('.emptyart')).toBeVisible();

    await page.click('#cOpen');
    await expect(page.locator('#cBox')).toBeVisible();
    await expect(page.locator('#cOpen')).toBeHidden();
    await page.fill('#cName', 'Throwaway');
    await page.click('#cCancel');
    await expect(page.locator('#cBox')).toBeHidden();
    await expect(page.locator('#cOpen')).toBeVisible();
    await expect(page.locator('#cName')).toHaveValue('');

    await page.click('#cOpen');
    await expect(page.locator('#cGo')).toBeDisabled();
    await page.fill('#cName', 'Theo');
    await page.fill('#cRel', 'talking');
    await page.fill('#cDate', '1998-07-02');
    await page.fill('#cCity', 'lagos');
    const pt = page.locator('#cAc button', { hasText: 'PT' }).first();
    await pt.click();
    await expect(page.locator('#cCity')).toHaveValue(/Lagos.*PT/);
    // The PT pick must be sent as the PT city, not a bare "Lagos" the server
    // would resolve to Nigeria.
    const [req] = await Promise.all([
      page.waitForRequest(r => r.url().endsWith('/api/connections') && r.method() === 'POST'),
      page.click('#cGo'),
    ]);
    expect(req.postDataJSON().city).toBe('Lagos|Faro|PT');
    const card = page.locator('.conn').first();
    await expect(card).toContainText('Theo');
    await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'true');  // opens on add

    // Tap the header: an open card holds a full reading, and taps inside the
    // reading are deliberately not a toggle.
    await card.locator('.top').click();
    await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'false');
    await card.locator('.conn-hd').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.conn').first().locator('.conn-hd')).toHaveAttribute('aria-expanded', 'true');

    await page.click('[data-del]');
    await expect(page.locator('#sheet')).toBeVisible();
    await page.click('#sheetCancel');                    // cancel keeps them
    await expect(page.locator('.conn')).toHaveCount(1);
    await page.click('[data-del]');
    await page.click('#sheetOk');
    await expect(page.locator('.conn')).toHaveCount(0);
    await expect(page.locator('.emptyart')).toBeVisible();
  });
});

test.describe('placement deep-dive', () => {
  test('tapping a big-three card opens its whole story, and back returns', async ({ page }) => {
    await signIn(page, 'btn-place');
    await onboard(page);                       // onboarding lands on the reading
    await on(page, '#vReading');
    const sun = page.locator('#rdBig3 [data-place="sun"]');
    await expect(sun).toContainText('Read the whole story');
    await sun.click();
    await on(page, '#vPlace');
    await expect(page.locator('.pl-hero .k')).toHaveText('Your Sun');
    // The astrology sits folded at the end, behind her story; it opens on tap.
    await expect(page.locator('#plAnat .pl-card').first()).toBeHidden();
    await page.click('#plMore > summary');
    await expect(page.locator('#plAnat .pl-card').first()).toBeVisible();
    await expect(page.locator('#plStory article').first()).toBeVisible({ timeout: 20_000 });
    const fit = page.locator('#plStory article').first().locator('[data-fit="me"]');
    await fit.click();
    await expect(fit).toHaveAttribute('aria-pressed', 'true');
    await page.click('#plBack');
    await on(page, '#vReading');
    // keyboard too
    await page.locator('#rdBig3 [data-place="moon"]').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.pl-hero .k')).toHaveText('Your Moon');
  });
});

test.describe('today with someone', () => {
  test('the card shows today, the reasons, and a check-in that sticks', async ({ page }) => {
    await signIn(page, 'btn-today');
    await onboard(page);
    await go(page, 'people');
    await page.click('#cOpen');
    await page.fill('#cName', 'Theo');
    await page.fill('#cDate', '2000-11-02');
    await page.fill('#cCity', 'Chicago');
    await page.locator('#cAc button').first().click();
    await page.click('#cGo');
    const card = page.locator('.conn').first();
    await expect(card.locator('.td')).toContainText('today');
    const today = card.locator('.pday');
    await expect(today.locator('h4')).toHaveText('Today with Theo', { timeout: 20_000 });
    await expect(today.locator('svg.trend')).toBeVisible();
    await expect(today.locator('.hl')).not.toBeEmpty();

    await today.locator('[data-feel-note]').fill('argued about the trip');
    await today.locator('[data-feel="strained"]').click();
    await expect(today.locator('[data-feel="strained"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(today.locator('.saved')).toBeVisible();
    await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'true');   // a check-in is not a toggle

    await page.reload();
    await on(page, '#vPeople');                          // a reload keeps the tab
    await page.locator('.conn').first().locator('.top').click();
    const again = page.locator('.conn').first().locator('.pday');
    await expect(again.locator('[data-feel="strained"]')).toHaveAttribute('aria-pressed', 'true', { timeout: 20_000 });
    await expect(again.locator('[data-feel-note]')).toHaveValue('argued about the trip');
  });
});

test.describe('plain words', () => {
  test('an astrology word is underlined, a tap explains it, and Settings can switch it off', async ({ page }) => {
    await signIn(page, 'btn-gloss');
    await onboard(page);                       // lands on the reading
    await on(page, '#vReading');
    const term = page.locator('#vReading .gloss').first();
    await expect(term).toBeVisible({ timeout: 10_000 });
    await term.click();
    await expect(page.locator('#glossPop')).toBeVisible();
    await expect(page.locator('#glossWhat')).not.toBeEmpty();
    await expect(page.locator('#glossYou')).not.toBeEmpty();
    await page.keyboard.press('Escape');
    await expect(page.locator('#glossPop')).toBeHidden();
    // The card it sits on did not open: a tap on the word is not a tap on the card.
    await expect(page.locator('#vReading')).toHaveClass(/on/);

    await go(page, 'settings');
    await page.click('#glossBtn');
    await expect(page.locator('#glossBtn')).toHaveAttribute('aria-checked', 'false');
    await expect(page.locator('.gloss')).toHaveCount(0);
    await page.click('#glossBtn');
    await expect(page.locator('#glossBtn')).toHaveAttribute('aria-checked', 'true');
  });
});

test.describe('settings', () => {
  test('country sheet validates, saves; Escape and the backdrop both dismiss', async ({ page }) => {
    await signIn(page, 'btn-set');
    await onboard(page);
    await go(page, 'settings');

    await page.click('#countryBtn');
    await page.keyboard.press('Escape');
    await expect(page.locator('#sheet')).toBeHidden();

    await page.click('#countryBtn');
    await page.mouse.click(10, 10);                      // the scrim
    await expect(page.locator('#sheet')).toBeHidden();

    // A list of names now, not two letters to get right.
    await page.click('#countryBtn');
    await page.selectOption('#sheetSelect', 'GB');
    await page.click('#sheetOk');
    await expect(page.locator('#sheet')).toBeHidden();
    await expect(page.locator('#countrySub')).toContainText('United Kingdom');
  });

  test('push button responds instead of failing silently', async ({ page }) => {
    await signIn(page, 'btn-push');
    await onboard(page);
    await go(page, 'settings');
    const btn = page.locator('#pushBtn');
    // Disabled until it knows, so an early tap can't hit the wrong action.
    await expect(btn).not.toHaveText('Checking…');
    const label = await btn.innerText();
    expect(['Turn on', 'Turn off', 'Blocked', 'Unavailable', 'Add to Home Screen']).toContain(label);
    if (label === 'Unavailable') return expect(btn).toBeDisabled();
    // Every other state is tappable. The help states explain themselves.
    await expect(btn).toBeEnabled();
    await btn.click();
    if (label === 'Add to Home Screen' || label === 'Blocked') {
      await expect(page.locator('#sheet')).toBeVisible();
      await page.click('#sheetOk');
      await expect(page.locator('#sheet')).toBeHidden();
    }
    await expect(page.locator('#pushSub')).not.toBeEmpty();
  });

  test('narrowing an unknown birth time walks five dated sheets', async ({ page }) => {
    await signIn(page, 'btn-rect');
    await onboard(page, { time: '' });
    await go(page, 'settings');
    await expect(page.locator('#rectRow')).toBeVisible();
    await page.click('#rectGo');
    const dates = ['2015-06-01', '2018-02-14', '', '2021-09-30', ''];
    for (const [i, d] of dates.entries()) {
      await expect(page.locator('#sheetStep')).toHaveText(`${i + 1} of 5`);
      if (d) await page.fill('#sheetInput', d);
      await page.click('#sheetOk');
    }
    await expect(page.locator('#rectSub')).not.toHaveText(/^Working/, { timeout: 20_000 });
    await expect(page.locator('#rectSub')).not.toContainText('at least three');
    if (await page.locator('#sheet').isVisible()) await page.click('#sheetCancel');
  });

  test('stopping the narrowing early says why', async ({ page }) => {
    await signIn(page, 'btn-rect2');
    await onboard(page, { time: '' });
    await go(page, 'settings');
    await page.click('#rectGo');
    await page.click('#sheetCancel');
    await expect(page.locator('#rectSub')).toContainText('at least three');
  });

  test('legal links open their pages', async ({ page }) => {
    await signIn(page, 'btn-legal');
    await onboard(page);
    await go(page, 'settings');
    for (const name of ['Privacy', 'Terms', 'Accuracy']) {
      const [doc] = await Promise.all([page.waitForEvent('popup'),
        page.locator('#vSettings a', { hasText: name }).click()]);
      await expect(doc.locator('h1')).toBeVisible();
      await doc.close();
    }
  });

  test('the accuracy page shows the live NASA comparison', async ({ page }) => {
    await page.goto('/app');
    const [doc] = await Promise.all([page.waitForEvent('popup'),
      page.locator('#accuracyLink a').click()]);
    await expect(doc.locator('h1')).toContainText('to the arcsecond');
    // The static figures are replaced by the server's own rerun.
    await expect(doc.locator('#liveText')).toContainText('Rerun live');
    await expect(doc.locator('#bodies tr')).toHaveCount(11);
    const worst = parseFloat(await doc.locator('#worst').innerText());
    expect(worst).toBeLessThan(1);
    await doc.close();
  });

  test('sign out stays in the app on its sign-in screen, and /app is signed out', async ({ page }) => {
    await signIn(page, 'btn-out');
    await onboard(page);
    await go(page, 'settings');
    await page.click('#signOut');
    await expect(page).toHaveURL(/\/app$/);                         // the app, not the website
    await on(page, '#vAuth');
    await signedOutReloaded(page);
    await page.goto('/app');
    await on(page, '#vAuth');
    await expect(page.locator('#tabbar')).toBeHidden();
  });

  test('delete needs both steps and the typed word', async ({ page }) => {
    await signIn(page, 'btn-del');
    await onboard(page);
    await go(page, 'settings');
    await page.click('#delAcct');
    await page.click('#sheetCancel');                    // step one, backed out
    await on(page, '#vSettings');
    await page.click('#delAcct');
    await page.click('#sheetOk');
    await page.fill('#sheetInput', 'delete');            // wrong case
    await page.click('#sheetOk');
    await expect(page.locator('#sheetErr')).toContainText('DELETE');
    await page.fill('#sheetInput', 'DELETE');
    await page.click('#sheetOk');
    await expect(page).toHaveURL(/\/app$/);
    await expect(page.locator('#authErr')).toContainText('deleted');
    await page.goto('/app');
    await on(page, '#vAuth');
  });
});

test('a lapsed session goes back to sign-in and says so', async ({ page, context }) => {
  await signIn(page, 'btn-lapse');
  await onboard(page);
  await context.clearCookies();
  // A request already in flight can find the lapse before the tap does;
  // either way she has to land on sign-in.
  await page.locator('#tabPpl').click({ timeout: 2000 }).catch(() => {});
  await on(page, '#vAuth');
  await expect(page.locator('#authErr')).toContainText('signed out');
});
