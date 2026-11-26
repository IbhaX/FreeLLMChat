const VERSION = 'ichat-v1';
const SHELL = [
  '/',
  '/chat',
  '/static/css/main.css',
  '/static/js/main.js',
  '/static/img/icon.svg',
  '/static/img/favicon.png',
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
