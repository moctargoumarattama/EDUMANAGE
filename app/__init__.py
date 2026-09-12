from flask import Flask, g
from .extensions import db, bcrypt, login_manager, migrate, moment, csrf, mail
import logging
from logging.handlers import RotatingFileHandler
import os
from werkzeug.security import generate_password_hash
from datetime import datetime
from .config import Config
from dotenv import load_dotenv
import threading

# app/__init__.py

# Charger les variables d'environnement
load_dotenv()

# -------------------
# Extensions (import depuis extensions.py)
# -------------------

# -------------------
# Logging
# -------------------
class RequestFormatter(logging.Formatter):
    """Formatter pour ajouter role et user_id si disponibles"""
    def format(self, record):
        try:
            record.role = getattr(g, 'role', 'SYSTEM')
            record.user_id = getattr(g, 'user_id', 0)
        except RuntimeError:  # hors contexte Flask
            record.role = 'SYSTEM'
            record.user_id = 0
        return super().format(record)

def setup_logging(app):
    if app.config.get('TESTING'):
        return

    if not os.path.exists('logs'):
        os.mkdir('logs')

    file_handler = RotatingFileHandler(
        'logs/ecole.log', maxBytes=10*1024*1024, backupCount=10, encoding='utf-8'
    )
    formatter = RequestFormatter(
        '%(asctime)s %(levelname)s [user_id=%(user_id)s role=%(role)s]: %(message)s [in %(pathname)s:%(lineno)d]'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)

    with app.app_context():
        app.logger.info("Démarrage de l'application", extra={'role': 'SYSTEM', 'user_id': 0})

# -------------------
# Création de l'application
# -------------------
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialisation des extensions
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page.'
    login_manager.login_message_category = 'warning'
    migrate.init_app(app, db)
    moment.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)

    # Logging
    setup_logging(app)

    # Import des modèles (APRES initialisation de db)
    with app.app_context():
        from .models import (
            Utilisateur, Professeur, Eleve, Cours, Note, Paiement, Absence,
            Classe, EmploiTemps, Bulletin, Log, SyncLog, JournalCorrection, Ecole,
            NiveauScolaire, EcoleNiveauConfig
        )

        # user_loader
        @login_manager.user_loader
        def load_user(user_id):
            return Utilisateur.query.get(int(user_id))

        @login_manager.unauthorized_handler
        def unauthorized_callback():
            from flask import request, jsonify, redirect, url_for
            if request.path.startswith('/api/') or request.is_json:
                return jsonify({
                    'success': False,
                    'error': 'Authentification requise',
                    'message': 'Session expirée ou non authentifiée'
                }), 401
            return redirect(url_for('main.login', next=request.url))

        # Blueprints
        from .routes import main
        app.register_blueprint(main)
        from .blueprints.api_sync import api_sync
        app.register_blueprint(api_sync)
        from .admin import admin_bp
        app.register_blueprint(admin_bp)

        # Middleware global (maintenance, auto-backup, etc.)
        from .middleware import before_request_handler, after_request_handler
        app.before_request(before_request_handler)
        app.after_request(after_request_handler)

        # -------------------
        # Context Processor pour année active & configuration école
        # -------------------
        @app.context_processor
        def inject_annee_active():
            from .utils import get_annee_active, get_school_setup_state
            from .middleware import get_ecole_courante
            from flask_login import current_user

            annee_active = None
            setup_state = None
            ecole = get_ecole_courante()

            if isinstance(ecole, tuple):  # Super-admin sans école choisie
                pass
            elif ecole:
                annee_active = get_annee_active(ecole.id)
                if getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'role', None) == 'admin':
                    setup_state = get_school_setup_state(ecole.id)
            elif getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'ecole', None):
                annee_active = get_annee_active(current_user.ecole.id)
                if getattr(current_user, 'role', None) == 'admin':
                    setup_state = get_school_setup_state(current_user.ecole.id)

            from app.models import ADMIN_TOUR_VERSION, SupportTicket
            nouveau_tickets_count = 0
            if getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'role', None) == 'super_admin':
                try:
                    nouveau_tickets_count = SupportTicket.query.filter_by(statut='nouveau').count()
                except Exception:
                    nouveau_tickets_count = 0

            return dict(
                annee_active=annee_active,
                school_setup_state=setup_state,
                ADMIN_TOUR_VERSION=ADMIN_TOUR_VERSION,
                SUPPORT_WHATSAPP_NUMBER=app.config.get('SUPPORT_WHATSAPP_NUMBER', '212770010264'),
                SUPPORT_EMAIL=app.config.get('SUPPORT_EMAIL', 'moctargoumarattama@gmail.com'),
                nouveau_tickets_count=nouveau_tickets_count
            )

        # Assurer la présence permanente du super administrateur
        try:
            from .init_superadmin import ensure_canonical_superadmin
            ensure_canonical_superadmin()
        except Exception:
            pass
    # -------------------
    # Fonction utilitaire pour journaliser les actions
    # -------------------
    def log_correction(action, description, ecole_id, cible_type=None, cible_id=None, ancienne_valeur=None, nouvelle_valeur=None, niveau="info"):
        from .models import JournalCorrection
        from flask_login import current_user

        correction = JournalCorrection(
            action=action,
            description=description,
            ecole_id=ecole_id,
            user_id=current_user.id if current_user.is_authenticated else None,
            cible_type=cible_type,
            cible_id=cible_id,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=nouvelle_valeur,
            niveau=niveau,
            date=datetime.utcnow()
        )
        db.session.add(correction)
        db.session.commit()

    # Support du reverse proxy (Nginx) pour la transmission de l'IP réelle et du protocole
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

    return app
