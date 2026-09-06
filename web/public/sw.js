// GoldPulse service worker:
// - navigasi (HTML): network-first -> versi baru selalu menang, cache hanya fallback offline
// - data JSON repo: network-first, fallback cache
// - asset shell (JS/CSS hashed): cache-first
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

  // Navigasi halaman: network-first supaya deploy baru langsung terlihat
  // (asset JS/CSS punya nama hashed, jadi HTML baru otomatis menarik bundle baru).
  if (event.request.mode === "navigate" && url.origin === self.location.origin) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => caches.match(event.request).then((hit) => hit || caches.match(SHELL)))
    );
    return;
  }

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