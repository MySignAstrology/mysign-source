// Builds the page the shell bundles: app/static/index.html plus bridge.js. The
// page must ship inside the app, not load from the site (App Review 4.2.3), and
// the web client stays as it is. MYSIGN_API points a build elsewhere, such as a
// local server or staging.
import {cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const www = join(here, 'www');
const api = (process.env.MYSIGN_API || 'https://mysignapp.com').replace(/\/$/, '');

rmSync(www, {recursive: true, force: true});
mkdirSync(join(www, 'static'), {recursive: true});
cpSync(join(repo, 'app/static/icons'), join(www, 'static/icons'), {recursive: true});
// The constellation figures behind a sign. The page asks for them by sign name
// from script, so the check below cannot see a missing one.
cpSync(join(repo, 'app/static/plates'), join(www, 'static/plates'), {recursive: true});
// The psychic room's crystal ball (orb-ui), loaded from script when the room opens.
cpSync(join(repo, 'app/static/orb.js'), join(www, 'static/orb.js'));
if (existsSync(join(repo, 'site/fonts'))) cpSync(join(repo, 'site/fonts'), join(www, 'fonts'), {recursive: true});

const page = readFileSync(join(repo, 'app/static/index.html'), 'utf8')
  .replace(/<link rel="manifest"[^>]*>\n?/, '')
  .replace('<head>', `<head>\n<script src="/native-bridge.js"></script>`);
if (!page.includes('native-bridge.js')) throw new Error('index.html has no <head> to add the bridge to');
writeFileSync(join(www, 'index.html'), page);
writeFileSync(join(www, 'native-bridge.js'), readFileSync(join(here, 'bridge.js'), 'utf8').replace('__MYSIGN_API__', api));

// A file the page asks for from its own origin and the bundle lacks would fail
// silently on a phone, so it fails here instead.
for (const [, path] of page.matchAll(/<(?:link|script)\b[^>]*?(?:href|src)="(\/[^"/][^"]*)"/g)) {
  if (!existsSync(join(www, path.split('?')[0]))) throw new Error(`index.html needs ${path}; add it to build-www.mjs`);
}
console.log(`www built for ${api}`);
