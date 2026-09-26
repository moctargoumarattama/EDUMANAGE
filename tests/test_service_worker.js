// tests/test_service_worker.js - Validation du Service Worker KLASORA
const fs = require('fs');
const path = require('path');
const assert = require('assert');

async function runTests() {
    console.log('=== TEST SERVICE WORKER KLASORA ===');

    const swPath = path.join(__dirname, '..', 'app', 'static', 'service-worker.js');
    const swCode = fs.readFileSync(swPath, 'utf-8');

    assert.ok(swCode.includes("const CACHE_NAME = 'klasora-cache-v14';"), 'CACHE_NAME doit etre v14');
    assert.ok(swCode.includes('const CACHE_VERSION = CACHE_NAME;'), 'CACHE_VERSION doit pointer sur CACHE_NAME');
    assert.ok(!swCode.includes('fetchWithTimeout'), 'fetchWithTimeout doit etre supprime');
    assert.ok(!swCode.includes('4000'), 'Le timeout de 4000ms doit etre supprime');
    console.log('OK 1 : version de cache et ancien timeout');

    assert.ok(swCode.includes("request.mode === 'navigate'"), 'Les navigations HTML doivent etre interceptees');
    assert.ok(swCode.includes('async function handleNavigation(request)'), 'handleNavigation doit exister');
    assert.ok(swCode.includes('offlineFallback()'), 'offlineFallback doit etre utilise');
    assert.ok(swCode.includes('return await fetch(request);'), 'Les pages HTML privees doivent etre network-only');
    assert.ok(!swCode.includes('await cache.put(request, networkResponse.clone());'), 'Les pages HTML privees ne doivent pas etre cachees');
    console.log('OK 2 : navigation network-only avec fallback offline');

    assert.ok(swCode.includes("url.pathname === '/logout'"), '/logout doit declencher une purge');
    assert.ok(swCode.includes("cacheName.startsWith('klasora-pages-')"), 'Les anciens caches de pages doivent etre purgeables');
    assert.ok(swCode.includes("url.pathname === '/api/connectivity'"), '/api/connectivity doit rester network-only');
    console.log('OK 3 : purge logout et connectivite reseau');

    assert.ok(swCode.includes("'/manifest.json'"), 'Le manifest public doit etre precache');
    assert.ok(swCode.includes("'/static/css/style.css?v=14'"), 'Le CSS principal v14 doit etre precache');
    assert.ok(swCode.includes("'/static/js/pwa.js?v=14'"), 'Le JS PWA v14 doit etre precache');
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
