// tests/test_service_worker.js - Validation du Service Worker KLASORA
const fs = require('fs');
const path = require('path');
const assert = require('assert');

async function runTests() {
    console.log('=== TEST SERVICE WORKER KLASORA ===');

    const swPath = path.join(__dirname, '..', 'app', 'static', 'service-worker.js');
    const swCode = fs.readFileSync(swPath, 'utf-8');

    assert.ok(swCode.includes("const CACHE_VERSION = 'klasora-static-v7';"), 'CACHE_VERSION doit etre v7');
    assert.ok(swCode.includes("const PAGE_CACHE = 'klasora-pages-v7';"), 'PAGE_CACHE doit exister');
    assert.ok(!swCode.includes('fetchWithTimeout'), 'fetchWithTimeout doit etre supprime');
    assert.ok(!swCode.includes('4000'), 'Le timeout de 4000ms doit etre supprime');
    console.log('OK 1 : versions de cache et ancien timeout');

    assert.ok(swCode.includes("request.mode === 'navigate'"), 'Les navigations HTML doivent etre interceptees');
    assert.ok(swCode.includes('async function handleNavigation(request)'), 'handleNavigation doit exister');
    assert.ok(swCode.includes('offlineFallback()'), 'offlineFallback doit etre utilise');
    assert.ok(swCode.includes('await cache.put(request, networkResponse.clone());'), 'Les pages HTML doivent etre cachees apres reponse reseau');
    console.log('OK 2 : navigation network-first avec cache runtime');

    assert.ok(swCode.includes("'/login'"), '/login doit etre exclu du cache de pages');
    assert.ok(swCode.includes("'/logout'"), '/logout doit etre exclu du cache de pages');
    assert.ok(swCode.includes("'/api/'"), '/api doit etre exclu du cache de pages');
    assert.ok(swCode.includes('await caches.delete(PAGE_CACHE)'), 'Le cache utilisateur doit etre purgeable');
    console.log('OK 3 : exclusions securite et purge logout');

    assert.ok(swCode.includes("'/manifest.json'"), 'Le manifest public doit etre precache');
    assert.ok(swCode.includes("'/static/css/style.css'"), 'Le CSS principal doit etre precache');
    assert.ok(swCode.includes("'/static/js/pwa.js'"), 'Le JS PWA doit etre precache');
    assert.ok(swCode.includes("'/static/img/icons/icon-192x192.png'"), 'Icone iOS critique precachee');
    console.log('OK 4 : assets critiques precaches');

    assert.ok(swCode.includes('await new Promise(resolve => setTimeout(resolve, 800));'), 'Retry court anti-rebond Flask conserve');
    assert.ok(swCode.includes('new Response('), 'Fallback ultime sans page blanche doit exister');
    assert.ok(swCode.includes('Connexion Internet requise pour vous authentifier'), 'Message login offline clair attendu');
    console.log('OK 5 : fallback offline sans page blanche');

    console.log('\nTOUS LES TESTS DU SERVICE WORKER ONT REUSSI');
}

runTests().catch(err => {
    console.error('ECHEC DES TESTS :', err);
    process.exit(1);
});
