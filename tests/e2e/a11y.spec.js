// The automated half of accessibility: alt text, labels, names, contrast. It
// can't judge everything, but what it can judge stays fixed. Keyboard is below.
const { test, expect } = require('@playwright/test');
const axePath = require.resolve('axe-core/axe.min.js');
const { signIn, onboard } = require('./helpers');

async function violations(page) {
  await page.addScriptTag({ path: axePath });
  await page.waitForTimeout(1600); // the entrance animations fade in; axe reads their mid-fade colors
  const r = await page.evaluate(() => window.axe.run(document, {
    runOnly: ['wcag2a', 'wcag2aa'] }));
  return r.violations.map(v => `${v.id} (${v.impact}): ` + v.nodes.slice(0, 8).map(n => {
    const c = n.any[0] && n.any[0].data;
    const ratio = c && c.contrastRatio ? ` ${c.contrastRatio} ${c.fgColor} on ${c.bgColor}` : '';
    return n.target.join(' ') + ratio;
  }).join(' | '));
}

const PAGES = ['/', '/ig', '/check', '/accuracy', '/privacy', '/terms', '/refunds', '/support', '/cookies', '/shop', '/couples', '/gifts', '/gifts/scorpio'];
for (const scheme of ['light', 'dark']) {
  for (const path of PAGES) {
    test(`${path} has no WCAG A/AA failures (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto(path);
      expect(await violations(page)).toEqual([]);
    });
  }
  test(`the app's screens have no WCAG A/AA failures (${scheme})`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto('/app');
    expect(await violations(page), 'sign in').toEqual([]);
    await signIn(page, 'a11y');
    expect(await violations(page), 'onboarding').toEqual([]);
    await onboard(page);
    expect(await violations(page), 'reading').toEqual([]);
  });
}

test('no random id until yes: the banner asks once, and no is respected', async ({ page }) => {
  const ids = () => page.evaluate(() => [localStorage.getItem('ms_anon'), sessionStorage.getItem('ms_sess')]);
  await page.goto('/');
  const bar = page.locator('#msConsent');
  await expect(bar).toHaveCount(0); // never on the first screen
  await page.evaluate(() => window.scrollTo(0, 400));
  await expect(bar).toBeVisible();
  expect(await violations(page), 'the banner').toEqual([]);
  await page.waitForFunction(() => window.MsTrack);
  expect(await page.evaluate(() => window.MsTrack.anonId())).toBeNull();
  expect(await ids()).toEqual([null, null]);
  await bar.getByRole('button', { name: 'No thanks' }).click();
  await expect(bar).toHaveCount(0);
  await page.reload();
  await expect(bar).toHaveCount(0);
  await page.waitForFunction(() => window.MsTrack);
  expect(await page.evaluate(() => window.MsTrack.anonId())).toBeNull();

  await page.goto('/cookies');
  await page.getByRole('button', { name: 'Allow' }).click();
  await expect(page.locator('#ccState')).toContainText('You said yes');
  await page.goto('/');
  await page.waitForFunction(() => window.MsTrack);
  expect(await page.evaluate(() => window.MsTrack.anonId())).toMatch(/^[0-9A-Z]{26}$/);
  await page.goto('/cookies');
  await page.getByRole('button', { name: 'No thanks' }).click();
  expect(await page.evaluate(() => localStorage.getItem('ms_anon'))).toBeNull();
});

test('Global Privacy Control means no banner and no id', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'globalPrivacyControl', { value: true }));
  await page.goto('/');
  await page.waitForFunction(() => window.MsTrack);
  await expect(page.locator('#msConsent')).toHaveCount(0);
  expect(await page.evaluate(() => window.MsTrack.anonId())).toBeNull();
});

test('nothing asks Google for fonts, and the fonts still load', async ({ page }) => {
  const hosts = [];
  page.on('request', r => hosts.push(new URL(r.url()).hostname));
  for (const path of ['/', '/privacy', '/app']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
  }
  expect(hosts.filter(h => /google|gstatic/.test(h))).toEqual([]);
  await page.goto('/');
  expect(await page.evaluate(async () => {
    await document.fonts.load('400 20px Newsreader'); await document.fonts.load('italic 400 20px Newsreader');
    await document.fonts.load('500 16px Geist');
    return ['400 20px Newsreader', 'italic 400 20px Newsreader', '500 16px Geist'].every(f => document.fonts.check(f));
  })).toBe(true);
});

test('the free reading works from the keyboard alone', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'keyboards are a desktop path; phones use their own date pickers');
  await page.goto('/');
  await page.locator('#frDate').focus();
  await page.keyboard.type('11081996');
  await page.keyboard.press('Enter');
  await expect(page.locator('#frMore')).toBeVisible();
  await page.locator('#frTime').focus();
  await page.keyboard.type('0640PM');
  await page.locator('#frCity').focus();
  await page.keyboard.type('Austin, T');
  await expect(page.locator('#frAc button').first()).toBeVisible();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await page.locator('#frDeep').focus();
  await page.keyboard.press('Enter');
  const card = page.locator('#frList .frcard button').first();
  await expect(card).toBeVisible();
  await card.focus();
  await expect(card).toBeFocused();
  const outline = await card.evaluate(e => getComputedStyle(e).outlineStyle + getComputedStyle(e).boxShadow);
  expect(outline).not.toBe('nonenone');
});

for (const path of ['/privacy', '/terms', '/refunds']) {
  test(`${path} names the company and has no placeholders left`, async ({ page }) => {
    await page.goto(path);
    const text = await page.locator('body').innerText();
    expect(text).toContain('XII Studios LLC');
    expect(text).not.toMatch(/to be completed|private testing|not live yet|\[operator/i);
  });
}

test('the app sends no random id before a yes, or after a no, even signed in', async ({ page }) => {
  const bodies = [];
  page.on('request', r => {
    const p = new URL(r.url()).pathname;
    if ((p === '/e' || p === '/api/activity') && r.method() === 'POST') bodies.push([p, r.postData() || '']);
  });
  await page.addInitScript(() => { localStorage.setItem('ms_consent', 'no'); });
  await page.route('**/e', route => route.fulfill({ status: 204 }));
  await signIn(page, 'noid');
  await onboard(page);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await page.waitForTimeout(400);
  expect(await page.evaluate(() => [localStorage.getItem('ms_anon'), sessionStorage.getItem('ms_sess')])).toEqual([null, null]);
  for (const [p, raw] of bodies) {
    const b = JSON.parse(raw);
    expect(b.anon_id || null, p).toBeNull();
    if (p === '/e') expect(b.session, p).toBeNull();
  }
});
