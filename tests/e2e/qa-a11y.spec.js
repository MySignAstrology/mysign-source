// QA: accessibility — axe on every screen in both themes, keyboard-only walk,
// dialog focus handling, touch target sizes.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { go, signIn, onboard, newEmail } = require('./helpers');
let AxeBuilder = null;
try { AxeBuilder = require('@axe-core/playwright').default; } catch (e) { /* optional */ }

const SHOTS = process.env.QA_SHOTS || path.resolve('test-results/qa-shots');
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name, full = true) => page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: full, animations: 'disabled' });
const OUT = path.join(SHOTS, 'axe');
fs.mkdirSync(OUT, { recursive: true });

test.beforeEach(({}, ti) => test.skip(ti.project.name !== 'mobile', 'QA walk runs once'));
test.use({ viewport: { width: 390, height: 844 } });

async function axe(page, name) {
  if (!AxeBuilder) return [];
  // Let the entrance animations (opacity/blur) finish, or contrast is measured
  // against half-faded text.
  await page.waitForTimeout(900);
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'best-practice']).analyze();
  const slim = r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help,
    nodes: v.nodes.slice(0, 6).map(n => ({ target: n.target.join(' '), summary: (n.failureSummary || '').split('\n').slice(0, 3).join(' ') })) ,
    count: v.nodes.length }));
  fs.writeFileSync(path.join(OUT, name + '.json'), JSON.stringify(slim, null, 1));
  return slim;
}

for (const scheme of ['light', 'dark']) {
  test.describe(`axe @${scheme}`, () => {
    test.use({ colorScheme: scheme });

    test('every screen', async ({ page }) => {
      test.setTimeout(120_000);
      const all = {};
      await page.goto('/app');
      all.signin = await axe(page, `${scheme}-signin`);
      await shot(page, `70-${scheme}-signin`, false);
      await signIn(page, `qa-axe-${scheme}`);
      all.onboard = await axe(page, `${scheme}-onboard`);
      await shot(page, `71-${scheme}-onboard`, false);
      await onboard(page);
      all.reading = await axe(page, `${scheme}-reading`);
      await shot(page, `72-${scheme}-reading`, false);
      await go(page, 'chat');
      await expect(page.locator('#checkin #moods')).toBeVisible();
      all.chat = await axe(page, `${scheme}-chat`);
      await go(page, 'me');
      await expect(page.locator('#mePath button').first()).toBeVisible();
      all.me = await axe(page, `${scheme}-me`);
      await go(page, 'today');
      await expect(page.locator('#today .k')).toBeVisible();
      all.today = await axe(page, `${scheme}-today`);
      await shot(page, `73-${scheme}-today`, false);
      await go(page, 'people');
      await page.click('#cOpen');
      await page.fill('#cName', 'Theo'); await page.fill('#cDate', '1997-06-02'); await page.fill('#cTime', '14:00');
      await page.fill('#cCity', 'chicago, il'); await page.locator('#cAc button').first().click();
      await page.click('#cGo');
      await expect(page.locator('.deep .score')).toBeVisible({ timeout: 20000 });
      all.people = await axe(page, `${scheme}-people`);
      await shot(page, `74-${scheme}-people`, false);
      await go(page, 'patterns');
      all.memory = await axe(page, `${scheme}-memory`);
      await go(page, 'settings');
      await expect(page.locator('#sEmail')).not.toBeEmpty();
      all.settings = await axe(page, `${scheme}-settings`);
      await shot(page, `75-${scheme}-settings`, false);
      await page.click('#delAcct');
      all.sheet = await axe(page, `${scheme}-sheet`);
      await shot(page, `76-${scheme}-sheet`, false);
      const summary = Object.fromEntries(Object.entries(all).map(([k, v]) => [k, v.map(x => `${x.id}(${x.count})`).join(', ')]));
      fs.writeFileSync(path.join(OUT, `${scheme}-summary.json`), JSON.stringify(summary, null, 1));
      test.info().annotations.push({ type: 'axe', description: JSON.stringify(summary) });
    });
  });
}

test.describe('keyboard only', () => {
  test.use({ viewport: { width: 1280, height: 900 }, isMobile: false, hasTouch: false });

  test('sign-in to first reading without a mouse', async ({ page }) => {
    await page.goto('/app');
    await page.keyboard.press('Tab');
    await expect(page.locator('#fEmail')).toBeFocused();
    await page.keyboard.type(newEmail('qa-kbd'));
    await page.keyboard.press('Enter');
    await expect(page.locator('#fCode')).toBeFocused();
    await expect(page.locator('#devCode')).toBeVisible();
    const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
    await page.keyboard.type(code);
    await page.keyboard.press('Enter');
    await expect(page.locator('#vConsent')).toHaveClass(/on/);
    // UX: focus is not moved to the new screen; Tab from <body>.
    const focusedAfterConsentShows = await page.evaluate(() => document.activeElement && document.activeElement.id);
    test.info().annotations.push({ type: 'focus-after-view-change', description: String(focusedAfterConsentShows) });
    await page.locator('#aiOk').focus();
    await page.keyboard.press('Space');
    await expect(page.locator('#aiOk')).toBeChecked();
    await page.keyboard.press('Tab');                     // privacy link
    await page.keyboard.press('Tab');                     // terms link
    await page.keyboard.press('Tab');
    await expect(page.locator('#consentGo')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#vOnboard')).toHaveClass(/on/);
    await page.locator('#fName').focus();
    await page.keyboard.type('Kay');
    // Date/time inputs tab through their own segments in Chromium; fill them
    // and move focus explicitly rather than count Tabs.
    await page.fill('#fDate', '1999-03-14'); await page.fill('#fTime', '04:42');
    await page.locator('#noTime').focus();
    await page.keyboard.press('Tab');                     // city
    await expect(page.locator('#fCity')).toBeFocused();
    await page.keyboard.type('chicago, il');
    await expect(page.locator('#ac button').first()).toBeVisible();
    // ArrowDown goes into the list (UX audit M2).
    await page.keyboard.press('ArrowDown');
    await expect(page.locator('#ac button').first()).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#go')).toBeEnabled();
    await shot(page, '77-keyboard-onboard-filled', false);
    await page.locator('#go').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#vReading')).toHaveClass(/on/, { timeout: 20000 });
  });

  // Was a BUG (MEDIUM, a11y): the city suggestions didn't respond to ArrowDown/Up.
  test('autocomplete supports arrow keys', async ({ page }) => {
    await signIn(page, 'qa-kbd-arrow');
    await page.locator('#fCity').focus();
    await page.keyboard.type('chicago');
    await expect(page.locator('#ac button').first()).toBeVisible();
    await page.keyboard.press('ArrowDown');
    await expect(page.locator('#ac button').first()).toBeFocused({ timeout: 1000 });
  });

  // Was a BUG (MEDIUM, a11y): the sheet was aria-modal but didn't trap focus.
  test('Tab stays inside the open sheet', async ({ page }) => {
    await signIn(page, 'qa-trap');
    await onboard(page);
    await go(page, 'settings');
    await page.click('#delAcct');
    await expect(page.locator('#sheet')).toBeVisible();
    for (let i = 0; i < 4; i++) await page.keyboard.press('Tab');
    const inside = await page.evaluate(() => !!document.activeElement.closest('#sheet'));
    expect(inside).toBe(true);
  });

  test('Escape closes the sheet and returns focus', async ({ page }) => {
    await signIn(page, 'qa-esc');
    await onboard(page);
    await go(page, 'settings');
    // Let the tab glide finish: focus moves to the new view's heading at its end.
    await expect(page.locator('#vSettings')).toHaveClass(/on/);
    await page.waitForFunction(() => !document.documentElement.dataset.vt);
    await page.locator('#delAcct').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#sheet')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#sheet')).toBeHidden();
    await expect(page.locator('#delAcct')).toBeFocused();
  });

  test('mood dial by keyboard', async ({ page }) => {
    await signIn(page, 'qa-kbd-mood');
    await onboard(page);
    await go(page, 'chat');
    await page.locator('#moods button[data-m="1"]').focus();
    // radiogroup pattern would move with arrows; here only Tab works.
    await page.keyboard.press('ArrowRight');
    const moved = await page.evaluate(() => document.activeElement.dataset.m);
    test.info().annotations.push({ type: 'mood-arrow-moves-to', description: String(moved) });
    await page.keyboard.press('Enter');
    await expect(page.locator('#ciGo')).toBeEnabled();
  });

  test('people card toggles with Enter/Space', async ({ page }) => {
    await signIn(page, 'qa-kbd-ppl');
    await onboard(page);
    await go(page, 'people');
    await page.click('#cOpen');
    await page.fill('#cName', 'K'); await page.fill('#cDate', '1997-06-02');
    await page.fill('#cCity', 'chicago, il'); await page.locator('#cAc button').first().click();
    await page.click('#cGo');
    const card = page.locator('.conn').first();
    await expect(card.locator('.conn-hd')).toHaveAttribute('aria-expanded', 'true', { timeout: 20000 });
    await card.locator('.conn-hd').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.conn').first().locator('.conn-hd')).toHaveAttribute('aria-expanded', 'false');
  });
});

test.describe('touch targets and small screens', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test('interactive controls are at least 44x44 (reported)', async ({ page }) => {
    await signIn(page, 'qa-targets');
    await onboard(page);
    const small = {};
    for (const [tab, sel] of [['chat', '#vMain'], ['me', '#vMe'], ['chart', '#vReading'], ['people', '#vPeople'], ['settings', '#vSettings']]) {
      await go(page, tab);
      await expect(page.locator(sel)).toHaveClass(/on/);
      await page.waitForTimeout(800);
      small[sel] = await page.evaluate(sel => [...document.querySelectorAll(`${sel} button, ${sel} a, ${sel} select, .tabbar button, .composer button`)]
        .filter(el => el.offsetParent)
        .map(el => { const r = el.getBoundingClientRect(); return { t: (el.id || el.textContent.trim().slice(0, 24)), w: Math.round(r.width), h: Math.round(r.height) }; })
        .filter(x => x.w < 44 || x.h < 44), sel);
    }
    fs.writeFileSync(path.join(OUT, 'small-targets.json'), JSON.stringify(small, null, 1));
  });

  test('on iPhone SE the composer + tab bar leave room for content', async ({ page }) => {
    await signIn(page, 'qa-se-dock');
    await onboard(page);
    await go(page, 'chat');
    const dock = await page.locator('#dock').boundingBox();
    test.info().annotations.push({ type: 'dock-height-px', description: String(Math.round(dock.height)) });
    // More than a third of a 667px screen is chrome — documented in report.
    await shot(page, '78-se-dock', false);
  });
});
