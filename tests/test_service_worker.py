# tests/test_service_worker.py - Validation PWA Service Worker KLASORA
import os
import subprocess

from app import create_app


class PwaRouteTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-service-worker"


def test_service_worker_static_analysis():
    """Verifie statiquement les regles critiques du Service Worker."""
    sw_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'static', 'service-worker.js')
    assert os.path.exists(sw_path), "service-worker.js doit exister"

    with open(sw_path, 'r', encoding='utf-8') as f:
        content = f.read()

    assert "const CACHE_VERSION = 'klasora-static-v7';" in content
    assert "const PAGE_CACHE = 'klasora-pages-v7';" in content
    assert "const CACHE_VERSION = 'klasora-static-v4';" not in content

    assert "fetchWithTimeout" not in content
    assert "4000" not in content

    assert "async function handleNavigation(request)" in content
    assert "request.mode === 'navigate'" in content
    assert "await new Promise(resolve => setTimeout(resolve, 800));" in content
    assert "event.respondWith(handleNavigation(request));" in content
    assert "await cache.put(request, networkResponse.clone());" in content
    assert "offlineFallback()" in content
    assert "Connexion Internet requise pour vous authentifier" in content

    assert "BYPASS_NAVIGATION_CACHE" in content
    assert "'/login'" in content
    assert "'/logout'" in content
    assert "'/api/'" in content
    assert "CLEAR_USER_CACHE" in content
    assert "await caches.delete(PAGE_CACHE)" in content

    assert "allowedCaches" in content
    assert "caches.delete(cacheName)" in content


def test_offline_template_has_clear_login_message():
    template_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'templates', 'offline.html')
    assert os.path.exists(template_path), "offline.html doit exister"

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    assert "Connexion Internet requise pour vous authentifier." in content
    assert "Vous etes hors connexion" in content
    assert "window.addEventListener('online', updateStatus)" in content


def test_pwa_public_routes_available():
    app = create_app(PwaRouteTestConfig)
    client = app.test_client()

    offline = client.get('/offline')
    assert offline.status_code == 200
    assert b'Connexion Internet requise pour vous authentifier.' in offline.data

    manifest = client.get('/manifest.json')
    assert manifest.status_code == 200
    assert manifest.mimetype in ('application/manifest+json', 'application/json')

    service_worker = client.get('/service-worker.js')
    assert service_worker.status_code == 200
    assert service_worker.headers.get('Service-Worker-Allowed') == '/'


def test_service_worker_runtime_node():
    """Execute les tests unitaires JS emulant le Service Worker sous Node.js."""
    test_js_path = os.path.join(os.path.dirname(__file__), 'test_service_worker.js')
    assert os.path.exists(test_js_path), "test_service_worker.js doit exister"

    result = subprocess.run(
        ['node', test_js_path],
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    assert result.returncode == 0, f"Les tests JS ont echoue:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "TOUS LES TESTS DU SERVICE WORKER ONT REUSSI" in result.stdout
