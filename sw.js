// San Vicente Weather service worker: Web Push notifications (wind alerts)
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
const post=m=>self.clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>cs.forEach(c=>c.postMessage(m)));
self.addEventListener('push',e=>{let d={};try{d=e.data?e.data.json():{}}catch(_){d={body:e.data&&e.data.text()}}
 const title=d.title||'💨 Windy for spraying',opt={body:d.body||'',icon:'icon-192.png',badge:'icon-192.png',tag:d.tag||'sv-wind',renotify:true,requireInteraction:true,silent:false,
  vibrate:[400,150,400,150,800,300,400,150,400],timestamp:Date.now(),data:{url:d.url||'./',sound:d.sound||'wind-gust.mp3'}};
 e.waitUntil(Promise.all([self.registration.showNotification(title,opt),post({svPush:d,play:true})]))});
self.addEventListener('notificationclick',e=>{e.notification.close();const nd=e.notification.data||{},url=new URL(nd.url||'./',self.registration.scope);url.searchParams.set('svsound','1');
 e.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs){if(c.url.startsWith(self.registration.scope)&&'focus' in c){c.postMessage({svPush:{},play:true,click:true});return c.focus()}}return self.clients.openWindow(url.href)}))});
