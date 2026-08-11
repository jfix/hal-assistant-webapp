const STATIC_CACHE = "hal-static-{{ asset_version }}";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key.startsWith("hal-static-") && key !== STATIC_CACHE)
      .map((key) => caches.delete(key)),
  )));
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.open(STATIC_CACHE).then(async (cache) => {
      const cached = await cache.match(request);
      if (cached) return cached;
      const response = await fetch(request);
      const responseUrl = new URL(response.url);
      const cacheControl = response.headers.get("Cache-Control") || "";
      if (response.ok && responseUrl.origin === self.location.origin && !/(private|no-store)/i.test(cacheControl)) {
        await cache.put(request, response.clone());
      }
      return response;
    }));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => new Response(
      "<!doctype html><html lang='fr'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Hors connexion</title><style>body{font:18px system-ui;margin:10vh auto;max-width:34rem;padding:1rem;color:#182026;background:#f3f5f2}h1{font-family:Georgia,serif}a{color:#005c53}</style><h1>Connexion indisponible</h1><p>Les notices et documents ne sont pas conservés hors ligne sur cet appareil. Reconnectez-vous pour accéder à l’application.</p><p><a href='/'>Réessayer</a></p></html>",
      {status: 503, headers: {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}},
    )));
  }
});
