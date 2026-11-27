// @ts-check
// Notifications inside the iPhone app. The shell is imitated the way
// iap.spec.js does it (a Capacitor global), plus the MySignPush that
// native/bridge.js provides there, faked: iOS itself is tested in
// native/ios/App/AppTests, the bridge and the server in tests/test_apns.py.
const { test, expect } = require('@playwright/test');
const { go, signIn, onboard } = require('./helpers');

async function toSettings(page) {
  await go(page, 'settings');
  await expect(page.locator('#vSettings')).toHaveClass(/on/);
}

/** The shell, with the phone in a given state. `allow` is her answer to iOS's sheet. */
async function shell(page, { available = true, permission = 'prompt', on = false, allow = true, token = true } = {}) {
  await page.addInitScript(({ available, permission, on, allow, token }) => {
    window.Capacitor = { isNativePlatform: () => true };
    const w = /** @type {any} */ (window);
    w.__push = { calls: [], permission, on, open: null };
    w.MySignPush = {
      available: () => available,
      state: async () => ({ permission: w.__push.permission, on: w.__push.permission === 'granted' && w.__push.on, today: null }),
      enable: async () => {
        w.__push.calls.push('enable');
        w.__push.permission = allow ? 'granted' : 'denied';
        if (!allow) return { ok: false, permission: 'denied' };
        if (!token) return { ok: false, permission: 'granted', message: '' };
        w.__push.on = true;
        return { ok: true, permission: 'granted' };
      },
      disable: async () => { w.__push.calls.push('disable'); w.__push.on = false; return { ok: true }; },
      openSettings: async () => { w.__push.calls.push('openSettings'); },
      onOpen: cb => { w.__push.open = cb; },
    };
  }, { available, permission, on, allow, token });
}

const calls = page => page.evaluate(() => /** @type {any} */ (window).__push.calls);

test.describe('Notifications in the iPhone app', () => {
  test('no switch at all until the server can send', async ({ page }) => {
    await shell(page, { available: false });
    await signIn(page, 'push-unavailable');
    await onboard(page);
    await toSettings(page);
    await expect(page.locator('#pushRow')).toBeHidden();
    await expect(page.locator('#pushPeopleRow')).toBeHidden();
    await expect(page.locator('#nTime')).toBeHidden();
    await expect(page.locator('#vSettings')).not.toContainText(/coming soon|app update|arrive in/i);
  });

  test('nothing asks for permission until she turns them on', async ({ page }) => {
    await shell(page);
    await signIn(page, 'push-quiet');
    await onboard(page);
    await toSettings(page);
    await expect(page.locator('#pushBtn')).toHaveText('Turn on');
    expect(await calls(page)).toEqual([]);
  });

  test('Turn on asks iOS, sends a test, and the switch reads Turn off', async ({ page }) => {
    await shell(page);
    await signIn(page, 'push-on');
    await onboard(page);
    await toSettings(page);
    const row = page.locator('#pushRow');
    await expect(row).toBeVisible();
    await expect(page.locator('#pushBtn')).toHaveText('Turn on');
    await expect(page.locator('#pushSub')).toHaveText('7:30am, your time.');
    await expect(row).not.toContainText(/Safari|Home Screen|Share/);
    const tested = page.waitForRequest(r => r.url().endsWith('/api/push/test') && r.method() === 'POST');
    await page.click('#pushBtn');
    await tested;
    await expect(page.locator('#pushBtn')).toHaveText('Turn off');
    expect(await calls(page)).toEqual(['enable']);
    // The same settings the web has: when, and what it says.
    await expect(page.locator('#nTime')).toBeVisible();
    await expect(page.locator('#nStyle')).toBeVisible();
    await expect(page.locator('#pushPeopleRow')).toBeVisible();
    await page.selectOption('#nTime', '09:00');
    await expect(page.locator('#pushSub')).toHaveText('9:00am, your time.');
  });

  test('Turn off takes this phone off', async ({ page }) => {
    await shell(page, { permission: 'granted', on: true });
    await signIn(page, 'push-off');
    await onboard(page);
    await toSettings(page);
    await expect(page.locator('#pushBtn')).toHaveText('Turn off');
    await page.click('#pushBtn');
    await expect(page.locator('#pushBtn')).toHaveText('Turn on');
    expect(await calls(page)).toEqual(['disable']);
  });

  test('saying no to iOS shows Blocked, with a way to the iPhone’s Settings', async ({ page }) => {
    await shell(page, { allow: false });
    await signIn(page, 'push-denied');
    await onboard(page);
    await toSettings(page);
    await page.click('#pushBtn');
    await expect(page.locator('#pushBtn')).toHaveText('Blocked');
    await expect(page.locator('#pushBtn')).toBeEnabled();
    await expect(page.locator('#pushSub')).toContainText('turned off in this iPhone’s Settings');
    await expect(page.locator('#nTime')).toBeHidden();
    await page.click('#pushBtn');
    await expect(page.locator('#sheetTitle')).toHaveText('Notifications are off for My Sign');
    await expect(page.locator('#sheetOk')).toHaveText('Open Settings');
    await expect(page.locator('#sheetBody')).not.toContainText(/Safari|browser|Home Screen/);
    await page.click('#sheetOk');
    await expect.poll(() => calls(page)).toEqual(['enable', 'openSettings']);
    // She allowed them there and came back: the switch catches up by itself.
    await page.evaluate(() => {
      /** @type {any} */ (window).__push.permission = 'granted';
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.locator('#pushBtn')).toHaveText('Turn on');
  });

  test('a phone that gives no token says so and stays off', async ({ page }) => {
    await shell(page, { token: false });
    await signIn(page, 'push-notoken');
    await onboard(page);
    await toSettings(page);
    await page.click('#pushBtn');
    await expect(page.locator('#toast')).toContainText('couldn’t turn notifications on');
    await expect(page.locator('#pushBtn')).toHaveText('Turn on');
  });

  test('tapping a notification lands where it points', async ({ page }) => {
    await shell(page, { permission: 'granted', on: true });
    await signIn(page, 'push-tap');
    await onboard(page);
    await toSettings(page);
    await expect.poll(() => page.evaluate(() => typeof /** @type {any} */ (window).__push.open)).toBe('function');
    // The morning note: Today.
    await page.evaluate(() => /** @type {any} */ (window).__push.open('/app?from=push&pk=morning'));
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    // A person who isn't hers (or is gone): still Today, never an error.
    await toSettings(page);
    await page.evaluate(() => /** @type {any} */ (window).__push.open('/app?p=nobody&from=push&pk=person'));
    await expect(page.locator('#vMain')).toHaveClass(/on/);
    // The sky heads-up: the events list.
    await toSettings(page);
    await page.evaluate(() => /** @type {any} */ (window).__push.open('/app?sky=events&from=push&pk=sky'));
    await expect(page.locator('#vReading')).toHaveClass(/on/);
    await expect(page.locator('#skyEventsHead')).toBeVisible();
  });

  test('in a browser nothing changes: the web’s own switch', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'an iPhone or iPad browser: a desktop one is never offered the Home Screen');
    await signIn(page, 'push-web');
    await onboard(page);
    await toSettings(page);
    await expect(page.locator('#pushRow')).toBeVisible();
    // An iPhone browser tab, not installed: the Home Screen instruction, as before.
    await expect(page.locator('#pushBtn')).toHaveText('Add to Home Screen');
  });
});

// The one-time invitation after the first check-in reply. In a browser it
// offers "add to Home Screen"; in the app it offers the notification itself,
// through the same sheet and the same "seen" record (ms_install_seen).
test.describe('The invitation to turn notifications on, in the iPhone app', () => {
  /** Her first check-in, answered: the moment the invitation waits for. */
  async function firstCheckIn(page) {
    await go(page, 'chat');
    await expect(page.locator('#sheet')).toBeHidden();               // never on first load
    await page.click('#moods button[data-m="6"]');
    await page.click('#ciGo');
    await expect(page.locator('.msg.bot').last()).toContainText('?', { timeout: 20_000 });
  }
  const seen = page => page.evaluate(() => localStorage.getItem('ms_install_seen'));
  /** Ask for the invitation again, as a later check-in would. */
  const again = page => page.evaluate(() => /** @type {any} */ (window).installNudge());

  test('shown once after the first check-in; Turn on is what asks iOS', async ({ page }) => {
    await shell(page);
    await signIn(page, 'push-invite', { installOffer: true });
    await onboard(page);
    await firstCheckIn(page);
    await expect(page.locator('#sheetTitle')).toHaveText('Get your morning note as a notification.');
    await expect(page.locator('#sheetBody')).toHaveText(
      'One note each morning at 7:30am, your time. You can change the time or turn it off in Settings.');
    await expect(page.locator('#sheetBody')).not.toContainText(/Safari|Home Screen|Install|Share/);
    await expect(page.locator('#sheetOk')).toHaveText('Turn on');
    await expect(page.locator('#sheetCancel')).toHaveText('Not now');
    expect(await calls(page)).toEqual([]);                           // iOS has not been asked yet
    const tested = page.waitForRequest(r => r.url().endsWith('/api/push/test') && r.method() === 'POST');
    await page.click('#sheetOk');
    await tested;
    expect(await calls(page)).toEqual(['enable']);
    await expect(page.locator('#sheet')).toBeHidden();
    // Settings agrees, and the invitation is spent.
    await toSettings(page);
    await expect(page.locator('#pushBtn')).toHaveText('Turn off');
    await again(page);
    await expect(page.locator('#sheet')).toBeHidden();
    expect(await calls(page)).toEqual(['enable']);
  });

  test('Not now: iOS is never asked, it is not shown again, and Settings is still the way in', async ({ page }) => {
    await shell(page);
    await signIn(page, 'push-invite-no', { installOffer: true });
    await onboard(page);
    await firstCheckIn(page);
    await expect(page.locator('#sheetTitle')).toContainText('morning note');
    await page.click('#sheetCancel');
    await expect(page.locator('#sheet')).toBeHidden();
    expect(await calls(page)).toEqual([]);
    expect(await seen(page)).toBe('1');
    await again(page);
    await expect(page.locator('#sheet')).toBeHidden();
    await page.reload();
    await expect(page.locator('#greet')).not.toBeEmpty({ timeout: 15_000 });
    await again(page);
    await expect(page.locator('#sheet')).toBeHidden();
    await toSettings(page);
    await expect(page.locator('#pushBtn')).toHaveText('Turn on');
    expect(await calls(page)).toEqual([]);
  });

  for (const [name, state] of /** @type {[string, object][]} */ ([
    ['notifications are already on', { permission: 'granted', on: true }],
    ['the iPhone has already been told no', { permission: 'denied' }],
    ['she allowed them once and turned them off herself', { permission: 'granted', on: false }],
  ])) {
    test(`not shown when ${name}`, async ({ page }) => {
      await shell(page, state);
      await signIn(page, 'push-invite-skip', { installOffer: true });
      await onboard(page);
      await firstCheckIn(page);
      await again(page);
      await expect(page.locator('#sheet')).toBeHidden();
      expect(await calls(page)).toEqual([]);
    });
  }

  test('not shown while the server can’t send, and kept for when it can', async ({ page }) => {
    await shell(page, { available: false });
    await signIn(page, 'push-invite-off', { installOffer: true });
    await onboard(page);
    await firstCheckIn(page);
    await again(page);
    await expect(page.locator('#sheet')).toBeHidden();
    expect(await calls(page)).toEqual([]);
    expect(await seen(page)).toBeNull();                             // not spent on nothing
  });

  test('in a browser it is still the Home Screen offer', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'an iPhone or iPad browser: a desktop one is never offered the Home Screen');
    await signIn(page, 'push-invite-web', { installOffer: true });
    await onboard(page);
    await firstCheckIn(page);
    await expect(page.locator('#sheetTitle')).toHaveText('Get your morning note as a notification.');
    await expect(page.locator('#sheetBody')).toHaveText('Add My Sign to your Home Screen and your note arrives as a notification.');
    await expect(page.locator('#sheetOk')).toHaveText('Show me how');
    await page.click('#sheetOk');
    await expect(page.locator('#sheetTitle')).toHaveText('Add to Home Screen');
  });
});

