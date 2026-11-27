/* Service worker: guarda o aplicativo no celular para abrir mesmo sem conexão com o notebook.
   Só funciona em HTTPS confiável (certificado instalado). Dados e fotos NÃO passam por aqui:
   são guardados pelo próprio aplicativo (IndexedDB) e enviados quando a conexão volta. */
const CACHE = 'frota-v3';
const CASCA = ['/m', '/manifest.webmanifest', '/web/estilo.css', '/web/comum.js', '/web/brasao.png',
  '/web/icon-192.png', '/web/icon-512.png', '/web/fa/css/all.min.css',
  '/web/fa/webfonts/fa-solid-900.woff2', '/web/fa/webfonts/fa-regular-400.woff2', '/web/fa/webfonts/fa-brands-400.woff2'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => Promise.all(CASCA.map(u => c.add(u).catch(() => {})))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
function comTempo(req, ms) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms);
  return fetch(req, {signal: ctl.signal}).finally(() => clearTimeout(t));
}
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  const p = u.pathname;
  if (!(p === '/m' || p === '/manifest.webmanifest' || p.startsWith('/web/'))) return;  // API, fotos, PDFs: rede normal
  // rede primeiro (pega atualizações); sem rede em 4 s, usa a cópia guardada
  e.respondWith(comTempo(e.request, 4000).then(r => {
    if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(e.request, cp)); }
    return r;
  }).catch(() => caches.match(e.request, {ignoreSearch: true}).then(r => r || Response.error())));
});
