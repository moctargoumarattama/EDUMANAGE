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
def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

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
            Classe, EmploiTemps, Bulletin, Log, SyncLog, JournalCorrection, Ecole
        )

        # user_loader
        @login_manager.user_loader
        def load_user(user_id):
            return Utilisateur.query.get(int(user_id))

        # Blueprints
        from .routes import main
        app.register_blueprint(main)
        from .blueprints.api_sync import api_sync
        app.register_blueprint(api_sync)
        from .admin import admin_bp
        app.register_blueprint(admin_bp)

        # -------------------
        # Context Processor pour année active
        # -------------------
        @app.context_processor
        def inject_annee_active():
            from .utils import get_annee_active
            from .middleware import get_ecole_courante
            from flask_login import current_user

            annee_active = None
            ecole = get_ecole_courante()

            if isinstance(ecole, tuple):  # Super-admin sans école choisie
                pass
            elif ecole:
                annee_active = get_annee_active(ecole.id)
            elif current_user.is_authenticated and getattr(current_user, 'ecole', None):
                annee_active = get_annee_active(current_user.ecole.id)

            return dict(annee_active=annee_active)

        # -------------------
        # Création tables et utilisateurs par défaut
        # -------------------
        db.create_all()

        # Initialisation du schéma (sans création de données de test au démarrage)
        # Les utilisateurs et écoles sont créés explicitement par l'administrateur ou via seed.

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

    app.log_correction = log_correction

    return app
