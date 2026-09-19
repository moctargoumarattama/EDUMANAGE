// static/service-worker.js - KLASORA PWA Service Worker
const CACHE_VERSION = 'klasora-static-v9';
const PAGE_CACHE = 'klasora-pages-v9';
const OFFLINE_URL = '/offline';

const PRECACHE_ASSETS = [
    OFFLINE_URL,
    '/manifest.json',
    '/static/manifest.json',
    '/static/css/style.css',
    '/static/js/db.js',
    '/static/js/offline-manager.js',
    '/static/js/offline-forms.js',
    '/static/js/pwa.js',
    '/static/js/main.js',
    '/static/img/logo-klasora.png',
    '/static/img/icons/icon-192x192.png',
    '/static/img/icons/icon-512x512.png',
    '/static/img/icons/icon-maskable-512x512.png',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css'
];

const BYPASS_NAVIGATION_CACHE = [
    '/login',
    '/logout',
    '/google/',
    '/api/',
    '/service-worker.js'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_VERSION)
            .then(cache => cache.addAll(PRECACHE_ASSETS.map(url => new Request(url, { cache: 'reload' }))))
            .catch(err => {
                console.warn('[SW] Pre-cache partiel:', err);
            })
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    const allowedCaches = new Set([CACHE_VERSION, PAGE_CACHE]);
    event.waitUntil(
        caches.keys()
            .then(cacheNames => Promise.all(
                cacheNames.map(cacheName => {
                    if (!allowedCaches.has(cacheName)) {
                        console.log('[SW] Suppression ancien cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                    return Promise.resolve();
                })
            ))
            .then(() => self.clients.claim())
    );
});

function isHtmlNavigation(request) {
    return request.mode === 'navigate' ||
        (request.method === 'GET' && (request.headers.get('accept') || '').includes('text/html'));
}

function shouldCacheNavigation(url) {
    if (url.origin !== self.location.origin) return false;
    return !BYPASS_NAVIGATION_CACHE.some(prefix => url.pathname.startsWith(prefix));
}

function isHtmlResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    return response.ok && contentType.includes('text/html');
}

async function offlineFallback() {
    const offlineResponse = await caches.match(OFFLINE_URL);
    if (offlineResponse) return offlineResponse;

    return new Response(
        '<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hors connexion</title><body><h1>Vous etes hors connexion</h1><p>Connexion Internet requise pour vous authentifier.</p></body></html>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
}

async function clearUserCaches() {
    await caches.delete(PAGE_CACHE);
}

// Network-first pour HTML, avec cache runtime des pages deja visitees.
async function handleNavigation(request) {
    const url = new URL(request.url);

    if (url.pathname === '/logout') {
        await clearUserCaches();
    }

    try {
        const networkResponse = await fetch(request);

        if (shouldCacheNavigation(url) && isHtmlResponse(networkResponse)) {
            const cache = await caches.open(PAGE_CACHE);
            await cache.put(request, networkResponse.clone());
        }

        return networkResponse;
    } catch (firstError) {
        await new Promise(resolve => setTimeout(resolve, 800));

        try {
            const retryResponse = await fetch(request);

            if (shouldCacheNavigation(url) && isHtmlResponse(retryResponse)) {
                const cache = await caches.open(PAGE_CACHE);
                await cache.put(request, retryResponse.clone());
            }

            return retryResponse;
        } catch (secondError) {
            if (url.pathname === '/login') {
                return offlineFallback();
            }

            const cachedResponse = await caches.match(request);
            if (cachedResponse) return cachedResponse;

            const pageCache = await caches.open(PAGE_CACHE);
            const cachedByPath = await pageCache.match(url.pathname);
            if (cachedByPath) return cachedByPath;

            return offlineFallback();
        }
    }
}

self.addEventListener('fetch', event => {
    const { request } = event;
    const url = new URL(request.url);

    if (request.method !== 'GET') {
        return;
    }

    if (isHtmlNavigation(request)) {
        event.respondWith(handleNavigation(request));
        return;
    }

    if (url.pathname === '/api/connectivity') {
        event.respondWith(fetch(request));
        return;
    }

    const isStaticAsset = (
        url.pathname.startsWith('/static/') ||
        url.pathname === '/manifest.json' ||
        url.host.includes('cdn.jsdelivr.net') ||
        url.host.includes('cdnjs.cloudflare.com') ||
        url.host.includes('fonts.googleapis.com') ||
        url.host.includes('fonts.gstatic.com')
    );

    if (isStaticAsset) {
        event.respondWith(
            caches.match(request).then(cachedResponse => {
                if (cachedResponse) {
                    fetch(request).then(networkResponse => {
                        if (networkResponse && networkResponse.status === 200) {
                            caches.open(CACHE_VERSION).then(cache => {
                                cache.put(request, networkResponse);
                            });
                        }
                    }).catch(() => {});
                    return cachedResponse;
                }

                return fetch(request).then(networkResponse => {
                    if (networkResponse && networkResponse.status === 200) {
                        const responseClone = networkResponse.clone();
                        caches.open(CACHE_VERSION).then(cache => {
                            cache.put(request, responseClone);
                        });
                    }
                    return networkResponse;
                }).catch(() => {
                    return caches.match(OFFLINE_URL);
                });
            })
        );
        return;
    }

    event.respondWith(
        fetch(request).catch(() => {
            return new Response(JSON.stringify({ error: 'Reseau indisponible' }), {
                status: 503,
                headers: { 'Content-Type': 'application/json; charset=utf-8' }
            });
        })
    );
});

self.addEventListener('message', event => {
    if (!event.data) return;

    if (event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }

    if (event.data.type === 'CLEAR_USER_CACHE') {
        console.log('[SW] Nettoyage session utilisateur demande');
        event.waitUntil(clearUserCaches());
    }
});

self.addEventListener('sync', event => {
    if (event.tag === 'sync-data') {
        event.waitUntil(notifyClientsToSync());
    }
});

async function notifyClientsToSync() {
    try {
        const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        for (const client of clients) {
            client.postMessage({ type: 'TRIGGER_SYNC' });
        }
    } catch (error) {
        console.warn('[SW] Notification sync clients:', error);
    }
}
