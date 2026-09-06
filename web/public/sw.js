// GoldPulse service worker: app shell cache-first (offline-ready),
// data JSON network-first (fresh, fallback ke cache saat offline).
const VERSION = "goldpulse-v1";
const SHELL = "./";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.add(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;

  // Data dari repo: network-first, fallback cache (app tetap terbuka offline).
  if (url.hostname === "raw.githubusercontent.com" || url.pathname.startsWith("/data/")) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((cache) => cache.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Spot live XAUS.com & Google Fonts: biarkan lewat browser (jangan di-cache SW).
  if (url.hostname !== self.location.hostname) return;

  // App shell (HTML/JS/CSS hashed): cache-first.
  event.respondWith(
    caches.match(event.request).then(
      (hit) =>
        hit ||
        fetch(event.request).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
    )
  );
});