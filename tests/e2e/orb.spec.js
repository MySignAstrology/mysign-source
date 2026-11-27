// @ts-check
const { test, expect } = require('@playwright/test');
const { go, newEmail, expectNoHorizontalOverflow } = require('./helpers');

/**
 * The psychic's room, end to end, with the browser's microphone and voice
 * stood in for: she taps the orb button, asks out loud, the question goes
 * through the same /api/message as the chat, the reply is shown and spoken in
 * its speakable form, and the turn is in the chat thread after.
 *
 * Two ways in: the big orb on Me (the first screen she sees) and the Speak
 * button beside the Oracle tab's composer. Both open the same room.
 */

// Speech recognition hears one sentence; speech synthesis records what it says.
const FAKE_SPEECH = () => {
  window.__said = [];
  window.__heard = 'why do i feel so restless this week';
  class FakeRecognition {
    start(){
      setTimeout(() => {
        this.onstart && this.onstart();
        const r = [{0: {transcript: window.__heard}, isFinal: true, length: 1}];
        this.onresult && this.onresult({results: r});
        this.onend && this.onend();
      }, 50);
    }
    stop(){ this.onend && this.onend(); }
    abort(){}
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
  const voices = [{name: 'Samantha', lang: 'en-US', voiceURI: 'samantha'},
                  {name: 'Daniel', lang: 'en-GB', voiceURI: 'daniel'}];
  const synth = {
    speaking: false,
    getVoices: () => voices,
    cancel(){},
    speak(u){
      if (u.text) window.__said.push(u.text);
      setTimeout(() => { u.onstart && u.onstart(); setTimeout(() => u.onend && u.onend(), 20); }, 10);
    },
  };
  Object.defineProperty(window, 'speechSynthesis', {value: synth, configurable: true});
  window.SpeechSynthesisUtterance = function(text){ this.text = text; };
};

async function signedIn(page, name){
  const email = newEmail(name);
  const code = (await (await page.request.post('/api/auth/request', { data: { email } })).json()).dev_code;
  await page.request.post('/api/auth/verify', { data: { email, code } });
  await page.request.post('/api/consent', { data: { ai: true } });
  const city = (await (await page.request.get('/api/cities?q=Chicago')).json())[0].id;
  expect((await page.request.post('/api/onboard',
    { data: { name: 'Ava', date: '1994-03-12', time: '08:30', city } })).ok()).toBeTruthy();
}

test('ask the orb out loud: the chat answers, she hears it, the thread keeps it', async ({ page }) => {
  await page.addInitScript(FAKE_SPEECH);
  await signedIn(page, 'orb');
  await page.goto('/app');

  await page.click('#meOrb');                                      // the orb on Me
  const room = page.locator('#room');
  await expect(room).toBeVisible();
  await expect(page.locator('#rmName')).toHaveText('Madame Lune');
  await expect(page.locator('#rmReply')).toContainText("I'm Madame Lune");
  await expect(page.locator('#rmAi')).toContainText('AI');
  await expect(page.locator('#rmOrb .orb-ui')).toBeVisible();      // orb-ui loaded and mounted
  await expectNoHorizontalOverflow(page);

  await page.click('#rmMic');
  await expect(page.locator('#rmYou')).toContainText('why do i feel so restless this week');
  // The greeting is cleared the moment she asks; wait for the answer itself.
  await expect(page.locator('#rmReply')).toHaveText(/^(?!Come in\.).{20,}/s, { timeout: 20000 });

  // What she heard is the reply, made speakable.
  await expect.poll(() => page.evaluate(() => window.__said.length)).toBeGreaterThan(1);
  const said = (await page.evaluate(() => window.__said.slice(1).join(' ')));
  expect(said).not.toMatch(/TrueNode|°|\*\*/);

  await page.keyboard.press('Escape');
  await expect(room).toBeHidden();
  await go(page, 'chat');                                          // the thread is on Oracle
  await expect(page.locator('#thread .msg.user').last()).toHaveText('why do i feel so restless this week');
  await expect(page.locator('#thread .msg.bot').last()).not.toBeEmpty();
});

test('iOS speech results: what she said shows once, even when they repeat and arrive after the end', async ({ page }) => {
  // What the iPhone app's WebKit did: every result is the whole utterance so
  // far (not the next piece), and results keep coming after onend. Joining
  // them showed "Test Test Test…" under the ball.
  await page.addInitScript(FAKE_SPEECH);
  await page.addInitScript(() => {
    class IOSRecognition {
      start(){
        const said = window.__heard, results = [];
        const fire = () => { results.push({0: {transcript: said}, isFinal: false, length: 1});
                             this.onresult && this.onresult({results: [...results]}); };
        setTimeout(() => {
          this.onstart && this.onstart();
          fire(); fire(); fire();
          this.onend && this.onend();
          for (let i = 1; i <= 20; i++) setTimeout(fire, i * 15);   // late, after the end
        }, 50);
      }
      stop(){ this.onend && this.onend(); }
      abort(){}
    }
    window.SpeechRecognition = IOSRecognition;
    window.webkitSpeechRecognition = IOSRecognition;
  });
  await signedIn(page, 'orbios');
  const sent = [];
  page.on('request', r => { if (r.url().endsWith('/api/message') && r.method() === 'POST') sent.push(r.postDataJSON().text); });
  await page.goto('/app');
  await page.click('#meOrb');
  await page.click('#rmMic');
  await expect(page.locator('#rmReply')).toHaveText(/^(?!Come in\.).{20,}/s, { timeout: 20000 });
  await page.waitForTimeout(500);                                  // past every late result
  await expect(page.locator('#rmYou')).toHaveText('“why do i feel so restless this week”');
  // The room listens again after each answer, so the fake may be heard more
  // than once; every question sent is still the sentence once, never repeated.
  expect(sent.length).toBeGreaterThan(0);
  for (const t of sent) expect(t).toBe('why do i feel so restless this week');
});

test('choosing a reader changes her look and voice, and is remembered', async ({ page }) => {
  await page.addInitScript(FAKE_SPEECH);
  await signedIn(page, 'orbreader');
  await page.goto('/app');
  await page.click('#meOrb');
  await page.click('#rmSetOpen');
  await expect(page.locator('#psReaders button')).toHaveCount(5);
  await page.click('#psReaders [data-k=silas]');
  await expect(page.locator('#rmName')).toHaveText('Old Silas');
  await expect(page.locator('#psName')).toHaveValue('Old Silas');
  await page.click('#psDone');
  await page.keyboard.press('Escape');

  await page.reload();
  await go(page, 'chat');
  await page.click('#orbOpen');                                    // the composer's Speak button
  await expect(page.locator('#rmName')).toHaveText('Old Silas');
});

test('without speech recognition she can type to the orb', async ({ page }) => {
  await page.addInitScript(FAKE_SPEECH);
  await page.addInitScript(() => { delete window.SpeechRecognition; delete window.webkitSpeechRecognition; });
  await signedIn(page, 'orbtype');
  await page.goto('/app');
  await go(page, 'chat');
  await page.click('#orbOpen');
  await expect(page.locator('#rmType')).toBeVisible();
  await page.fill('#rmText', 'what should i focus on today');
  await page.click('#rmType button[type=submit]');
  await expect(page.locator('#rmYou')).toContainText('what should i focus on today');
  await expect(page.locator('#rmReply')).toHaveText(/^(?!Come in\.).{20,}/s, { timeout: 20000 });
});

test('her name, outfit, look, room and ball can be changed from Me and Settings, and the app uses her name', async ({ page }) => {
  await page.addInitScript(FAKE_SPEECH);
  await signedIn(page, 'orbname');
  await page.goto('/app');
  await expect(page.locator('#meOrb b')).toHaveText('Ask Madame Lune');

  // From Me: straight to the sheet, with no greeting spoken over it.
  await page.click('#meOrbSet');
  await expect(page.locator('#rmSet')).toBeVisible();
  expect(await page.evaluate(() => window.__said.filter(t => /Come in/.test(t)).length)).toBe(0);
  await page.fill('#psName', 'Selene');
  await expect(page.locator('#rmName')).toHaveText('Selene');
  // She stays in view above the sheet while she is changed.
  await expect(page.locator('.rm-reader')).toBeInViewport();
  const tab = t => page.click(`#psTabs [data-t=${t}]`);
  await tab('outfit');
  await page.click('#psOutfit [data-v=turban]');
  await page.click('#psRobe button >> nth=2');
  await page.click('#psTrim button >> nth=1');
  await expect(page.locator('#room')).toHaveAttribute('data-outfit', 'turban');
  await expect(page.locator('.rm-reader .of-turban')).toBeVisible();
  await expect(page.locator('.rm-reader .of-hood')).toBeHidden();
  await tab('look');
  await page.click('#psFace [data-v=mask]');
  await page.click('#psEyes [data-v=sly]');
  await page.click('#psMark [data-v=moon]');
  await page.click('#psHand button >> nth=2');
  await expect(page.locator('.rm-reader path.mask')).toBeVisible();
  expect((await page.locator('.rm-reader .mk-moon').boundingBox()).width).toBeGreaterThan(2);   // a crescent, not a hairline
  await expect(page.locator('#rmEyeL')).toHaveAttribute('transform', 'rotate(14 181 152)');
  await tab('scene');
  await page.click('#psScene [data-v=moon]');
  await expect(page.locator('.rm-moon')).toBeVisible();
  await tab('ball');
  await page.click('#psCrystal [data-v=rose]');
  await page.click('#psSize [data-v=large]');
  await page.click('#psStand [data-v=gold]');
  await page.click('#psGlow [data-v=bright]');
  await expect(page.locator('#room')).toHaveAttribute('data-stand', 'gold');
  expect(await page.evaluate(() => getComputedStyle(document.querySelector('#room')).getPropertyValue('--orbw').trim())).toBe('50%');
  await expectNoHorizontalOverflow(page);
  await page.click('#psDone');                                     // back where she was, not left in the room
  await expect(page.locator('#room')).toBeHidden();
  await expect(page.locator('#meOrb b')).toHaveText('Ask Selene');

  // It is remembered, the chat names her, and Settings has the same way in.
  await page.reload();
  await expect(page.locator('#meOrb b')).toHaveText('Ask Selene');
  await go(page, 'chat');
  await expect(page.locator('#orbOpen')).toHaveAttribute('aria-label', 'Speak to Selene');
  await go(page, 'settings');
  await expect(page.locator('#psSum')).toContainText('Selene');
  await expect(page.locator('#psSum')).toContainText('Rose ball');
  await page.click('#psCustom');
  await expect(page.locator('#psName')).toHaveValue('Selene');
  await expect(page.locator('#room')).toHaveAttribute('data-outfit', 'turban');   // the look came back with her
  await expect(page.locator('#room')).toHaveAttribute('data-scene', 'moon');

  // Her own colours, for the robe and for the ball.
  await page.click('#psTabs [data-t=ball]');
  // A hue slider, not the system colour picker: that one never opens inside the iPhone app.
  const cssVar = name => page.evaluate(n => getComputedStyle(document.querySelector('#room')).getPropertyValue(n).trim(), name);
  await expect(page.locator('#psCrystalOwn')).toHaveAttribute('type', 'range');
  await page.fill('#psCrystalOwn', '170');
  const ball = await cssVar('--glowc');
  expect(ball).toMatch(/^#[0-9a-f]{6}$/);
  expect(await page.evaluate(() => document.querySelector('#psCrystalOwn').style.getPropertyValue('--own'))).toBe(ball);
  await page.fill('#psCrystalOwn', '20');
  expect(await cssVar('--glowc')).not.toBe(ball);                              // another hue, another ball
  await page.click('#psTabs [data-t=outfit]');
  await page.fill('#psRobeOwn', '210');
  expect(await cssVar('--robe')).toMatch(/^#[0-9a-f]{6}$/);
  expect(await cssVar('--robe')).not.toBe('#3b2166');

  // All six tabs are on screen at once: nothing hides off the right edge.
  const tabs = await page.locator('#psTabs button').evaluateAll(bs => bs.map(b => b.getBoundingClientRect().right));
  expect(tabs).toHaveLength(6);
  expect(Math.max(...tabs)).toBeLessThanOrEqual(await page.evaluate(() => innerWidth));

  // A forehead mark takes the place of the veil's middle beads, so the two never overlap.
  await page.click('#psOutfit [data-v=veil]');
  await page.click('#psTabs [data-t=look]');
  await page.click('#psMark [data-v=star]');
  await expect(page.locator('.rm-reader .of-veil .mid').first()).toBeHidden();
  await expect(page.locator('.rm-reader .mk-star')).toBeVisible();
  await page.click('#psDone');
  await expect(page.locator('#room')).toBeHidden();
  await expect(page.locator('#psSum')).toContainText('Custom ball');

  // Start over puts Madame Lune back.
  await page.click('#psCustom');
  await page.click('#psReset');
  await expect(page.locator('#psName')).toHaveValue('Madame Lune');
  await expect(page.locator('#room')).toHaveAttribute('data-outfit', 'hood');
  await page.click('#psDone');
  await expect(page.locator('#room')).toBeHidden();
  await expectNoHorizontalOverflow(page);
});

test('words the glossary underlines stay in her answer, on screen and out loud', async ({ page }) => {
  // A reply that streams like the model's: the words first, "done" a moment
  // later, so the chat bubble has been through the glossary (which turns
  // "Pluto" and "Mars" into buttons) before the room reads its final text.
  await page.addInitScript(FAKE_SPEECH);
  await page.addInitScript(() => {
    const real = window.fetch;
    window.fetch = (url, init) => {
      if (!String(url).includes('/api/message')) return real(url, init);
      const enc = new TextEncoder();
      const sse = (ev, d) => enc.encode(`event: ${ev}\ndata: ${JSON.stringify(d)}\n\n`);
      const body = new ReadableStream({
        async start(c){
          c.enqueue(sse('meta', {band: 'mid', score: 50, tier: 'green'}));
          c.enqueue(sse('delta', {t: 'Pluto, the slow planet of big change, is sitting right on your Mars today. '}));
          c.enqueue(sse('delta', {t: 'Read it twice before you hit send.'}));
          await new Promise(r => setTimeout(r, 600));
          c.enqueue(sse('done', {tier: 'green', id: 'test1'}));
          c.close();
        },
      });
      return Promise.resolve(new Response(body, {headers: {'Content-Type': 'text/event-stream'}}));
    };
  });
  await signedIn(page, 'orbgloss');
  await page.goto('/app');
  await page.click('#meOrb');
  await page.click('#rmMic');
  await expect(page.locator('#rmReply')).toContainText('Read it twice', { timeout: 20000 });
  await page.waitForTimeout(1200);                                // past "done" and the glossary sweep
  await expect(page.locator('#rmReply')).toContainText('Pluto, the slow planet of big change, is sitting right on your Mars today.');
  // Every line she spoke that carries the sentence carries its planets too.
  const said = await page.evaluate(() => window.__said);
  const lines = said.filter(s => s.includes('slow planet'));
  expect(lines.length).toBeGreaterThan(0);
  for (const s of lines) { expect(s).toContain('Pluto'); expect(s).toContain('Mars'); }
});
