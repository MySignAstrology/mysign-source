/* My Sign — the public site. No framework: a header that frosts on scroll,
   sections that rise in, the invite banner, and the start sheet.

   The start sheet speaks the same auth API as the app:
     GET  /api/auth/channels  -> {"email": true, "sms": bool}   (absent = email only)
     POST /api/auth/request   {"email"|"phone": ..., "anon_id"?} -> {"sent", "channel"?, "dev_code"?}
     POST /api/auth/verify    {"email"|"phone": ..., "code", "anon_id"?} -> session cookie
   While a code is outstanding, sessionStorage "ms_code_for" holds
   {"kind": "email"|"phone", "value": ...} so /app can resume the code step. */
(function(){
  'use strict';
  var $ = function(s, r){ return (r || document).querySelector(s); };
  var $$ = function(s, r){ return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var KEY = 'ms_code_for', COOLDOWN = 30;
  var track = function(type, name, props){ if (window.MsTrack) window.MsTrack.track(type, name, props); };
  var anon = function(){ return window.MsTrack && window.MsTrack.anonId ? window.MsTrack.anonId() : null; };
  document.documentElement.classList.add('js');

  /* ---------- chrome ---------- */
  var top = $('#top');
  function onScroll(){ top.classList.toggle('scrolled', scrollY > 8); }
  addEventListener('scroll', onScroll, {passive: true}); onScroll();
  $('#yr').textContent = new Date().getFullYear();

  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function(es){
      es.forEach(function(e){ if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, {rootMargin: '0px 0px -8% 0px', threshold: 0.12});
    $$('.rv').forEach(function(el){ io.observe(el); });
  } else {
    $$('.rv').forEach(function(el){ el.classList.add('in'); });
  }

  /* ---------- arriving from someone's invite ---------- */
  var inviteTok = new URLSearchParams(location.search).get('invite');
  if (inviteTok && /^[A-Za-z0-9_-]{10,40}$/.test(inviteTok)) {
    fetch('/api/invites/' + encodeURIComponent(inviteTok), {credentials: 'same-origin'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(inv){
        if (!inv || !inv.valid || !inv.from) return;
        var bar = $('#invite'), b = document.createElement('b');
        b.textContent = inv.from;
        bar.textContent = '';
        bar.append(b, ' invited you to link charts ✦', document.createElement('br'));
        var s = document.createElement('small');
        s.textContent = 'Make your chart first, then you can accept.';
        bar.append(s);
        bar.hidden = false;
      }).catch(function(){});
  }

  /* ---------- the start sheet ---------- */
  var dlg = $('#start'), form = $('#sForm'), idIn = $('#sId'), codeIn = $('#sCode'),
      go = $('#sGo'), err = $('#sErr'), status = $('#sStatus'), dev = $('#devCode'),
      hint = $('#sIdHint'), opener = null, smsOk = null, channelsP = null,
      pending = null, busy = false, waitT = null, otpAbort = null, mode = 'start';

  function channels(){
    if (channelsP) return channelsP;
    var ctl = 'AbortController' in window ? new AbortController() : null;
    var t = ctl && setTimeout(function(){ ctl.abort(); }, 4000);
    channelsP = fetch('/api/auth/channels', {credentials: 'same-origin', signal: ctl && ctl.signal})
      .then(function(r){ return r.ok ? r.json() : {}; })
      .catch(function(){ return {}; })
      .then(function(c){ clearTimeout(t); smsOk = !!(c && c.sms); applyChannels(); return smsOk; });
    return channelsP;
  }
  function noun(){ return smsOk ? 'phone or email' : 'email'; }
  function applyChannels(){
    $('#sIdLabel').textContent = smsOk ? 'Phone or email' : 'Email';
    idIn.placeholder = smsOk ? 'you@example.com or (555) 123-4567' : 'you@example.com';
    idIn.setAttribute('inputmode', smsOk ? 'text' : 'email');
    idIn.setAttribute('autocomplete', smsOk ? 'username' : 'email');
    setLede();
    validate();
  }
  function setLede(){
    var how = smsOk ? 'Enter your phone or email' : 'Enter your email';
    $('#sLede').textContent = mode === 'signin'
      ? how + ' and we’ll send you a code.'
      : how + ' and we’ll send you a six-digit code. There’s no password to forget or leak.';
  }

  /* contains @ -> email; otherwise digits -> a US phone number, sent as E.164 */
  function fmtUS(d){ return '(' + d.slice(0, 3) + ') ' + d.slice(3, 6) + '-' + d.slice(6); }
  function parse(raw){
    var v = String(raw || '').trim();
    if (!v) return {ok: false};
    if (v.indexOf('@') !== -1) {
      return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)
        ? {ok: true, kind: 'email', value: v, show: v} : {ok: false, kind: 'email'};
    }
    if (!/^[\d\s().+-]+$/.test(v)) return {ok: false, kind: 'email'};
    if (!smsOk) return {ok: false, kind: 'phone', msg: 'Enter your email address — we can’t text codes yet.'};
    var d = v.replace(/\D/g, '');
    if (/^\+/.test(v) && !/^\+\s*1/.test(v)) return {ok: false, kind: 'phone', msg: 'Only US numbers work for now. You can use your email instead.'};
    if (d.length === 11 && d[0] === '1') d = d.slice(1);
    if (d.length > 10) return {ok: false, kind: 'phone', msg: 'That’s too many digits for a US number.'};
    if (d.length === 10 && /^[2-9]\d{2}[2-9]/.test(d)) return {ok: true, kind: 'phone', value: '+1' + d, show: fmtUS(d)};
    return {ok: false, kind: 'phone'};
  }
  function validate(){
    if (pending) { go.disabled = busy || codeIn.value.length !== 6; return; }
    var p = parse(idIn.value);
    go.disabled = busy || !p.ok;
    hint.textContent = p.ok ? (p.kind === 'phone' ? 'We’ll text a code to ' + p.show + '.' : 'We’ll email you a code.')
                            : (p.msg || '');
    return p;
  }
  idIn.addEventListener('input', function(){ err.textContent = ''; validate(); });
  idIn.addEventListener('blur', function(){
    var p = parse(idIn.value);
    if (p.ok && p.kind === 'phone') idIn.value = p.show;
  });
  codeIn.addEventListener('input', function(){
    codeIn.value = codeIn.value.replace(/\D/g, '').slice(0, 6);
    err.textContent = '';
    validate();
    if (codeIn.value.length === 6 && !busy) verify();
  });

  function errorText(body, r){
    var d = body && body.detail;
    if (typeof d === 'string') return d;
    if (d && typeof d.message === 'string') return d.message;
    if (Array.isArray(d) && d.length) return 'That didn’t look right. Check it and try again.';
    if (r && r.status === 404) return 'Sign-in isn’t available here yet. Try again shortly.';
    if (r && r.status >= 500) return 'Something went wrong on our end. Try again in a moment.';
    return 'Something went wrong. Try again.';
  }
  function post(url, data){
    return fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)})
      .then(function(r){
        return r.json().catch(function(){ return null; }).then(function(body){
          if (!r.ok) { var er = new Error(errorText(body, r)); er.status = r.status; throw er; }
          return body || {};
        });
      }, function(){ throw new Error('Can’t reach My Sign. Check your connection and try again.'); });
  }
  function body(extra){
    var b = {}; b[pending.kind] = pending.value;
    var a = anon(); if (a) b.anon_id = a;
    for (var k in extra) b[k] = extra[k];
    return b;
  }
  function save(p){ try { p ? sessionStorage.setItem(KEY, JSON.stringify({kind: p.kind, value: p.value}))
                            : sessionStorage.removeItem(KEY); } catch (e) {} }
  // Also reads what an older app wrote: {id, kind}, or a bare email string.
  function load(){
    try {
      var raw = sessionStorage.getItem(KEY);
      if (!raw) return null;
      var p = raw.charAt(0) === '{' ? JSON.parse(raw) : {kind: 'email', value: raw};
      if (p && typeof p.value !== 'string') p = {kind: p.kind, value: p.id};
      if (p && (p.kind === 'email' || p.kind === 'phone') && typeof p.value === 'string' && p.value) return p;
    } catch (e) {}
    return null;
  }
  function display(p){
    if (p.kind === 'phone') { var d = p.value.replace(/\D/g, '').replace(/^1/, ''); return d.length === 10 ? fmtUS(d) : p.value; }
    return p.value;
  }

  function cooldown(){
    var left = COOLDOWN, btn = $('#sResend'), w = $('#sWait');
    function tick(){
      if (left <= 0) { clearInterval(waitT); btn.disabled = false; w.textContent = ''; return; }
      w.textContent = 'in 0:' + (left < 10 ? '0' : '') + left;
      left--;
    }
    clearInterval(waitT);
    btn.disabled = true;
    tick();
    waitT = setInterval(tick, 1000);
  }
  function webOtp(){
    if (!('OTPCredential' in window) || !pending || pending.kind !== 'phone') return;
    if (otpAbort) otpAbort.abort();
    otpAbort = new AbortController();
    navigator.credentials.get({otp: {transport: ['sms']}, signal: otpAbort.signal})
      .then(function(c){
        if (c && c.code && pending) { codeIn.value = String(c.code).replace(/\D/g, '').slice(0, 6); validate(); if (codeIn.value.length === 6) verify(); }
      }).catch(function(){});
  }

  function toCodeStep(p, devCode){
    pending = p; save(p);
    $('#sStep1').hidden = true; $('#sStep2').hidden = false;
    var sent = $('#sSent'), b = document.createElement('b'), chg = document.createElement('button');
    b.textContent = display(p);
    chg.type = 'button'; chg.className = 'link'; chg.id = 'sChange';
    chg.textContent = 'Use a different ' + (smsOk ? 'phone or email' : p.kind === 'phone' ? 'number' : 'email');
    chg.onclick = toIdStep;
    sent.textContent = 'Code sent to ';
    sent.append(b, ' · ', chg);
    go.textContent = 'Continue'; go.dataset.track = 'code_submit';
    if (devCode) { dev.hidden = false; dev.textContent = 'No ' + (p.kind === 'phone' ? 'SMS' : 'mail') + ' configured — your code is ' + devCode; }
    validate();
    codeIn.focus();
    webOtp();
  }
  function toIdStep(){
    pending = null; save(null);
    if (otpAbort) { otpAbort.abort(); otpAbort = null; }
    clearInterval(waitT);
    $('#sStep2').hidden = true; $('#sStep1').hidden = false;
    codeIn.value = ''; dev.hidden = true; err.textContent = ''; status.textContent = '';
    go.textContent = 'Send me a code'; go.dataset.track = 'start_submit';
    validate();
    idIn.focus();
  }

  function request(){
    var p = validate();
    if (!p || !p.ok) return;
    busy = true; go.disabled = true; err.textContent = '';
    pending = {kind: p.kind, value: p.value};
    post('/api/auth/request', body({}))
      .then(function(r){
        var ch = r.channel || (p.kind === 'phone' ? 'sms' : 'email');
        track('tap', 'start_code_sent', {channel: ch});
        busy = false;
        toCodeStep(pending, r.dev_code);
        cooldown();
      })
      .catch(function(e){ pending = null; busy = false; err.textContent = e.message; validate(); });
  }
  function verify(){
    if (busy || !pending || codeIn.value.length !== 6) return;
    busy = true; go.disabled = true; err.textContent = '';
    var p = pending;
    post('/api/auth/verify', body({code: codeIn.value}))
      .then(function(){
        save(null);
        track('tap', 'start_verified', {channel: p.kind === 'phone' ? 'sms' : 'email'});
        track('tap', 'code_verified', {channel: p.kind === 'phone' ? 'sms' : 'email'});
        if (window.MsTrack) window.MsTrack.flush();
        status.textContent = 'Signed in. Opening My Sign…';
        location.assign('/app' + location.search);
      })
      .catch(function(e){ busy = false; err.textContent = e.message; codeIn.select(); validate(); });
  }
  $('#sResend').addEventListener('click', function(){
    if (!pending) return;
    err.textContent = ''; status.textContent = '';
    post('/api/auth/request', body({}))
      .then(function(r){
        status.textContent = 'New code sent.';
        codeIn.value = ''; codeIn.focus(); validate(); cooldown(); webOtp();
        if (r.dev_code) dev.textContent = 'No ' + (pending.kind === 'phone' ? 'SMS' : 'mail') + ' configured — your code is ' + r.dev_code;
      })
      .catch(function(e){ err.textContent = e.message; });
  });
  form.addEventListener('submit', function(e){
    e.preventDefault();
    if (pending) verify(); else request();
  });

  function open(m, from){
    mode = m; opener = from || document.activeElement;
    $('#sTitle').innerHTML = m === 'signin' ? 'Welcome <em>back.</em>'
      : m === 'save' ? 'Get your full <em>reading.</em>'
      : m === 'ask' ? 'Get your <em>answer.</em>' : 'Make your <em>chart.</em>';
    setLede();
    err.textContent = ''; status.textContent = '';
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    var resume = !pending && load();
    if (resume) toCodeStep(resume);
    else (pending ? codeIn : idIn).focus();
    channels();
  }
  function close(){
    if (otpAbort) { otpAbort.abort(); otpAbort = null; }
    if (dlg.open) dlg.close();
  }
  dlg.addEventListener('close', function(){ if (opener && opener.focus) opener.focus(); });
  dlg.addEventListener('click', function(e){ if (e.target === dlg) close(); });
  $$('[data-close]', dlg).forEach(function(b){ b.addEventListener('click', close); });
  $$('[data-open]').forEach(function(b){
    b.addEventListener('click', function(){ open(b.dataset.open, b); });
  });
  // Warm the channel check after first paint, so the label is right by the time she taps.
  setTimeout(channels, 1200);
  window.MsStart = {open: open, close: close, parse: parse};

  /* ---------- the free reading ----------
     POST /api/free-reading -> {signs, time_known, sections, locked}. Nothing is
     saved on our side. sessionStorage "ms_free_read" holds what she typed so
     the app can fill in its form after she signs up, in this tab only. Once she
     reads someone else nothing is kept: that chart must never become her account. */
  var free = $('#free');
  if (!free) return;
  var fCity = $('#frCity'), fBox = $('#frAc'), fErr = $('#frErr'), fGo = $('#frGo'),
      fRes = $('#freeRes'), cityId = null, cityT = null, citySeq = 0, shareLine = '', forSomeoneElse = false;
  var SHARE_URL = location.origin + '/?utm_source=share&utm_medium=free_reading';

  function fold(t){
    return String(t || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  }
  function cityRows(rows){
    fBox.textContent = '';
    rows.forEach(function(c){
      var b = document.createElement('button');
      b.type = 'button'; b.textContent = c.label;
      // Two towns can share a label; the detail is what tells them apart.
      if (c.detail) { var sm = document.createElement('small'); sm.textContent = c.detail; b.append(sm); }
      b.addEventListener('click', function(){
        fCity.value = c.detail ? c.label + ' (' + c.detail + ')' : c.label; cityId = c.id; fBox.hidden = true; fErr.textContent = '';
      });
      fBox.append(b);
    });
    if (!rows.length) {
      var p = document.createElement('p');
      p.textContent = 'I can’t find that one. Try the nearest bigger town.';
      fBox.append(p);
    }
    fBox.hidden = false;
  }
  function findCities(q){
    return fetch('/api/cities?q=' + encodeURIComponent(q), {credentials: 'same-origin'})
      .then(function(r){ return r.ok ? r.json() : []; }).catch(function(){ return []; });
  }
  fCity.addEventListener('input', function(){
    cityId = null; clearTimeout(cityT);
    var q = fCity.value.trim(), mine = ++citySeq;
    if (q.length < 2) { fBox.hidden = true; return; }
    cityT = setTimeout(function(){
      findCities(q).then(function(rows){ if (mine === citySeq) cityRows(rows); });
    }, 140);
  });
  fCity.addEventListener('keydown', function(e){
    if (e.key === 'Escape') fBox.hidden = true;
    var first = $('button', fBox);
    if (e.key === 'ArrowDown' && !fBox.hidden && first) { e.preventDefault(); first.focus(); }
  });
  fBox.addEventListener('keydown', function(e){
    var at = document.activeElement;
    if (e.key === 'ArrowDown' && at.nextElementSibling) { e.preventDefault(); at.nextElementSibling.focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); (at.previousElementSibling || fCity).focus(); }
    if (e.key === 'Escape') { fBox.hidden = true; fCity.focus(); }
  });
  document.addEventListener('click', function(e){
    if (!e.target.closest || !e.target.closest('.acwrap')) fBox.hidden = true;
  });
  $('.acwrap', free).addEventListener('focusout', function(){
    setTimeout(function(){ if (!$('.acwrap', free).contains(document.activeElement)) fBox.hidden = true; }, 200);
  });

  /* ---------- the card ----------
     One line of the reading as a 1080x1920 image for a Story. Drawn here, so
     nothing about the reading goes anywhere unless she shares the file herself,
     and it never carries a birth date, a time or a place. */
  var CARD_W = 1080, CARD_H = 1920, CARD_FILL = 860, CARD_TOP = 400, CARD_BOTTOM = 1370, CARD_FONT_WAIT = 2500;
  var MINE = {toxic_trait: 'My toxic trait', in_love: 'Me in love', when_mad: 'Me when I’m mad',
              at_work: 'Me at work', misread: 'What people get wrong about me'};

  function wrapText(ctx, text, width){
    var out = [], line = '';
    text.split(/\s+/).forEach(function(w){
      var next = line ? line + ' ' + w : w;
      if (line && ctx.measureText(next).width > width) { out.push(line); line = w; }
      else line = next;
    });
    if (line) out.push(line);
    return out;
  }
  function spaced(ctx, on){
    // Older Safari has no canvas letterSpacing; the card is still correct without it.
    if ('letterSpacing' in ctx) ctx.letterSpacing = on;
  }
  function drawCard(section, sign, label){
    var c = document.createElement('canvas'), ctx = c.getContext('2d'), i;
    c.width = CARD_W; c.height = CARD_H;
    var mid = CARD_W / 2;
    ctx.fillStyle = '#0F0B0D';
    ctx.fillRect(0, 0, CARD_W, CARD_H);
    var glow = ctx.createRadialGradient(mid, 800, 60, mid, 800, 1120);
    glow.addColorStop(0, '#5A1A2C'); glow.addColorStop(.55, '#2A0F18'); glow.addColorStop(1, 'rgba(15,11,13,0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, CARD_W, CARD_H);
    // The same sky for the same line, so a card never changes between taps.
    var seed = 7;
    for (i = 0; i < section.text.length; i++) seed = (seed * 31 + section.text.charCodeAt(i)) % 2147483647;
    function rnd(){ seed = seed * 48271 % 2147483647; return seed / 2147483647; }
    for (i = 0; i < 190; i++) {
      var big = rnd();
      ctx.fillStyle = 'rgba(243,234,226,' + (.18 + rnd() * .6).toFixed(2) + ')';
      ctx.beginPath();
      ctx.arc(rnd() * CARD_W, rnd() * CARD_H, big > .9 ? 2.6 : 1.3, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#F3EAE2';
    ctx.font = 'italic 500 64px Newsreader, Georgia, serif';
    ctx.fillText('My Sign', mid, 330);

    // The longest line a reading can give still fits: the type shrinks to hold it.
    var size = 108, lines, lead;
    do {
      size -= 4;
      ctx.font = '400 ' + size + 'px Newsreader, Georgia, serif';
      lines = wrapText(ctx, section.text, CARD_FILL);
      lead = Math.round(size * 1.16);
    } while (size > 44 && lines.length * lead > 740);
    // Label, line and sign sit as one block, centred above the sign-off.
    var block = 50 + size + (lines.length - 1) * lead + 96;
    var labelY = Math.round(CARD_TOP + (CARD_BOTTOM - CARD_TOP - block) / 2);

    ctx.fillStyle = '#E6CFA8';
    ctx.font = '700 34px Geist, "Helvetica Neue", Arial, sans-serif';
    spaced(ctx, '5px');
    ctx.fillText(label.toUpperCase(), mid, labelY);
    spaced(ctx, '0px');

    ctx.fillStyle = '#FFFAF4';
    ctx.font = '400 ' + size + 'px Newsreader, Georgia, serif';
    var y = labelY + 50 + size;
    lines.forEach(function(ln, n){ ctx.fillText(ln, mid, y + n * lead); });
    y += (lines.length - 1) * lead;

    if (sign) {
      ctx.fillStyle = '#E7A79C';
      ctx.font = '500 42px Geist, "Helvetica Neue", Arial, sans-serif';
      ctx.fillText(sign, mid, y + 96);
    }

    ctx.fillStyle = '#F3EAE2';
    ctx.font = 'italic 400 58px Newsreader, Georgia, serif';
    ctx.fillText('What does yours say?', mid, 1440);
    ctx.fillStyle = '#F3EAE2';
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(mid - 230, 1480, 460, 84, 42) : ctx.rect(mid - 230, 1480, 460, 84);
    ctx.fill();
    ctx.fillStyle = '#1E1518';
    ctx.font = '700 36px Geist, "Helvetica Neue", Arial, sans-serif';
    ctx.fillText('mysignapp.com', mid, 1535);
    return c;
  }
  function cardFile(section, sign, label){
    // Drawn before anything is tapped: a phone only opens its share sheet from
    // the tap itself, and a wait for fonts or a canvas can use that up. A font
    // that stalls only costs the typeface, so it doesn't get to hold up the card.
    var fonts = document.fonts && document.fonts.load
      ? Promise.race([
          Promise.all(['italic 500 64px Newsreader', '400 64px Newsreader', '700 34px Geist', '500 42px Geist']
            .map(function(f){ return document.fonts.load(f); })),
          new Promise(function(done){ setTimeout(done, CARD_FONT_WAIT); })
        ]).catch(function(){})
      : Promise.resolve();
    return fonts.then(function(){
      return new Promise(function(done, fail){
        var c = drawCard(section, sign, label);
        c.toBlob(function(blob){
          if (!blob) return fail(new Error('no image'));
          done({
            file: new File([blob], 'my-sign-' + section.key.replace(/_/g, '-') + '.png', {type: 'image/png'}),
            canvas: c
          });
        }, 'image/png');
      });
    });
  }
  function saveFile(file){
    var url = URL.createObjectURL(file), a = document.createElement('a');
    a.href = url; a.download = file.name;
    document.body.append(a); a.click(); a.remove();
    setTimeout(function(){ URL.revokeObjectURL(url); }, 10000);
  }
  function cardButton(section, signs, other){
    var wrap = document.createElement('div'), btn = document.createElement('button'),
        said = document.createElement('p'), ready = null, drawing = false, shown = null;
    var label = (!other && MINE[section.key]) || section.title;
    wrap.className = 'frcard';
    btn.type = 'button'; btn.className = 'ghost';
    btn.dataset.track = 'free_card_' + section.key;
    btn.textContent = 'Share this card';
    btn.setAttribute('aria-label', 'Share this card: ' + section.title);
    said.className = 'hint'; said.setAttribute('role', 'status');
    function prepare(){
      if (drawing) return;
      drawing = true;
      cardFile(section, signs[0] || '', label).then(function(made){
        ready = made; btn.dataset.ready = '1';
      }).catch(function(){}).then(function(){ drawing = false; });
    }
    // In-app browsers, Instagram's among them, can't share a file and often drop
    // a download without a word, so a phone gets the image itself to hold and save.
    function offer(){
      if (!window.matchMedia('(pointer: coarse)').matches) {
        saveFile(ready.file);
        said.textContent = 'Check your downloads, then post it to your Story.';
        return;
      }
      if (!shown) {
        shown = document.createElement('img');
        shown.className = 'frcardimg';
        shown.alt = label + ': ' + section.text;
        // Built here, not with the file: most phones share and never need it.
        shown.src = ready.canvas.toDataURL('image/png');
        wrap.insertBefore(shown, said);
      }
      said.textContent = 'Press and hold the image to save it. If nothing happens, open this page in your browser.';
    }
    prepare();
    btn.addEventListener('click', function(){
      track('tap', 'share_card', {section: section.key});
      said.textContent = '';
      if (!ready) {
        prepare();
        said.textContent = 'One second, still drawing it. Tap again.';
        return;
      }
      if (navigator.canShare && navigator.share && navigator.canShare({files: [ready.file]})) {
        navigator.share({files: [ready.file]}).catch(function(e){
          if (!e || e.name !== 'AbortError') offer();
        });
        return;
      }
      offer();
    });
    wrap.append(btn, said);
    return wrap;
  }

  function showReading(r){
    $('#frSigns').textContent = r.signs.join(' · ');
    var list = $('#frList');
    list.textContent = '';
    r.sections.forEach(function(s){
      var li = document.createElement('li'), lab = document.createElement('p'),
          q = document.createElement('q'), why = document.createElement('p');
      li.className = 'card';
      lab.className = 'lab'; lab.textContent = s.title;
      q.textContent = s.text;
      why.className = 'why'; why.textContent = s.because;
      li.append(lab, q, why, cardButton(s, r.signs, forSomeoneElse));
      list.append(li);
    });
    $('#frLocked').textContent = r.locked.join(', ').toLowerCase();
    shareLine = r.sections.length ? r.sections[0].text : '';
    mail.hidden = forSomeoneElse;
    $('#frShared').textContent = '';
    fRes.hidden = false;
    fRes.scrollIntoView({block: 'start'});
    $('#h-free').focus({preventScroll: true});
    track('tap', 'reading_view');
    watchBottom();
  }
  // The email card closes the reading; reaching it is the scroll-depth signal.
  var bottomSent = false;
  function watchBottom(){
    var card = $('#frMail');
    if (bottomSent || !card || !('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function(es){
      if (!es.some(function(e){ return e.isIntersecting; })) return;
      bottomSent = true; io.disconnect();
      track('tap', 'reading_scroll_bottom');
    }, {threshold: 0.5});
    io.observe(card);
  }

  /* ---------- steps: birthday, then the rest ---------- */
  var fDate = $('#frDate'), fTime = $('#frTime'), fMini = $('#frMini'), fMore = $('#frMore'),
      fDeep = $('#frDeep'), step = 1, READ_KEY = 'mysign.reading.v1';
  function saved(){
    try {
      var v = JSON.parse(localStorage.getItem(READ_KEY) || 'null');
      return v && v.v === 1 && v.date && v.city && v.reading && v.reading.sections ? v : null;
    } catch (e) { return null; }
  }
  function keep(v){ try { localStorage.setItem(READ_KEY, JSON.stringify(v)); } catch (e) {} }
  function unkeep(){ try { localStorage.removeItem(READ_KEY); } catch (e) {} }
  function toStep1(){
    step = 1; fGo.hidden = false; fMini.hidden = true; fMore.hidden = true;
  }
  function toStep2(g){
    step = 2; fGo.hidden = true;
    var has = g && g.section;
    fMini.hidden = !has;
    if (has) {
      $('#frMiniLab').textContent = g.signs[0] + ' · a first look';
      $('#frMiniText').textContent = g.section.text;
      $('#frMiniWhy').textContent = g.section.because;
      track('tap', 'mini_reading_view');
    }
    $('#frMoreH').textContent = has ? 'Now the rest of your chart.' : 'Now where you were born.';
    fMore.hidden = false;
    fMore.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    fCity.focus({preventScroll: true});
  }
  function submitBirthday(){
    fErr.textContent = '';
    var date = fDate.value;
    if (!date) { fErr.textContent = 'Add your birthday first.'; fDate.focus(); return; }
    track('tap', 'form_birthday_submit');
    fGo.disabled = true; fGo.textContent = 'Reading…';
    var a = anon(), data = {date: date};
    post('/api/free-glimpse', a ? Object.assign({anon_id: a}, data) : data).then(function(g){
      fGo.disabled = false; fGo.textContent = 'Get my reading';
      toStep2(g);
    }).catch(function(e){
      fGo.disabled = false; fGo.textContent = 'Get my reading';
      // A real no (too young, not a date) is final. Anything else must not block: go on without the first look.
      if (e.status === 400 || e.status === 403) { fErr.textContent = e.message; return; }
      toStep2(null);
    });
  }
  fDate.addEventListener('change', function(){ if (step === 2) toStep1(); });
  $('#frSkip').addEventListener('click', function(){
    fTime.value = '';
    if (fCity.value.trim()) submitFree(true); else fCity.focus();
  });
  function submitFree(skipped){
    fErr.textContent = '';
    var date = fDate.value, time = fTime.value;
    if (!date) { fErr.textContent = 'Add your birthday first.'; fDate.focus(); return; }
    var typed = fCity.value.trim();
    if (!typed) { fErr.textContent = 'Add the city you were born in.'; fCity.focus(); return; }
    clearTimeout(cityT); citySeq++;
    track('tap', 'form_details_submit', {time_skipped: !time});
    fDeep.disabled = true; fDeep.textContent = 'Reading your chart…';
    var done = function(){ fDeep.disabled = false; fDeep.textContent = 'Go deeper'; };
    // Typed a whole name and never tapped the list: fine if only one place fits.
    var ready = cityId ? Promise.resolve(cityId) : findCities(typed).then(function(rows){
      var low = fold(typed),
          exact = rows.filter(function(c){ return !c.fuzzy && (fold(c.name) === low || fold(c.label) === low); });
      if (exact.length === 1) { fCity.value = exact[0].label; cityId = exact[0].id; return cityId; }
      cityRows(rows);
      return null;
    });
    ready.then(function(id){
      if (!id) { fErr.textContent = 'Pick your city from the list so the time zone is right.'; done(); return; }
      var a = anon(), data = {date: date, time: time, city: id};
      return post('/api/free-reading', a ? Object.assign({anon_id: a}, data) : data).then(function(r){
        if (!forSomeoneElse) {
          try { sessionStorage.setItem('ms_free_read', JSON.stringify({date: date, time: time, city: id, label: fCity.value})); } catch (e) {}
          // On this device only: the server never sees it again. Clear removes it.
          keep({v: 1, date: date, time: time, city: id, label: fCity.value, reading: r, at: Date.now()});
        }
        track('tap', 'free_shown');
        done();
        showReading(r);
      });
    }).catch(function(e){ fErr.textContent = e.message; done(); });
  }
  free.addEventListener('submit', function(e){ e.preventDefault(); if (step === 1) submitBirthday(); else submitFree(); });
  $('#frSave').addEventListener('click', function(){ open(forSomeoneElse ? 'start' : 'save', this); });
  // Her first question, kept on this device until the account exists; the app
  // asks it for her straight after onboarding (askPendingQuestion).
  function ask(q, btn){
    q = (q || '').trim();
    if (!q) { $('#frQ').focus(); return; }
    // Stamped, so a question nobody signed up for doesn't sit in the browser
    // and get sent from whichever account opens the app here next.
    try { localStorage.setItem('ms_pending_q', JSON.stringify({q: q.slice(0, 400), at: Date.now()})); } catch (e) {}
    open('ask', btn);
  }
  $('#frAsk').addEventListener('submit', function(e){ e.preventDefault(); ask($('#frQ').value, $('#frAskGo')); });
  $('#frChips').addEventListener('click', function(e){
    var b = e.target.closest('button'); if (!b) return;
    $('#frQ').value = b.textContent; ask(b.textContent, b);
  });
  $('#frAgain').addEventListener('click', function(){
    forSomeoneElse = true;
    try { sessionStorage.removeItem('ms_free_read'); } catch (e) {}
    $('#frSave').textContent = 'Make my own chart';
    $('#frAsk').hidden = true;
    $('#h-free').innerHTML = 'This is <em>them.</em>';
    free.reset(); cityId = null; fRes.hidden = true; fErr.textContent = ''; toStep1();
    free.scrollIntoView({block: 'center'});
    fDate.focus({preventScroll: true});
  });

  /* ---------- the email card, and the way back to a saved reading ---------- */
  var mail = $('#frMail');
  mail.addEventListener('submit', function(e){
    e.preventDefault();
    var p = parse($('#frEmail').value), er = $('#frMailErr');
    if (!p.ok || p.kind !== 'email') { er.textContent = 'Enter a valid email address.'; return; }
    er.textContent = '';
    // The sheet opens straight on the code step, with this address already sent to.
    pending = null; save(null);
    open('save', $('#frMailGo'));
    idIn.value = p.value; validate(); request();
  });
  var back = $('#frBack'), mine = saved();
  if (back && mine) back.hidden = false;
  if (back) {
    $('#frBackSee').addEventListener('click', function(){
      var v = saved();
      if (!v) { back.hidden = true; return; }
      forSomeoneElse = false;
      fDate.value = v.date; fTime.value = v.time || ''; fCity.value = v.label || ''; cityId = v.city;
      step = 2; fGo.hidden = true; fMini.hidden = true; fMore.hidden = false;
      try { sessionStorage.setItem('ms_free_read', JSON.stringify({date: v.date, time: v.time || '', city: v.city, label: v.label || ''})); } catch (e) {}
      back.hidden = true;
      showReading(v.reading);
    });
    $('#frBackClear').addEventListener('click', function(){ unkeep(); back.hidden = true; });
  }

  /* ---------- one way in: every "read me" lands on the form ---------- */
  function toForm(){
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    free.scrollIntoView({behavior: calm ? 'auto' : 'smooth', block: 'start'});
    (step === 1 ? fDate : fCity).focus({preventScroll: true});
  }
  $$('a[href="#free"]').forEach(function(a){
    a.addEventListener('click', function(e){ e.preventDefault(); history.replaceState(null, '', '#free'); toForm(); });
  });
  if (location.hash === '#free') setTimeout(function(){ (step === 1 ? fDate : fCity).focus({preventScroll: true}); }, 0);
  track('tap', 'hero_view');

  /* The sticky button shows only once the form has scrolled off, and never over the reading itself. */
  var bar = document.getElementById('stickyBar');   // optional: only longer pages carry one
  if (bar && 'IntersectionObserver' in window) {
    var offscreen = false, others = new Set();
    // ...and steps aside while another "Get my reading" is already on screen, so there are never two.
    var syncBar = function(){ bar.classList.toggle('on', offscreen && fRes.hidden && !others.size); };
    new IntersectionObserver(function(en){ offscreen = !en[0].isIntersecting && en[0].boundingClientRect.top < 0; syncBar(); }).observe(free);
    var seen = new IntersectionObserver(function(en){ en.forEach(function(e){ e.isIntersecting ? others.add(e.target) : others.delete(e.target); }); syncBar(); });
    document.querySelectorAll('main .ctas').forEach(function(el){ seen.observe(el); });
    new MutationObserver(syncBar).observe(fRes, {attributes: true, attributeFilter: ['hidden']});
  }
  $('#frShare').addEventListener('click', function(){
    var text = '“' + shareLine + '” My Sign read my chart and I feel seen. What does yours say?';
    var said = $('#frShared');
    if (navigator.share) {
      navigator.share({text: text, url: SHARE_URL}).catch(function(){});
    } else if (navigator.clipboard) {
      navigator.clipboard.writeText(text + ' ' + SHARE_URL).then(function(){ said.textContent = 'Copied. Paste it to a friend.'; },
        function(){ said.textContent = SHARE_URL; });
    } else {
      said.textContent = SHARE_URL;
    }
  });
})();
