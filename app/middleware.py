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

from flask import session, g, current_app, redirect, url_for, flash, render_template, abort, request, jsonify
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
                ecole = db.session.get(Ecole, ecole_id)
                if ecole:
                    session['ecole_courante'] = {'id': ecole.id, 'nom': ecole.nom}
                    session['ecole_nom'] = ecole.nom
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
            ecole = db.session.get(Ecole, current_user.ecole_id)
            if ecole:
                # Synchroniser session si absent ou différent
                try:
                    session['ecole_id'] = int(ecole.id)
                except (TypeError, ValueError):
                    session['ecole_id'] = ecole.id
                session['ecole_courante'] = {'id': ecole.id, 'nom': ecole.nom}
                session['ecole_nom'] = ecole.nom
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
    if hasattr(g, 'annee_courante') and g.annee_courante is not None:
        return g.annee_courante

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
        annee = get_annee_active(result.id)
        try:
            g.annee_courante = annee
        except RuntimeError:
            pass
        return annee
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
        session['ecole_courante'] = {'id': ecole.id, 'nom': ecole.nom}
        session['ecole_nom'] = ecole.nom
        g.ecole_courante = ecole
        current_app.logger.info(f"Super-admin {getattr(current_user, 'email', '')} a sélectionné l'école {ecole.nom}")
        return True
    return False


def clear_ecole_courante():
    """Efface l'école courante de la session"""
    session.pop('ecole_id', None)
    session.pop('ecole_courante', None)
    session.pop('ecole_nom', None)
    for attr in ('ecole_courante', 'annee_courante', '_school_setup_cache', '_annee_active_cache', '_annee_consultee_cache'):
        if hasattr(g, attr):
            delattr(g, attr)


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
                        f"Tentative d'accès non autorisé: {getattr(current_user, 'email', '')} "
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
        setup_state = None
        try:
            if not isinstance(ec_res, tuple) and ec_res:
                annee = get_annee_courante()
                if getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'role', None) == 'admin':
                    from app.utils import get_school_setup_state
                    setup_state = get_school_setup_state(ec_res.id)
        except Exception as e:
            current_app.logger.debug(f"Impossible d'injecter l'année courante ou setup_state: {e}")
            annee = None

        from app.models import ADMIN_TOUR_VERSION
        return {
            'ecole_courante': ec_res if not isinstance(ec_res, tuple) else None,
            'annee_courante': annee,
            'school_setup_state': setup_state,
            'ADMIN_TOUR_VERSION': ADMIN_TOUR_VERSION,
            'is_super_admin': is_super_admin(),
            'get_ecole_id': get_ecole_id
        }


# ====================================================================
# 🔄 MIDDLEWARE POUR CHAQUE REQUÊTE
# ====================================================================

def before_request_handler():
    """Exécuté avant chaque requête pour initialiser le contexte école et vérifier la maintenance"""
    # 0️⃣ Autoriser sans restriction les fichiers statiques
    if request.endpoint and (request.endpoint.startswith('static') or request.endpoint == 'admin.static') or request.path.startswith('/static/'):
        return None

    # 1️⃣ Vérification de la sauvegarde automatique quotidienne
    # En production, les sauvegardes sont gérées par un système externe planifié (cron/worker).
    # Ce contrôle inline n'est activé que si CHECK_BACKUP_ON_REQUEST est expressément configuré (ex: dev autonome).
    if current_app.config.get('CHECK_BACKUP_ON_REQUEST', False):
        try:
            from app.admin.scripts import check_and_run_daily_backup
            check_and_run_daily_backup()
        except Exception as e:
            current_app.logger.debug(f"Erreur vérification sauvegarde quotidienne: {e}")

    # 2️⃣ Vérification du Mode Maintenance
    try:
        from app.admin.scripts import get_maintenance_status
        maint = get_maintenance_status()
        if maint.get('active'):
            # Le super_admin conserve un accès absolu à l'ensemble de la plateforme
            is_super_admin_user = getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'role', '') == 'super_admin'
            # Les routes d'authentification restent accessibles pour permettre la connexion
            is_auth_route = request.endpoint in ('main.login', 'main.logout') or request.path in ('/login', '/logout')

            if not is_super_admin_user and not is_auth_route:
                return render_template('maintenance_client.html', maintenance_message=maint.get('message')), 503
    except Exception as e:
        current_app.logger.error(f"Erreur vérification mode maintenance: {e}")

    # 3️⃣ Contexte utilisateur authentifié
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
                    f"Accès: {getattr(current_user, 'email', 'anonymous')} ({getattr(current_user, 'role', '')}) "
                    f"-> {request.endpoint} [École: {get_ecole_id()}]"
                )
            except Exception as e:
                current_app.logger.debug(f"Impossible d'écrire le log d'accès: {e}")

        # 3️⃣-bis Vérification école bloquée / suspendue / maintenance pour les sessions actives
        if getattr(current_user, 'role', None) != 'super_admin':
            ecole = getattr(g, 'ecole_courante', None) or getattr(current_user, 'ecole', None)
            if ecole and ecole.statut in ('bloque', 'suspendu', 'maintenance'):
                allowed_eps = {'main.logout', 'main.login'}
                current_ep = request.endpoint or ''
                if current_ep not in allowed_eps and not current_ep.startswith('static') and current_ep != 'admin.static' and not request.path.startswith('/static/'):
                    if ecole.statut == 'maintenance':
                        m_msg = ecole.motif_blocage or f"L'établissement '{ecole.nom}' est actuellement en maintenance."
                        return render_template('maintenance_client.html', maintenance_message=m_msg), 503

                    from flask_login import logout_user
                    if current_user.role == 'admin':
                        motif = f" Motif : {ecole.motif_blocage}." if ecole.motif_blocage else ""
                        msg = f"L'accès à votre établissement ({ecole.nom}) est suspendu.{motif} Veuillez contacter l'administration de la plateforme."
                    else:
                        # Confidentialité : les parents et professeurs ne voient jamais le motif
                        msg = f"L'accès à l'espace de votre établissement ({ecole.nom}) est temporairement indisponible. Veuillez contacter la direction de votre école."

                    logout_user()
                    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                        return jsonify({'error': 'school_blocked', 'message': msg}), 403
                    flash(msg, "danger")
                    return redirect(url_for('main.login'))

        # 4️⃣ Contrôle serveur du parcours d'onboarding obligatoire pour admin
        if getattr(current_user, 'role', None) == 'admin':
            ecole_id = getattr(current_user, 'ecole_id', None)
            if ecole_id:
                try:
                    from app.utils import get_school_setup_state
                    setup_state = get_school_setup_state(ecole_id)
                    allowed_endpoints = {
                        'main.onboarding',
                        'main.login',
                        'main.logout',
                        'main.choisir_ecole',
                        'main.creer_support_ticket',
                        'main.structure_annee',
                    }
                    current_ep = request.endpoint or ''
                    if not setup_state.get('setup_complete', False):
                        if current_ep not in allowed_endpoints and not current_ep.startswith('static') and current_ep != 'admin.static' and not request.path.startswith('/static/'):
                            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                                return jsonify({
                                    'error': 'school_setup_required',
                                    'current_step': setup_state.get('current_step', 'year')
                                }), 403
                            return redirect(url_for('main.onboarding'))
                except Exception as e:
                    current_app.logger.exception(f"Erreur vérification onboarding admin: {e}")
                    # Comportement fail-safe (fermé) : en cas d'erreur de vérification, on ne laisse pas passer l'admin vers les routes métier
                    allowed_endpoints = {
                        'main.onboarding',
                        'main.login',
                        'main.logout',
                        'main.choisir_ecole',
                        'main.creer_support_ticket',
                        'main.structure_annee',
                    }
                    current_ep = request.endpoint or ''
                    if current_ep not in allowed_endpoints and not current_ep.startswith('static') and current_ep != 'admin.static' and not request.path.startswith('/static/'):
                        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                            return jsonify({
                                'error': 'school_setup_required',
                                'current_step': 'year'
                            }), 403
                        return redirect(url_for('main.onboarding'))


def after_request_handler(response):
    """Exécuté après chaque requête pour nettoyer le contexte et appliquer la politique de sécurité / cache HTTP"""
    try:
        # En-têtes de sécurité de base (Défense en profondeur)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')

        # Politique de cache HTTP
        path = request.path if request else ''
        # Ajout du X-Robots-Tag global (SEO)
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            excluded_seo_paths = ('/', '/robots.txt', '/sitemap.xml')
            if path not in excluded_seo_paths:
                response.headers['X-Robots-Tag'] = 'noindex, follow'

        # 1. Service Worker : STRICTEMENT JAMAIS EN CACHE (pour mises à jour instantanées)
        if path == '/login':
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        elif path in ('/service-worker.js', '/static/service-worker.js'):
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.headers['Service-Worker-Allowed'] = '/'
        # 2. Manifest PWA : Cache court (1h)
        elif path in ('/manifest.json', '/static/manifest.json'):
            response.headers.setdefault('Cache-Control', 'public, max-age=3600')
        # 3. Fichiers statiques publics (CSS, JS, images, polices) : Cache long (30 jours)
        elif path.startswith('/static/'):
            response.headers['Cache-Control'] = 'public, max-age=2592000'
        # 4. Données sensibles, API et pages d'utilisateurs connectés : Anti-cache strict
        elif getattr(current_user, 'is_authenticated', False) or path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        # 5. Pages publiques dynamiques (login, register, welcome)
        else:
            response.headers.setdefault('Cache-Control', 'no-cache, private')
    except Exception as e:
        try:
            current_app.logger.debug(f"Erreur application en-têtes HTTP after_request: {e}")
        except Exception:
            pass

    for attr in (
        'ecole_courante', 'annee_courante', '_school_setup_cache',
        '_annee_active_cache', '_annee_consultee_cache',
        '_system_params', '_maintenance_status', '_super_admin_counts',
        '_backup_checked_in_request', '_login_user', 'csrf_token'
    ):
        try:
            if hasattr(g, attr):
                delattr(g, attr)
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
