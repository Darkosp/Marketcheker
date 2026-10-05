/* Service worker за Marketchecker.

   Намерата е скромна и чесна: апликацијата да се отвора веднаш и да каже
   нешто разумно кога нема интернет. НЕ се прави прелистување офлајн -
   цените се менуваат секој ден и стара цена е полоша од никаква.

   Две стратегии:
   - Статичните делови (CSS, икони, манифест) одат од кешот, а се
     освежуваат во позадина.
   - Страниците одат од мрежата. Ако мрежата нема, се враќа порака што
     кажува дека податоците се стари, со датумот кога биле зачувани.
*/

const VERSION = "v1";
const SHELL = `marketchecker-shell-${VERSION}`;
const PAGES = `marketchecker-pages-${VERSION}`;

const STATIC = [
  "/static/app.css",
  "/static/icon.svg",
  "/static/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  // Старите верзии на кешот се бришат, за да ажурирање не остави
  // измешани делови од две верзии.
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name !== SHELL && name !== PAGES)
            .map((name) => caches.delete(name))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // HTMX барањата не се кешираат: тие се делчиња, не цели страници.
  if (request.headers.get("HX-Request")) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
  }
});

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL);
  const cached = await cache.match(request);
  const fresh = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || fresh;
}

async function networkFirst(request) {
  const cache = await caches.open(PAGES);
  try {
    const response = await fetch(request);
    if (response.ok) {
      const copy = response.clone();
      // Моментот на зачувување, за да офлајн страницата може да каже
      // колку е стара.
      const stamped = new Headers(copy.headers);
      stamped.set("X-Cached-At", new Date().toISOString());
      cache.put(request, new Response(await copy.blob(), { headers: stamped }));
    }
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (cached) return withStaleNotice(cached);
    return offlinePage();
  }
}

async function withStaleNotice(response) {
  const when = response.headers.get("X-Cached-At");
  const html = await response.text();
  const notice = `<p class="notice" style="margin:12px 16px;">
      Нема интернет — прикажано е последното што беше вчитано${
        when ? ` (${new Date(when).toLocaleString("mk-MK")})` : ""
      }. Цените веројатно се променети.
    </p>`;
  return new Response(html.replace(/(<main[^>]*>)/i, `$1${notice}`), {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

function offlinePage() {
  return new Response(
    `<!doctype html><html lang="mk"><head><meta charset="utf-8">
     <meta name="viewport" content="width=device-width, initial-scale=1">
     <title>Нема интернет</title>
     <link rel="stylesheet" href="/static/app.css"></head>
     <body><main class="content" style="padding:40px 16px;text-align:center;">
       <h1>Нема интернет</h1>
       <p class="subtitle">
         Попустите се менуваат секој ден, па не се чуваат за офлајн.<br>
         Пробај повторно кога ќе има врска.
       </p>
     </main></body></html>`,
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
  );
}
