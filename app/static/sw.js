/* Service worker.
 *
 * Two jobs. Push — iOS will not deliver a notification to a site that has not
 * been installed, and installation requires this file. And the offline shell:
 * this is a phone app people open on trains and in lifts, and a blank white page
 * is a worse answer than "you're offline".
 *
 * The shell is cached; the API never is. Stale astrology would be worse than
 * none — a cached reading is about a day that has already ended.
 */
const VERSION = 'mysign-v3';
// The app lives at /app; / is the website. The worker's scope is still the
// whole origin, because push subscriptions belong to the registration and
// every one made before the move was made at /.
const APP = '/app';
const SHELL = [
  APP,
  '/static/icons/icon-192.png',
  '/static/icons/apple-touch-icon.png',
  '/manifest.webmanifest',
];
const isApp = path => path === APP || path.startsWith(APP + '/');

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

// Old versions cached the app as '/', which is the website now.
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Never serve the API from cache. A cached reading is about a day that has
  // already ended, and a cached sign-in state is a lie.
  if (url.pathname.startsWith('/api/')) return;

  // Every /app/<view> is the same document, so it is kept once, as the shell.
  const appPage = req.mode === 'navigate' && isApp(url.pathname);
  const key = appPage ? APP : req;
  event.respondWith(
    fetch(req)
      .then(res => {
        // Only a plain answer from here: a redirect (a signed-in / goes to
        // /app) must never be stored under the address that asked for it.
        if (res.ok && res.type === 'basic' && !res.redirected) {
          const copy = res.clone();
          caches.open(VERSION).then(c => c.put(key, copy));
        }
        return res;
      })
      // Offline, the app falls back to its shell. The website does not: it
      // gets its own last copy or the browser's offline page, never the app.
      .catch(() => caches.match(key).then(hit => hit || Response.error()))
  );
});

// Notifications shown before the move carry '/', '/?p=…' or '/?sky=…'.
function appUrl(u){
  const x = new URL(u, self.location.origin);
  if (x.origin !== self.location.origin) return x.href;
  if (x.pathname === '/') x.pathname = APP;
  return x.pathname + x.search + x.hash;
}

self.addEventListener('push', event => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) {}
  event.waitUntil(self.registration.showNotification(d.title || 'My Sign', {
    body: d.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: 'mysign-daily',
    data: { url: d.url || APP },
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = appUrl((event.notification.data && event.notification.data.url) || APP);
  event.waitUntil(clients.matchAll({type:'window', includeUncontrolled:true}).then(list => {
    // An open app gets told where to go; focusing alone dropped the deep link,
    // so a push about one person landed wherever she last was. A website tab
    // cannot follow the message, so it is left alone.
    for (const c of list) if ('focus' in c && isApp(new URL(c.url).pathname)) {
      c.postMessage({type:'open', url});
      return c.focus();
    }
    return clients.openWindow(url);
  }));
});
