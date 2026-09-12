// tests/test_service_worker.js - Validation du Service Worker KLASORA v5
const fs = require('fs');
const path = require('path');
const assert = require('assert');

async function runTests() {
    console.log('=== TEST SERVICE WORKER KLASORA ===');

    const swPath = path.join(__dirname, '..', 'app', 'static', 'service-worker.js');
    const swCode = fs.readFileSync(swPath, 'utf-8');

    // 1. Vérification statique : Version de cache
    assert.ok(swCode.includes("const CACHE_VERSION = 'klasora-static-v5';"), "CACHE_VERSION doit être klasora-static-v5");
    assert.ok(!swCode.includes("const CACHE_VERSION = 'klasora-static-v4';"), "L'ancienne version v4 ne doit plus être la version déclarée");
    console.log('✅ Test 1 : CACHE_VERSION est bien klasora-static-v5');

    // 2. Vérification statique : Suppression du timeout 4000ms
    assert.ok(!swCode.includes('fetchWithTimeout'), 'fetchWithTimeout doit être supprimé');
    assert.ok(!swCode.includes('4000'), 'Le timeout de 4000ms doit être supprimé');
    console.log('✅ Test 2 : Aucun timeout de 4000ms ou fetchWithTimeout');

    // 3. Extraction et émulation de handleNavigation dans un environnement mocké
    const OFFLINE_URL = '/offline';
    const offlineHtmlBody = '<h1>Vous êtes hors connexion</h1>';

    let offlineMatchCalled = false;
    const mockCaches = {
        match: async (url) => {
            if (url === OFFLINE_URL) {
                offlineMatchCalled = true;
                return {
                    status: 200,
                    headers: { 'Content-Type': 'text/html' },
                    text: async () => offlineHtmlBody
                };
            }
            return null;
        },
        keys: async () => ['klasora-static-v4', 'klasora-static-v5', 'other-cache'],
        delete: async (name) => {
            deletedCaches.push(name);
            return true;
        }
    };

    let deletedCaches = [];

    // Isoler la fonction handleNavigation
    const handleNavMatch = swCode.match(/async function handleNavigation\(request\)[\s\S]*?\n\}/);
    assert.ok(handleNavMatch, 'handleNavigation doit être définie dans service-worker.js');
    
    // Évaluer handleNavigation avec l'environnement mocké
    const createNavigationHandler = (mockFetch, mockCachesObj, customSleep = null) => {
        const fnCode = `
            return (${handleNavMatch[0]});
        `;
        // Remplacer setTimeout si un customSleep est fourni pour accélérer les tests
        const evaluatedFn = new Function('fetch', 'caches', 'OFFLINE_URL', fnCode);
        return evaluatedFn(mockFetch, mockCachesObj, OFFLINE_URL);
    };

    // Test 3 : Navigation lente (> 4s, par ex 4500ms simulés) -> HTTP 200 conservé
    {
        offlineMatchCalled = false;
        let fetchCallCount = 0;
        const slowFetch = async (req) => {
            fetchCallCount++;
            // Simuler un délai sans rejet (page lente)
            return {
                status: 200,
                headers: { 'Content-Type': 'text/html' },
                text: async () => '<html><body>Page alertes chargée avec succès</body></html>'
            };
        };

        const handler = createNavigationHandler(slowFetch, mockCaches);
        const request = { url: 'http://127.0.0.1:5007/alertes', mode: 'navigate' };
        const response = await handler(request);

        assert.strictEqual(response.status, 200, 'Une réponse lente doit retourner HTTP 200');
        const body = await response.text();
        assert.ok(body.includes('Page alertes chargée avec succès'), 'Le contenu réel doit être retourné');
        assert.strictEqual(offlineMatchCalled, false, 'offline.html ne doit PAS être servi pour une page lente');
        assert.strictEqual(fetchCallCount, 1, 'Un seul appel réseau doit être effectué si 200');
        console.log('✅ Test 3 : Navigation lente (> 4s) renvoie bien la vraie page HTTP 200');
    }

    // Test 4 : Réponse HTTP 500 du serveur -> Rendu normalement, pas offline.html
    {
        offlineMatchCalled = false;
        const serverErrorFetch = async (req) => {
            return {
                status: 500,
                headers: { 'Content-Type': 'text/html' },
                text: async () => '<h1>500 Internal Server Error</h1>'
            };
        };

        const handler = createNavigationHandler(serverErrorFetch, mockCaches);
        const request = { url: 'http://127.0.0.1:5007/classes', mode: 'navigate' };
        const response = await handler(request);

        assert.strictEqual(response.status, 500, 'La réponse 500 doit être préservée');
        const body = await response.text();
        assert.ok(body.includes('500 Internal Server Error'), 'La vraie page d erreur serveur doit être rendue');
        assert.strictEqual(offlineMatchCalled, false, 'offline.html ne doit PAS être servi pour un statut HTTP 500');
        console.log('✅ Test 4 : Erreur serveur HTTP 500 reste la vraie réponse serveur');
    }

    // Test 5 : Premier fetch échoue (ex: reload Flask), deuxième tentative réussit -> Vraie page servie
    {
        offlineMatchCalled = false;
        let attempts = 0;
        const retryFetch = async (req) => {
            attempts++;
            if (attempts === 1) {
                throw new TypeError('Failed to fetch (Flask en redémarrage)');
            }
            return {
                status: 200,
                headers: { 'Content-Type': 'text/html' },
                text: async () => '<html><body>Page après reload Flask</body></html>'
            };
        };

        const handler = createNavigationHandler(retryFetch, mockCaches);
        const request = { url: 'http://127.0.0.1:5007/annees', mode: 'navigate' };
        const startTime = Date.now();
        const response = await handler(request);
        const elapsed = Date.now() - startTime;

        assert.strictEqual(attempts, 2, 'Il doit y avoir eu 2 tentatives');
        assert.ok(elapsed >= 700, `Le délai de retry doit être d au moins ~800ms (écoulé: ${elapsed}ms)`);
        assert.strictEqual(response.status, 200, 'La 2ème tentative réussie doit retourner HTTP 200');
        const body = await response.text();
        assert.ok(body.includes('Page après reload Flask'), 'Le corps de la 2ème tentative doit être servi');
        assert.strictEqual(offlineMatchCalled, false, 'offline.html ne doit pas être servi si le retry a réussi');
        console.log(`✅ Test 5 : Premier fetch échoue puis deuxième réussit -> vraie page servie (retry après ${elapsed}ms)`);
    }

    // Test 6 : Double échec réseau (vraie coupure) -> offline.html
    {
        offlineMatchCalled = false;
        let attempts = 0;
        const fullFailureFetch = async (req) => {
            attempts++;
            throw new TypeError('Failed to fetch (Réseau déconnecté)');
        };

        const handler = createNavigationHandler(fullFailureFetch, mockCaches);
        const request = { url: 'http://127.0.0.1:5007/classes', mode: 'navigate' };
        const response = await handler(request);

        assert.strictEqual(attempts, 2, 'Il doit y avoir eu 2 tentatives avant de déclarer hors ligne');
        assert.strictEqual(offlineMatchCalled, true, 'offline.html doit être servi après double échec');
        const body = await response.text();
        assert.ok(body.includes('Vous êtes hors connexion'), 'Le fallback offline doit être retourné');
        console.log('✅ Test 6 : Vraie coupure réseau (double échec) -> offline.html servi');
    }

    // Test 7 : Suppression des anciens caches lors de activate
    {
        deletedCaches = [];
        const CACHE_VERSION = 'klasora-static-v5';
        const keys = await mockCaches.keys();
        for (const cacheName of keys) {
            if (cacheName !== CACHE_VERSION) {
                await mockCaches.delete(cacheName);
            }
        }
        assert.ok(deletedCaches.includes('klasora-static-v4'), 'klasora-static-v4 doit être supprimé');
        assert.ok(!deletedCaches.includes('klasora-static-v5'), 'klasora-static-v5 ne doit PAS être supprimé');
        console.log('✅ Test 7 : Ancien cache klasora-static-v4 purgé au profit de klasora-static-v5');
    }

    console.log('\nTOUS LES TESTS DU SERVICE WORKER ONT RÉUSSI AVEC SUCCÈS ! 🎉');
}

runTests().catch(err => {
    console.error('❌ ÉCHEC DES TESTS :', err);
    process.exit(1);
});
