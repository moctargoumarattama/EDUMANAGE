# app/middleware.py
"""
Middleware de sécurité multi-écoles
Garantit l'isolation stricte des données par école

Corrections et renforts sans déformation du code original :
- Ajout d'une fonction get_annee_courante() (intégration année active)
- Nettoyage des appels répétés à get_ecole_courante()
- Défensive programming (vérifications is_authenticated)
- Nettoyage g.annee_courante après requête
- Robustification de log_action (ip safe, gestion d'erreur)
- Petites améliorations de sécurité (has_ecole_access extended)
"""

from flask import session, g, current_app, redirect, url_for, flash, render_template, abort, request
from flask_login import current_user
from functools import wraps
from app.models import Ecole, Log
from app import db
import json
import traceback
from typing import Optional


# ====================================================================
# 🏫 GESTION ÉCOLE COURANTE
# ====================================================================

def get_ecole_courante():
    """
    Récupère l'école courante selon le rôle utilisateur.

    Returns:
        - Ecole : pour les utilisateurs normaux
        - (None, liste_ecoles) : pour super_admin sans école choisie
        - None : si aucune école valide
    """
    # 1️⃣ Déjà stockée dans g pour cette requête
    if hasattr(g, 'ecole_courante'):
        return g.ecole_courante

    # Si non authentifié -> pas d'école
    if not getattr(current_user, 'is_authenticated', False):
        g.ecole_courante = None
        return None

    # 2️⃣ Super-admin : peut choisir n'importe quelle école
    try:
        if current_user.role == 'super_admin':
            ecole_id = session.get('ecole_id')
            if ecole_id:
                try:
                    ecole_id = int(ecole_id)
                except (TypeError, ValueError):
                    ecole_id = None
            if ecole_id:
                ecole = Ecole.query.get(ecole_id)
                if ecole:
                    g.ecole_courante = ecole
                    return ecole

            # Pas d'école choisie : retourner la liste pour sélection
            ecoles = Ecole.query.order_by(Ecole.nom).all()
            g.ecole_courante = None
            return None, ecoles
    except Exception as e:
        current_app.logger.error(f"Erreur get_ecole_courante (super_admin flow): {e}\n{traceback.format_exc()}")
        g.ecole_courante = None
        return None

    # 3️⃣ Utilisateur normal : école fixe
    try:
        if getattr(current_user, 'ecole_id', None):
            ecole = Ecole.query.get(current_user.ecole_id)
            if ecole:
                # Synchroniser session si absent ou différent
                try:
                    session['ecole_id'] = int(ecole.id)
                except (TypeError, ValueError):
                    session['ecole_id'] = ecole.id
                g.ecole_courante = ecole
                return ecole
    except Exception as e:
        current_app.logger.error(f"Erreur get_ecole_courante (user flow): {e}\n{traceback.format_exc()}")

    # 4️⃣ Aucune école trouvée
    g.ecole_courante = None
    return None


def get_annee_courante():
    """Retourne l'année scolaire active pour l'école courante (ou None).

    Note: import effectué localement pour éviter import circulaire.
    """
    try:
        from app.utils import get_annee_active
    except ImportError as e:
        current_app.logger.error(f"Impossible d'importer get_annee_active: {e}")
        return None

    result = get_ecole_courante()
    # Super-admin without choice -> no single ecole
    if isinstance(result, tuple):
        return None
    if not result:
        return None

    try:
        return get_annee_active(result.id)
    except Exception as e:
        current_app.logger.error(f"Erreur get_annee_courante: {e}\n{traceback.format_exc()}")
        return None


def set_ecole_courante(ecole_id):
    """Définit l'école courante (super-admin uniquement)"""
    if not getattr(current_user, 'is_authenticated', False):
        return False
    if getattr(current_user, 'role', None) != 'super_admin':
        return False

    try:
        ecole = Ecole.query.get(int(ecole_id))
    except (TypeError, ValueError):
        return False

    if ecole:
        try:
            session['ecole_id'] = int(ecole_id)
        except (TypeError, ValueError):
            session['ecole_id'] = ecole_id
        g.ecole_courante = ecole
        current_app.logger.info(f"Super-admin {getattr(current_user, 'email', '?')} a sélectionné l'école {ecole.nom}")
        return True
    return False


def clear_ecole_courante():
    """Efface l'école courante de la session"""
    session.pop('ecole_id', None)
    if hasattr(g, 'ecole_courante'):
        del g.ecole_courante
    if hasattr(g, 'annee_courante'):
        del g.annee_courante


# ====================================================================
# 🔒 DÉCORATEURS DE SÉCURITÉ
# ====================================================================

def require_ecole(f):
    """Force la sélection d'une école avant d'accéder à une route"""
    @wraps(f)
    def decorated(*args, **kwargs):
        result = get_ecole_courante()

        # Super-admin sans école sélectionnée
        if isinstance(result, tuple):
            _, ecoles = result
            if not ecoles:
                flash("Aucune école disponible dans le système.", "warning")
                return redirect(url_for("main.index"))

            # Rediriger vers la page de sélection
            flash("Veuillez sélectionner une école pour continuer.", "info")
            return render_template("choisir_ecole.html", ecoles=ecoles)

        # Pas d'école du tout
        if not result:
            try:
                role = getattr(current_user, 'role', None)
            except Exception:
                role = None
            if role == 'admin':
                flash("Votre compte n'est associé à aucune école. Contactez le super-administrateur.", "danger")
            else:
                flash("Erreur de configuration. Contactez l'administrateur.", "danger")
            return redirect(url_for("main.index"))

        return f(*args, **kwargs)
    return decorated


def ecole_required(f):
    """Vérifie que l'utilisateur a une école assignée (sauf super-admin)"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if getattr(current_user, 'role', None) != 'super_admin' and not getattr(current_user, 'ecole_id', None):
            abort(403, "Accès refusé : utilisateur sans école assignée")
        return f(*args, **kwargs)
    return decorated


# ====================================================================
# 🎯 FILTRAGE SÉCURISÉ PAR ÉCOLE
# ====================================================================

def filtre_par_ecole(query, modele=None):
    """
    Filtre une requête SQLAlchemy selon l'école courante.

    Args:
        query: SQLAlchemy query ou liste Python
        modele: Classe du modèle (optionnel)

    Returns:
        Query filtrée ou liste filtrée
    """
    # Super-admin sans école sélectionnée : voir tout
    result = get_ecole_courante()
    if isinstance(result, tuple):
        return query

    ecole = result
    if not ecole:
        # Pas d'école : requête vide pour sécurité
        try:
            if hasattr(query, 'filter'):
                return query.filter(False)
        except Exception as e:
            current_app.logger.debug(f"Impossible de retourner une query vide filtrée: {e}")
        return []

    ecole_id = ecole.id

    # Filtrage SQLAlchemy
    try:
        if hasattr(query, 'filter'):
            # Tentative avec le modèle fourni
            if modele and hasattr(modele, 'ecole_id'):
                return query.filter(modele.ecole_id == ecole_id)

            # Tentative générique via filter_by
            return query.filter_by(ecole_id=ecole_id)
    except Exception as e:
        current_app.logger.warning(f"Impossible de filtrer par ecole_id: {e} - {traceback.format_exc()}")
        return query

    # Filtrage Python sur liste
    if isinstance(query, list):
        return [obj for obj in query if getattr(obj, 'ecole_id', None) == ecole_id]

    return query


# ====================================================================
# 🛡️ VÉRIFICATION D'ACCÈS AUX RESSOURCES
# ====================================================================

def ajouter_ecole_id(obj):
    """Assigne automatiquement l'ecole_id courant a un objet avant creation."""
    ecole = get_ecole_courante()
    if ecole and not isinstance(ecole, tuple) and hasattr(obj, 'ecole_id'):
        obj.ecole_id = ecole.id


def check_ecole_access(model_class, object_id=None, ecole_field='ecole_id'):
    """
    Vérifie qu'un objet appartient bien à l'école courante.

    Args:
        model_class: Classe du modèle
        object_id: ID de l'objet à vérifier
        ecole_field: Nom du champ contenant l'ecole_id

    Returns:
        bool: True si accès autorisé
    """
    # Super-admin a toujours accès
    if getattr(current_user, 'role', None) == 'super_admin':
        return True

    result = get_ecole_courante()
    if not result or isinstance(result, tuple):
        return False

    try:
        if object_id:
            obj = model_class.query.get(object_id)
            if not obj:
                return False

            obj_ecole_id = getattr(obj, ecole_field, None)
            return obj_ecole_id == result.id
    except Exception as e:
        current_app.logger.error(f"Erreur check_ecole_access: {e} - {traceback.format_exc()}")
        return False

    return True


def ecole_access_required(model_class, id_param_name='id', ecole_field='ecole_id'):
    """
    Décorateur pour bloquer l'accès à une ressource d'une autre école.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            object_id = kwargs.get(id_param_name)

            if not check_ecole_access(model_class, object_id, ecole_field):
                try:
                    current_app.logger.warning(
                        f"Tentative d'accès non autorisé: {getattr(current_user, 'email', '?')} "
                        f"vers {model_class.__name__} ID={object_id}"
                    )
                except Exception as e:
                    current_app.logger.debug(f"Impossible de journaliser un accès refusé: {e}")
                flash("Accès non autorisé à cette ressource.", "danger")
                return redirect(url_for('main.index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ====================================================================
# 🔧 FONCTIONS UTILITAIRES
# ====================================================================

def get_ecole_id():
    """Retourne l'ID de l'école courante ou None"""
    result = get_ecole_courante()
    if isinstance(result, tuple):
        return None  # Super-admin sans école choisie
    return result.id if result else None


def inject_ecole_courante():
    """Injection automatique dans les templates Jinja2"""
    result = get_ecole_courante()
    if isinstance(result, tuple):
        return dict(ecole_courante=None, ecoles_disponibles=result[1])
    return dict(ecole_courante=result, ecoles_disponibles=[])


def is_super_admin():
    """Vérifie si l'utilisateur est super-admin"""
    return getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'role', None) == 'super_admin'


def has_ecole_access(ecole_id):
    """Vérifie si l'utilisateur a accès à une école spécifique"""
    if is_super_admin():
        return True

    if not getattr(current_user, 'is_authenticated', False):
        return False

    # accès direct
    try:
        if getattr(current_user, 'ecole_id', None) == ecole_id:
            return True
    except Exception as e:
        current_app.logger.debug(f"Erreur lecture ecole_id utilisateur: {e}")

    # si l'utilisateur gère plusieurs écoles (attribut optionnel)
    try:
        ecoles_gerees = getattr(current_user, 'ecoles_gerees', None) or []
        if any(getattr(e, 'id', e) == ecole_id or e == ecole_id for e in ecoles_gerees):
            return True
    except Exception as e:
        current_app.logger.debug(f"Erreur lecture ecoles_gerees utilisateur: {e}")

    return False


# ====================================================================
# 📝 JOURNALISATION SÉCURISÉE
# ====================================================================

def log_action(module, action, level="INFO", user_id=None, details=None):
    """
    Journalise une action avec contexte école.
    Fonction centralisée pour toute l'application.
    """
    try:
        ecole_id = get_ecole_id()
        try:
            ip_address = request.remote_addr if request else None
        except RuntimeError:
            ip_address = None

        log_entry = Log(
            level=(level or "INFO").upper(),
            module=module,
            action=action,
            details=details,
            utilisateur_id=user_id or (getattr(current_user, 'id', None) if getattr(current_user, 'is_authenticated', False) else None),
            ip_address=ip_address,
            ecole_id=ecole_id
        )

        db.session.add(log_entry)
        db.session.commit()

        # Log système également
        lvl = (level or "INFO").upper()
        msg = f"[{module}] {action} - {details or ''}"
        if lvl == "ERROR":
            current_app.logger.error(msg)
        elif lvl == "WARNING":
            current_app.logger.warning(msg)
        else:
            current_app.logger.info(msg)

    except Exception as e:
        try:
            current_app.logger.error(f"Erreur journalisation: {e}\n{traceback.format_exc()}")
        except Exception:
            # dernier recours, éviter crash
            pass


# ====================================================================
# 🎨 CONTEXTE POUR TEMPLATES
# ====================================================================

def setup_template_context():
    """Configure le contexte global pour les templates"""
    @current_app.context_processor
    def inject_globals():
        ec_res = get_ecole_courante()
        annee = None
        try:
            if not isinstance(ec_res, tuple) and ec_res:
                annee = get_annee_courante()
        except Exception as e:
            current_app.logger.debug(f"Impossible d'injecter l'année courante: {e}")
            annee = None

        return {
            'ecole_courante': ec_res if not isinstance(ec_res, tuple) else None,
            'annee_courante': annee,
            'is_super_admin': is_super_admin(),
            'get_ecole_id': get_ecole_id
        }


# ====================================================================
# 🔄 MIDDLEWARE POUR CHAQUE REQUÊTE
# ====================================================================

def before_request_handler():
    """Exécuté avant chaque requête pour initialiser le contexte école"""
    if getattr(current_user, 'is_authenticated', False):
        # Précharger l'école courante dans g
        get_ecole_courante()
        # Précharger l'année courante
        try:
            g.annee_courante = get_annee_courante()
        except Exception as e:
            current_app.logger.debug(f"Impossible de précharger l'année courante: {e}")
            g.annee_courante = None

        # Logger l'accès pour audit
        if current_app.config.get('LOG_ALL_ACCESS', False):
            try:
                current_app.logger.debug(
                    f"Accès: {getattr(current_user, 'email', 'anonymous')} ({getattr(current_user, 'role', '?')}) "
                    f"-> {request.endpoint} [École: {get_ecole_id()}]"
                )
            except Exception as e:
                current_app.logger.debug(f"Impossible d'écrire le log d'accès: {e}")


def after_request_handler(response):
    """Exécuté après chaque requête pour nettoyer le contexte"""
    try:
        if hasattr(g, 'ecole_courante'):
            del g.ecole_courante
    except RuntimeError:
        pass
    try:
        if hasattr(g, 'annee_courante'):
            del g.annee_courante
    except RuntimeError:
        pass
    return response


# ====================================================================
# 🚀 INITIALISATION
# ====================================================================

def init_middleware(app):
    """Initialise le middleware avec l'application Flask"""
    app.before_request(before_request_handler)
    app.after_request(after_request_handler)
    setup_template_context()

    # Ajouter la fonction de log au contexte de l'app
    app.log_action = log_action

    app.logger.info("Middleware multi-écoles initialisé avec succès")
