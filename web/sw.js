/* sw.js — service worker.
   Stratégie « réseau d'abord » pour l'app et les données : on charge TOUJOURS la dernière
   version quand il y a du réseau (fini l'appli figée sur une vieille version), et on retombe
   sur le cache seulement hors-ligne. Les icônes/manifest (immuables) restent en cache d'abord. */
const CACHE = "dai-coach-v7";
const SHELL = [
  "./",
  "./manifest.webmanifest",
  "./icons/icon-180.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();                 // le nouveau SW prend la main tout de suite
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  const immuable = url.pathname.startsWith("/icons/") || url.pathname.endsWith("manifest.webmanifest");

  if (immuable) {
    e.respondWith(caches.match(req).then((r) => r || fetch(req)));   // cache d'abord
  } else {
    // réseau d'abord (HTML + coach_state.json + citations…), cache en secours hors-ligne
    e.respondWith(
      fetch(req)
        .then(async (r) => {
          // Safari refuse une réponse « redirigée » pour une navigation pilotée par le SW
          // (ex. ASSETS Cloudflare : /index.html → 301 /). On la reconstruit à plat.
          if (r.redirected) {
            const body = await r.arrayBuffer();
            r = new Response(body, { status: r.status, statusText: r.statusText, headers: r.headers });
          }
          // Ne cacher QUE les GET réussis (jamais un 401, ni la page de login) → pas d'erreur collante.
          if (req.method === "GET" && r.ok && r.headers.get("X-Daimon-Auth") !== "login") {
            const copy = r.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return r;
        })
        .catch(() => caches.match(req).then((r) => r || caches.match("./")))
    );
  }
});
