// The site's service worker: push notices plus a deliberately tiny offline shell.
//
// Registered only when a reader turns notifications on (lib/push.ts) - nobody else pays
// for a worker, and no cached shell can mask a deploy for readers who never opted in.
// The cache holds exactly one thing: the home page, as a fallback when the network is
// gone. Everything else is network-only, so fresh deploys stay fresh.
//
// Bump the version when the shell strategy changes; activation drops older caches.

const CACHE = 'rs-shell-v1';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.add('/'))
      .catch(() => undefined),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  // Navigations only, network-first: the worker never serves stale pages while online.
  if (event.request.mode !== 'navigate') return;
  event.respondWith(
    fetch(event.request).catch(async () => {
      const cached = await caches.match('/');
      return cached ?? Response.error();
    }),
  );
});

self.addEventListener('push', (event) => {
  let payload = { title: 'ResearchScout', body: 'Fresh reading is ready.', url: '/digests' };
  try {
    payload = { ...payload, ...event.data.json() };
  } catch {
    // A payload that does not parse still deserves a notice.
  }
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      data: { url: payload.url },
      icon: '/icon-192.png',
      badge: '/icon-192.png',
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/digests';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
