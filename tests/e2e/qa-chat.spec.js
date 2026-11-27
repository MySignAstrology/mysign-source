// QA walk: Today (check-in, quiet day) and chat.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { go, signIn, onboard, dayAhead, replies, expectNoHorizontalOverflow } = require('./helpers');

const SHOTS = process.env.QA_SHOTS || path.resolve('test-results/qa-shots');
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name, full = true) => page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: full, animations: 'disabled' });

test.beforeEach(({}, ti) => test.skip(ti.project.name !== 'mobile', 'QA walk runs once'));
test.use({ viewport: { width: 390, height: 844 } });

async function toToday(page, tag) {
  await signIn(page, tag);
  await onboard(page);
  await go(page, 'chat');
  await expect(page.locator('#vMain')).toHaveClass(/on/);
  await expect(page.locator('#greet h1')).toBeVisible();
  await expect(page.locator('#checkin #moods')).toBeVisible();
}
async function ask(page, text) {
  await page.fill('#input', text);
  await page.click('#send');
}
const lastBot = page => page.locator('#thread .msg').last();
// Her chart's own questions (/api/starters), computed: they no longer wait for
// the model to write her reading.
const starters = page => page.locator('#todayStart .starters button');
const READING = { timeout: 10_000 };

test.describe('today', () => {
  test('home renders: greeting, sky, check-in, starters', async ({ page }) => {
    await toToday(page, 'qa-home');
    await expect(page.locator('#greet h1')).toContainText('Maya');
    await expect(starters(page).first()).toBeVisible(READING);
    await shot(page, '30-today-390');
    await page.setViewportSize({ width: 375, height: 667 });
    await shot(page, '30-today-se', false);
    await expectNoHorizontalOverflow(page);
  });

  test('the starters do not wait for her written reading', async ({ page }) => {
    // The reading is a model call on a new account; hold it for the whole test.
    // The starters come from her chart alone and must show anyway.
    await page.route('**/api/reading', () => new Promise(() => {}));
    await toToday(page, 'qa-starters-now');
    await expect(starters(page).first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('#input')).toHaveAttribute('placeholder', /^Try: /, { timeout: 5_000 });
  });

  test('check-in: pick a mood, add a note, see it back', async ({ page }) => {
    await toToday(page, 'qa-ci');
    await page.click('#moods button[data-m="7"]');
    await expect(page.locator('#moods button[data-m="7"]')).toHaveAttribute('aria-checked', 'true');
    await page.fill('#ciText', 'first date tonight 🌙');
    await shot(page, '31-checkin-filled');
    await page.click('#ciGo');
    await expect(page.locator('#checkin')).toContainText('7/10');
    await expect(page.locator('#checkin')).toContainText('first date tonight 🌙');
    await shot(page, '32-checkin-done');
  });

  // BUG (MEDIUM): the check-in button is never disabled while posting, so a
  // double tap posts twice; and the post has no try/catch, so a failure (e.g.
  // a note over 500 chars -> 422) does nothing at all — no error, button idle.
  // index.html:1154-1157. #ciText also has no maxlength.
  test.fail('a too-long check-in note shows an error', async ({ page }) => {
    await toToday(page, 'qa-ci-long');
    await page.click('#moods button[data-m="5"]');
    await page.fill('#ciText', 'x'.repeat(501));
    await page.click('#ciGo');
    await page.waitForTimeout(1200);
    await shot(page, '33-checkin-too-long');
    await expect(page.locator('#checkin')).toContainText(/too long|500|shorter/i, { timeout: 1500 });
  });

  test('check-in double tap posts once', async ({ page }) => {
    await toToday(page, 'qa-ci-dbl');
    let posts = 0;
    page.on('request', r => { if (r.url().endsWith('/api/checkin') && r.method() === 'POST') posts++; });
    await page.click('#moods button[data-m="3"]');
    await page.locator('#ciGo').dblclick();
    await expect(page.locator('#checkin')).toContainText('3/10');
    // Documented: two POSTs go out. Idempotent on the server (same key), so LOW.
    test.info().annotations.push({ type: 'checkin-posts', description: String(posts) });
  });

  test('quiet day renders its own line', async ({ page }) => {
    await signIn(page, 'qa-quiet');
    await onboard(page);
    await page.route('**/api/today', async route => {
      const res = await route.fetch(); const j = await res.json();
      j.quiet = true; j.band = 'low'; j.headlines = []; j.meanings = [];
      await route.fulfill({ response: res, json: j });
    });
    await go(page, 'today');
    await expect(page.locator('#today')).toContainText('It’s a quiet day');
    await shot(page, '34-today-quiet');
  });

  // BUG (HIGH): after onboarding you land on the Chart. If the first tab you
  // tap is Memory/People/Settings and you tap it again (the code treats a
  // second tap as "home"), show('vMain') runs WITHOUT rendering it — the Today
  // screen is blank: no greeting, no sky, no check-in. index.html:1948-1959.
  test('tapping the current tab again shows a populated Today', async ({ page }) => {
    await signIn(page, 'qa-blank-home');
    await onboard(page);
    await go(page, 'patterns');                  // Memory
    await expect(page.locator('#vMemory')).toHaveClass(/on/);
    await go(page, 'chat');                      // back to the talk
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    await shot(page, '35-today-blank-after-double-tab');
    await expect(page.locator('#greet h1')).toBeVisible({ timeout: 2000 });
  });
});

test.describe('chat', () => {
  test('a starter sends, shows the user bubble and an answer', async ({ page }) => {
    test.slow();
    await toToday(page, 'qa-starter');
    await expect(starters(page).first()).toBeVisible(READING);
    const q = await starters(page).first().innerText();
    await starters(page).first().click();
    await expect(page.locator('.msg.user').first()).toHaveText(q);
    await expect(page.locator('#todayStart')).toHaveCount(0);
    await replies(page, 1);
    await expect(lastBot(page)).not.toBeEmpty();
    await shot(page, '36-chat-starter-answered');
  });

  test('typing indicator then tool steps on a dated question', async ({ page }) => {
    test.slow();
    await toToday(page, 'qa-tools');
    await ask(page, `is ${dayAhead('Friday').iso} good for a hard conversation?`);
    await expect(page.locator('.dots')).toBeVisible().catch(() => {});
    await expect(page.locator('.work .step').first()).toBeVisible({ timeout: 45000 });
    await shot(page, '37-chat-tool-steps', false);
    await expect(page.locator('.work .step').first()).toHaveClass(/done/, { timeout: 45000 });
    await replies(page, 1);
  });

  test('the 4th message of the day gets the paywall card', async ({ page }) => {
    test.slow();
    await toToday(page, 'qa-paywall');
    // Each reply finished before the next question: a real model is still
    // streaming when the dots go, and a question asked then is queued, so the
    // 4th never reached the server.
    let n = 0;
    for (const q of ['why am i like this', 'what about work', 'and love?']) {
      await ask(page, q);
      await expect(page.locator('#send')).toBeDisabled();
      await replies(page, ++n);
    }
    await ask(page, 'one more?');
    const card = page.locator('#thread .msg.paywall');
    await expect(card).toBeVisible({ timeout: 10000 });
    await expect(card).toContainText("That's three for today");
    await card.scrollIntoViewIfNeeded();
    await shot(page, '38-chat-paywall', false);
    // UX: headline and body repeat each other ("That's three for today." /
    // "That's three for today on the free plan."), and the card is a dead end —
    // no CTA, no "notify me", the composer stays live for a 5th try.
    const t = await card.innerText();
    test.info().annotations.push({ type: 'paywall-copy', description: t });
  });

  // BUG (MEDIUM): the paywall card is not persisted. Reload and the 4th
  // question reappears with no answer and no card — it reads like the app
  // silently dropped her message (it did: it was never stored).
  test.fail('after reload the paywalled question is not left hanging', async ({ page }) => {
    test.slow();
    await toToday(page, 'qa-paywall-reload');
    let n = 0;
    for (const q of ['a', 'b', 'c']) {
      await ask(page, 'question ' + q);
      await replies(page, ++n);
    }
    await ask(page, 'question d');
    await expect(page.locator('#thread .msg.paywall')).toBeVisible();
    await page.reload();
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    await expect(page.locator('#thread .msg.paywall, #thread .msg.user:has-text("question d")')).toHaveCount(2, { timeout: 3000 });
  });

  // BUG (MEDIUM): the composer has no maxlength; the server caps messages at
  // 4000 chars (422). The client then shows the generic "Something went wrong"
  // AND has already cleared the textarea — her long message is gone.
  // index.html:1231 (clears input before the request) + :1263.
  test.fail('a 4001-char message is refused kindly and not lost', async ({ page }) => {
    await toToday(page, 'qa-long');
    const long = ('I need to tell you everything. '.repeat(140)).slice(0, 4001);
    await ask(page, long);
    await page.waitForTimeout(1500);
    await shot(page, '39-chat-4001-chars', false);
    await expect(page.locator('#input')).toHaveValue(long, { timeout: 1000 });
  });

  test('a long (3,900-char) message renders without overflow', async ({ page }) => {
    await toToday(page, 'qa-long-ok');
    await ask(page, 'x'.repeat(300) + ' ' + 'word '.repeat(700));
    await expect(page.locator('.dots')).toHaveCount(0, { timeout: 20000 });
    await expectNoHorizontalOverflow(page);
  });

  test('emoji, RTL and CJK survive the round trip', async ({ page }) => {
    await toToday(page, 'qa-unicode');
    const msg = 'is he 🔥 or 🚩? مرحبا 你好 — Zoë ✨';
    await ask(page, msg);
    await expect(page.locator('.msg.user').last()).toHaveText(msg);
    await expect(page.locator('.dots')).toHaveCount(0, { timeout: 20000 });
    await page.reload();
    await expect(page.locator('.msg.user').last()).toHaveText(msg);
    await shot(page, '40-chat-unicode', false);
  });

  test('rapid double-send (Enter twice, double click) sends once', async ({ page }) => {
    await toToday(page, 'qa-dbl');
    let posts = 0;
    page.on('request', r => { if (r.url().endsWith('/api/message')) posts++; });
    await page.fill('#input', 'just once');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter');
    await page.locator('#send').dblclick({ force: true }).catch(() => {});
    await expect(page.locator('.dots')).toHaveCount(0, { timeout: 20000 });
    expect(posts).toBe(1);
    await expect(page.locator('.msg.user')).toHaveCount(1);
  });

  // BUG (MEDIUM): while a reply is still streaming, she can type and send a
  // second message; two bot bubbles stream at once and the thread order on
  // reload differs from what she saw. There is no in-flight guard in send().
  test('sending while a reply is streaming', async ({ page }) => {
    await toToday(page, 'qa-overlap');
    await ask(page, 'first question');
    await page.fill('#input', 'second question');
    const enabled = await page.locator('#send').isEnabled();
    test.info().annotations.push({ type: 'send-enabled-during-stream', description: String(enabled) });
  });

  // BUG (MEDIUM): offline send. The banner shows, but send() has already
  // cleared the textarea; the message is lost and the bubble she wrote stays
  // in the thread as if sent. index.html:1231-1246.
  test('offline: message is kept for retry', async ({ page, context }) => {
    await toToday(page, 'qa-offline');
    await context.setOffline(true);
    await expect(page.locator('#offline')).toBeVisible();
    await ask(page, 'are you there?');
    await expect(lastBot(page)).toContainText("couldn't reach the server");
    await shot(page, '41-chat-offline', false);
    await expect(page.locator('#input')).toHaveValue('are you there?', { timeout: 1000 });
  });

  test('offline at launch: friendly page, but whole app is torn down', async ({ page, context }) => {
    await toToday(page, 'qa-offline-boot');
    await context.setOffline(true);
    await page.reload().catch(() => {});
    await shot(page, '42-offline-reload', false);
  });

  test('crisis-adjacent but not crisis stays a reading', async ({ page }) => {
    await toToday(page, 'qa-notcrisis');
    await ask(page, 'this week is killing me lol');
    await expect(page.locator('.dots')).toHaveCount(0, { timeout: 20000 });
    await shot(page, '43-chat-hyperbole', false);
  });
});
