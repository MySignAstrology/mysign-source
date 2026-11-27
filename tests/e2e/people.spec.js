const { test, expect } = require('@playwright/test');
const { go, newEmail, expectNoHorizontalOverflow } = require('./helpers');

/**
 * team2/people: "Best day to..." and the mutual reveal, end to end.
 *
 * Accounts are made through the API (the sign-up UI has its own specs), then
 * the two browsers do the parts that matter here: Maya opens Ari, re-ranks the
 * week for a date, Ari answers "How's it going between us?" first, Maya gets
 * the Home nudge without a hint of Ari's answer, answers, and both see both.
 */
async function account(ctx, tag, name, date, time) {
  const email = newEmail(tag);
  const code = (await (await ctx.request.post('/api/auth/request', { data: { email } })).json()).dev_code;
  await ctx.request.post('/api/auth/verify', { data: { email, code } });
  await ctx.request.post('/api/consent', { data: { ai: true } });
  const city = (await (await ctx.request.get('/api/cities?q=Chicago')).json())[0].id;
  const r = await ctx.request.post('/api/onboard', { data: { name, date, time, city } });
  expect(r.ok()).toBeTruthy();
}

test('plan the week with someone, then reveal how it is going', async ({ browser }) => {
  const mctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const actx = await browser.newContext({ viewport: { width: 375, height: 812 } });
  await account(mctx, 'maya-ppl', 'Maya', '1998-03-14', '04:42');
  await account(actx, 'ari-ppl', 'Ari', '1996-07-02', '21:15');
  const inv = await (await mctx.request.post('/api/invites')).json();
  expect((await actx.request.post(`/api/invites/${inv.token}/accept`,
    { data: { share_chart: true, share_mood: true } })).ok()).toBeTruthy();

  // --- Maya opens Ari: the week planner sits under today's section.
  const maya = await mctx.newPage();
  await maya.goto('/app');
  await go(maya, 'people');
  const card = maya.locator('.conn', { hasText: 'Ari' });
  await card.click();
  const week = card.locator('[id^="week-"]');
  await expect(week).toContainText('Best day to');
  await expect(week.locator('ol li')).toHaveCount(7);
  await expect(week.locator('li.best')).toHaveCount(1);
  await week.locator('[data-t2-intent="date"]').click();
  await expect(week.locator('[data-t2-intent="date"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(week.locator('.t2sum')).toContainText(/date|No day this week/);
  await expect(week.locator('ol li')).toHaveCount(7);
  await expectNoHorizontalOverflow(maya);

  // --- Ari answers first, with a private note.
  const ari = await actx.newPage();
  await ari.goto('/app');
  await go(ari, 'people');
  const mcard = ari.locator('.conn', { hasText: 'Maya' });
  await mcard.click();
  const rv = mcard.locator('[id^="reveal-"]');
  await expect(rv).toContainText("How's it going between us?");
  await rv.locator('[data-t2-note]').fill('PRIVATEWORD');
  await rv.locator('[data-t2-feel="strained"]').click();
  await expect(rv).toContainText('waiting on Maya');

  // --- Maya's Home: the nudge, and nothing about what Ari chose.
  await go(maya, 'chat');
  await maya.reload();
  await go(maya, 'today');
  const nudge = maya.locator('#pplToday .t2nudge');
  await expect(nudge).toContainText('Ari answered');
  const raw = await maya.evaluate(() => fetch('/api/reveals/waiting').then(r => r.text()));
  expect(raw).not.toContain('strained');
  await nudge.locator('button').click();
  const mrv = maya.locator('.conn', { hasText: 'Ari' }).locator('[id^="reveal-"]');
  await expect(mrv).toContainText('Ari answered');
  await mrv.locator('[data-t2-feel="closer"]').click();
  await expect(mrv).toContainText('You: Closer');
  await expect(mrv).toContainText('Ari: Strained');
  // The sky is the past week, each reason a dated stretch ("Tue–Thu: ...") or the quiet line.
  await expect(mrv).toContainText(/this past week|this week — this one was all yours/);
  for (const li of await mrv.locator('.t2sky li').all()) await expect(li).toHaveText(/^\w+(–\w+)?: /);
  await expect(mrv).not.toContainText('PRIVATEWORD');
  await expectNoHorizontalOverflow(maya);
});

test('a used score link explains itself to whoever opens it', async ({ browser }) => {
  const opts = { viewport: { width: 375, height: 812 } };
  const mctx = await browser.newContext(opts), tctx = await browser.newContext(opts);
  const sctx = await browser.newContext(opts), anon = await browser.newContext(opts);
  await account(mctx, 'maya-used', 'Maya', '1998-03-14', '04:42');
  await account(tctx, 'theo-used', 'Theo', '1996-07-02', '21:15');
  await account(sctx, 'sam-used', 'Sam', '1991-11-23', '09:30');
  const row = await (await mctx.request.post('/api/connections', { data: {
    name: 'Theo', relation: 'talking', date: '1996-07-02', time: '21:15', city: 'Chicago IL' } })).json();
  const url = (await (await mctx.request.post(`/api/connections/${row.id}/send`)).json()).url;
  const link = new URL(url).pathname, token = link.split('/').pop();
  expect((await tctx.request.post(`/api/invites/${token}/accept`, { data: { share_chart: true } })).ok()).toBeTruthy();

  // A stranger with no account: told, and it's still there once a toast would be gone.
  const stranger = await anon.newPage();
  await stranger.goto(link);
  const bar = stranger.locator('#inviteBar');
  await expect(bar).toContainText('already been used');
  await expect(bar).toContainText('Maya');
  await stranger.waitForTimeout(3500);
  await expect(bar).toBeVisible();
  expect(new URL(stranger.url()).search).toBe('');

  // The one who joined, tapping their message again.
  const theo = await tctx.newPage();
  await theo.goto(link);
  await expect(theo.locator('#toast')).toContainText('already linked');
  await expect(theo.locator('#toast')).toHaveClass(/on/);

  // Someone signed in who it wasn't for.
  const sam = await sctx.newPage();
  await sam.goto(link);
  await expect(sam.locator('#toast')).toContainText('already been used');
  await expect(sam.locator('#toast')).toHaveClass(/on/);
  for (const c of [mctx, tctx, sctx, anon]) await c.close();
});

test('a network blip never tells a visitor their invite is gone', async ({ browser }) => {
  const opts = { viewport: { width: 375, height: 812 } };
  const mctx = await browser.newContext(opts), anon = await browser.newContext(opts);
  await account(mctx, 'maya-blip', 'Maya', '1998-03-14', '04:42');
  const { token } = await (await mctx.request.post('/api/invites')).json();

  const page = await anon.newPage();
  let failing = true;
  await page.route(`**/api/invites/${token}`, (r) => (failing ? r.abort() : r.continue()));
  await page.goto(`/app?invite=${token}`);
  await expect(page.locator('#fEmail')).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page.locator('#inviteBar')).toBeHidden();
  await expect(page.locator('#toast')).not.toHaveClass(/on/);

  // Kept, so it's still theirs when the network comes back.
  failing = false;
  await page.reload();
  await expect(page.locator('#inviteBar')).toContainText('Maya');
  await mctx.close(); await anon.close();
});
