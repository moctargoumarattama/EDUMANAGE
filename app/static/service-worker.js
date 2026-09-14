// static/service-worker.js - KLASORA PWA Service Worker
const CACHE_VERSION = 'klasora-static-v6';
const OFFLINE_URL = '/offline';

// Ressources publiques et statiques génériques autorisées en cache
const PRECACHE_ASSETS = [
    '/offline',
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

// Installation du Service Worker
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_VERSION)
            .then(cache => {
                return cache.addAll(PRECACHE_ASSETS.map(url => new Request(url, { cache: 'reload' })))
                    .catch(err => {
                        console.warn('[SW] Pré-cache partiel:', err);
                    });
            })
            .then(() => self.skipWaiting())
    );
});

// Activation et nettoyage des anciens caches (ex: klasora-static-v4)
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_VERSION) {
                        console.log('[SW] Suppression ancien cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Gestionnaire de navigation HTML : Network-First sans timeout artificiel
// Tente fetch(request) normalement (les réponses lentes ou HTTP 4xx/5xx sont rendues telles quelles).
// Si fetch échoue (rejet réseau), fait une seconde tentative après 800ms pour absorber un reload Flask temporaire.
// Uniquement si la 2ème tentative échoue également, bascule sur OFFLINE_URL.
async function handleNavigation(request) {
    try {
        return await fetch(request);
    } catch (firstError) {
        // Pause de 800ms pour laisser le temps à un redémarrage temporaire de Flask
        await new Promise(resolve => setTimeout(resolve, 800));
        try {
            return await fetch(request);
        } catch (secondError) {
            const offlineResponse = await caches.match(OFFLINE_URL);
            if (offlineResponse) {
                return offlineResponse;
            }
            return new Response('Hors connexion. Veuillez vérifier votre accès Internet.', {
                status: 503,
                headers: { 'Content-Type': 'text/plain; charset=utf-8' }
            });
        }
    }
}

// Interception des requêtes réseau
self.addEventListener('fetch', event => {
    const { request } = event;
    const url = new URL(request.url);

    // 1. Ignorer les méthodes non-GET (POST, PUT, DELETE, etc.)
    if (request.method !== 'GET') {
        return;
    }

    // 2. Requêtes de navigation HTML (pages de l'application)
    // Network-First strict sans timeout artificiel, avec retry anti-rebond Flask
    if (request.mode === 'navigate') {
        event.respondWith(handleNavigation(request));
        return;
    }

    // 2.5 Endpoint de connectivité (jamais en cache, réseau direct)
    if (url.pathname === '/api/connectivity') {
        event.respondWith(fetch(request));
        return;
    }

    // 3. Assets statiques (CSS, JS, Images, Polices, CDN) -> Cache First avec rafraîchissement
    const isStaticAsset = (
        url.pathname.startsWith('/static/') ||
        url.host.includes('cdn.jsdelivr.net') ||
        url.host.includes('cdnjs.cloudflare.com') ||
        url.host.includes('fonts.googleapis.com') ||
        url.host.includes('fonts.gstatic.com')
    );

    if (isStaticAsset) {
        event.respondWith(
            caches.match(request).then(cachedResponse => {
                if (cachedResponse) {
                    // Revalidation silencieuse en arrière-plan
                    fetch(request).then(networkResponse => {
                        if (networkResponse && networkResponse.status === 200) {
                            caches.open(CACHE_VERSION).then(cache => {
                                cache.put(request, networkResponse);
                            });
                        }
                    }).catch(() => {/* Hors ligne, ignorer */});
                    return cachedResponse;
                }

                // Si non en cache, aller chercher sur le réseau et mettre en cache
                return fetch(request).then(networkResponse => {
                    if (networkResponse && networkResponse.status === 200) {
                        const responseClone = networkResponse.clone();
                        caches.open(CACHE_VERSION).then(cache => {
                            cache.put(request, responseClone);
                        });
                    }
                    return networkResponse;
                });
            })
        );
        return;
    }

    // 4. Par défaut : Réseau direct sans cache aveugle (API, endpoints dynamiques)
    event.respondWith(
        fetch(request).catch(() => {
            return new Response(JSON.stringify({ error: 'Réseau indisponible' }), {
                status: 503,
                headers: { 'Content-Type': 'application/json' }
            });
        })
    );
});

// Écoute des messages du client
self.addEventListener('message', event => {
    if (!event.data) return;

    if (event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }

    if (event.data.type === 'CLEAR_USER_CACHE') {
        console.log('[SW] Nettoyage session utilisateur demandé');
        // Ne conserve que les assets génériques pré-cachés
        caches.keys().then(keys => {
            keys.forEach(key => {
                if (key !== CACHE_VERSION) {
                    caches.delete(key);
                }
            });
        });
    }
});

// Synchronisation en arrière-plan sécurisée : notifier les fenêtres clientes actives
// Ne jamais expédier directement pendingSync depuis le Service Worker sans connaître l'utilisateur actif
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
