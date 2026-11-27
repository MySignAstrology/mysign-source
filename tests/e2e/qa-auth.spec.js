// QA walk: sign-in and consent, as a brand-new user, at three sizes.
// Runs in the `mobile` project only; each describe sets its own viewport.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { newEmail } = require('./helpers');

const SHOTS = process.env.QA_SHOTS || path.resolve('test-results/qa-shots');
const DATA = process.env.QA_DATA || path.resolve('tests/e2e/.data');
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true, animations: 'disabled' });

test.beforeEach(({}, ti) => test.skip(ti.project.name !== 'mobile', 'QA walk runs once, viewports set per describe'));

async function requestCode(page, email) {
  await page.goto('/app');
  await page.fill('#fEmail', email);
  await page.click('#authGo');
  await expect(page.locator('#devCode')).toBeVisible();
  return (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
}

const SIZES = [
  { tag: '390', viewport: { width: 390, height: 844 } },
  { tag: 'se', viewport: { width: 375, height: 667 } },
  { tag: 'desktop', viewport: { width: 1280, height: 900 }, isMobile: false, hasTouch: false },
];

for (const size of SIZES) {
  test.describe(`sign-in @${size.tag}`, () => {
    test.use({ viewport: size.viewport, ...(size.isMobile === false ? { isMobile: false, hasTouch: false } : {}) });

    test('empty, then invalid email keeps the button disabled', async ({ page }) => {
      await page.goto('/app');
      await shot(page, `01-signin-empty-${size.tag}`);
      await expect(page.locator('#authGo')).toBeDisabled();
      await page.fill('#fEmail', 'not-an-email');
      await expect(page.locator('#authGo')).toBeDisabled();
      await page.fill('#fEmail', 'maya@gmail');
      await expect(page.locator('#authGo')).toBeDisabled();
      // A doubled paste, two @s, a space: none can receive a code.
      for (const bad of ['maya@gmail.commaya@gmail.com', 'maya@x@gmail.com', 'ma ya@gmail.com', 'maya@gmail..com']) {
        await page.fill('#fEmail', bad);
        await expect(page.locator('#authGo')).toBeDisabled();
      }
      await page.fill('#fEmail', 'maya.k+tag@mail.gmail.com');
      await expect(page.locator('#authGo')).toBeEnabled();
      await shot(page, `02-signin-invalid-${size.tag}`);
    });

    test('code step, wrong code, then right code lands on consent', async ({ page }) => {
      const email = newEmail('qa-auth');
      const code = await requestCode(page, email);
      await expect(page.locator('#sentTo')).toContainText(email);
      await shot(page, `03-signin-code-step-${size.tag}`);
      const wrong = code === '000000' ? '111111' : '000000';
      await page.fill('#fCode', wrong);
      await page.click('#authGo');
      await expect(page.locator('#authErr')).toHaveText("That code isn't right.");
      await shot(page, `04-signin-wrong-code-${size.tag}`);
      await page.fill('#fCode', code);
      await page.click('#authGo');
      await expect(page.locator('#vConsent')).toHaveClass(/on/);
      await shot(page, `05-consent-${size.tag}`);
    });
  });
}

test.describe('sign-in edge cases', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('too many wrong tries locks the code', async ({ page }) => {
    const email = newEmail('qa-tries');
    const code = await requestCode(page, email);
    const wrong = code === '000000' ? '111111' : '000000';
    for (let i = 0; i < 5; i++) {
      await page.fill('#fCode', wrong);
      await page.click('#authGo');
      await expect(page.locator('#authErr')).toHaveText("That code isn't right.");
    }
    // The sixth attempt — even the right code — is refused.
    await page.fill('#fCode', code);
    await page.click('#authGo');
    await expect(page.locator('#authErr')).toHaveText('Too many tries. Ask for a new code.');
    await shot(page, '06-signin-too-many-tries');
  });

  // BUG: after "Too many tries" / "expired", the only way to get a fresh code is
  // the small "Use a different email" link — there is no "Send a new code".
  test('locked-out / expired state offers a one-tap "send a new code"', async ({ page }) => {
    const email = newEmail('qa-resend');
    await requestCode(page, email);
    await expect(page.getByRole('button', { name: /new code|resend|send again/i })).toBeVisible({ timeout: 2000 });
  });

  test('an expired code says so', async ({ page }) => {
    const email = newEmail('qa-expired');
    const code = await requestCode(page, email);
    // Age the stored code past its 15-minute TTL.
    const k = crypto.createHash('sha256').update(email.trim().toLowerCase()).digest('hex').slice(0, 24);
    const f = path.join(DATA, '_auth', `code-${k}.json`);
    const raw = JSON.parse(fs.readFileSync(f, 'utf8'));
    // Codes in flight are a list now (a new one doesn't void the last).
    for (const c of (raw.__v || raw).codes) c.expires = 1;
    fs.writeFileSync(f, JSON.stringify(raw));
    await page.fill('#fCode', code);
    await page.click('#authGo');
    await expect(page.locator('#authErr')).toHaveText('That code expired. Ask for a new one.');
    await shot(page, '07-signin-expired');
  });

  test('too many codes requested in an hour', async ({ page }) => {
    const email = newEmail('qa-sends');
    for (let i = 0; i < 5; i++) {
      const r = await page.request.post('/api/auth/request', { data: { email } });
      expect(r.ok()).toBeTruthy();
    }
    await page.goto('/app');
    await page.fill('#fEmail', email);
    await page.click('#authGo');
    await expect(page.locator('#authErr')).toContainText('Too many codes requested');
    await shot(page, '08-signin-too-many-sends');
  });

  test('"use a different email" resets the form', async ({ page }) => {
    await requestCode(page, newEmail('qa-reemail'));
    await page.click('#reEmail');
    await expect(page.locator('#codeField')).toBeHidden();
    await expect(page.locator('#fEmail')).toBeEditable();
    await expect(page.locator('#authGo')).toHaveText('Send me a code');
    await expect(page.locator('#devCode')).toBeHidden();
  });

  // BUG (MEDIUM): a refresh while waiting for the code throws the code step
  // away — the user is back at "Send me a code" and a second send burns one of
  // the five hourly sends. iOS reloads backgrounded tabs constantly (switching
  // to Mail to read the code is exactly when it happens).
  test('refresh on the code step keeps the code step', async ({ page }) => {
    await requestCode(page, newEmail('qa-refresh-code'));
    await page.reload();
    await expect(page.locator('#codeField')).toBeVisible({ timeout: 2000 });
  });

  // The website (site/site.js) writes {kind, value}; the app before the move
  // wrote {id, kind}, and before phone sign-in a bare email.
  test('the code step resumes from what the website or an older app stored', async ({ page }) => {
    const email = newEmail('qa-handoff');
    await requestCode(page, email);
    expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('ms_code_for'))))
      .toEqual({ kind: 'email', value: email });
    for (const stored of [{ kind: 'email', value: email }, { id: email, kind: 'email' }, email]) {
      await page.evaluate(v => sessionStorage.setItem('ms_code_for', typeof v === 'string' ? v : JSON.stringify(v)), stored);
      await page.reload();
      await expect(page.locator('#codeField')).toBeVisible({ timeout: 2000 });
      await expect(page.locator('#fEmail')).toHaveValue(email);
    }
  });

  // BUG (LOW): #fCode has maxlength=6, so a pasted "217-772" or "217 772" is
  // truncated by the browser to six characters BEFORE the digit filter runs,
  // leaving five digits. index.html:642 (maxlength) + :1021 (filter).
  test('pasting a code with spaces/dashes is normalised', async ({ page }) => {
    const code = await requestCode(page, newEmail('qa-paste'));
    await page.locator('#fCode').fill(`${code.slice(0, 3)}-${code.slice(3)}`);
    await page.locator('#fCode').dispatchEvent('input');
    await expect(page.locator('#fCode')).toHaveValue(code);
    await expect(page.locator('#authGo')).toBeEnabled();
  });

  test('a phone number signs in by text, masked, and survives a refresh', async ({ page }) => {
    // Its own caller address and number: SMS limits outlive a run in the shared store.
    await page.setExtraHTTPHeaders({ 'cloudfront-viewer-address': `198.51.100.${Math.floor(Math.random() * 250)}:443` });
    const last4 = String(Math.floor(Math.random() * 1e4)).padStart(4, '0');
    await page.goto('/app');
    await expect(page.locator('#fEmailLabel')).toHaveText('Phone or email');
    await page.fill('#fEmail', `(312) 555-${last4}`);
    await page.click('#authGo');
    await expect(page.locator('#devCode')).toBeVisible();
    const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
    await expect(page.locator('#sentTo')).toContainText(`(•••) •••-${last4}`);
    await expect(page.locator('#sentTo')).not.toContainText('312');
    await page.reload();
    await expect(page.locator('#sentTo')).toContainText(`•••-${last4}`);
    await page.fill('#fCode', code);
    await page.click('#authGo');
    await expect(page.locator('#vConsent')).toHaveClass(/on/);
  });

  test('consent links open privacy and terms', async ({ page, context }) => {
    const r1 = await page.request.get('/privacy'); expect(r1.ok()).toBeTruthy();
    const r2 = await page.request.get('/terms'); expect(r2.ok()).toBeTruthy();
  });
});
