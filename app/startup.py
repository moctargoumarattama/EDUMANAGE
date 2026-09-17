from flask import current_app
from flask_login import current_user
from datetime import datetime
from sqlalchemy.orm import joinedload
from app.middleware import get_ecole_courante, get_ecole_id, log_action

# app/startup.py

# ============================================================
# 🔒 SAFE QUERY — Filtrage global et sécurisé par école
# ============================================================
def safe_query(model, ecole_id=None):
    """
    Retourne une query filtrée par école pour l'utilisateur actuel.
    ✅ Compatible modèles avec ou sans ecole_id direct.
    ✅ Super-admin doit choisir une école.
    """
    from app.models import Eleve, Classe

    try:
        # Si ecole_id est fourni en paramètre (pour corriger_donnees)
        if ecole_id:
            return _filter_by_ecole_id(model, ecole_id)
        
        # Sinon, utilisation normale avec current_user
        ecole = get_ecole_courante()
        if not ecole or isinstance(ecole, tuple):
            current_app.logger.warning(f"[safe_query] Aucun ecole active. Query bloquée pour {model.__name__}")
            return model.query.filter(False)

        return _filter_by_ecole_id(model, ecole.id)

    except Exception as e:
        current_app.logger.error(f"[safe_query] Erreur: {e}")
        return model.query.filter(False)

def _filter_by_ecole_id(model, ecole_id):
    """Filtre une requête par ecole_id"""
    from app.models import Eleve, Classe
    
    # --- Modèle avec ecole_id direct ---
    if hasattr(model, "ecole_id"):
        return model.query.filter_by(ecole_id=ecole_id)

    # --- Modèle lié via eleve -> ecole ---
    if hasattr(model, "eleve_id"):
        return model.query.join(Eleve).filter(Eleve.ecole_id == ecole_id)

    # --- Fallback : aucun champ exploitable → query vide ---
    current_app.logger.warning(f"[safe_query] Impossible de filtrer {model.__name__} automatiquement")
    return model.query.filter(False)

# ============================================================
# 🧹 CORRECTION DES DONNÉES PAR ÉCOLE (DÉPRÉCIÉ)
# ============================================================
def corriger_donnees():
    """
    [DÉPRÉCIÉ] Dans l'architecture annuelle stricte, la création automatique
    d'écoles ou de classes par défaut est formellement interdite.
    Cette fonction est désactivée afin de garantir des installations neuves reproductibles.
    """
    current_app.logger.warning("[startup] corriger_donnees() est désactivé : aucune école ni classe fictive ne sera créée.")
    return
