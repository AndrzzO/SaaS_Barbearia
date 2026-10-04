const CACHE='barbearia-publico-v3';
const PUBLICOS=['/static/fila/titulos.ttf','/static/fila/app.css','/static/fila/app.js','/static/fila/referencia.png','/static/fila/icone-192.png','/static/fila/icone-512.png','/static/fila/offline.html'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(PUBLICOS)));self.skipWaiting()});
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('barbearia-publico-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{const url=new URL(event.request.url);if(url.origin!==self.location.origin||event.request.method!=='GET')return;
if(PUBLICOS.includes(url.pathname)){event.respondWith(caches.match(event.request).then(hit=>hit||fetch(event.request)));return;}
if(event.request.mode==='navigate')event.respondWith(fetch(event.request).catch(()=>caches.match('/static/fila/offline.html')));
// APIs, sessões, formulários e dados de clientes nunca entram no cache.
});
