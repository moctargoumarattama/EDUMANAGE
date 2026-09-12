# tests/test_service_worker.py - Validation PWA Service Worker KLASORA v5
import os
import subprocess
import pytest


def test_service_worker_static_analysis():
    """Vérifie statiquement les règles de conception du Service Worker."""
    sw_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'static', 'service-worker.js')
    assert os.path.exists(sw_path), "service-worker.js doit exister"

    with open(sw_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Version de cache
    assert "const CACHE_VERSION = 'klasora-static-v5';" in content, "CACHE_VERSION doit être klasora-static-v5"
    assert "const CACHE_VERSION = 'klasora-static-v4';" not in content, "v4 ne doit plus être actif"

    # 2. Aucun timeout artificiel
    assert "fetchWithTimeout" not in content, "fetchWithTimeout ne doit plus exister"
    assert "4000" not in content, "Le timeout de 4000ms ne doit plus exister"

    # 3. Fonction handleNavigation avec retry
    assert "async function handleNavigation(request)" in content
    assert "await new Promise(resolve => setTimeout(resolve, 800));" in content
    assert "event.respondWith(handleNavigation(request));" in content

    # 4. Suppression des anciens caches dans activate
    assert "if (cacheName !== CACHE_VERSION)" in content
    assert "caches.delete(cacheName)" in content


def test_service_worker_runtime_node():
    """Exécute les tests unitaires JS émulant le Service Worker sous Node.js."""
    test_js_path = os.path.join(os.path.dirname(__file__), 'test_service_worker.js')
    assert os.path.exists(test_js_path), "test_service_worker.js doit exister"

    result = subprocess.run(
        ['node', test_js_path],
        capture_output=True,
        text=True,
        encoding='utf-8'
    )
    assert result.returncode == 0, f"Les tests JS ont échoué:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "TOUS LES TESTS DU SERVICE WORKER ONT RÉUSSI AVEC SUCCÈS" in result.stdout
