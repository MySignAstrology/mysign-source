const { test, expect } = require('@playwright/test');
const { go, signIn, onboard, expectNoHorizontalOverflow } = require('./helpers');

/**
 * Layout regressions this repo has actually shipped, now pinned.
 *
 *   - `.wire` rows had flex-basis:100% without flex-wrap, so the aspect pair,
 *     its orb and its description fought over one line.
 *   - `.hero`'s padding shorthand cancelled `.wrap`'s horizontal padding, and
 *     `.wrap`'s cancelled the vertical padding on header/section/footer. Text
 *     sat flush against the left edge at 375px.
 *
 * Both were found by eye. Neither was visible to pytest.
 */
test.describe('layout holds at every breakpoint', () => {
  test('sign-in', async ({ page }) => {
    await page.goto('/app');
    await expectNoHorizontalOverflow(page);
    const box = await page.locator('#fEmail').boundingBox();
    expect(box.x, 'the email field is jammed against the edge').toBeGreaterThan(8);
  });

  test('the reading', async ({ page }) => {
    await signIn(page, 'layout');
    await onboard(page);
    await expectNoHorizontalOverflow(page);

    // Nothing may sit flush against the viewport edge.
    for (const sel of ['#rdName', '.b3', '#rdStarters button']) {
      const box = await page.locator(sel).first().boundingBox();
      expect(box.x, `${sel} has no left margin`).toBeGreaterThan(8);
    }

    // Each link row: what it means for her first, on its own line; the pair
    // and orb share a small caption line under it.
    const rows = page.locator('.wire');
    const n = await rows.count();
    expect(n).toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      // Read together: sections still loading above move the rows, and a move
      // between two separate reads looks like the pair sitting on the description.
      const { pair, desc } = await rows.nth(i).evaluate((w) => {
        const box = (sel) => {
          const { y, height } = w.querySelector(sel).getBoundingClientRect();
          return { y, height };
        };
        return { pair: box('.p'), desc: box('.d') };
      });
      expect(pair.height, 'the aspect pair wrapped onto two lines')
        .toBeLessThan(40);
      expect(pair.y, 'the pair is sharing a line with the description')
        .toBeGreaterThan(desc.y + desc.height - 3);
    }
  });

  test('the chat', async ({ page }) => {
    await signIn(page, 'layoutchat');
    await onboard(page);
    await go(page, 'chat');
    await page.fill('#input', 'should i answer him');
    await page.click('#send');
    await expect(page.locator('.msg.bot').last()).not.toBeEmpty({ timeout: 20_000 });
    await expectNoHorizontalOverflow(page);

    const bubble = await page.locator('.msg.bot').last().boundingBox();
    const vw = page.viewportSize().width;
    expect(bubble.x + bubble.width, 'a message runs past the viewport')
      .toBeLessThanOrEqual(vw + 1);
  });

  test('settings', async ({ page }) => {
    await signIn(page, 'layoutset');
    await onboard(page);
    await go(page, 'settings');
    await expectNoHorizontalOverflow(page);
    await expect(page.locator('#delAcct')).toBeVisible();
  });
});

test.describe('both themes are legible', () => {
  for (const scheme of ['light', 'dark']) {
    test(`${scheme}: text is not painted on its own background`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await signIn(page, `theme-${scheme}`);
      await onboard(page);
      // The reading renders after a fetch; reading it before it lands is a race.
      await page.locator('.b3 p').first().waitFor();

      const contrast = await page.evaluate(() => {
        const lum = (c) => {
          const [r, g, b] = c.match(/\d+/g).map(Number).map(v => {
            v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          });
          return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };
        const el = document.querySelector('.b3 p');
        const fg = getComputedStyle(el).color;
        const bg = getComputedStyle(document.body).backgroundColor;
        const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
        return (a + 0.05) / (b + 0.05);
      });
      // WCAG AA for body text is 4.5:1.
      expect(contrast, 'body text fails AA contrast').toBeGreaterThan(4.5);
    });
  }
});
