const { test, expect } = require('@playwright/test');
const { newEmail, signIn, onboard } = require('./helpers');

/**
 * The public check, as a stranger meets it: two birthdays and no account, a
 * link that shows the result to the next person, and a sign-up that puts the
 * person she checked into People.
 */
async function check(page, { a = '1996-07-22', b = '1995-11-03', me = 'Sam', them = 'Theo' } = {}) {
  await page.fill('#cDateA', a);
  await page.fill('#cDateB', b);
  await page.fill('#cNameA', me);
  await page.fill('#cNameB', them);
  await page.click('#cGo');
}

test('two birthdays give a score, with no account and no date in the address', async ({ page }) => {
  await page.goto('/check');
  await check(page);
  await expect(page.locator('#res')).toBeVisible();
  await expect(page.locator('#rPair')).toHaveText('Sam & Theo');
  await expect(page.locator('#rNum')).toHaveText(/^\d+$/);
  await expect(page.locator('#rLine')).not.toBeEmpty();
  await expect(page.locator('#rRange')).toContainText('once real birth times are in');
  await expect(page.locator('#rDims li')).toHaveCount(3);
  expect(page.url()).not.toContain('1996');
  const noSideways = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  expect(noSideways).toBe(true);
});

test('a date under 18 gets said no to, in words', async ({ page }) => {
  const year = new Date().getFullYear() - 15;
  await page.goto('/check');
  await check(page, { b: `${year}-01-01` });
  await expect(page.locator('#cErr')).toContainText('18 and over');
  await expect(page.locator('#res')).toBeHidden();
});

test('both birthdays are asked for before anything is sent', async ({ page }) => {
  await page.goto('/check');
  await page.fill('#cDateA', '1996-07-22');
  await page.click('#cGo');
  await expect(page.locator('#cErr')).toContainText('their birthday');
});

test('a sent link shows the result, and the card holds no birthday', async ({ page, request }) => {
  await page.goto('/check');
  const got = page.waitForResponse('**/api/vs');
  await check(page);
  const body = await (await got).json();

  const preview = await request.get(`/api/vs/r/${body.token}`, { maxRedirects: 0 });
  expect(await preview.text()).toContain(`Sam &amp; Theo got ${body.score} on My Sign`);

  const card = await (await request.get(`/api/vs/card/${body.token}.svg`)).text();
  expect(card).toContain('Sam &amp; Theo');
  expect(card).not.toContain('1996');

  await page.goto(`/check?r=${body.token}`);
  await expect(page.locator('#got')).toContainText(`Sam & Theo got ${body.score}`);
  await expect(page.locator('#chk')).toBeVisible();
});

test('the card can be saved from the result', async ({ page }) => {
  await page.goto('/check');
  await check(page);
  await expect(page.locator('#res')).toBeVisible();
  const download = page.waitForEvent('download');
  await page.click('#rPost');
  expect((await download).suggestedFilename()).toBe('my-sign-check.png');
});

test('saving carries her birthday to the form and them into People', async ({ page }) => {
  await page.goto('/check');
  await check(page);
  await expect(page.locator('#res')).toBeVisible();
  await page.click('#rSave');
  await page.fill('#sId', newEmail('check'));
  await page.click('#sGo');
  await expect(page.locator('#devCode')).toBeVisible();
  const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await page.fill('#sCode', code);
  await page.waitForURL(u => new URL(u).pathname === '/app');

  await expect(page.locator('#vConsent')).toHaveClass(/on/);
  await page.check('#aiOk');
  await page.click('#consentGo');
  await expect(page.locator('#vOnboard')).toHaveClass(/on/);
  await expect.poll(() => page.inputValue('#fDate')).toBe('1996-07-22');
  await expect(page.locator('#obFromFree')).toContainText('check it’s yours');
  await page.fill('#fName', 'Sam');
  await page.click('#noTime');
  await page.fill('#fCity', 'chicago, il');
  await page.locator('#ac button').first().click();
  const added = page.waitForResponse(r => r.url().endsWith('/api/connections') && r.request().method() === 'POST');
  await page.click('#go');
  const row = await (await added).json();
  expect(row.name).toBe('Theo');
  expect(row.birth_date).toBe('1995-11-03');
  expect(await page.evaluate(() => sessionStorage.getItem('ms_check'))).toBeNull();
});

test('backing out of the sign-up leaves nothing for the next person', async ({ page }) => {
  await page.goto('/check');
  await check(page);
  await expect(page.locator('#res')).toBeVisible();
  await page.click('#rSave');
  await expect(page.locator('#start')).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('ms_check'))).toContain('1995-11-03');
  await page.keyboard.press('Escape');
  await expect(page.locator('#start')).toBeHidden();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('ms_check'))).toBeNull();
});

test('already signed in, saving goes straight to People with them in it', async ({ page }) => {
  await signIn(page, 'check-in');
  await onboard(page);
  await page.goto('/check');
  await check(page);
  await expect(page.locator('#res')).toBeVisible();
  await page.click('#rSave');
  await page.waitForURL(u => new URL(u).pathname === '/app');
  await expect(page.locator('#vPeople')).toHaveClass(/on/);
  await expect(page.locator('#vPeople')).toContainText('Theo');
  expect(await page.evaluate(() => sessionStorage.getItem('ms_check'))).toBeNull();
});

test('a reload of a shared link counts one open', async ({ page }) => {
  await page.goto('/check');
  const got = page.waitForResponse('**/api/vs');
  await check(page);
  const { token } = await (await got).json();
  let opens = 0;
  page.on('request', r => { if (r.url().includes('/api/vs/result/')) opens += 1; });
  await page.goto(`/check?r=${token}`);
  await expect(page.locator('#got')).toContainText('Sam & Theo');
  await page.reload();
  await expect(page.locator('#got')).toContainText('Sam & Theo');
  expect(opens).toBe(1);
});
