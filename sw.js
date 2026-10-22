// San Vicente Weather service worker: Web Push notifications
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('push',e=>{let d={};try{d=e.data?e.data.json():{}}catch(_){d={body:e.data&&e.data.text()}}
 const title=d.title||'San Vicente Weather',opt={body:d.body||'',icon:'icon-192.png',badge:'icon-192.png',tag:d.tag||'sv-wind',renotify:true,data:{url:d.url||'./'}};
 e.waitUntil(Promise.all([self.registration.showNotification(title,opt),self.clients.matchAll({includeUncontrolled:true}).then(cs=>cs.forEach(c=>c.postMessage({svPush:d})))]))});
self.addEventListener('notificationclick',e=>{e.notification.close();const url=new URL(e.notification.data&&e.notification.data.url||'./',self.registration.scope).href;
 e.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(cs=>{for(const c of cs){if(c.url.startsWith(self.registration.scope)&&'focus' in c)return c.focus()}return self.clients.openWindow(url)}))});
