const { test, expect } = require('@playwright/test');
const { go, newEmail, signIn, onboard } = require('./helpers');

/**
 * The social loop, end to end, in two separate browsers: she invites, a
 * stranger opens the link, makes an account and a chart, says yes, and each
 * appears in the other's People. Then the stranger turns "share how I'm
 * feeling" off and her card says so.
 */
test('invite a stranger, link up, and respect their privacy', async ({ browser }) => {
  // --- Maya: an account and an invite link
  const maya = await browser.newPage();
  await signIn(maya, 'maya-inv');
  await onboard(maya);
  await go(maya, 'people');
  await maya.click('#inviteBtn');
  await expect(maya.locator('#sheet')).toBeVisible();
  const link = await maya.locator('#sheetInput').inputValue();
  expect(link).toMatch(/\/app\?invite=[A-Za-z0-9_-]{10,}$/);
  await maya.click('#sheetCancel');

  // --- A stranger opens it in their own browser
  const ctx = await browser.newContext();
  const ari = await ctx.newPage();
  await ari.goto(link.replace(/^https?:\/\/[^/]+/, ''));
  await expect(ari.locator('#inviteBar')).toContainText('Maya invited you to link charts');
  await expect(ari).toHaveURL(/\/app$/);                     // the token left the address bar
  await ari.fill('#fEmail', newEmail('ari-inv'));
  await ari.click('#authGo');
  await expect(ari.locator('#devCode')).toContainText(/\d{6}/);
  const code = (await ari.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await ari.fill('#fCode', code);
  await ari.click('#authGo');
  await ari.check('#aiOk');
  await ari.click('#consentGo');
  await onboard(ari, { name: 'Ari', date: '1999-06-21', time: '08:05' });

  // The yes/no arrives once her chart exists. Sharing the mood is opt-in.
  await expect(ari.locator('#sheet')).toBeVisible();
  await expect(ari.locator('#sheetTitle')).toHaveText('Link charts with Maya?');
  await expect(ari.locator('#sheetTogBox')).not.toBeChecked();
  await ari.check('#sheetTogBox');
  await ari.click('#sheetOk');
  await expect(ari.locator('#toast')).toContainText('linked');
  const mayaCard = ari.locator('.conn', { hasText: 'Maya' });
  await expect(mayaCard).toContainText('linked');

  // --- Ari checks in; Maya sees the number and never the words
  await go(ari, 'chat');
  await ari.click('#moods button[data-m="9"]');
  await ari.fill('#ciText', 'nervous about the interview');
  await ari.click('#ciGo');
  await expect(ari.locator('#checkin')).toContainText('9/10');

  await go(maya, 'chat');
  await go(maya, 'people');
  const ariCard = maya.locator('.conn', { hasText: 'Ari' });
  await expect(ariCard).toContainText('linked');
  await expect(ariCard).toContainText('feeling 9/10');
  const raw = await maya.evaluate(() => fetch('/api/connections').then(r => r.text()));
  expect(raw).not.toContain('interview');
  // She can see who joined through her link, and it is spent.
  await expect(maya.locator('#invList')).toContainText('Joined: Ari');
  await expect(maya.locator('#invList [data-inv-cancel]')).toHaveCount(0);

  // A forwarded copy of the same link: a third browser is told up front.
  const ctx3 = await browser.newContext();
  const kim = await ctx3.newPage();
  await kim.goto(link.replace(/^https?:\/\/[^/]+/, ''));
  await expect(kim.locator('#inviteBar')).toContainText('already been used');
  await ctx3.close();

  // Ari opening it again is told they're already linked, not asked to join.
  await ari.goto(link.replace(/^https?:\/\/[^/]+/, ''));
  await expect(ari.locator('#toast')).toContainText('already linked');
  await expect(ari.locator('#sheet')).toBeHidden();

  // --- Ari turns sharing off; Maya's card says so, and the number is gone
  await go(ari, 'settings');
  await expect(ari.locator('#shareMoodBtn')).toHaveText('On');
  await ari.click('#shareMoodBtn');
  await expect(ari.locator('#shareMoodBtn')).toHaveText('Off');

  await go(maya, 'chat');
  await go(maya, 'people');
  await expect(maya.locator('.conn', { hasText: 'Ari' })).toContainText('keeping today private');
  const after = await maya.evaluate(() => fetch('/api/people/today').then(r => r.text()));
  expect(after).not.toContain('"mood":9');

  await ctx.close();
});

/**
 * The growth loop: she saves someone, sends them the score, they open the link
 * (the preview page first, as a chat would), make a chart with the same birth
 * date and say yes. One person in her People, not two.
 */
test('send a score, they join, and it becomes one linked person', async ({ browser }) => {
  const maya = await browser.newPage();
  await signIn(maya, 'maya-send');
  await onboard(maya);
  const row = await maya.evaluate(() => fetch('/api/connections', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: 'Theo Lane', relation: 'talking', date: '1999-06-21', time: '08:05', city: 'Chicago IL'}),
  }).then(r => r.json()));
  await go(maya, 'people');
  const card = maya.locator('.conn', { hasText: 'Theo' });
  await card.click();
  const send = maya.locator(`[data-send-compat="${row.id}"]`);
  await expect(send).toHaveText('Send to Theo');
  await expect(maya.locator(`[data-share-compat="${row.id}"]`)).toBeVisible();
  await send.click();
  await expect(maya.locator('#sheetTitle')).toHaveText('Send this to Theo?');
  await expect(maya.locator('#sheetBody')).toContainText('whoever opens it first');
  const link = await maya.locator('#sheetInput').inputValue();
  expect(link).toMatch(/\/api\/i\/[A-Za-z0-9_-]{10,}$/);
  await maya.click('#sheetCancel');
  await expect(maya.locator('#invList')).toContainText('Sent to Theo');

  const ctx = await browser.newContext();
  const theo = await ctx.newPage();
  await theo.goto(link.replace(/^https?:\/\/[^/]+/, ''));
  await expect(theo).toHaveURL(/\/app/);
  await expect(theo.locator('#inviteBar')).toContainText(`Maya got ${row.overall} with you`);
  await theo.fill('#fEmail', newEmail('theo-send'));
  await theo.click('#authGo');
  await expect(theo.locator('#devCode')).toContainText(/\d{6}/);
  await theo.fill('#fCode', (await theo.locator('#devCode').innerText()).match(/(\d{6})/)[1]);
  await theo.click('#authGo');
  await theo.check('#aiOk');
  await theo.click('#consentGo');
  await onboard(theo, { name: 'Theo', date: '1999-06-21', time: '08:05' });
  await expect(theo.locator('#sheetTitle')).toHaveText('Link charts with Maya?');
  await theo.click('#sheetOk');
  await expect(theo.locator('#toast')).toContainText('linked');

  await go(maya, 'chat');
  await go(maya, 'people');
  await expect(maya.locator('.conn', { hasText: 'Theo' })).toHaveCount(1);
  await expect(maya.locator('.conn', { hasText: 'Theo' })).toContainText('linked');
  await maya.locator('.conn', { hasText: 'Theo' }).click();
  await expect(maya.locator(`[data-share-compat="${row.id}"]`)).toBeVisible();
  await expect(maya.locator(`[data-send-compat="${row.id}"]`)).toHaveCount(0);
});
