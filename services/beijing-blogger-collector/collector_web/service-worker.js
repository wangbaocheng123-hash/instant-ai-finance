const SHELL_CACHE = "north-pole-collector-shell-v5";
const SCOPE_PATH = new URL(self.registration.scope).pathname.replace(/\/$/, "");
const scoped = (path) => `${SCOPE_PATH}${path}` || "/";
const SHELL_FILES = [
  "/", "/index.html", "/styles.css", "/app.js", "/manifest.webmanifest",
  "/apple-touch-icon.png", "/favicon-32.png",
  "/north-pole-collector-icon-192.png", "/north-pole-collector-icon-512.png",
].map(scoped);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(
      names.filter((name) => name !== SHELL_CACHE).map((name) => caches.delete(name)),
    )),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.includes("/api/collector/") || url.pathname.includes("/health")) return;
  if (SHELL_FILES.includes(url.pathname)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request)),
    );
  }
});
