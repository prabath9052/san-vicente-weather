// San Vicente Weather service worker: Web Push notifications (wind alerts)
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE&&k!==LIB).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
// v2: pages are network-first (bypassing the 10-min HTTP cache) so updates show on the next open; cached copy only when offline.
const CACHE='sv-pages-v15';
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET'||r.mode!=='navigate')return;
 e.respondWith(fetch(r,{cache:'no-store'}).then(res=>{if(res.ok){const c=res.clone();caches.open(CACHE).then(x=>x.put(r,c))}return res}).catch(()=>caches.match(r).then(m=>m||Response.error())))});
// Offline view: libraries the app shell needs (Firebase sign-in, Leaflet, Excel export) and the app's own icons are kept
// in a runtime cache (cache-first for versioned CDN files, network-first for same-origin static files). Weather data and
// the private station data are NOT cached here (the page keeps a per-user copy in localStorage and clears it on sign-out).
const LIB='sv-lib-v1',LIBRE=/^https:\/\/(www\.gstatic\.com\/firebasejs\/|unpkg\.com\/leaflet@|cdn\.jsdelivr\.net\/npm\/exceljs@)/,STAT=/\.(png|ico|mp3|webmanifest)$/;
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET'||r.mode==='navigate')return;const u=r.url;
 if(LIBRE.test(u))e.respondWith(caches.open(LIB).then(c=>c.match(r).then(m=>m||fetch(r).then(res=>{if(res.ok||res.type==='opaque')c.put(r,res.clone());return res}))));
 else if(u.startsWith(self.registration.scope)&&STAT.test(new URL(u).pathname))e.respondWith(fetch(r).then(res=>{if(res.ok){const c=res.clone();caches.open(LIB).then(x=>x.put(r,c))}return res}).catch(()=>caches.match(r).then(m=>m||Response.error())))});
const post=m=>self.clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>cs.forEach(c=>c.postMessage(m)));
self.addEventListener('push',e=>{let d={};try{d=e.data?e.data.json():{}}catch(_){d={body:e.data&&e.data.text()}}
  // daily ET0 summary (tag 'eto') is a quiet notification: not sticky, one short buzz; wind/humidity alerts stay on screen with the long pattern
 const quiet=d.tag==='eto'||d.type==='eto';
 const k=(d.type||d.tag||'sv-wind')+'',icon=/health/.test(k)?'icon-192.png':/hum/.test(k)?'icon-humidity.png':/eto/.test(k)?'icon-eto.png':/wind/.test(k)?'icon-wind.png':'icon-192.png';
 const title=d.title||'💨 Wind alert',opt={body:d.body||'',icon,badge:'badge-96.png',tag:d.tag||'sv-wind',renotify:true,requireInteraction:!quiet,silent:false,
  vibrate:quiet?[200]:[400,150,400,150,800,300,400,150,400],timestamp:Date.now(),data:{url:d.url||'./',sound:d.sound||'wind-gust.mp3'}};
 e.waitUntil(Promise.all([self.registration.showNotification(title,opt),post({svPush:d,play:d.sound!=='none'})]))});
self.addEventListener('notificationclick',e=>{e.notification.close();try{self.navigator.clearAppBadge&&self.navigator.clearAppBadge().catch(()=>{})}catch(_){}const nd=e.notification.data||{},url=new URL(nd.url||'./',self.registration.scope);if(nd.sound!=='none')url.searchParams.set('svsound','1');
 e.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs){if(c.url.startsWith(self.registration.scope)&&'focus' in c){c.postMessage({svPush:{},play:nd.sound!=='none',click:true});return c.focus()}}return self.clients.openWindow(url.href)}))});
