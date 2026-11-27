/* My Sign — the one choice the website asks for: may the browser keep a random
   number so a return visit can be told from a new one (track.js)? Asked once,
   the same two plain buttons either way, and no is the default until answered.
   /cookies has the same buttons, to change your mind. Global Privacy Control or
   Do Not Track already answered no, so those visitors aren't asked. It waits for
   the first scroll, so it never sits on the button a phone's first screen is for;
   nothing is kept until a yes, so nothing is lost by asking late. */
(function(){
  'use strict';
  var KEY = 'ms_consent';
  var saidNo = navigator.globalPrivacyControl === true ||
               navigator.doNotTrack === '1' || window.doNotTrack === '1';
  function read(){ try { return localStorage.getItem(KEY); } catch (e) { return 'no'; } }
  function choose(v){
    try {
      localStorage.setItem(KEY, v);
      if (v !== 'yes') { localStorage.removeItem('ms_anon'); localStorage.removeItem('ms_utm'); sessionStorage.removeItem('ms_sess'); }
    } catch (e) {}
  }
  window.MsConsent = {read: read, choose: choose, blocked: saidNo};
  // Ids from before this banner existed go too.
  if (saidNo || read() !== 'yes') {
    try { localStorage.removeItem('ms_anon'); localStorage.removeItem('ms_utm'); sessionStorage.removeItem('ms_sess'); } catch (e) {}
  }
  if (saidNo || read() || location.pathname === '/cookies') return;
  var bar = document.createElement('section');
  bar.setAttribute('aria-label', 'Your privacy choice');
  bar.id = 'msConsent';
  bar.innerHTML = '<style>#msConsent{position:fixed;left:12px;right:12px;bottom:max(12px,env(safe-area-inset-bottom));z-index:50;max-width:34rem;margin:0 auto;' +
    'display:flex;flex-wrap:wrap;gap:.6rem .8rem;align-items:center;padding:.75rem .9rem;border-radius:6px;' +
    'background:#10142A;color:#EFE6D2;border:1px solid rgba(200,162,74,.36);box-shadow:0 8px 28px rgba(0,0,0,.5);font:14px/1.4 Geist,"Helvetica Neue",Arial,sans-serif}' +
    '#msConsent p{margin:0;flex:1 1 15rem}#msConsent a{color:#F0745C}' +
    '#msConsent button{font:inherit;font-weight:600;min-height:44px;padding:0 1rem;border-radius:4px;border:1px solid rgba(239,230,210,.6);background:transparent;color:inherit;cursor:pointer}' +
    '</style>' +
    '<p>Can we keep a random number in your browser to count return visits? Never what you type. <a href="/cookies">Details</a>.</p>' +
    '<button type="button" data-v="no">No thanks</button><button type="button" data-v="yes">Allow</button>';
  bar.addEventListener('click', function(e){
    var v = e.target.getAttribute && e.target.getAttribute('data-v');
    if (v) { choose(v); bar.remove(); }
  });
  function show(){
    if (window.scrollY < 80) return;
    window.removeEventListener('scroll', show);
    document.body.appendChild(bar);
  }
  window.addEventListener('scroll', show, {passive: true});
})();
