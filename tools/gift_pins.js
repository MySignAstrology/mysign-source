// Render the Pinterest images for the gift guide: site/gifts/pin-<sign>.jpg, 1000x1500.
// They use the product photos already on each sign's page. Run after tools/gift_guide.py:
//   node tools/gift_pins.js
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const SITE = path.join(__dirname, '..', 'site');
const GIFTS = path.join(SITE, 'gifts');
const SIGNS = ['aries', 'taurus', 'gemini', 'cancer', 'leo', 'virgo', 'libra', 'scorpio', 'sagittarius', 'capricorn', 'aquarius', 'pisces'];
const STYLE = `*{box-sizing:border-box}
body{margin:0;width:1000px;height:1500px;background:#2A0F18;color:#F8EEE4;font-family:Geist,Arial,sans-serif;display:flex;flex-direction:column;padding:56px}
.t{font-size:30px;letter-spacing:.09em;text-transform:uppercase;color:#E6CFA8}
h1{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:128px;line-height:.98;margin:18px 0}
.d{font-size:36px;color:#F0DDD2}
.g{flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:minmax(0,1.25fr) minmax(0,1fr);gap:16px;margin:38px 0 34px;min-height:0}
.g img{width:100%;height:100%;object-fit:cover;border-radius:22px;display:block;background:#3c1a26}
.g img:first-child{grid-column:1/-1}
.f{display:flex;justify-content:space-between;align-items:baseline;font-size:34px}
.w{font-family:'Instrument Serif',Georgia,serif;font-size:48px}`;

(async () => {
  const browser = await chromium.launch();
  const tab = await browser.newPage({ viewport: { width: 1000, height: 1500 } });
  const tmp = path.join(os.tmpdir(), 'mysign-gift-pin.html');
  for (const sign of SIGNS) {
    const page = fs.readFileSync(path.join(GIFTS, sign + '.html'), 'utf8');
    const shots = [...page.matchAll(/<img src="([^"]+)"/g)]
      .map(m => m[1].replace(/&amp;/g, '&').replace('width=600', 'width=1000'))
      .slice(0, 3);
    const [, article, name] = page.match(/<h1>Gifts for (an?) ([^<]+)<\/h1>/);
    const dates = page.match(/<p class="eyebrow">\S+ ([^·]+) ·/)[1].trim();
    const low = Math.min(...[...page.matchAll(/<b>(?:from )?\$([\d.]+)<\/b>/g)].map(m => +m[1]));
    const lowText = Number.isInteger(low) ? low : low.toFixed(2);
    fs.writeFileSync(tmp, `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="file://${SITE}/fonts/fonts.css"><style>${STYLE}</style>
<div class="t">Zodiac gift guide</div><h1>Gifts for ${article}<br>${name}</h1><div class="d">${dates} · from $${lowText}</div>
<div class="g">${shots.map(s => `<img src="${s}" alt="">`).join('')}</div>
<div class="f"><span class="w">mySign</span><span>mysignapp.com/gifts</span></div>`);
    await tab.goto('file://' + tmp, { waitUntil: 'networkidle' });
    await tab.evaluate(() => document.fonts.ready);
    await tab.screenshot({ path: path.join(GIFTS, `pin-${sign}.jpg`), type: 'jpeg', quality: 82 });
    console.log(sign, shots.length);
  }
  await browser.close();
})();
