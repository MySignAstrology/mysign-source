/* My Sign — site analytics. First-party, no third parties, no cookies.

   What leaves the page: event names from a fixed vocabulary (page, taps on
   elements that carry data-track, sections seen, the sign-in steps), the
   first-touch utm_* tags, and the referring domain. Never anything typed.

   anon_id  localStorage "ms_anon": 26 chars, Crockford base32, ULID-shaped.
   session  sessionStorage "ms_sess": rolls over after 30 idle minutes.
   utm      localStorage "ms_utm": first touch only.

   Nothing is stored and no anon_id or session_id is sent until the visitor says
   yes on the banner (consent.js), or ever with Global Privacy Control or Do Not
   Track on. Events still count, but can't be joined.

   Events are batched to POST /e with navigator.sendBeacon. A missing
   collector (404) fails silently; sendBeacon never surfaces it anyway. */
(function(){
  'use strict';
  var ENDPOINT = '/e', IDLE = 30 * 60e3, MAX_BATCH = 20;
  var NAME_RE = /^[a-z0-9][a-z0-9_.:-]{0,59}$/;
  var B32 = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  function isPriv(){
    if (navigator.globalPrivacyControl === true ||
        navigator.doNotTrack === '1' || window.doNotTrack === '1') return true;
    try { return localStorage.getItem('ms_consent') !== 'yes'; } catch (e) { return true; }
  }

  function ulid(){
    var t = Date.now(), out = '', i;
    for (i = 0; i < 10; i++) { out = B32[t % 32] + out; t = Math.floor(t / 32); }
    var r = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(r);
    for (i = 0; i < 16; i++) out += B32[r[i] % 32];
    return out;
  }
  function get(store, k){ try { return window[store].getItem(k); } catch (e) { return null; } }
  function set(store, k, v){ try { window[store].setItem(k, v); } catch (e) {} }

  function anonId(){
    if (isPriv()) return null;
    var a = get('localStorage', 'ms_anon');
    if (!a || !/^[0-9A-Z]{26}$/.test(a)) { a = ulid(); set('localStorage', 'ms_anon', a); }
    return a;
  }
  function sessionId(){
    if (isPriv()) return null;
    var now = Date.now(), s = null;
    try { s = JSON.parse(get('sessionStorage', 'ms_sess') || 'null'); } catch (e) {}
    if (!s || !s.id || now - s.at > IDLE) s = {id: ulid()};
    s.at = now;
    set('sessionStorage', 'ms_sess', JSON.stringify(s));
    return s.id;
  }

  var qs = new URLSearchParams(location.search);
  function utm(){
    var here = {};
    ['utm_source', 'utm_medium', 'utm_campaign'].forEach(function(k){
      var v = qs.get(k); if (v) here[k] = v.slice(0, 80);
    });
    if (isPriv()) return here;
    var first = null;
    try { first = JSON.parse(get('localStorage', 'ms_utm') || 'null'); } catch (e) {}
    if (!first && Object.keys(here).length) { first = here; set('localStorage', 'ms_utm', JSON.stringify(here)); }
    return first || {};
  }
  function refDomain(){
    try {
      var h = document.referrer ? new URL(document.referrer).hostname : '';
      return h && h !== location.hostname ? h.replace(/^www\./, '') : '';
    } catch (e) { return ''; }
  }

  var u = utm();
  var ctx = {utm_source: u.utm_source || null, utm_medium: u.utm_medium || null,
             utm_campaign: u.utm_campaign || null, referrer_domain: refDomain() || null,
             landing_path: location.pathname};
  // The first-touch tags ride on the landing URL, the only place the
  // collector reads them from; nothing else from the query leaves the page.
  var landing = location.pathname + (function(){
    var p = new URLSearchParams();
    ['utm_source', 'utm_medium', 'utm_campaign'].forEach(function(k){ if (u[k]) p.set(k, u[k]); });
    var t = p.toString();
    return t ? '?' + t : '';
  })();
  var screen = (document.body && document.body.dataset.page) || 'home';
  var q = [];

  function track(type, name, props){
    name = String(name || '').toLowerCase();
    if (!NAME_RE.test(name)) return;
    var now = Date.now();
    q.push({event_id: ulid(), ts: now, at: now, source: 'site', anon_id: anonId(),
            session_id: sessionId(), type: type, name: name, screen: screen,
            props: props || {}, ctx: ctx});
    if (q.length >= MAX_BATCH) flush();
  }
  function flush(){
    if (!q.length) return;
    var batch = q.splice(0, MAX_BATCH), last = batch[batch.length - 1];
    // The collector reads the ids and context once per batch, at the top
    // (deploy/collect/handler.py build()); `at` per event. The per-event
    // copies stay for anything reading the raw beacon.
    var body = JSON.stringify({source: 'site', anon_id: last.anon_id, session: last.session_id,
                               ctx: {referrer: document.referrer || null, landing: landing},
                               events: batch});
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(ENDPOINT, new Blob([body], {type: 'application/json'}))) return;
      fetch(ENDPOINT, {method: 'POST', keepalive: true, credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: body}).catch(function(){});
    } catch (e) {}
  }

  document.addEventListener('click', function(e){
    var el = e.target.closest && e.target.closest('[data-track]');
    if (el && el.tagName !== 'DETAILS') track('tap', el.dataset.track);
  }, true);
  document.addEventListener('toggle', function(e){
    var d = e.target;
    if (d.tagName === 'DETAILS' && d.open && d.dataset.track) track('tap', d.dataset.track);
  }, true);
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(en){
        if (!en.isIntersecting) return;
        io.unobserve(en.target);
        track('tap', 'section_view:' + en.target.dataset.section);
      });
    }, {threshold: 0.35});
    document.querySelectorAll('[data-section]').forEach(function(s){ io.observe(s); });
  }
  document.addEventListener('visibilitychange', function(){
    if (document.visibilityState === 'hidden') flush();
  });
  addEventListener('pagehide', flush);
  setInterval(flush, 10000);

  window.MsTrack = {track: track, flush: flush, anonId: anonId, isPrivate: isPriv};
  track('page', screen);
})();
