// The seam between the web app and the iPhone shell. app/static/index.html runs
// unchanged, with relative /api paths and a session cookie; a page bundled at
// capacitor://localhost has neither a server behind it nor a cookie that would
// survive. So this script, added only to the bundled copy (build-www.mjs),
// points those paths at the API and carries the session as a bearer token.
(() => {
  const API = '__MYSIGN_API__';
  const C = window.Capacitor;
  const call = (plugin, method, opts) => C.nativePromise(plugin, method, opts || {});
  const quietly = p => p.catch(() => {});

  // ------------------------------------------------------------ the session
  let token = quietly(call('TokenStore', 'get')).then(r => (r && r.token) || '');
  const remember = t => {
    token = Promise.resolve(t);
    // Signed out: the server has dropped this phone's notifications with the
    // session (app/apns.py forget), so the phone forgets it had them on.
    if (!t) keepPush('');
    return quietly(t ? call('TokenStore', 'set', {token: t}) : call('TokenStore', 'clear'));
  };

  // Tells this phone's code guesses apart from a stranger's (app/auth.py VERIFY_*).
  // The web client's cookie does this job; this one can't cross origins.
  const device = (() => {
    try {
      let d = localStorage.getItem('ms_device');
      if (!d) {
        d = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
        localStorage.setItem('ms_device', d);
      }
      return d;
    } catch (e) { return ''; }
  })();

  // ------------------------------------------------------------- the network
  const send = window.fetch.bind(window);
  window.fetch = async (url, opts) => {
    if (typeof url !== 'string' || !url.startsWith('/')) return send(url, opts);
    const held = await token;
    const headers = Object.assign({}, opts && opts.headers, {'X-Client': 'ios-app', 'X-Device': device});
    if (held) headers.Authorization = `Bearer ${held}`;
    let r;
    try {
      r = await send(API + url, Object.assign({}, opts, {headers, credentials: 'omit'}));
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      // WebKit's own wording is "Load failed", and the page shows what it's given.
      throw new Error("I couldn't reach the server. Check your connection and try again.");
    }
    const method = (opts && opts.method) || 'GET';
    const post = method === 'POST';
    if (post && r.ok && url === '/api/auth/verify') {
      const fresh = await r.clone().json().catch(() => null);
      if (fresh && fresh.token) await remember(fresh.token);
    }
    if (post && url === '/api/auth/signout') await remember('');
    if (method === 'DELETE' && r.ok && url === '/api/account') await remember('');
    // A token the server stopped honouring: deleted account, or signed out elsewhere.
    if (r.status === 401 && held && !url.startsWith('/api/auth/')) await remember('');
    if (held && r.ok && url === '/api/state') {
      const state = await r.clone().json().catch(() => null);
      if (state && state.signed_in === false) await remember('');
      noteOffer(state && state.plan && state.plan.apple);
      notePush(state && state.native_push);
    }
    if (post && r.ok && /^\/api\/(checkin|connections\/[^/]+\/checkin)$/.test(url))
      quietly(call('Haptics', 'notification', {type: 'SUCCESS'}));
    return r;
  };
  // A beacon can't carry the token, and analytics never needs to outlive the app.
  // /e is the signed-out collector. It answers no preflight, so those events stay
  // on the phone rather than fail on every send.
  navigator.sendBeacon = (url, body) => {
    if (url === '/e') return true;
    window.fetch(url, {method: 'POST', body, keepalive: true}).catch(() => {});
    return true;
  };

  // -------------------------------------------------------------------- Plus
  // Plus is sold here through the App Store and nowhere else (App Review 3.1.1).
  // StoreKit does the buying (ios/App/App/PlusStore.swift); the server decides
  // what a purchase unlocks (app/apple.py). This passes the signed transaction
  // from one to the other, and tells StoreKit a transaction is finished only
  // after the server has it. Until then StoreKit hands it back at every launch,
  // so a purchase made on a train is not lost in a tunnel.
  const shop = {offer: null, products: null, loading: null, caughtUp: ''};
  const changed = () => window.dispatchEvent(new CustomEvent('mysign:plan'));

  const confirm = async signed => {
    let r;
    try {
      r = await window.fetch('/api/billing/apple/sync', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({signedTransaction: signed.jws})});
    } catch (e) { return {ok: false, unreachable: true, message: e.message}; }
    const body = await r.json().catch(() => ({}));
    const detail = body.detail && typeof body.detail === 'object' ? body.detail : {};
    if (r.ok ? body.finish : detail.finish) await quietly(call('PlusStore', 'finish', {id: signed.id}));
    // Recorded is not the same as unlocked: an expired or refunded transaction
    // is taken down by the server too, and gives her nothing.
    if (r.ok) return {ok: !!(body.plan && body.plan.tier === 'plus'), recorded: true, plan: body.plan};
    return {ok: false, code: detail.code,
            message: detail.message || (typeof body.detail === 'string' ? body.detail : '')};
  };
  const confirmAll = async list => {
    const results = [];
    for (const signed of list || []) results.push(await confirm(signed));
    if (results.some(x => x.ok)) changed();
    return results;
  };

  // The server's word on the shop rides on /api/state: whether it is open, her
  // appAccountToken, the product ids, and whether she should be offered them
  // (never when she already has Plus, from the web or anywhere else).
  function noteOffer(offer) {
    shop.offer = offer || null;
    if (!offer || !offer.available) return;
    if (!shop.products && !shop.loading)
      shop.loading = call('PlusStore', 'products', {ids: offer.products})
        .then(r => { shop.products = (r && r.products) || []; changed(); })
        .catch(() => {})
        .then(() => { shop.loading = null; });
    // Once per launch and account: anything bought or renewed while the app
    // was closed, or bought before the server could be told.
    if (shop.caughtUp !== offer.token) {
      shop.caughtUp = offer.token;
      quietly(call('PlusStore', 'entitlements').then(r => confirmAll(r && r.transactions)));
    }
  }
  // And each time she comes back to the app: StoreKit's live updates are not
  // promised to arrive, and asking again costs one request the server answers
  // "same" to.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && shop.offer && shop.offer.available)
      quietly(call('PlusStore', 'entitlements').then(r => confirmAll(r && r.transactions)));
  });
  // Renewals, refunds and Ask to Buy approvals while the app is open.
  if (C.addListener) C.addListener('PlusStore', 'transaction', signed => { quietly(confirmAll([signed])); });

  window.MySignStore = {
    // Something to sell, to someone who should be offered it.
    ready: () => !!(shop.offer && shop.offer.offer && shop.products && shop.products.length),
    // The shop exists at all: Restore has something to ask.
    open: () => !!(shop.offer && shop.offer.available),
    products: () => shop.products || [],
    async buy(productId) {
      // Asked again right before the sheet: someone who got Plus a minute ago
      // on the web must not be sold a second subscription.
      await window.fetch('/api/state').catch(() => {});
      if (!this.ready()) return {ok: false, outcome: 'unavailable'};
      const r = await call('PlusStore', 'purchase', {productId, appAccountToken: shop.offer.token});
      if (r.outcome !== 'purchased') return {ok: false, outcome: r.outcome};
      const done = await confirm(r.transaction);
      if (done.ok) changed();
      return Object.assign({outcome: 'purchased'}, done);
    },
    async restore() {
      const r = await call('PlusStore', 'restore');
      const results = await confirmAll(r && r.transactions);
      return {found: results.length, ok: results.some(x => x.ok),
              message: (results.find(x => !x.ok && x.message) || {}).message || ''};
    },
    // Apple's own sheet for changing or cancelling. The app can't cancel for her.
    manage: () => call('PlusStore', 'manage'),
  };

  // ----------------------------------------------------------- notifications
  // The morning note and the rest arrive through Apple's push service here,
  // because Web Push does not run inside the app. iOS gives this phone a
  // device token (ios/App/App/PushCenter.swift); the server keeps it beside
  // her account and sends to it (app/apns.py). What is sent, when, and whether
  // at all are her settings on the server, the same ones the web uses. iOS is
  // asked for permission only when she turns notifications on in Settings.
  const PUSH_KEPT = 'ms_push_token';      // the token this phone last gave the server
  const pushed = {available: false, caughtUp: false, taps: [], onOpen: null};
  const keptPush = () => { try { return localStorage.getItem(PUSH_KEPT) || ''; } catch (e) { return ''; } };
  function keepPush(t) {
    try { if (t) localStorage.setItem(PUSH_KEPT, t); else localStorage.removeItem(PUSH_KEPT); } catch (e) {}
  }
  const zone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { return ''; } };
  const tell = async (path, body) => {
    const r = await window.fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify(body)});
    const answer = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof answer.detail === 'string' ? answer.detail : "That didn't work. Try again in a moment.");
    return answer;
  };
  // iOS can change the token (a restore, a new install). The server is told
  // which one it replaces, so the old line goes instead of lingering.
  const moved = async fresh => {
    const old = keptPush();
    if (!fresh || !old || fresh === old) return;
    await tell('/api/push/apns', {token: fresh, tz: zone(), replaces: old});
    keepPush(fresh);
  };
  // The server's word rides on /api/state: whether notifications exist here at
  // all. Then, once per launch, a phone that has them on asks iOS for its
  // token again, which is how a changed one is noticed.
  function notePush(available) {
    pushed.available = !!available;
    if (!pushed.available || pushed.caughtUp || !keptPush()) return;
    pushed.caughtUp = true;
    quietly(call('NativePush', 'token').then(r => moved(r && r.token)));
  }
  if (C.addListener) {
    C.addListener('NativePush', 'token', e => { quietly(moved(e && e.token)); });
    // A tap on a notification. Held until the page says it is ready for it:
    // the tap may be what started the app.
    C.addListener('NativePush', 'opened', e => {
      const url = e && typeof e.url === 'string' && /^\/app([/?]|$)/.test(e.url) ? e.url : '/app';
      if (pushed.onOpen) pushed.onOpen(url); else pushed.taps.push(url);
    });
  }

  window.MySignPush = {
    // The server can send here. False until the APNs key is in: no switch shown.
    available: () => pushed.available,
    // {permission: 'granted' | 'denied' | 'prompt', on, today}. `on` is the
    // server's answer for this phone, never a guess kept on the device.
    async state() {
      const {permission} = await call('NativePush', 'status');
      const held = keptPush();
      if (permission !== 'granted' || !held) return {permission, on: false};
      const st = await tell('/api/push/apns/status', {token: held});
      return {permission, on: !!st.registered, today: st.today || null};
    },
    // She turned them on. The first time, iOS shows its permission sheet.
    async enable() {
      const r = await call('NativePush', 'enable');
      if (r.permission !== 'granted') return {ok: false, permission: r.permission};
      if (!r.token) return {ok: false, permission: 'granted', message: r.error || ''};
      const old = keptPush();
      await tell('/api/push/apns', Object.assign({token: r.token, tz: zone()},
                                                 old && old !== r.token ? {replaces: old} : {}));
      keepPush(r.token);
      return {ok: true, permission: 'granted'};
    },
    // This phone only. Her other devices keep theirs.
    async disable() {
      const held = keptPush();
      if (held) await tell('/api/push/apns/remove', {token: held});
      keepPush('');
      return {ok: true};
    },
    openSettings: () => call('NativePush', 'openSettings'),
    onOpen(cb) {
      pushed.onOpen = cb;
      pushed.taps.splice(0).forEach(url => cb(url));
    },
  };

  // ------------------------------------------------------------------- links
  // Pages like /privacy live on the website, and core screens never leave the app.
  const open = url => quietly(call('Browser', 'open', {url, presentationStyle: 'popover'}));
  const outward = href => {
    if (!href) return null;
    if (href.startsWith('/') && !href.startsWith('//')) return inApp(API + href);
    return /^https?:/i.test(href) ? inApp(href) : null;
  };
  // Our own pages, opened from here, carry ?app=1 so they hide every route to
  // the web app and its Stripe checkout (app/static/inapp.js, guideline 3.1.1).
  const inApp = url => {
    try {
      const u = new URL(url);
      if (u.origin !== new URL(API).origin && !/^(www\.)?mysignapp\.com$/.test(u.hostname)) return url;
      u.searchParams.set('app', '1');
      return u.href;
    } catch (e) { return url; }
  };
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (a && !a.hasAttribute('download')) {
      const url = outward(a.getAttribute('href'));
      if (url) { e.preventDefault(); open(url); return; }
    }
    // The 1 to 10 mood row and the Closer / Steady / Strained buttons.
    if (e.target.closest && e.target.closest('#moods button, [data-feel]'))
      quietly(call('Haptics', 'impact', {style: 'LIGHT'}));
  }, true);
  const openWindow = window.open.bind(window);
  window.open = (url, ...rest) => {
    const out = outward(String(url || ''));
    if (out) { open(out); return null; }
    return openWindow(url, ...rest);
  };
})();
