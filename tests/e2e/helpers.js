const { expect } = require('@playwright/test');

/** A fresh address per test — the store is shared, accounts are not. */
function newEmail(tag) {
  return `${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.test`;
}

/** Sign in and accept the terms. Returns the address used. */
async function signIn(page, tag, { installOffer = false } = {}) {
  const email = newEmail(tag);
  // The one-time "add to Home Screen" sheet appears after the first check-in
  // reply on phones; specs that aren't about it start with it already seen.
  if (!installOffer) await page.addInitScript(() => { try { localStorage.setItem('ms_install_seen', '1'); } catch (e) {} });
  await page.goto('/app');
  await page.fill('#fEmail', email);
  await page.click('#authGo');
  // Dev mode echoes the code back; the banner is the only place it appears.
  await expect(page.locator('#devCode')).toBeVisible();
  const code = (await page.locator('#devCode').innerText()).match(/(\d{6})/)[1];
  await page.fill('#fCode', code);
  await page.click('#authGo');
  await expect(page.locator('#vConsent')).toHaveClass(/on/);
  await page.check('#aiOk');
  await page.click('#consentGo');
  await expect(page.locator('#vOnboard')).toHaveClass(/on/);
  return email;
}

/** Fill the birth form. Pass time: '' to exercise the unknown-time path. */
async function onboard(page, { name = 'Maya', date = '1999-03-14',
                               time = '04:42', city = 'chicago, il' } = {}) {
  await page.fill('#fName', name);
  await page.fill('#fDate', date);
  if (time) await page.fill('#fTime', time);
  else await page.click('#noTime');

  await page.fill('#fCity', city);
  const option = page.locator('#ac button').first();
  await expect(option).toBeVisible();
  await option.click();

  await page.click('#go');
  await expect(page.locator('#vReading')).toHaveClass(/on/, { timeout: 20_000 });
}

/** The "about you" deck used to sit between the birth form and the reading.
 *  It is asked on Home now (abNudge), so sign-up goes straight to the reading;
 *  kept as a no-op so older specs read the same. */
async function skipAbout(page) {}

/** Three tabs: Me, People, Oracle. The chart, today, her patterns and settings
 *  are rows on Me; 'chat' is the Oracle tab, where the check-in and thread are. */
async function go(page, where) {
  if (where === 'chat') return page.click('#tabChat');
  if (where === 'people') return page.click('#tabPpl');
  if (where === 'me') return page.click('#tabMe');
  await page.click('#tabMe');
  await page.click(`#mePath [data-go="${where}"]`);
}

/** A plain check-in from Today. Home's promo cards wait for one. */
async function checkIn(page, mood = 6) {
  await go(page, 'chat');
  await page.click(`#moods button[data-m="${mood}"]`);
  await page.click('#ciGo');
  await expect(page.locator('#checkin')).toContainText(`${mood}/10`);
}

/** Open the "about you" deck the way a new account now meets it: from Home,
 *  after the day's check-in. */
async function openAboutFromHome(page) {
  await checkIn(page);
  await go(page, 'me');                    // the card sits on Me now
  await page.click('#abNudgeGo');
  await expect(page.locator('#vAbout')).toHaveClass(/on/, { timeout: 20_000 });
}

/** Nothing may overflow sideways at any breakpoint. */
async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => {
    const de = document.documentElement;
    return { scrollW: de.scrollWidth, clientW: de.clientWidth };
  });
  expect(overflow.scrollW,
    `page scrolls sideways: ${overflow.scrollW} > ${overflow.clientW}`)
    .toBeLessThanOrEqual(overflow.clientW + 1);
}

/**
 * Signing out shows the sign-in screen at once, then reloads. Seeing that screen
 * doesn't mean the reload is over, and navigating during it fails on a slow
 * machine. The reloaded page is what clears this flag.
 */
/** A named weekday far enough ahead that no model can answer it from the week
 *  it was handed: the demo looks up any YYYY-MM-DD it is given, and a real model
 *  has to call get_sky for it. Returns { iso, weekday, day }. */
function dayAhead(weekday, minDays = 49) {
  const names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const want = names.indexOf(weekday);
  const d = new Date();
  d.setUTCHours(12, 0, 0, 0);
  d.setUTCDate(d.getUTCDate() + minDays);
  while (d.getUTCDay() !== want) d.setUTCDate(d.getUTCDate() + 1);
  return { iso: d.toISOString().slice(0, 10), weekday, day: d.getUTCDate() };
}

/** A reply that has finished, not merely started: "Report this reply" is added
 *  on the done event, by either model. Waiting on the typing dots only proves
 *  the first words arrived, and a real model is still streaming then. */
async function replies(page, n, timeout = 60_000) {
  await expect(page.locator('#thread .msg.bot .report')).toHaveCount(n, { timeout });
}

const signedOutReloaded = page =>
  page.waitForFunction(() => !sessionStorage.getItem('ms_signed_out'));

module.exports = { go, dayAhead, replies, newEmail, signIn, onboard, skipAbout, checkIn, openAboutFromHome, expectNoHorizontalOverflow, signedOutReloaded };
