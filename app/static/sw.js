const VERSION = 'ichat-v8-brand3';
const SHELL = [
  '/',
  '/chat',
  '/static/css/main.css',
  '/static/js/main.js',
  '/static/img/ichat-mark-v4-192.png?v=b3',
  '/static/img/ichat-mark-v4-192.png?v=b3',
  '/static/img/ichat-mark-v4-512.png?v=b3',
  '/static/img/ichat-mark-v4-apple.png?v=b3',
  '/site.webmanifest?v=ichat-install-v5',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(VERSION)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== VERSION).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Skip non-GET and API/auth requests
  if (e.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/auth/') ||
      url.pathname.startsWith('/v1/')) return;

  e.respondWith(
    fetch(e.request)
      .then(resp => {
        if (resp.ok && url.origin === location.origin) {
          const clone = resp.clone();
          caches.open(VERSION).then(c => c.put(e.request, clone));
        }
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
