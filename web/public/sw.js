// GoldPulse service worker:
// - navigasi (HTML): network-first -> versi baru selalu menang, cache hanya fallback offline
// - data JSON repo: network-first, fallback cache
// - ikon & manifest: network-first (file tidak hashed — ganti logo tidak
//   boleh tertahan versi lama di cache)
// - asset shell (JS/CSS hashed): cache-first
// - Web Push (VAPID): tampilkan notifikasi payload dari job daily,
//   klik notif -> fokus/buka PWA (URL relatif terhadap scope, mis. "#/analysis")
const VERSION = "goldpulse-v4";
const SHELL = "./";
const NET_FIRST_STATIC =
  /(^|\/)(icon[^/]*\.(svg|png)|apple-touch-icon\.png|manifest\.webmanifest)$/;

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

// ---- Web Push: payload JSON dari analyzer.push (title/body/tag/url) ----

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "GoldPulse", {
      body: data.body || "",
      // tag = notif baru menimpa yang lama (entry & agenda tidak numpuk)
      tag: data.tag || "goldpulse",
      icon: "./icon-192.png",
      badge: "./icon-192.png",
      data: { url: data.url || "./" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const hash = (event.notification.data && event.notification.data.url) || "./";
  // url payload berupa hash route ("#/analysis") — resolve ke scope
  const target = new URL(hash, self.registration.scope).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
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

  // Data dari repo (JSON + PDF via jsDelivr): network-first,
  // fallback cache (app tetap terbuka offline).
  if (
    url.hostname === "raw.githubusercontent.com" ||
    url.hostname === "cdn.jsdelivr.net" ||
    url.pathname.startsWith("/data/")
  ) {
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

  // Ikon & manifest (tanpa hash di nama file): network-first supaya
  // pergantian logo langsung terlihat, cache hanya fallback offline.
  if (NET_FIRST_STATIC.test(url.pathname)) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

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