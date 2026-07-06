/* Service worker for qBittorrent Auto-Sorter.
 *
 * Caches only the static app shell so the UI installs and loads offline.
 * API calls (/api/*) are always passed straight to the network — never
 * cached — because this is a control panel over a live backend and stale
 * data would be dangerous. Bump CACHE to invalidate old shells on deploy.
 */
const CACHE = "qbit-sorter-v1";
const SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./favicon.svg",
  "./favicon-32.png",
  "./apple-touch-icon.png",
  "./icon-192.png",
  "./icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                 // only cache reads
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // let cross-origin (backend API) pass through
  if (url.pathname.startsWith("/api/")) return;     // never cache live API responses

  // App navigations: try network first (fresh UI), fall back to the cached shell offline.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("./index.html").then((r) => r || caches.match("./")))
    );
    return;
  }

  // Static assets (icons, manifest): cache-first, then fill the cache on miss.
  event.respondWith(
    caches.match(req).then((hit) =>
      hit ||
      fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
    )
  );
});
