const SHELL_CACHE = 'instant-ai-shell-v0.27.0';
const SHELL_ASSETS = [
  '/', '/app.js', '/styles.css', '/manifest.webmanifest',
  '/app-icon-192.png', '/app-icon-512.png', '/apple-touch-icon.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/') || url.pathname.startsWith('/media/')) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          void caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match('/'))),
  );
});

self.addEventListener('push', (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = { title: '即时 AI', body: event.data ? event.data.text() : '发现一条重要新消息。' };
  }
  const title = typeof message.title === 'string' ? message.title : '即时 AI';
  const options = {
    body: typeof message.body === 'string' ? message.body : '发现一条重要新消息。',
    icon: '/app-icon-192.png',
    badge: '/app-icon-192.png',
    tag: typeof message.tag === 'string' ? message.tag : 'instant-ai-important',
    data: { url: typeof message.url === 'string' ? message.url : '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/', self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      const existing = clients.find((client) => new URL(client.url).origin === self.location.origin);
      if (existing) {
        return existing.navigate(target).then(() => existing.focus());
      }
      return self.clients.openWindow(target);
    }),
  );
});
