// Pages the iPhone app opens in its in-app browser (native/bridge.js adds
// ?app=1). There, a page must not lead to the web app or the home page's
// prices: both reach Stripe Checkout, a way to pay inside the app that isn't
// Apple's (App Review 3.1.1). So, in that browser only, every link to our own
// site that isn't one of these reference pages is hidden, and the ones that
// are keep the flag. Everywhere else this does nothing.
//
// The same rule covers digital goods sold on the web: a paid report is digital
// content, so inside the app it may only be sold through Apple. Anything marked
// as a digital offer is hidden here, and a link to a report's page in the shop
// can't be followed. The shop's physical gifts are fine and stay.
(() => {
  const FLAG = 'ms_inapp';
  let on = false;
  try {
    if (new URLSearchParams(location.search).get('app') === '1') sessionStorage.setItem(FLAG, '1');
    on = sessionStorage.getItem(FLAG) === '1';
  } catch (e) {
    on = /[?&]app=1\b/.test(location.search);
  }
  if (!on) return;
  document.documentElement.classList.add('inapp');
  const css = document.createElement('style');
  css.textContent = 'html.inapp [data-digital], html.inapp [data-section="check_report_offer"]{display:none!important}';
  (document.head || document.documentElement).appendChild(css);

  const OURS = /^(www\.)?mysignapp\.com$/;
  const SAFE = /^\/(privacy|terms|refunds|cookies|support|accuracy|couples|shop)(\/|$)|^\/api\/accuracy$/;
  const DIGITAL = /^shop\.mysignapp\.com\/products\/[^/?#]*report/i;
  const ours = u => u.host === location.host || OURS.test(u.hostname);
  const allowed = href => {
    let u;
    try { u = new URL(href, location.href); } catch (e) { return true; }
    if (DIGITAL.test(u.hostname + u.pathname)) return false;        // a paid report: digital, so not sold here
    if (!/^https?:$/.test(u.protocol) || !ours(u)) return true;    // mail, the shop's own site, credits
    return SAFE.test(u.pathname);
  };
  const tidy = root => {
    (root.querySelectorAll ? root.querySelectorAll('a[href]') : []).forEach(a => {
      const href = a.getAttribute('href');
      if (!allowed(href)) { a.hidden = true; a.setAttribute('aria-hidden', 'true'); a.style.display = 'none'; return; }
      try {
        const u = new URL(href, location.href);
        if (ours(u) && u.searchParams.get('app') !== '1') { u.searchParams.set('app', '1'); a.href = u.href; }
      } catch (e) {}
    });
  };
  // A link drawn after load (the couples and shop pages build theirs) is
  // tidied as it arrives; one that slips through still can't be followed.
  const watch = () => {
    tidy(document);
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { if (n.nodeType === 1) tidy(n.parentNode || n); })))
      .observe(document.body, {childList: true, subtree: true});
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (a && !allowed(a.getAttribute('href'))) { e.preventDefault(); e.stopPropagation(); }
  }, true);
})();
