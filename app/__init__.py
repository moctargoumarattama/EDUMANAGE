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
            elif current_user.is_authenticated and hasattr(current_user, 'ecole'):
                annee_active = get_annee_active(current_user.ecole.id)

            return dict(annee_active=annee_active)

        # -------------------
        # Création tables et utilisateurs par défaut
        # -------------------
        db.create_all()

        from .init_ecoles import init_ecoles_par_defaut
        init_ecoles_par_defaut(db)

        # Assurer une école par défaut
        ecole_defaut = Ecole.query.first()
        if not ecole_defaut:
            ecole_defaut = Ecole(nom="École par défaut")
            db.session.add(ecole_defaut)
            db.session.commit()

        # Création utilisateurs par défaut
        def creer_utilisateurs_par_defaut():
            # Super Admin (pas d'école obligatoire)
            if not Utilisateur.query.filter_by(email='superadmin@ecole.ne').first():
                super_admin = Utilisateur(
                    nom='Super',
                    prenom='Administrateur',
                    email='superadmin@ecole.ne',
                    mot_de_passe=generate_password_hash('superadmin123'),
                    role='super_admin',
                    telephone='+22700000000'
                )
                db.session.add(super_admin)
                app.logger.info("Super Admin créé : superadmin@ecole.ne / superadmin123")

            # Admin
            if not Utilisateur.query.filter_by(email='admin@ecole.ne').first():
                admin = Utilisateur(
                    nom='Admin',
                    prenom='System',
                    email='admin@ecole.ne',
                    mot_de_passe=generate_password_hash('admin123'),
                    role='admin',
                    telephone='+22700000000',
                    ecole_id=ecole_defaut.id
                )
                db.session.add(admin)
                app.logger.info("Admin créé : admin@ecole.ne / admin123")

            # Professeur
            if not Utilisateur.query.filter_by(email='prof@ecole.ne').first():
                prof_user = Utilisateur(
                    nom='Professeur',
                    prenom='Test',
                    email='prof@ecole.ne',
                    mot_de_passe=generate_password_hash('prof123'),
                    role='enseignant',
                    telephone='+22700000001',
                    ecole_id=ecole_defaut.id
                )
                prof_profile = Professeur(
                    nom=prof_user.nom,
                    prenom=prof_user.prenom,
                    date_naissance=datetime(1990, 1, 1),
                    adresse='Adresse par défaut',
                    telephone=prof_user.telephone,
                    email=prof_user.email,
                    specialite='Informatique',
                    matieres_enseignees='Maths,Physique',
                    photo='default_prof.png',
                    date_embauche=datetime.utcnow(),
                    ecole_id=ecole_defaut.id
                )
                prof_user.professeur_rel = prof_profile
                db.session.add(prof_user)
                db.session.add(prof_profile)
                app.logger.info("Professeur créé : prof@ecole.ne / prof123")

            # Parent
            if not Utilisateur.query.filter_by(email='parent@ecole.ne').first():
                parent_user = Utilisateur(
                    nom='Parent',
                    prenom='Test',
                    email='parent@ecole.ne',
                    mot_de_passe=generate_password_hash('parent123'),
                    role='parent',
                    telephone='+22700000002',
                    ecole_id=ecole_defaut.id
                )
                db.session.add(parent_user)
                app.logger.info("Parent créé : parent@ecole.ne / parent123")

            db.session.commit()
            app.logger.info("Utilisateurs par défaut créés avec profils correspondants")

        creer_utilisateurs_par_defaut()

        # -------------------
        # Correction des données au démarrage dans un thread
        # -------------------
        from .startup import corriger_donnees
        
        def startup_correction():
            with app.app_context():
                corriger_donnees()

        threading.Thread(target=startup_correction).start()

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