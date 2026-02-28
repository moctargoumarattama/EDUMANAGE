// static/service-worker.js
const CACHE_NAME = 'ecole-app-v1';
const OFFLINE_URL = '/offline';

// Fichiers à mettre en cache immédiatement
const STATIC_CACHE = [
    '/',
    '/static/css/style.css',
    '/offline',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css'
];

// Installation du Service Worker
self.addEventListener('install', event => {
    console.log('🔧 Service Worker: Installation');
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('📦 Mise en cache des ressources statiques');
                return cache.addAll(STATIC_CACHE.map(url => new Request(url, { cache: 'reload' })))
                    .catch(err => console.warn('⚠️ Certaines ressources n\'ont pas pu être mises en cache:', err));
            })
            .then(() => self.skipWaiting())
    );
});

// Activation du Service Worker
self.addEventListener('activate', event => {
    console.log('✅ Service Worker: Activation');
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('🗑️ Suppression ancien cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Interception des requêtes
self.addEventListener('fetch', event => {
    const { request } = event;
    const url = new URL(request.url);

    // Ignorer les requêtes non-GET
    if (request.method !== 'GET') {
        return;
    }

    // Ignorer les requêtes vers des domaines externes (sauf CDN)
    if (url.origin !== location.origin && !url.host.includes('cdn')) {
        return;
    }

    // Stratégie: Network First, puis Cache
    event.respondWith(
        fetch(request)
            .then(response => {
                // Cloner la réponse car elle ne peut être utilisée qu'une fois
                const responseClone = response.clone();
                
                // Mettre en cache les réponses réussies
                if (response.status === 200) {
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(request, responseClone);
                    });
                }
                
                return response;
            })
            .catch(() => {
                // Si le réseau échoue, essayer le cache
                return caches.match(request).then(cachedResponse => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    
                    // Si pas de cache et URL de navigation, afficher page offline
                    if (request.mode === 'navigate') {
                        return caches.match(OFFLINE_URL);
                    }
                    
                    // Sinon, retourner une réponse vide
                    return new Response('Ressource non disponible hors ligne', {
                        status: 503,
                        statusText: 'Service Unavailable'
                    });
                });
            })
    );
});

// Écouter les messages du client
self.addEventListener('message', event => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    
    if (event.data && event.data.type === 'CACHE_URLS') {
        event.waitUntil(
            caches.open(CACHE_NAME).then(cache => {
                return cache.addAll(event.data.urls);
            })
        );
    }
});

// Synchronisation en arrière-plan
self.addEventListener('sync', event => {
    console.log('🔄 Background Sync:', event.tag);
    
    if (event.tag === 'sync-data') {
        event.waitUntil(syncOfflineData());
    }
});

// Fonction de synchronisation des données
async function syncOfflineData() {
    try {
        console.log('🚀 Synchronisation automatique des données...');
        
        // Ouvrir IndexedDB
        const db = await openDatabase();
        const tx = db.transaction(['pendingSync'], 'readonly');
        const store = tx.objectStore('pendingSync');
        const allData = await getAllFromStore(store);
        
        if (allData.length === 0) {
            console.log('ℹ️ Aucune donnée à synchroniser');
            return;
        }
        
        // Envoyer les données au serveur
        const response = await fetch('/api/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(allData)
        });
        
        if (response.ok) {
            // Supprimer les données synchronisées
            const txWrite = db.transaction(['pendingSync'], 'readwrite');
            const storeWrite = txWrite.objectStore('pendingSync');
            await clearStore(storeWrite);
            
            console.log('✅ Synchronisation automatique réussie');
            
            // Notifier les clients
            const clients = await self.clients.matchAll();
            clients.forEach(client => {
                client.postMessage({
                    type: 'SYNC_SUCCESS',
                    count: allData.length
                });
            });
        }
        
    } catch (error) {
        console.error('❌ Erreur synchronisation automatique:', error);
        throw error; // Relancer pour réessayer plus tard
    }
}

// Helpers pour IndexedDB
function openDatabase() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('EcoleDB', 1);
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);
    });
}

function getAllFromStore(store) {
    return new Promise((resolve, reject) => {
        const request = store.getAll();
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);
    });
}

function clearStore(store) {
    return new Promise((resolve, reject) => {
        const request = store.clear();
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve();
    });
}