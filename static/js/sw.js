// MyMoney Tracker service worker.
//
// Scope: caches ONLY same-origin /static/ assets (CSS, JS, icons, manifest)
// so the app shell loads instantly and installs as a PWA. It deliberately
// never caches HTML pages or API responses — those always contain a
// specific user's private financial data, and caching them in a shared
// browser cache would be an easy way to leak that data. There is no
// offline transaction queueing here: writes always require a live network
// request to the server.

const CACHE_NAME = "mymoney-static-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isSameOrigin = url.origin === self.location.origin;
  const isStaticAsset = isSameOrigin && url.pathname.startsWith("/static/");

  if (event.request.method !== "GET" || !isStaticAsset) {
    return; // let the browser handle everything else normally (network-only)
  }

  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response.ok) cache.put(event.request, response.clone());
          return response;
        });
      })
    )
  );
});
