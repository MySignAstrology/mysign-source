// Runs native/bridge.js outside the app, with StoreKit and the server faked,
// and prints what happened in each case as JSON. tests/test_apple.py reads it.
// The point is the order: the server first, StoreKit's finish only after.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '../native/bridge.js'), 'utf8').replace('__MYSIGN_API__', 'https://api.test');

const OFFER = {available: true, token: '11111111-2222-4333-8444-555555555555', offer: true,
               products: ['mysign_plus_monthly', 'mysign_plus_annual']};
const TX = {id: '77', productId: 'mysign_plus_annual', jws: 'h.p.s'};

async function scenario({offer = OFFER, sync = {status: 200, body: {ok: true, finish: true, plan: {tier: 'plus'}}},
                         entitlements = [], purchase = {outcome: 'purchased', transaction: TX}}, act) {
  const log = [];
  let listener = null;
  const native = (plugin, method, opts) => {
    if (plugin !== 'PlusStore') return Promise.resolve({});
    log.push([method, opts.productId || opts.id || (opts.ids && opts.ids.join(',')) || '', opts.appAccountToken || ''].filter(Boolean));
    if (method === 'products') return Promise.resolve({products: opts.ids.map(id => ({id}))});
    if (method === 'entitlements' || method === 'restore') return Promise.resolve({transactions: entitlements});
    if (method === 'purchase') return Promise.resolve(purchase);
    return Promise.resolve({finished: true});
  };
  const json = (status, body) => ({ok: status < 300, status, json: async () => body, clone() { return this; }});
  const window = {
    Capacitor: {nativePromise: native, addListener: (p, e, cb) => { if (p === 'PlusStore') listener = cb; }},
    fetch: async (url, opts) => {
      const at = url.replace('https://api.test', '');
      if (at === '/api/state') return json(200, {signed_in: true, plan: {apple: offer}});
      if (at === '/api/billing/apple/sync') {
        log.push(['sync', JSON.parse(opts.body).signedTransaction, (opts.headers.Authorization || '').slice(0, 6)]);
        if (sync === 'offline') throw new TypeError('Load failed');
        return json(sync.status, sync.body);
      }
      return json(200, {});
    },
    open: () => null, dispatchEvent: e => { log.push(['event', e.type]); }, addEventListener: () => {},
  };
  const store = {};
  const sandbox = {
    window, document: {addEventListener: () => {}}, navigator: {},
    localStorage: {getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; }},
    crypto: require('crypto').webcrypto, CustomEvent: class { constructor(type) { this.type = type; } },
    URL, Promise, JSON, Object, Array, Uint8Array, TypeError, Error,
  };
  // The session token the Keychain would hold.
  const realNative = window.Capacitor.nativePromise;
  window.Capacitor.nativePromise = (plugin, method, opts) =>
    plugin === 'TokenStore' && method === 'get' ? Promise.resolve({token: 'session'}) : realNative(plugin, method, opts);
  new Function(...Object.keys(sandbox), src)(...Object.values(sandbox));
  const settle = () => new Promise(r => setTimeout(r, 20));
  await window.fetch('/api/state');           // the page's first call; the offer rides on it
  await settle();
  const result = await act({store: window.MySignStore, emit: x => listener && listener(x), settle});
  await settle();
  return {log, result, ready: window.MySignStore.ready(), open: window.MySignStore.open()};
}

(async () => {
  const out = {};
  out.boot = await scenario({entitlements: [{id: '5', jws: 'old.jws.x'}]}, async () => null);
  out.buy = await scenario({}, ({store}) => store.buy('mysign_plus_annual'));
  out.offline = await scenario({sync: 'offline'}, ({store}) => store.buy('mysign_plus_annual'));
  out.refused = await scenario({sync: {status: 400, body: {detail: 'That purchase couldn\'t be verified. Nothing changed.'}}},
    ({store}) => store.buy('mysign_plus_annual'));
  out.other = await scenario({sync: {status: 409, body: {detail: {code: 'other_account', finish: true, message: 'belongs to a different account'}}}},
    ({store}) => store.buy('mysign_plus_annual'));
  out.cancelled = await scenario({purchase: {outcome: 'cancelled'}}, ({store}) => store.buy('mysign_plus_monthly'));
  out.has_plus = await scenario({offer: {...OFFER, offer: false}}, ({store}) => store.buy('mysign_plus_annual'));
  out.closed = await scenario({offer: {available: false, token: null, offer: false, products: []}}, ({store}) => store.buy('mysign_plus_annual'));
  out.renewal = await scenario({}, async ({emit, settle}) => { emit({id: '88', jws: 'renew.jws.x'}); await settle(); return null; });
  out.restore = await scenario({entitlements: [{id: '5', jws: 'old.jws.x'}]}, ({store}) => store.restore());
  out.lapsed = await scenario({entitlements: [{id: '5', jws: 'old.jws.x'}],
    sync: {status: 200, body: {ok: true, finish: true, code: 'expired', plan: {tier: 'free'}}}}, ({store}) => store.restore());
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
