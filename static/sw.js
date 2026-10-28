/* Only static assets and explicitly public catalog responses enter this cache.
 * No background orders, payments, private pages, media uploads or inventory writes.
 */
const CACHE='shamaa-public-v1.0.3';
const SHELL=['/','/offline.html','/static/style.css','/static/app.js','/static/i18n.js','/static/api.js','/static/icons.js','/static/logo.svg','/static/images/placeholder.svg','/static/icons/icon-192.png','/static/icons/icon-512.png',...['arabic','latin'].flatMap(script=>[400,500,700,800].map(weight=>`/static/fonts/tajawal-${script}-${weight}-normal.woff2`))];
self.addEventListener('install',event=>{
 event.waitUntil(caches.open(CACHE)
  .then(cache=>cache.addAll(SHELL.map(url=>new Request(url,{cache:'reload'}))))
  .then(()=>self.skipWaiting()));
});
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('shamaa-public-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
function publicApi(path){return path==='/api/bootstrap'||path==='/api/products'||/^\/api\/products\/\d+$/.test(path)||path==='/api/stores'||/^\/api\/stores\/[a-z0-9-]+$/.test(path);}
async function remember(request,response){
 if(!response.ok||response.type==='opaque'||response.headers.has('set-cookie'))return;
 // Clone synchronously before awaiting cache I/O: the browser may consume the original body.
 const copy=response.clone();
 const cache=await caches.open(CACHE);await cache.put(request,copy);
 const keys=await cache.keys();const catalog=keys.filter(k=>new URL(k.url).pathname.startsWith('/api/'));
 if(catalog.length>40)await cache.delete(catalog[0]);
}
self.addEventListener('fetch',event=>{
 const request=event.request,url=new URL(request.url);
 if(request.method!=='GET'||url.origin!==location.origin)return;
 if(url.pathname.startsWith('/static/')){
  event.respondWith(caches.match(request).then(cached=>cached||fetch(request).then(r=>{event.waitUntil(remember(request,r));return r;})));return;
 }
 if(publicApi(url.pathname)){
  event.respondWith(fetch(request).then(r=>{event.waitUntil(remember(request,r));return r;}).catch(async()=>{
   const cached=await caches.match(request);if(cached)return cached;
   return new Response(JSON.stringify({code:'offline',message:'هذا المنتج لم يُحمّل سابقًا. أعد الاتصال. / Reconnect to load this product.'}),{status:503,headers:{'Content-Type':'application/json'}});
  }));return;
 }
 if(request.mode==='navigate'){
  const publicPage=url.pathname==='/'||url.pathname==='/search'||url.pathname==='/stores'||/^\/(product|store)\//.test(url.pathname);
  event.respondWith(fetch(request).catch(()=>caches.match(publicPage?'/':'/offline.html')));
 }
 // All authenticated API calls and /media bypass this cache, even while offline.
});
