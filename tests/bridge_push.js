// Runs native/bridge.js outside the app, with iOS and the server faked, and
// prints what happened in each case as JSON. tests/test_apns.py reads it.
// The point is the order and the restraint: iOS is asked only when she turns
// notifications on, and the server hears a token only after iOS allows it.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '../native/bridge.js'), 'utf8').replace('__MYSIGN_API__', 'https://api.test');

const A = 'aa'.repeat(32), B = 'bb'.repeat(32), C = 'cc'.repeat(32);

async function scenario({available = true, kept = '', enable = {permission: 'granted', token: A},
                         token = {permission: 'granted', token: A}, permission = 'granted'}, act) {
  const log = [];
  const listeners = {};
  const registered = new Set(kept ? [kept] : []);
  const native = (plugin, method) => {
    if (plugin === 'TokenStore') return Promise.resolve(method === 'get' ? {token: 'session'} : {});
    if (plugin !== 'NativePush') return Promise.resolve({});
    if (method === 'status') return Promise.resolve({permission});
    log.push(['native', method]);
    if (method === 'enable') return Promise.resolve(enable);
    if (method === 'token') return Promise.resolve(token);
    return Promise.resolve({});
  };
  const json = (status, body) => ({ok: status < 300, status, json: async () => body, clone() { return this; }});
  const window = {
    Capacitor: {nativePromise: native, addListener: (plugin, event, cb) => { listeners[`${plugin}:${event}`] = cb; }},
    fetch: async (url, opts) => {
      const at = url.replace('https://api.test', '');
      const body = opts && opts.body ? JSON.parse(opts.body) : null;
      if (at === '/api/state') return json(200, {signed_in: true, native_push: available, plan: {}});
      if (at === '/api/push/apns') { log.push(['POST', at, body]); registered.delete(body.replaces); registered.add(body.token); return json(200, {devices: registered.size, registered: true}); }
      if (at === '/api/push/apns/remove') { log.push(['POST', at, body]); registered.delete(body.token); return json(200, {devices: registered.size, registered: false}); }
      if (at === '/api/push/apns/status') return json(200, {registered: registered.has(body.token), devices: registered.size, today: null});
      return json(200, {});
    },
    open: () => null, dispatchEvent: () => {}, addEventListener: () => {},
  };
  const store = kept ? {ms_push_token: kept} : {};
  const sandbox = {
    window, document: {addEventListener: () => {}}, navigator: {},
    localStorage: {getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; }},
    crypto: require('crypto').webcrypto, CustomEvent: class { constructor(type) { this.type = type; } },
    Intl: {DateTimeFormat: () => ({resolvedOptions: () => ({timeZone: 'UTC'})})},
    URL, Promise, JSON, Object, Array, Uint8Array, TypeError, Error,
  };
  new Function(...Object.keys(sandbox), src)(...Object.values(sandbox));
  const settle = () => new Promise(r => setTimeout(r, 20));
  await window.fetch('/api/state');           // the page's first call; availability rides on it
  await settle();
  const push = window.MySignPush;
  const result = act ? await act({push, window, settle, emit: (event, data) => listeners[`NativePush:${event}`](data)}) : null;
  await settle();
  const state = await push.state().catch(e => ({error: e.message}));
  return {log, result, state, available: push.available(), kept: store.ms_push_token || ''};
}

(async () => {
  const out = {};
  out.unavailable = await scenario({available: false});
  out.enable = await scenario({}, ({push}) => push.enable());
  out.denied = await scenario({enable: {permission: 'denied'}, permission: 'denied'}, ({push}) => push.enable());
  out.no_token = await scenario({enable: {permission: 'granted', error: "This iPhone didn't answer."}}, ({push}) => push.enable());
  out.disable = await scenario({}, async ({push}) => { await push.enable(); return push.disable(); });
  out.refresh_at_launch = await scenario({kept: A, token: {permission: 'granted', token: B}});
  out.refresh_live = await scenario({kept: A}, async ({emit, settle}) => { emit('token', {token: C}); await settle(); });
  out.launch_untouched = await scenario({});
  out.signout = await scenario({kept: A}, ({window}) => window.fetch('/api/auth/signout', {method: 'POST'}));
  const early = [], late = [];
  await scenario({}, async ({push, emit}) => {
    emit('opened', {url: '/app?p=c1&from=push&pk=person'});      // before the page is ready
    push.onOpen(url => early.push(url));
    push.onOpen(url => late.push(url));
    emit('opened', {url: '/app?sky=events&from=push&pk=sky'});
    emit('opened', {url: 'https://evil.example/app'});
    emit('opened', {url: 7});
  });
  out.taps = {early, late};
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
