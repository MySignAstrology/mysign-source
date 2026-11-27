// Viewer-request router for the S3 default behaviour (cloudfront-js-2.0).
//
// `/` is the website for strangers and the app for everyone else: a session
// cookie, or a link carrying app state (an invite, a person, a sky), goes
// straight to /app. Until the website exists the build puts the app's own
// index.html at the root, so a stranger sees exactly what they always did.
//
// /api, /bt, /ops and /e never reach this — they are Lambda's behaviours.

var PAGES = { '/privacy': '/privacy.html', '/terms': '/terms.html', '/refunds': '/refunds.html', '/support': '/support.html', '/cookies': '/cookies.html', '/accuracy': '/accuracy.html', '/ig': '/ig.html', '/check': '/check.html' };
var APP_PARAMS = ['invite', 'p', 'sky'];
// /shop is our own storefront page; Shopify (on its subdomain) does product
// pages and checkout, so deeper /shop/... links redirect there, temporarily.
var SHOP = 'https://shop.mysignapp.com';

// The AWS samples rebuild the query by concatenation: values arrive as sent,
// still percent-encoded, so encoding them again would double it.
function query(qs) {
  var out = [];
  for (var k in qs) {
    var v = qs[k];
    var all = v.multiValue || [v];
    for (var i = 0; i < all.length; i++) {
      out.push(all[i].value === '' ? k : k + '=' + all[i].value);
    }
  }
  return out.length ? '?' + out.join('&') : '';
}

function redirect(location) {
  return {
    statusCode: 302,
    statusDescription: 'Found',
    headers: {
      location: { value: location },
      'cache-control': { value: 'no-store' },
    },
  };
}

function handler(event) {
  var req = event.request;
  var uri = req.uri;

  if (uri === '/') {
    var qs = req.querystring || {};
    var signedIn = req.cookies && req.cookies.ms_session;
    var appLink = APP_PARAMS.some(function (p) { return p in qs; });
    if (signedIn || appLink) {
      return redirect('/app' + query(qs));
    }
    req.uri = '/index.html';
    return req;
  }

  // The app is one document; any extension-less path under /app is a view
  // inside it, and anything with an extension is a real file.
  if (uri === '/app' || uri.indexOf('/app/') === 0) {
    var last = uri.slice(uri.lastIndexOf('/') + 1);
    if (last.indexOf('.') === -1) {
      req.uri = '/app/index.html';
    }
    return req;
  }

  // /shop is the page; /shop/products/x lands on the same product at Shopify,
  // and the query keeps utm tags.
  if (uri === '/shop' || uri === '/shop/') {
    req.uri = '/shop.html';
    return req;
  }
  if (uri.indexOf('/shop/') === 0) {
    return redirect(SHOP + uri.slice(5) + query(req.querystring || {}));
  }

  // The gift guide: /gifts and one page per sign. Its images keep their own paths.
  var gift = uri.match(/^\/gifts(?:\/(aries|taurus|gemini|cancer|leo|virgo|libra|scorpio|sagittarius|capricorn|aquarius|pisces))?\/?$/);
  if (gift) {
    req.uri = '/gifts/' + (gift[1] || 'index') + '.html';
    return req;
  }

  var page = PAGES[uri.replace(/\/$/, '')];
  if (page) {
    req.uri = page;
  }
  return req;
}
