// TechLend Service Worker v4 — Optimized with Stale-While-Revalidate & Smart Caching

const CACHE_NAME = 'techlend-dynamic-cache-v4';

const STATIC_ASSETS = [
  '/',                       // home route
  '/login',
  '/dashboard',
  '/offline.html',
  '/static/css/style.css',
  '/static/js/main.js',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  '/static/images/logo.png'
];

// 🧱 Install — Cache essential assets safely
self.addEventListener('install', event => {
  console.log('[ServiceWorker] Installing new version...');
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return Promise.all(
        STATIC_ASSETS.map(asset =>
          cache.add(asset).catch(err =>
            console.warn('⚠️ Skipped asset (missing or failed):', asset, err)
          )
        )
      );
    })
  );
  self.skipWaiting(); // Activate new version immediately
});

// ♻️ Activate — Clear old caches
self.addEventListener('activate', event => {
  console.log('[ServiceWorker] Activating new version...');
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.map(key => {
          if (key !== CACHE_NAME) {
            console.log('[ServiceWorker] Deleting old cache:', key);
            return caches.delete(key);
          }
        })
      )
    )
  );
  self.clients.claim(); // Start controlling all open pages
});

// 🌍 Fetch — Stale-While-Revalidate strategy
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return; // Skip POST, PUT, etc.

  const url = new URL(event.request.url);

  // Only handle requests from same origin
  if (url.origin !== location.origin) return;

  // ⚠️ Skip caching API routes or admin endpoints
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin/')) {
    return; // Let network handle these
  }

  event.respondWith(
    caches.match(event.request).then(cachedResponse => {
      const networkFetch = fetch(event.request)
        .then(networkResponse => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return networkResponse;
        })
        .catch(() => caches.match('/offline.html')); // Offline fallback

      // Return cached first (instant load), then update silently
      return cachedResponse || networkFetch;
    })
  );
});

// 💬 Listen for SKIP_WAITING messages (for manual updates)
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    console.log('[ServiceWorker] Received SKIP_WAITING message');
    self.skipWaiting();
  }
});
