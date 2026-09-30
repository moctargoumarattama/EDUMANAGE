"""Service de gestion et de cycle de vie du cache des codes QR (app/static/qrcache).

Ce service centralise :
- La localisation sécurisée et la création automatique du répertoire de cache.
- La purge des fichiers orphelins ou expirés selon une politique TTL (ex: 48h ou 7 jours).
- Le calcul des métriques de stockage du cache pour le tableau de bord de maintenance.
- Le déclenchement opportuniste non bloquant lors de requêtes QR codes.
"""

import os
import time
import threading
from typing import Dict, Any
from flask import current_app

_last_opportunistic_cleanup = 0.0
_cleanup_lock = threading.Lock()


def get_qr_cache_dir() -> str:
    """Retourne le chemin absolu du dossier app/static/qrcache en garantissant son existence."""
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_dir = os.path.join(base_dir, 'static', 'qrcache')
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir
    except Exception:
        # Fallback si current_app est disponible
        if current_app:
            cache_dir = os.path.join(current_app.root_path, 'static', 'qrcache')
            os.makedirs(cache_dir, exist_ok=True)
            return cache_dir
        raise


def cleanup_qr_cache(max_age_days: float = 7.0) -> Dict[str, Any]:
    """
    Supprime les fichiers dans static/qrcache dont la dernière modification dépasse max_age_days.
    Si max_age_days <= 0, tous les fichiers sont purgés immédiatement.
    """
    cache_dir = get_qr_cache_dir()
    now = time.time()
    max_age_seconds = max(0.0, float(max_age_days)) * 86400.0

    deleted_count = 0
    freed_bytes = 0
    errors_count = 0
    remaining_count = 0

    if not os.path.exists(cache_dir):
        return {
            'deleted': 0,
            'freed_bytes': 0,
            'freed_kb': 0.0,
            'freed_mb': 0.0,
            'remaining': 0,
            'errors': 0,
            'directory': cache_dir
        }

    for filename in os.listdir(cache_dir):
        filepath = os.path.join(cache_dir, filename)
        if not os.path.isfile(filepath):
            continue

        try:
            stat = os.stat(filepath)
            file_age = now - stat.st_mtime

            if max_age_days <= 0 or file_age >= max_age_seconds:
                freed_bytes += stat.st_size
                os.remove(filepath)
                deleted_count += 1
            else:
                remaining_count += 1
        except OSError:
            errors_count += 1

    return {
        'deleted': deleted_count,
        'freed_bytes': freed_bytes,
        'freed_kb': round(freed_bytes / 1024.0, 2),
        'freed_mb': round(freed_bytes / (1024.0 * 1024.0), 2),
        'remaining': remaining_count,
        'errors': errors_count,
        'directory': cache_dir
    }


def get_qr_cache_stats() -> Dict[str, Any]:
    """Retourne les statistiques actuelles du dossier static/qrcache."""
    cache_dir = get_qr_cache_dir()
    total_files = 0
    total_bytes = 0

    if os.path.exists(cache_dir):
        for filename in os.listdir(cache_dir):
            filepath = os.path.join(cache_dir, filename)
            if os.path.isfile(filepath):
                try:
                    total_files += 1
                    total_bytes += os.path.getsize(filepath)
                except OSError:
                    pass

    return {
        'count': total_files,
        'size_bytes': total_bytes,
        'size_kb': round(total_bytes / 1024.0, 2),
        'size_mb': round(total_bytes / (1024.0 * 1024.0), 2),
        'directory': cache_dir
    }


def maybe_cleanup_qr_cache(max_age_days: float = 7.0, interval_seconds: int = 3600) -> bool:
    """Déclenche une purge opportuniste en arrière-plan sans bloquer la requête courante."""
    global _last_opportunistic_cleanup
    now = time.time()

    if now - _last_opportunistic_cleanup < interval_seconds:
        return False

    with _cleanup_lock:
        if now - _last_opportunistic_cleanup < interval_seconds:
            return False
        _last_opportunistic_cleanup = now

    def _worker():
        try:
            cleanup_qr_cache(max_age_days=max_age_days)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True, name="qr_cache_cleanup_worker").start()
    return True
