// Viewer-request router for admin.<domain> (cloudfront-js-2.0).
//
//   /                      sign in, then pick an area        -> app  /admin
//   /manage  /analytics    the operator console              -> app
//   /social/...            the social media tool             -> its CloudFront, prefix removed
//   /api/ops/*, and the two admin sign-in calls              -> app
//
// Everything else is a 404 here: this host must not be a second front door to
// the consumer app or its sign-in.

var PAGES = { '/manage': 1, '/analytics': 1 };
var CALLS = { '/api/auth/request-admin': 1, '/api/auth/verify-admin': 1 };

function handler(event) {
  var req = event.request;
  var uri = req.uri;

  if (uri === '/social' || uri.indexOf('/social/') === 0) {
    if (uri === '/social') {
      return { statusCode: 302, statusDescription: 'Found',
               headers: { location: { value: '/social/' }, 'cache-control': { value: 'no-store' } } };
    }
    // Tells the tool's own edge this came through here, so it serves instead of
    // redirecting back. Routing only; its API checks the session itself.
    req.headers['x-admin-edge'] = { value: '1' };
    req.uri = uri.slice('/social'.length);
    return req;
  }

  var page = uri.replace(/\/$/, '');
  if (uri === '/') {
    req.uri = '/admin';
    return req;
  }
  if (PAGES[page]) {
    req.uri = page;
    return req;
  }
  if (CALLS[uri] || uri.indexOf('/api/ops/') === 0) {
    return req;
  }
  return { statusCode: 404, statusDescription: 'Not Found',
           headers: { 'cache-control': { value: 'no-store' } } };
}
