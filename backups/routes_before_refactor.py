# app/routes.py
# ==============================================================================
# IMPORTS ET CONFIGURATION
# ==============================================================================

# Importations standards Flask et extensions
from flask import (
    Blueprint, render_template, redirect, url_for, flash, request,
    send_file, jsonify, session, current_app, send_from_directory, abort
)
from flask_login import (
    login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps, lru_cache
import io
import os
import re
import sys
import json
import base64
from sqlalchemy import func
import uuid
import qrcode
import smtplib
import shutil
import random
import string
import secrets
import sqlite3
import zipfile
import tempfile
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta, date
from .middleware import ecole_required
import pandas as pd
from unidecode import unidecode
from sqlalchemy import func, literal, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload, aliased
from flask_bcrypt import Bcrypt
from itsdangerous import URLSafeTimedSerializer
from markupsafe import escape
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
# ReportLab (PDF/Excel)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# Email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Application interne (app/*)
from . import db
from app.models import (
    Utilisateur, Eleve, Professeur, Cours, Note, Paiement, Absence,
    Bulletin, EmploiTemps, Classe, Inscription, HistoriqueImport,
    Ecole, Presence, PeriodeBulletin, JournalCorrection,
    professeur_classes, AnneeScolaire, gestion_ecole
)
from app.forms import (
    LoginForm, EleveForm, NoteForm, PaiementForm, AbsenceForm,
    ProfesseurForm, CoursForm, ParentLoginForm, CreateUserForm,
    ResetPasswordForm, DeleteForm, EcoleForm, ChoisirEcoleForm,
    AjouterEmploiForm, ClasseForm, PeriodeForm, AssignerClassesForm,
    CSRFForm, GererEcolesForm
)
from app.authorization import (
    role_required, parent_access_required, check_parent_access
)
from app.middleware import (
    filtre_par_ecole, get_ecole_courante, require_ecole
)
from app.multi_ecoles_creation import ajouter_ecole_id
from app.multi_ecoles_security import ecole_access_required
from app.utils import (
    get_ecole_filter_query, bulletins_accessible_pour_parent
)
from app.notifications import (
    envoyer_email, envoyer_telegram, envoyer_telegram_image,
    TELEGRAM_CHAT_ID
)

# Alias utile
from app.models import Professeur as User

# Initialisation Bcrypt
bcrypt = Bcrypt()

# Ajouter le chemin parent si nécessaire
sys.path.append(str(Path(__file__).parent.parent.resolve()))

# Configuration du blueprint principal
main = Blueprint('main', __name__)


# ======================================================================
# Configuration Email
# ======================================================================
try:
    SMTP_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('MAIL_PORT', 587))
    EMAIL_ADDRESS = os.environ['MAIL_USERNAME']  # ✅ correspond à ton .env
    EMAIL_PASSWORD = os.environ['MAIL_PASSWORD']  # ✅ mot de passe d’application
except KeyError as e:
    raise RuntimeError(f"⚠️ Variable d'environnement manquante : {e.args[0]} (nécessaire pour l'envoi d'e-mails)")

# ======================================================================
# FONCTIONS UTILITAIRES
# ======================================================================
def envoyer_email(destinataire, sujet, message):
    """Fonction pour envoyer des emails de notification via SMTP"""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = destinataire
        msg['Subject'] = sujet
        msg.attach(MIMEText(message, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()

        current_app.logger.info(f"📧 Email envoyé à {destinataire}")
        return True

    except Exception as e:
        current_app.logger.error(f"Erreur lors de l'envoi de l'email: {e}")
        return False


def envoyer_sms(numero, message):
    """Fonction pour envoyer des SMS (simulée, à implémenter avec un service SMS)"""
    current_app.logger.info(f"📱 SMS simulé à {numero}: {message}")
    return True



# ==============================================================================
# ROUTES D'AUTHENTIFICATION ET DE SESSION
# ==============================================================================
@main.route('/')
@login_required
def index():
    """Route principale - Redirige vers le dashboard approprié selon le rôle"""
    current_user.dernier_acces = datetime.utcnow()
    db.session.commit()

    # -----------------------------
    # ADMIN / SUPER_ADMIN
    # -----------------------------
    if current_user.role in ['admin', 'super_admin']:
        ecole_id = current_user.ecole_id  # ✅ Filtrage multi-écoles
        stats = {
            'total_eleves': Eleve.query.filter_by(ecole_id=ecole_id).count(),
            'total_professeurs': Professeur.query.filter_by(ecole_id=ecole_id).count(),
            'total_cours': Cours.query.filter_by(ecole_id=ecole_id).count(),
            'paiements_attente': Paiement.query.filter_by(ecole_id=ecole_id, statut='en attente').count(),
            'eleves_nouveaux': Eleve.query.filter(
                Eleve.ecole_id==ecole_id,
                Eleve.date_inscription >= datetime.utcnow().replace(day=1)
            ).count()
        }
        return render_template('index.html', stats=stats)

    # -----------------------------
    # ENSEIGNANT / PROFESSEUR
    # -----------------------------
    elif current_user.role in ['enseignant', 'professeur']:
        emploi_temps = EmploiTemps.query.filter_by(professeur_id=current_user.id).all()
        return render_template('enseignant_home.html', emploi_temps=emploi_temps)

    # -----------------------------
    # PARENT
    # -----------------------------
    elif current_user.role == 'parent':
        return render_template('parent_home.html')

    # -----------------------------
    # ROLE INCONNU
    # -----------------------------
    else:
        flash("Rôle inconnu. Veuillez contacter l'administrateur.", "warning")
        logout_user()
        return redirect(url_for('main.login'))




# --- AJOUTS POUR CORRIGER L'ERREUR limiter ---
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import current_app

# Initialisation locale et sécurisée du limiter
try:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://"
    )
    # Lier automatiquement à l'application si elle existe
    if current_app:
        limiter.init_app(current_app)
except Exception:
    # En cas d'import avant création de l'app, on crée un faux objet neutre
    class DummyLimiter:
        def limit(self, *_args, **_kwargs):
            def decorator(f):
                return f
            return decorator
    limiter = DummyLimiter()
# --- FIN AJOUT ---
# imports nécessaires (ajoute en haut de ton module)
from markupsafe import escape
from flask import current_app, request, session, redirect, url_for, render_template, flash
from flask_login import login_user, logout_user, current_user, login_required
from sqlalchemy.orm import selectinload
from flask_limiter.util import get_remote_address

# NOTE: initialisation du limiter -> dans create_app(app):
# from flask_limiter import Limiter
# limiter = Limiter(key_func=get_remote_address, default_limits=None, storage_uri="redis://localhost:6379")
# limiter.init_app(app)

# --------------------
# Route login (améliorée)
# --------------------
@main.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", key_func=get_remote_address)  # limite par IP
def login():
    """Route de connexion principale pour tous les utilisateurs avec sécurité multi-écoles"""
    if current_user.is_authenticated:
        role = getattr(current_user, "role", None)
        endpoint_par_role = {
            "admin": "main.admin_dashboard",
            "super_admin": "main.admin_dashboard",
            "enseignant": "main.enseignant_dashboard",
            "professeur": "main.enseignant_dashboard",
            "parent": "main.parent_dashboard",
        }
        return redirect(url_for(endpoint_par_role.get(role, "main.index")))

    form = LoginForm()
    if form.validate_on_submit():
        # sanitize + normaliser l'identifiant
        identifiant = escape(form.email.data.strip().lower())

        # Option: implementer un throttle/lockout par identifiant ici (compte)
        # Exemple (pseudo): if too_many_failed_attempts(identifiant): flash(...); return redirect(...)

        # Recherche utilisateur par email (case-insensitive) ou telephone
        # Assure-toi d'avoir les colonnes indexées pour la perf
        query = Utilisateur.query.filter(
            (Utilisateur.email.ilike(identifiant)) | (Utilisateur.telephone == identifiant)
        )
        utilisateur = query.first()

        # IP via get_remote_address (plus fiable avec flask-limiter)
        ip = get_remote_address()

        if utilisateur and check_password_hash(utilisateur.mot_de_passe, form.mot_de_passe.data):
            # utilisateur existe et mot de passe correct

            # Vérification école pour tous sauf super_admin
            if utilisateur.role != "super_admin" and not utilisateur.ecole_id:
                flash("Votre compte n'est associé à aucune école. Contactez l'administrateur.", "danger")
                current_app.logger.warning(f"Connexion échouée (pas d'école) pour {identifiant} depuis {ip}")
                return redirect(url_for("main.login"))

            # Nettoyage / mitigation session fixation
            session_keys = list(session.keys())
            for k in session_keys:
                session.pop(k, None)

            # Mapping rôles
            role_login = "enseignant" if utilisateur.role == "professeur" else utilisateur.role

            login_user(utilisateur)  # tu peux ajouter remember=form.remember.data si tu veux
            session["role"] = role_login

            # ASSIGNATION ÉCOLE
            if utilisateur.role == "admin" and utilisateur.ecole_id:
                session["ecole_id"] = utilisateur.ecole_id
                current_app.logger.info(f"École assignée automatiquement à {utilisateur.email} (admin) depuis {ip}")
            elif utilisateur.role == "super_admin":
                # get_ecole_filter_query doit être défini ailleurs : renvoie query Ecole (filtrée si nécessaire)
                premiere_ecole = get_ecole_filter_query(Ecole).first()
                if premiere_ecole:
                    session["ecole_id"] = premiere_ecole.id
                    current_app.logger.info(f"École par défaut assignée à super_admin {utilisateur.email} depuis {ip}")

            # Logging succinct (éviter d'écrire info sensibles)
            current_app.logger.info(f"Connexion réussie pour utilisateur id={utilisateur.id} depuis {ip} rôle={role_login}")

            # traitement safe du next param (ne pas rediriger vers un domaine externe)
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'):
                next_page = None

            endpoint_par_role = {
                "admin": "main.admin_dashboard",
                "super_admin": "main.admin_dashboard",
                "enseignant": "main.enseignant_dashboard",
                "parent": "main.parent_dashboard",
            }
            return redirect(next_page) if next_page else redirect(url_for(endpoint_par_role.get(role_login, "main.index")))
        else:
            # échec de connexion
            current_app.logger.warning(f"Tentative de connexion échouée pour identifiant={identifiant} depuis {ip}")
            flash('Identifiant ou mot de passe incorrect', 'danger')

    return render_template('login.html', form=form)


# --------------------
# Portal parent (amélioré)
# --------------------
@main.route('/portal_parent')
@login_required
@role_required('parent')
def portal_parent():
    """Portail parent sécurisé multi-écoles avec vue consolidée des enfants"""
    try:
        # Vérifier que le parent a bien une ecole_id (si la logique le requiert)
        if not getattr(current_user, "ecole_id", None):
            flash("Votre compte parent n'est associé à aucune école.", "warning")
            return redirect(url_for('main.parent_dashboard'))

        # Requête filtrée strictement par parent ET par école (prévenir fuite de données)
        enfants = (
            db.session.query(Eleve)
            .filter(
                Eleve.parent_id == current_user.id,
                Eleve.ecole_id == current_user.ecole_id
            )
            .options(
                selectinload(Eleve.notes),
                selectinload(Eleve.absences),
                selectinload(Eleve.paiements)
            )
            .all()
        )

        if not enfants:
            flash("Aucun élève associé à votre compte parent dans votre école", "warning")
            return redirect(url_for('main.parent_dashboard'))

        # Calculs légers côté application : acceptable si le nombre d'enfants est limité
        for eleve in enfants:
            eleve.notes_sorted = sorted(eleve.notes, key=lambda n: n.date_evaluation or datetime.min, reverse=True)
            eleve.absences_sorted = sorted(eleve.absences, key=lambda a: a.date_absence or datetime.min, reverse=True)
            eleve.paiements_sorted = sorted(eleve.paiements, key=lambda p: p.date_paiement or datetime.min, reverse=True)

            total_pondere = sum((n.valeur or 0) * (n.coefficient or 0) for n in eleve.notes)
            total_coefficients = sum((n.coefficient or 0) for n in eleve.notes)
            eleve.moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients else 0

            eleve.total_notes = len(eleve.notes)
            eleve.total_absences = len(eleve.absences)
            eleve.total_paiements = len(eleve.paiements)

        current_app.logger.info(f"Parent id={current_user.id} a accédé au portal_parent depuis {get_remote_address()}")

        return render_template('portal_parent.html', enfants=enfants)

    except Exception as e:
        current_app.logger.exception(f"Erreur portal_parent pour parent id={current_user.id}")
        flash("Une erreur est survenue. Veuillez réessayer plus tard.", "danger")
        return redirect(url_for('main.parent_dashboard'))


# --------------------
# Logout (amélioré)
# --------------------
@main.route('/logout')
@login_required
def logout():
    """Déconnexion générale sécurisée de l'application"""

    current_app.logger.info(f"Déconnexion de l'utilisateur id={current_user.id} - rôle={current_user.role}")

    # Clear session puis logout
    session_keys = list(session.keys())
    for k in session_keys:
        session.pop(k, None)

    logout_user()

    # Optionnel: force session cookie nouvelle génération côté client (si utilisé)
    # session.modified = True

    flash('Vous avez été déconnecté avec succès', 'info')
    return redirect(url_for('main.login'))

# ==============================================================================
# ROUTES ADMINISTRATIVES - GESTION DES UTILISATEURS
# ==============================================================================

@main.route('/admin/create_user', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def create_user():
    """Création d'utilisateurs par l'administrateur"""
    form = CreateUserForm()
    form.eleve_id.choices = [(0, "--- Aucun ---")] + [(e.id, f"{e.nom} {e.prenom} ({e.classe})") for e in get_ecole_filter_query(Eleve).all()]

    if form.validate_on_submit():
        try:
            hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
            user = Utilisateur(
                nom=form.nom.data,
                email=form.email.data,
                mot_de_passe=hashed_password,
                role=form.role.data
            )
            
            if form.role.data == 'parent' and form.eleve_id.data != 0:
                user.eleve_id = form.eleve_id.data

            db.session.add(user)
            db.session.commit()
            flash(f"Utilisateur {user.nom} créé avec succès !", "success")
            return redirect(url_for('main.dashboard'))
            
        except IntegrityError as e:
            db.session.rollback()
            if 'email' in str(e):
                flash("Cet email est déjà utilisé.", "danger")
            else:
                flash("Erreur lors de la création de l'utilisateur.", "danger")
                current_app.logger.error(f"IntegrityError: {e}")
        except Exception as e:
            db.session.rollback()
            flash("Erreur inattendue lors de la création.", "danger")
            current_app.logger.error(f"Erreur création utilisateur: {e}")

    return render_template('admin/create_user.html', form=form)

# ==============================================================================
# GESTION DES ÉLÈVES
# ==============================================================================
# ---------------- Liste des élèves ----------------
# ---------------- Liste des élèves ----------------
@main.route('/eleves')
@login_required
@role_required('admin', 'enseignant', 'super_admin')
def eleves():
    """Liste des élèves, filtrée par école et rôle avec sécurité multi-écoles"""
    page = request.args.get('page', 1, type=int)
    per_page = 50  # peut rester à 50 pour la pagination

    # ---------------- Base query avec relations pour éviter N+1 ----------------
    base_query = Eleve.query.options(
        db.selectinload(Eleve.classe),
        db.selectinload(Eleve.parent)
    )

    # ---------------- Filtrage multi-écoles selon rôle ----------------
    if current_user.role == 'admin':
        eleves_query = filtre_par_ecole(base_query, Eleve).order_by(Eleve.nom, Eleve.prenom)

    elif current_user.role == 'enseignant':
        professeur_id = getattr(current_user.professeur_rel, 'id', None)
        eleves_query = (
            base_query.join(Classe)
            .filter(
                Classe.ecole_id == current_user.ecole_id,
                db.or_(
                    Classe.professeur_id == professeur_id,
                    Classe.professeurs_assignes.any(id=professeur_id)
                ),
                Eleve.ecole_id == current_user.ecole_id
            )
            .order_by(Eleve.nom, Eleve.prenom)
        )

    elif current_user.role == 'super_admin':
        ecole_id = session.get('ecole_id')
        if ecole_id:
            eleves_query = base_query.filter(Eleve.ecole_id == ecole_id).order_by(Eleve.nom, Eleve.prenom)
        else:
            eleves_query = base_query.order_by(Eleve.nom, Eleve.prenom)

    else:
        abort(403)  # Sécurité supplémentaire

    # ---------------- Pagination ----------------
    eleves_pagination = eleves_query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template('eleves.html', eleves=eleves_pagination)

# ---------------- Ajouter un élève ----------------
@main.route('/ajouter_eleve', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_eleve():
    """Ajout d’un élève avec contrôle de cohérence, sécurité multi-écoles et notifications parent."""
    form = EleveForm()

    # ---------------- École courante ----------------
    if current_user.role == 'super_admin':
        ecole_id = session.get('ecole_id')
        if not ecole_id:
            flash("⚠️ Aucune école sélectionnée pour le super-admin.", "danger")
            return redirect(url_for('main.eleves'))
    else:
        ecole_id = current_user.ecole_id

    # ---------------- Année scolaire active ----------------
    annees_ecole = AnneeScolaire.query.filter_by(ecole_id=ecole_id).order_by(AnneeScolaire.id.desc()).all()
    annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()
    if not annee_active and annees_ecole:
        annee_active = annees_ecole[0]

    # ---------------- Classes ----------------
    classes = Classe.query.filter_by(ecole_id=ecole_id).order_by(Classe.nom).all()
    form.classe_id.choices = [(c.id, c.nom_complet) for c in classes]
    if not classes:
        flash("⚠️ Aucune classe disponible. Créez une classe avant d’ajouter un élève.", "warning")

    # ---------------- Parents ----------------
    form.parent_id.choices = [(0, "--- Aucun parent ---")]
    parents = Utilisateur.query.filter_by(role='parent', ecole_id=ecole_id).order_by(Utilisateur.nom).all()
    form.parent_id.choices += [(p.id, f"{p.prenom or ''} {p.nom} ({p.email})") for p in parents]

    # ---------------- Soumission du formulaire ----------------
    if form.validate_on_submit():
        try:
            # 🔸 Vérif classe valide avec filtre multi-écoles
            classe_selectionnee = filtre_par_ecole(Classe.query, Classe).filter_by(id=form.classe_id.data).first()
            if not classe_selectionnee or classe_selectionnee.ecole_id != ecole_id:
                flash("❌ Classe invalide ou non autorisée.", "danger")
                return redirect(url_for('main.ajouter_eleve'))

            # ---------------- Gestion parent ----------------
            parent_id_final = None
            code_parent = None
            email_parent = request.form.get("parent_email")
            telephone_parent = request.form.get("parent_telephone")

            # 🔸 Nouveau parent
            if form.parent_id.data == 0 and any([
                request.form.get("parent_nom"),
                request.form.get("parent_prenom"),
                email_parent,
                telephone_parent
            ]):
                # Vérifie doublon parent par email
                if email_parent and Utilisateur.query.filter_by(email=email_parent, role='parent', ecole_id=ecole_id).first():
                    flash("❌ Cet email est déjà utilisé par un autre parent.", "danger")
                    return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                                           annee_active=annee_active, classes=classes)

                # Génère le code parent
                code_parent = Eleve.generer_code_parent()
                parent_utilisateur = Utilisateur(
                    nom=request.form.get("parent_nom"),
                    prenom=request.form.get("parent_prenom"),
                    email=email_parent,
                    telephone=telephone_parent,
                    role='parent',
                    ecole_id=ecole_id
                )
                parent_utilisateur.set_mot_de_passe(code_parent)
                db.session.add(parent_utilisateur)
                db.session.flush()  # Pour récupérer l'ID
                parent_id_final = parent_utilisateur.id

                email_parent = parent_utilisateur.email
                telephone_parent = parent_utilisateur.telephone

            else:
                # 🔸 Parent existant avec filtre multi-écoles
                parent_id_final = form.parent_id.data or None
                parent_obj = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=parent_id_final).first() if parent_id_final else None
                if parent_obj:
                    email_parent = parent_obj.email
                    telephone_parent = parent_obj.telephone
                elif parent_obj is None and parent_id_final:
                    flash("❌ Ce parent n'appartient pas à votre école.", "danger")
                    return redirect(url_for('main.ajouter_eleve'))

            # ---------------- Création élève ----------------
            nouvel_eleve = Eleve(
                nom=form.nom.data.strip(),
                prenom=form.prenom.data.strip(),
                date_naissance=form.date_naissance.data,
                lieu_naissance=form.lieu_naissance.data.strip() if form.lieu_naissance.data else None,
                adresse=form.adresse.data.strip() if form.adresse.data else None,
                
                # Suppression des champs email/téléphone élève
                contact_parent=telephone_parent,
                email_parent=email_parent.lower() if email_parent else None,
                
                classe_id=form.classe_id.data,
                frais_annuels=form.frais_annuels.data or 0.0,
                code_parent=code_parent,
                parent_id=parent_id_final,
                ecole_id=ecole_id
            )
            db.session.add(nouvel_eleve)
            db.session.flush()

            # ---------------- Inscription automatique aux cours ----------------
            if getattr(classe_selectionnee, "cours", None):
                inscriptions = [
                    Inscription(
                        eleve_id=nouvel_eleve.id,
                        classe_id=classe_selectionnee.id,
                        cours_id=c.id,
                        annee_scolaire_id=classe_selectionnee.annee_scolaire_id
                    ) for c in classe_selectionnee.cours
                ]
                db.session.bulk_save_objects(inscriptions)

            db.session.commit()

            # ---------------- Notifications après commit ----------------
            if parent_id_final and code_parent:
                try:
                    import qrcode, io, base64
                    qr_data = f"Parent: {parent_utilisateur.prenom} {parent_utilisateur.nom}\nEmail: {email_parent}\nMot de passe: {code_parent}"
                    qr = qrcode.QRCode(
                        version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4
                    )
                    qr.add_data(qr_data)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    buffer.seek(0)
                    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

                    # Envoi email
                    if email_parent:
                        from app.notifications import envoyer_email
                        sujet = "Création de votre compte parent"
                        message = f"""
                        <html>
                        <body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:20px;">
                            <div style="max-width:600px; margin:auto; background:#fff; border-radius:10px; padding:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);">
                                <h2 style="color:#4CAF50;">Bonjour {parent_utilisateur.prenom or ''} {parent_utilisateur.nom},</h2>
                                <p>Un compte parent a été créé pour suivre la scolarité de votre enfant.</p>
                                <h3>Vos identifiants :</h3>
                                <ul>
                                    <li><b>Email :</b> {email_parent}</li>
                                    <li><b>Mot de passe :</b> {code_parent}</li>
                                </ul>
                                <p>
                                    <a href="{request.host_url}login_parent" style="display:inline-block; padding:10px 20px; background:#4CAF50; color:#fff; text-decoration:none; border-radius:5px;">Se connecter</a>
                                </p>
                                <img src="data:image/png;base64,{qr_base64}" width="150" height="150"/><br>
                                <p style="font-size:12px; color:#555;">Cordialement,<br>L’administration</p>
                            </div>
                        </body>
                        </html>
                        """
                        envoyer_email(email_parent, sujet, message)

                    # Envoi Telegram (optionnel)
                    try:
                        from app.notifications import envoyer_telegram_image
                        envoyer_telegram_image(buffer, caption=f"👨‍👩‍👧 Nouveau compte parent : {parent_utilisateur.prenom} {parent_utilisateur.nom}")
                    except Exception as e:
                        current_app.logger.warning(f"Erreur Telegram parent : {e}")

                except Exception as e:
                    current_app.logger.error(f"Erreur QR/Email/Telegram : {e}")

            flash("✅ Élève ajouté avec succès et inscrit à tous les cours de sa classe.", "success")
            return redirect(url_for('main.eleves'))

        except Exception as e:
            db.session.rollback()
            import traceback
            current_app.logger.error(f"Erreur ajout élève: {e}\n{traceback.format_exc()}")
            flash("❌ Erreur lors de l'ajout de l'élève. Veuillez vérifier les informations saisies.", "danger")

        
    # ---------------- Affichage du formulaire ----------------
    return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                           annee_active=annee_active, classes=classes)

# API pour récupérer toutes les classes


# --- API : Liste des classes ---
@main.route('/api/classes')
@login_required
@role_required('admin', 'enseignant')
@ecole_required
def api_classes():
    """Retourne la liste des classes filtrée par école et année active (JSON)"""
    # Détermination de l'école
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        return jsonify([]), 403  # Super-admin sans école sélectionnée

    # Récupération de l'année scolaire active
    annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()

    # Filtrage des classes
    classes_query = Classe.query.filter_by(ecole_id=ecole_id)
    if annee_active:
        classes_query = classes_query.filter_by(annee_scolaire_id=annee_active.id)
    classes = classes_query.order_by(Classe.nom).all()

    # Logging sécurisé
    current_app.log_correction(
        action="consultation",
        description="Liste des classes récupérée via API",
        ecole_id=ecole_id,
        cible_type="classe",
        cible_id=None,
        ancienne_valeur=None,
        nouvelle_valeur=None,
        niveau="info"
    )

    return jsonify([{'id': c.id, 'nom': c.nom} for c in classes])


# --- API : Liste des élèves par classe ---
@main.route('/api/eleves/classe/<int:classe_id>')
@login_required
@role_required('admin', 'enseignant', 'super_admin')
@ecole_required
def api_eleves_par_classe(classe_id):
    """Retourne la liste des élèves d'une classe filtrée par école et année active (JSON)"""

    # --- Détermination de l'école selon le rôle ---
    if current_user.role == 'super_admin':
        ecole_id = None  # super_admin n'a pas besoin d'école
    else:
        ecole_id = current_user.ecole_id
        if not ecole_id:
            return jsonify({'eleves': []}), 403

    # --- Récupération de l'année scolaire active (uniquement si ecole_id défini) ---
    annee_active = None
    if ecole_id:
        annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()

    # --- Vérification que la classe appartient à l'école (si admin/enseignant) ---
    if ecole_id:
        classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
        if not classe:
            return jsonify({'eleves': []}), 404
        query = Eleve.query.filter(Eleve.classe_id == classe_id, Eleve.ecole_id == ecole_id)
        if annee_active:
            query = query.join(Classe).filter(Classe.annee_scolaire_id == annee_active.id)
    else:
        # super_admin : accès global
        classe = Classe.query.get(classe_id)
        if not classe:
            return jsonify({'eleves': []}), 404
        query = Eleve.query.filter(Eleve.classe_id == classe_id)

    # --- Récupération des élèves ---
    eleves = query.order_by(Eleve.nom, Eleve.prenom).all()

    # --- Construction du JSON CORRIGÉ ---
    eleves_list = [
        {
            'id': e.id,
            'nom': e.nom,
            'prenom': e.prenom,
            'telephone': e.contact_parent or '-',  # ← CORRECTION ICI : utiliser contact_parent au lieu de telephone
            'classe': e.classe.nom if e.classe else "Sans classe",
            'parent': f"{e.parent.prenom} {e.parent.nom}" if e.parent else "Non assigné"
        } for e in eleves
    ]

    # --- Logging sécurisé ---
    current_app.log_correction(
        action="consultation",
        description=f"Liste des élèves de la classe {classe_id} récupérée via API",
        ecole_id=ecole_id,
        cible_type="eleve",
        cible_id=None,
        ancienne_valeur=None,
        nouvelle_valeur=None,
        niveau="info"
    )

    return jsonify({'eleves': eleves_list})

# --- Export PDF des notes d'un élève ---
@main.route('/eleve/<int:id>/export_notes_pdf') 
@login_required
@role_required('super-admin', 'admin', 'enseignant', 'parent')
def export_notes_eleve_pdf(id):
    """Génère et retourne le relevé de notes PDF d'un élève avec contrôle multi-écoles"""
    eleve = Eleve.query.get_or_404(id)

    # Vérification des accès selon rôle
    if current_user.role == 'parent' and not check_parent_access(id):
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    if current_user.role in ['admin', 'enseignant'] and eleve.ecole_id != current_user.ecole_id:
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.eleves'))

    if current_user.role == 'enseignant' and (not eleve.classe or eleve.classe.professeur_id != current_user.id):
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.eleves'))

    # Création PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
    styles = getSampleStyleSheet()
    elements = []

    # Nom de l'école et titre
    ecole_nom = eleve.ecole.nom if eleve.ecole else "N/A"
    elements.append(Paragraph(f"{ecole_nom}", ParagraphStyle('SchoolTitle', fontSize=18, alignment=1, spaceAfter=5, fontName='Helvetica-Bold')))
    elements.append(Paragraph("RELEVÉ DE NOTES", ParagraphStyle('Title', fontSize=16, alignment=1, spaceAfter=10, fontName='Helvetica-Bold')))

    # Année scolaire active
    annee_active = AnneeScolaire.query.filter_by(
        ecole_id=eleve.ecole_id,
        statut="active"
    ).first()
    annee_text = annee_active.nom if annee_active else "N/A"
    elements.append(Paragraph(f"<b>Année scolaire :</b> {annee_text}", styles['Normal']))
    elements.append(Spacer(1, 10))

    # Informations élève
    premiere_annee = str(eleve.annee_premiere_ecole) if eleve.annee_premiere_ecole else "N/A"
    info_text = f"""
    <b>Élève :</b> {eleve.prenom} {eleve.nom}<br/>
    <b>Classe :</b> {eleve.classe.nom if eleve.classe else 'Non assignée'}<br/>
    <b>Date de naissance :</b> {eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else 'Non renseignée'}<br/>
    <b>Parent :</b> {eleve.parent.nom if eleve.parent else 'N/A'}<br/>
    <b>1ère année dans l'école :</b> {premiere_annee}<br/>
    <b>Date d'édition :</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}
    """
    elements.append(Paragraph(info_text, styles['Normal']))
    elements.append(Spacer(1, 20))

    # Notes filtrées par année active
    notes = [n for n in eleve.notes if not annee_active or n.annee_id == annee_active.id]
    notes = sorted(notes, key=lambda n: (n.cours.nom if n.cours else "", n.date_evaluation))

    # Création d'un tableau unique
    data = [['Matière', 'Date', 'Type d\'évaluation', 'Note', 'Coefficient']]
    total_pondere_global = 0
    total_coefficients_global = 0

    for note in notes:
        cours_nom = note.cours.nom if note.cours else "N/A"
        data.append([
            cours_nom,
            note.date_evaluation.strftime('%d/%m/%Y') if note.date_evaluation else "N/A",
            note.type_evaluation or "N/A",
            str(note.valeur),
            str(note.coefficient)
        ])
        total_pondere_global += note.valeur * note.coefficient
        total_coefficients_global += note.coefficient

    # Moyenne générale
    moyenne_generale = round(total_pondere_global / total_coefficients_global, 2) if total_coefficients_global > 0 else 0
    data.append(['', '', '', '', ''])
    data.append(['', '', 'Moyenne générale', str(moyenne_generale), str(total_coefficients_global)])

    table = Table(data, colWidths=[100, 70, 150, 60, 60])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#4B8BBE")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, -2), (-1, -1), colors.HexColor("#FFE699")),
        ('FONTNAME', (0, -2), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
    ]))

    elements.append(table)
    doc.build(elements)
    buffer.seek(0)

    # Logging export
    current_app.logger.info(f"Export PDF notes élève {eleve.id} ({eleve.prenom} {eleve.nom}) par {current_user.id}")

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"releve_notes_{eleve.prenom}_{eleve.nom}.pdf",
        mimetype='application/pdf'
    )

# --- Export Excel complet des élèves (avec année filtrée et mise en forme) ---
@main.route('/eleves/export_excel')
@login_required
@role_required('super-admin', 'admin')
def export_eleves_excel():
    # Année scolaire active
    annee_active = AnneeScolaire.query.filter_by(
        statut="active",
        ecole_id=current_user.ecole_id if current_user.role == "admin" else None
    ).first()

    # Filtrage selon rôle et année
    if current_user.role == 'super-admin':
        eleves = get_ecole_filter_query(Eleve).all()
    else:
        eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).all()

    # Filtrer seulement élèves inscrits dans l'année active
    if annee_active:
        eleves = [e for e in eleves if e.date_inscription.year <= int(annee_active.nom.split('-')[0])]

    data = {
        'ID': [e.id for e in eleves],
        'Nom': [e.nom for e in eleves],
        'Prénom': [e.prenom for e in eleves],
        'Date de naissance': [e.date_naissance.strftime('%d/%m/%Y') if e.date_naissance else '' for e in eleves],
        'Classe': [e.classe.nom if e.classe else "Non assignée" for e in eleves],
        'Téléphone': [e.telephone for e in eleves],
        'Email': [e.email for e in eleves],
        'Téléphone parent': [e.contact_parent for e in eleves],
        'Email parent': [e.email_parent for e in eleves],
        'Date inscription': [e.date_inscription.strftime('%d/%m/%Y') for e in eleves]
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Élèves', index=False)

        # Mise en forme Excel : largeur automatique et en-têtes en gras
        ws = writer.sheets['Élèves']
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            adjusted_width = max_length + 2
            ws.column_dimensions[column].width = adjusted_width
        for cell in ws[1]:
            cell.font = cell.font.copy(bold=True)

    output.seek(0)

    # Log de l'export
    current_app.logger.info(f"Export Excel élèves par {current_user.id} ({current_user.role})")

    return send_file(
        output,
        as_attachment=True,
        download_name="liste_eleves.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ==============================================================================
# GESTION DES PROFESSEURS
# ==============================================================================


# ---------------- Liste des professeurs ----------------

import json  # ajouter en haut de ton fichier routes.py
# ---------------- Liste des professeurs ----------------
# ---------------- Liste des professeurs ----------------
# --- Liste des professeurs ---
@main.route('/professeurs')
@login_required
@role_required('admin')
def professeurs():
    """Liste de tous les professeurs avec pagination filtrée par école et recherche optionnelle"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = request.args.get('search', '', type=str).strip()

    # Filtrage par école de l'utilisateur
    profs_query = Professeur.query.filter_by(ecole_id=current_user.ecole_id)

    # Recherche sur nom, prénom, email et spécialité
    if search:
        profs_query = profs_query.filter(
            db.or_(
                Professeur.nom.ilike(f"%{search}%"),
                Professeur.prenom.ilike(f"%{search}%"),
                Professeur.email.ilike(f"%{search}%"),
                Professeur.specialite.ilike(f"%{search}%")
            )
        )

    # Tri et pagination
    profs_query = profs_query.order_by(Professeur.nom, Professeur.prenom)
    profs_pagination = profs_query.paginate(page=page, per_page=per_page, error_out=False)
    delete_form = DeleteForm()

    # Journalisation sécurisée
    current_app.log_correction(
        action="consultation",
        description=f"Consultation liste professeurs page {page} (search='{search}')",
        ecole_id=current_user.ecole_id,
        cible_type="professeur",
        cible_id=None,
        ancienne_valeur=None,
        nouvelle_valeur=None,
        niveau="info"
    )

    return render_template(
        'professeurs.html',
        professeurs=profs_pagination,
        delete_form=delete_form,
        search=search
    )


# --- Ajouter un professeur ---
@main.route('/ajouter_professeur', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_professeur():
    """Ajout d'un professeur avec contrôle de cohérence et notifications"""
    form = ProfesseurForm()
    ecole_id = current_user.ecole_id

    if form.validate_on_submit():
        try:
            # ---------------- Code professeur ----------------
            code_prof = form.code_prof.data.strip() if form.code_prof.data else Professeur.generer_code()

            # ---------------- Vérification unicité ----------------
            if Professeur.query.filter_by(code_prof=code_prof, ecole_id=ecole_id).first():
                flash("Ce code professeur existe déjà dans votre école.", "danger")
                return redirect(url_for('main.ajouter_professeur'))

            if Utilisateur.query.filter_by(email=form.email.data, ecole_id=ecole_id).first():
                flash("Cet email est déjà utilisé dans votre école.", "danger")
                return redirect(url_for('main.ajouter_professeur'))

            # ---------------- Création utilisateur ----------------
            utilisateur = Utilisateur(
                nom=form.nom.data.strip(),
                prenom=form.prenom.data.strip(),
                email=form.email.data.lower(),
                mot_de_passe=generate_password_hash(code_prof),
                role="professeur",
                telephone=form.telephone.data.strip() if form.telephone.data else None,
                statut="actif",
                ecole_id=ecole_id
            )

            # ---------------- Création professeur ----------------
            nouveau_professeur = Professeur(
                nom=form.nom.data.strip(),
                prenom=form.prenom.data.strip(),
                date_naissance=form.date_naissance.data,
                adresse=form.adresse.data.strip() if form.adresse.data else None,
                telephone=form.telephone.data.strip() if form.telephone.data else None,
                email=form.email.data.lower(),
                specialite=form.specialite.data,
                matieres_enseignees=form.matieres_enseignees.data,
                code_prof=code_prof,
                ecole_id=ecole_id,
                utilisateur=utilisateur
            )

            # ---------------- Commit unique ----------------
            db.session.add(utilisateur)
            db.session.add(nouveau_professeur)
            db.session.commit()

            # ---------------- Journalisation ----------------
            current_app.log_correction(
                action="ajout",
                description=f"Professeur ajouté : {nouveau_professeur.nom} {nouveau_professeur.prenom}",
                ecole_id=ecole_id,
                cible_type="professeur",
                cible_id=nouveau_professeur.id,
                ancienne_valeur=None,
                nouvelle_valeur=json.dumps({
                    "nom": nouveau_professeur.nom,
                    "prenom": nouveau_professeur.prenom,
                    "email": nouveau_professeur.email,
                    "code_prof": nouveau_professeur.code_prof
                }, ensure_ascii=False),
                niveau="info"
            )

            # ---------------- Envoi email ----------------
            if nouveau_professeur.email:
                try:
                    from app.notifications import envoyer_email
                    sujet = "Création de votre compte professeur"
                    message = f"""<html>
                    <body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:20px;">
                        <div style="max-width:600px; margin:auto; background:#fff; border-radius:10px; padding:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);">
                            <h2 style="color:#2196F3;">Bonjour {nouveau_professeur.prenom or ''} {nouveau_professeur.nom},</h2>
                            <p>Votre compte professeur a été créé avec succès !</p>
                            <h3>Vos identifiants :</h3>
                            <ul>
                                <li><b>Email:</b> {nouveau_professeur.email}</li>
                                <li><b>Mot de passe:</b> {code_prof}</li>
                            </ul>
                            <p><a href="{request.host_url}login" style="display:inline-block; padding:10px 20px; background:#2196F3; color:#fff; text-decoration:none; border-radius:5px;">Se connecter</a></p>
                            <hr style="border:none; border-top:1px solid #eee;">
                            <p style="font-size:12px; color:#555;">Ne partagez pas vos identifiants.<br>Cordialement,<br>L'administration</p>
                        </div>
                    </body>
                    </html>"""
                    envoyer_email(nouveau_professeur.email, sujet, message)
                    current_app.logger.info(f"Email envoyé à {nouveau_professeur.email}")
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email: {e}")
                    flash("Professeur ajouté mais email non envoyé.", "warning")

            # ---------------- Notification Telegram ----------------
            try:
                from app.notifications import envoyer_telegram
                telegram_message = (
                    f"👨‍🏫 Nouveau compte professeur créé !\n"
                    f"Nom: {nouveau_professeur.prenom or ''} {nouveau_professeur.nom}\n"
                    f"Email: {nouveau_professeur.email}\n"
                    f"Mot de passe: {code_prof}\n"
                    f"Connexion: {request.host_url}login"
                )
                envoyer_telegram(telegram_message)
                current_app.logger.info("Notification Telegram envoyée")
            except Exception as e:
                current_app.logger.error(f"Erreur envoi Telegram: {e}")

            flash(f"✅ Professeur ajouté avec succès. Code d'accès: {code_prof}", "success")
            return redirect(url_for('main.professeurs'))

        except Exception as e:
            db.session.rollback()
            import traceback
            current_app.logger.error(f"Erreur ajout professeur: {e}\n{traceback.format_exc()}")
            flash("❌ Erreur lors de l'ajout du professeur.", "danger")

    return render_template('ajouter_professeur.html', form=form)

# ==============================================================================
# GESTION DES COURS
# ==============================================================================
# ------------------- Liste des cours -------------------
# ---------------- Page des cours ----------------
@main.route('/cours')
@login_required
@role_required('admin', 'enseignant', 'professeur')
def cours():
    """
    Page de gestion des cours filtrée par école
    Affichage différencié selon le rôle utilisateur
    """
    
    # === INITIALISATION DES DONNÉES DE BASE ===
    ecole_courante = get_ecole_courante()
    delete_form = DeleteForm()
    is_super_admin = getattr(current_user, 'is_super_admin', False)
    
    # === FONCTION UTILITAIRE POUR LA SÉRIALISATION JSON ===
    def cours_to_dict(cours_item):
        """Transforme un objet Cours en dictionnaire pour JSON"""
        return {
            'id': cours_item.id,
            'nom': cours_item.nom,
            'description': cours_item.description or "",
            'coefficient': cours_item.coefficient,
            'classe': {
                'id': cours_item.classe.id,
                'nom': cours_item.classe.nom,
                'niveau': cours_item.classe.niveau
            } if cours_item.classe else None,
            'professeur': {
                'id': cours_item.professeur.id,
                'prenom': cours_item.professeur.prenom,
                'nom': cours_item.professeur.nom
            } if cours_item.professeur else None,
            'notes_count': len(cours_item.notes) if hasattr(cours_item, 'notes') else 0,
            'ecole_id': cours_item.ecole_id
        }

    # === LOGIQUE SPÉCIFIQUE PAR RÔLE ===
    
    if current_user.role == 'admin':
        # === ADMINISTRATEUR ===
        form = CoursForm()
        
        # Récupération des données de l'école
        professeurs = Professeur.query.filter_by(
            ecole_id=ecole_courante.id
        ).order_by(Professeur.nom, Professeur.prenom).all()
        
        classes = Classe.query.filter_by(
            ecole_id=ecole_courante.id
        ).order_by(Classe.niveau, Classe.nom).all()
        
        # Récupération des cours avec filtre super admin
        if is_super_admin:
            tous_cours = Cours.query.order_by(Cours.nom).all()
        else:
            tous_cours = Cours.query.filter_by(
                ecole_id=ecole_courante.id
            ).order_by(Cours.nom).all()
        
        # Peuplement des choix des formulaires
        form.professeur_id.choices = [
            (prof.id, f"{prof.prenom} {prof.nom}") 
            for prof in professeurs
        ]
        form.classe_id.choices = [
            (classe.id, f"{classe.nom} ({classe.niveau})") 
            for classe in classes
        ]
        
        # Calcul des statistiques
        professeurs_actifs = len(set([
            c.professeur_id for c in tous_cours 
            if c.professeur_id
        ]))
        notes_total = sum([
            len(c.notes) for c in tous_cours 
            if hasattr(c, 'notes')
        ])
        cours_total = len(tous_cours)
        
        # Sérialisation JSON
        cours_json = [cours_to_dict(c) for c in tous_cours]
        
    else:
        # === ENSEIGNANT / PROFESSEUR ===
        form = None
        
        # Vérification du profil enseignant
        professeur = Professeur.query.filter_by(
            utilisateur_id=current_user.id,
            ecole_id=ecole_courante.id
        ).first()
        
        if not professeur:
            flash(
                "❌ Profil enseignant introuvable pour cette école.", 
                "danger"
            )
            return redirect(url_for('main.index'))
        
        # Récupération des cours assignés
        mes_cours = Cours.query.filter_by(
            professeur_id=professeur.id,
            ecole_id=ecole_courante.id
        ).order_by(Cours.nom).all()
        
        # Calcul des statistiques
        notes_total = sum([
            len(c.notes) for c in mes_cours 
            if hasattr(c, 'notes')
        ])
        cours_total = len(mes_cours)
        professeurs_actifs = 1  # L'enseignant courant
        
        # Sérialisation JSON
        cours_json = [cours_to_dict(c) for c in mes_cours]

    # === JOURNALISATION DE L'ACTION ===
    current_app.log_correction(
        action="consultation_cours",
        description=(
            f"Consultation de la liste des cours "
            f"({current_user.role} - {cours_total} cours)"
        ),
        ecole_id=ecole_courante.id if not is_super_admin else None,
        cible_type="cours",
        cible_id=None,
        ancienne_valeur=None,
        nouvelle_valeur=None,
        niveau="info"
    )

    # === RENDU DU TEMPLATE ===
    return render_template(
        'cours.html',
        cours=cours_json,
        form=form,
        delete_form=delete_form,
        professeurs_count=professeurs_actifs,
        notes_count=notes_total,
        cours_count=cours_total,
        ecole_nom=ecole_courante.nom if ecole_courante else "Système"
    )



# ---------------- Ajouter un cours ----------------
@main.route('/ajouter_cours', methods=['POST'])
@login_required
@role_required('admin')
def ajouter_cours():
    ecole_courante = get_ecole_courante()
    form = CoursForm()

    # Choix restreints à l'école courante
    form.professeur_id.choices = [
        (p.id, f"{p.prenom} {p.nom}") 
        for p in Professeur.query.filter_by(ecole_id=ecole_courante.id).order_by(Professeur.nom).all()
    ]
    form.classe_id.choices = [
        (c.id, f"{c.nom} ({c.niveau})") 
        for c in Classe.query.filter_by(ecole_id=ecole_courante.id).order_by(Classe.nom).all()
    ]

    if form.validate_on_submit():
        try:
            # Vérification stricte dans l'école courante
            prof = Professeur.query.filter_by(id=form.professeur_id.data, ecole_id=ecole_courante.id).first()
            classe = Classe.query.filter_by(id=form.classe_id.data, ecole_id=ecole_courante.id).first()

            if not prof or not classe:
                flash("Le professeur ou la classe n’appartient pas à votre école.", "danger")
                return redirect(url_for('main.cours'))

            doublon = Cours.query.filter_by(
                nom=form.nom.data,
                classe_id=form.classe_id.data,
                ecole_id=ecole_courante.id
            ).first()
            if doublon:
                flash("Un cours avec ce nom existe déjà pour cette classe.", "danger")
                return redirect(url_for('main.cours'))

            nouveau_cours = Cours(
                nom=form.nom.data,
                description=form.description.data,
                coefficient=form.coefficient.data,
                professeur_id=prof.id,
                classe_id=classe.id,
                ecole_id=ecole_courante.id
            )

            db.session.add(nouveau_cours)
            db.session.commit()

            current_app.log_correction(
                action="ajout",
                description=f"Cours ajouté : {nouveau_cours.nom}",
                ecole_id=ecole_courante.id,
                cible_type="cours",
                cible_id=nouveau_cours.id,
                ancienne_valeur=None,
                nouvelle_valeur=json.dumps({
                    "nom": nouveau_cours.nom,
                    "coefficient": nouveau_cours.coefficient,
                    "professeur_id": nouveau_cours.professeur_id,
                    "classe_id": nouveau_cours.classe_id
                }),
                niveau="info"
            )

            flash('Cours ajouté avec succès', 'success')

        except IntegrityError as e:
            db.session.rollback()
            flash("Erreur d’intégrité (doublon possible).", "danger")
            current_app.logger.error(f"IntegrityError cours: {e}")

        except Exception as e:
            db.session.rollback()
            flash("Erreur inattendue lors de l’ajout du cours.", "danger")
            current_app.logger.error(f"Erreur ajout cours: {e}")

    else:
        flash("Le formulaire contient des erreurs.", "warning")

    return redirect(url_for('main.cours'))

# ------------------- Gestion des notes -------------------
# ------------------- Gestion des notes -------------------
@main.route('/notes', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'enseignant', 'professeur', 'parent')
def notes():
    form = NoteForm()
    ecole_courante = get_ecole_courante()

    # ------------------- Récupération de l'année active -------------------
    annee_active = AnneeScolaire.query.filter_by(
        active=True,
        ecole_id=current_user.ecole_id
    ).first()

    # ------------------- Gestion des élèves selon rôle -------------------
    if current_user.role == 'parent':
        enfants = filtre_par_ecole(Eleve.query.filter_by(parent_id=current_user.id), Eleve).all()
        eleves = enfants
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]
        form.eleve_id.render_kw = {'disabled': True} if len(enfants) == 1 else {}

    elif current_user.role in ['enseignant', 'professeur']:
        professeur = filtre_par_ecole(Professeur.query.filter_by(utilisateur_id=current_user.id), Professeur).first()
        if professeur:
            cours_prof = filtre_par_ecole(Cours.query.filter_by(professeur_id=professeur.id), Cours).all()
            cours_ids = [c.id for c in cours_prof]
            eleves = (
                filtre_par_ecole(Eleve.query.join(Inscription), Eleve)
                .filter(Inscription.cours_id.in_(cours_ids)).all()
            )
            form.eleve_id.choices = [
                (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
                for e in eleves
            ]
            form.cours_id.choices = [(c.id, c.nom) for c in cours_prof]
        else:
            eleves = []
            form.eleve_id.choices = []
            form.cours_id.choices = []
            flash("Aucun professeur n'est associé à votre compte.", "warning")

    else:  # admin
        eleves = filtre_par_ecole(Eleve.query.outerjoin(Classe).order_by(Classe.nom, Eleve.nom), Eleve).all()
        form.eleve_id.choices = [(e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}") for e in eleves]
        form.cours_id.choices = [(c.id, c.nom) for c in filtre_par_ecole(Cours.query.order_by(Cours.nom), Cours).all()]

    # ------------------- Pré-remplissage année scolaire -------------------
    if hasattr(form, 'annee_id'):
        annees = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).order_by(AnneeScolaire.nom.desc()).all()
        form.annee_id.choices = [(a.id, a.nom) for a in annees]
        if annee_active:
            form.annee_id.data = annee_active.id
        elif annees:
            form.annee_id.data = annees[0].id

    # ------------------- Ajout d'une note -------------------
    if form.validate_on_submit():
        if current_user.role == 'parent':
            flash("Vous n'êtes pas autorisé à ajouter des notes.", "danger")
            return redirect(url_for('main.notes'))

        # Déterminer l'année scolaire
        annee_id = getattr(form, 'annee_id', None) and form.annee_id.data or (annee_active.id if annee_active else None)
        if not annee_id:
            nouvelle_annee = AnneeScolaire(
                nom=f"{datetime.utcnow().year}-{datetime.utcnow().year+1}",
                date_debut=datetime(datetime.utcnow().year, 9, 1),
                date_fin=datetime(datetime.utcnow().year+1, 6, 30),
                active=True,
                ecole_id=current_user.ecole_id
            )
            db.session.add(nouvelle_annee)
            db.session.commit()
            annee_id = nouvelle_annee.id
            flash(f"Année scolaire {nouvelle_annee.nom} créée automatiquement.", "info")

        # Vérifier le droit de l'enseignant sur le cours
        if current_user.role in ['enseignant', 'professeur']:
            professeur = filtre_par_ecole(Professeur.query.filter_by(utilisateur_id=current_user.id), Professeur).first()
            cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()
            if not cours or cours.professeur_id != professeur.id:
                flash("Vous ne pouvez pas ajouter de notes pour ce cours.", "danger")
                return redirect(url_for('main.notes'))

        # Création et sauvegarde
        try:
            nouvelle_note = Note(
                valeur=form.valeur.data,
                coefficient=form.coefficient.data,
                type_evaluation=form.type_evaluation.data,
                periode=form.periode.data,
                eleve_id=form.eleve_id.data,
                cours_id=form.cours_id.data,
                date_evaluation=datetime.utcnow(),
                ecole_id=current_user.ecole_id,
                annee_id=annee_id
            )
            db.session.add(nouvelle_note)
            db.session.commit()

            # Journalisation JSON pour SQLite
            import json
            current_app.log_correction(
                action="ajout",
                description=f"Note ajoutée pour l'élève {nouvelle_note.eleve_id} en cours {nouvelle_note.cours_id}",
                ecole_id=nouvelle_note.ecole_id,
                cible_type="note",
                cible_id=nouvelle_note.id,
                ancienne_valeur=None,
                nouvelle_valeur=json.dumps({
                    "valeur": nouvelle_note.valeur,
                    "coefficient": nouvelle_note.coefficient,
                    "type_evaluation": nouvelle_note.type_evaluation,
                    "periode": nouvelle_note.periode,
                    "eleve_id": nouvelle_note.eleve_id,
                    "cours_id": nouvelle_note.cours_id
                }),
                niveau="info"
            )

            # Notification email
            eleve = filtre_par_ecole(Eleve.query.filter_by(id=form.eleve_id.data), Eleve).first()
            cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()
            if eleve and eleve.email_parent and cours:
                sujet = f"Nouvelle note en {cours.nom}"
                message = f"""Bonjour,

Une nouvelle note a été ajoutée pour {eleve.prenom} {eleve.nom} en {cours.nom}:
- Note: {form.valeur.data}/20
- Type: {form.type_evaluation.data}
- Coefficient: {form.coefficient.data}
- Période: {form.periode.data}

Connectez-vous au portail parent pour plus de détails.

Cordialement,
L'équipe pédagogique"""
                try:
                    envoyer_email(eleve.email_parent, sujet, message)
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email note: {e}")

            flash('Note ajoutée avec succès', 'success')
            return redirect(url_for('main.notes'))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'ajout de la note.", "danger")
            current_app.logger.error(f"Erreur ajout note: {e}")

    # ------------------- Filtrage affichage notes selon année -------------------
    if current_user.role == 'parent':
        enfants_ids = [e.id for e in filtre_par_ecole(Eleve.query.filter_by(parent_id=current_user.id), Eleve).all()]
        query_notes = Note.query.filter(Note.eleve_id.in_(enfants_ids))
        if annee_active:
            query_notes = query_notes.filter_by(annee_id=annee_active.id)
        toutes_notes = filtre_par_ecole(query_notes.order_by(Note.date_evaluation.desc()), Note).all()

    elif current_user.role in ['enseignant', 'professeur']:
        professeur = filtre_par_ecole(Professeur.query.filter_by(utilisateur_id=current_user.id), Professeur).first()
        if professeur:
            cours_ids = [c.id for c in filtre_par_ecole(Cours.query.filter_by(professeur_id=professeur.id), Cours).all()]
            query_notes = Note.query.filter(Note.cours_id.in_(cours_ids))
            if annee_active:
                query_notes = query_notes.filter_by(annee_id=annee_active.id)
            toutes_notes = filtre_par_ecole(query_notes.order_by(Note.date_evaluation.desc()), Note).all()
        else:
            toutes_notes = []

    else:  # admin
        query_notes = Note.query
        if annee_active:
            query_notes = query_notes.filter_by(annee_id=annee_active.id)
        toutes_notes = filtre_par_ecole(query_notes.order_by(Note.date_evaluation.desc()), Note).all()

    # ------------------- Statistiques -------------------
    if toutes_notes:
        total_pondere = sum(n.valeur * n.coefficient for n in toutes_notes)
        total_coefficients = sum(n.coefficient for n in toutes_notes)
        moyenne_generale = round(total_pondere / total_coefficients, 2) if total_coefficients else 0
        notes_reussites = sum(1 for n in toutes_notes if n.valeur >= 10)
        taux_reussite = round((notes_reussites / len(toutes_notes)) * 100, 1)
        matieres_evaluees = len(set(n.cours_id for n in toutes_notes))
    else:
        moyenne_generale = taux_reussite = matieres_evaluees = 0

    return render_template(
        'notes.html',
        form=form,
        notes=toutes_notes,
        moyenne_generale=moyenne_generale,
        taux_reussite=taux_reussite,
        matieres_evaluees=matieres_evaluees,
        eleves=eleves,
        tous_les_cours=filtre_par_ecole(Cours.query.order_by(Cours.nom), Cours).all(),
        annee_active=annee_active
    )



# ------------------- Export Excel notes -------------------
@main.route('/notes/export_excel')
@login_required
@role_required('admin')
def export_notes_excel():
    """Export Excel de toutes les notes avec jointures élèves/cours"""
    notes = filtre_par_ecole(Note.query.join(Eleve).join(Cours).order_by(Note.date_evaluation.desc()), Note).all()
    
    data = {
        'Date': [n.date_evaluation.strftime('%d/%m/%Y') for n in notes],
        'Élève': [f"{n.eleve.prenom} {n.eleve.nom}" for n in notes],
        'Classe': [n.eleve.classe.nom if n.eleve.classe else 'Sans classe' for n in notes],
        'Cours': [n.cours.nom for n in notes],
        'Note': [n.valeur for n in notes],
        'Coefficient': [n.coefficient for n in notes],
        'Type': [n.type_evaluation for n in notes]
    }
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Notes', index=False)
    
    output.seek(0)

    # --- Journalisation export ---
    current_app.log_correction(
        action="export",
        description="Export Excel des notes",
        ecole_id=getattr(current_user, 'ecole_id', None),
        cible_type="note",
        cible_id=None,
        ancienne_valeur=None,
        nouvelle_valeur=None,
        niveau="info"
    )
    
    return send_file(
        output,
        as_attachment=True,
        download_name="liste_notes.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# ==============================================================================
# GESTION DES PAIEMENTS
# ==============================================================================



# ------------------- Paiements (admin) -------------------
@main.route('/paiements', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def paiements():
    form = PaiementForm()
    page_eleves = request.args.get('page', 1, type=int)
    page_paiements = request.args.get('page_paiements', 1, type=int)
    per_page_eleves = 50
    per_page_paiements = 20
    classe_id = request.args.get('classe', type=int)
    recherche = request.args.get('recherche', '', type=str)

    # --- TRAITEMENT DU POST ---
    if form.validate_on_submit():
        try:
            paiement = Paiement(
                eleve_id=form.eleve_id.data,
                montant=form.montant.data,
                mois=form.mois.data,
                annee=form.annee.data,
                mode_paiement=form.mode_paiement.data,
                reference=form.reference.data
            )
            ajouter_ecole_id(paiement)
            db.session.add(paiement)
            db.session.commit()
            flash("Paiement enregistré avec succès !", "success")
            return redirect(url_for('main.paiements'))
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'enregistrement du paiement: {e}", "danger")

    # --- QUERY DE BASE POUR LES ÉLÈVES ---
    query_base = filtre_par_ecole(Eleve.query.outerjoin(Classe), Eleve)
    if classe_id:
        query_base = query_base.filter(Eleve.classe_id == classe_id)
    if recherche:
        query_base = query_base.filter(
            (Eleve.nom.ilike(f"%{recherche}%")) | (Eleve.prenom.ilike(f"%{recherche}%"))
        )

    # --- PAGINATION DES ÉLÈVES POUR L'AFFICHAGE ---
    ClasseAlias = aliased(Classe)
    eleves_pagination = query_base.outerjoin(ClasseAlias, Eleve.classe_id == ClasseAlias.id)\
                                  .order_by(ClasseAlias.nom, Eleve.nom)\
                                  .paginate(page=page_eleves, per_page=per_page_eleves, error_out=False)
    eleves = eleves_pagination.items

    # --- STATISTIQUES RÉELLES (OPTIMISÉES) ---
    eleve_ids = [e.id for e in query_base.with_entities(Eleve.id)]
    paiements_totaux = db.session.query(
        Paiement.eleve_id,
        func.sum(Paiement.montant).label('total_paye')
    ).filter(Paiement.eleve_id.in_(eleve_ids)).group_by(Paiement.eleve_id).all()
    paiements_dict = {p.eleve_id: p.total_paye for p in paiements_totaux}

    stats = {'total_eleves': len(eleve_ids), 'complet': 0, 'partiel': 0, 'aucun': 0}
    for e in eleves:
        total_paye = paiements_dict.get(e.id, 0)
        reste = max(e.frais_annuels - total_paye, 0)
        if reste <= 0:
            stats['complet'] += 1
        elif total_paye == 0:
            stats['aucun'] += 1
        else:
            stats['partiel'] += 1

    # --- Données pour affichage paginé ---
    paiements_par_eleve = {}
    for e in eleves:
        total_paye = paiements_dict.get(e.id, 0)
        reste = max(e.frais_annuels - total_paye, 0)
        paiements_par_eleve[e.id] = {
            'total_paye': total_paye,
            'reste_a_payer': reste,
            'frais_annuels': e.frais_annuels,
            'eleve': e,
            'pourcentage_paye': round((total_paye / e.frais_annuels) * 100, 2) if e.frais_annuels else 0
        }

    # --- PAGINATION DES PAIEMENTS ---
    query_paiements = filtre_par_ecole(
        Paiement.query.order_by(Paiement.date_paiement.desc(), Paiement.annee.desc(), Paiement.mois.desc()),
        Paiement
    )
    paiements_pagination = query_paiements.paginate(
        page=page_paiements, per_page=per_page_paiements, error_out=False
    )

    # --- CLASSES ---
    classes = filtre_par_ecole(Classe.query.order_by(Classe.nom), Classe).all()

    return render_template(
        "paiements.html",
        form=form,
        paiements_pagination=paiements_pagination,
        paiements_par_eleve=paiements_par_eleve,
        eleves_pagination=eleves_pagination,
        stats=stats,
        classes=classes,
        classe_id=classe_id,
        recherche=recherche
    )

# ------------------- Paiements parent -------------------
@main.route('/parent/paiements')
@login_required
@role_required('parent')
def paiements_parent():
    paiements = []
    for enfant in filtre_par_ecole(current_user.enfants, Eleve):
        paiements.extend(
            filtre_par_ecole(
                Paiement.query.filter_by(eleve_id=enfant.id)
                .order_by(Paiement.annee.desc(), Paiement.mois.desc()),
                Paiement
            ).all()
        )
    
    # Calcul du montant total
    total_amount = sum(p.montant for p in paiements) if paiements else 0

    return render_template("paiements_parent.html", paiements=paiements, total_amount=total_amount)


# ------------------- Reçu paiement -------------------
# ------------------- Reçu paiement -------------------
@main.route('/paiement/<int:id>/recu')
@login_required
@role_required('admin', 'parent')
def recu_paiement(id):
    # Récupère la query filtrée par école puis l'objet
    paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

    if current_user.role == 'parent' and not check_parent_access(paiement.eleve_id):
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    return render_template('recu_paiement.html', paiement=paiement, now=datetime.now())


# ------------------- PDF reçu -------------------
@main.route('/paiement/<int:id>/pdf')
@login_required
@role_required('admin', 'parent')
def generer_recu_pdf(id):
    paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

    if current_user.role == 'parent' and not check_parent_access(paiement.eleve_id):
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    eleve = paiement.eleve
    # --- Infos école dynamiques ---
    if eleve and eleve.ecole:
        ecole = eleve.ecole
        nom_ecole = ecole.nom
        adresse_ecole = ecole.adresse or ""
        contact_ecole = f"Tél: {ecole.telephone or '-'}"
    else:
        nom_ecole = "ÉCOLE INCONNUE"
        adresse_ecole = "Non renseignée"
        contact_ecole = "-"

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # --- En-tête ---
    p.setFont("Helvetica-Bold", 16)
    p.drawString(100, height - 100, nom_ecole)
    p.setFont("Helvetica", 12)
    p.drawString(100, height - 120, adresse_ecole)
    p.drawString(100, height - 140, contact_ecole)

    p.setFont("Helvetica-Bold", 14)
    p.drawString(100, height - 180, "REÇU DE PAIEMENT")
    p.line(100, height - 185, 300, height - 185)

    # --- Infos paiement ---
    y = height - 220
    p.setFont("Helvetica", 12)
    p.drawString(100, y, f"Référence: {paiement.id:06d}")
    y -= 25
    p.drawString(100, y, f"Date: {paiement.date_paiement.strftime('%d/%m/%Y %H:%M')}")
    y -= 25
    p.drawString(100, y, f"Élève: {eleve.prenom} {eleve.nom}")
    y -= 25
    p.drawString(100, y, f"Classe: {eleve.classe.nom if eleve.classe else 'Sans classe'}")
    y -= 25
    p.drawString(100, y, f"Mois payé: {paiement.mois} {paiement.annee}")
    y -= 25
    p.drawString(100, y, f"Montant: {paiement.montant:,.0f} FCFA")
    y -= 25
    p.drawString(100, y, f"Mode de paiement: {paiement.mode_paiement}")
    if paiement.reference:
        y -= 25
        p.drawString(100, y, f"Référence: {paiement.reference}")

    # --- Signature & cachet ---
    p.line(50, 120, 250, 120)
    p.drawString(70, 100, "Signature du Caissier")

    p.line(300, 120, 500, 120)
    p.drawString(320, 100, "Signature du Parent")

    p.drawString(100, 60, "Cachet de l'Établissement")
    p.drawString(100, 40, f"Édition du: {datetime.utcnow().strftime('%d/%m/%Y %H:%M')}")

    p.showPage()
    p.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"reçu_paiement_{paiement.id}.pdf",
        mimetype='application/pdf'
    )

# ------------------- Export Excel Paiements -------------------
@main.route('/paiements/export_excel')
@login_required
@role_required('admin')
def export_paiements_excel():
    # Récupère tous les paiements filtrés par école
    paiements = filtre_par_ecole(
        Paiement.query.join(Eleve).order_by(Paiement.date_paiement.desc()), Paiement
    ).all()

    data = {
        'Date': [p.date_paiement.strftime('%d/%m/%Y') for p in paiements],
        'Élève': [f"{p.eleve.prenom} {p.eleve.nom}" for p in paiements],
        'Classe': [p.eleve.classe.nom if p.eleve.classe else 'Sans classe' for p in paiements],
        'Mois': [p.mois for p in paiements],
        'Année': [p.annee for p in paiements],
        'Montant': [p.montant for p in paiements],
        'Mode': [p.mode_paiement for p in paiements],
        'Statut': [p.statut for p in paiements],
        'Référence': [p.reference for p in paiements]
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Paiements', index=False)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="liste_paiements.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# ------------------- Gestion des absences -------------------
# ------------------- Route Absences -------------------
@main.route('/absences', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'enseignant', 'parent')
def absences():
    form = AbsenceForm()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    # --- Fonction utilitaire pour transformer Query en liste ---
    def to_list(query_or_list):
        if hasattr(query_or_list, 'all'):
            return query_or_list.all()
        return list(query_or_list)

    # --- Choix des élèves selon le rôle ---
    if current_user.role == 'parent':
        enfants = to_list(filtre_par_ecole(current_user.enfants, Eleve))
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]
        form.eleve_id.render_kw = {'disabled': True} if len(enfants) == 1 else {}
    else:
        enfants_query = filtre_par_ecole(
            Eleve.query.options(selectinload(Eleve.classe)), Eleve
        )
        enfants = to_list(enfants_query)
        # --- Tri par nom de classe et nom de l'élève en Python (compatible SQLite) ---
        enfants.sort(key=lambda e: ((e.classe.nom if e.classe else ""), e.nom))
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]

    # --- Choix des cours ---
    cours_query = filtre_par_ecole(Cours.query.order_by(Cours.nom), Cours)
    cours_list = to_list(cours_query)
    form.cours_id.choices = [(c.id, c.nom) for c in cours_list]

    # --- Gestion du formulaire POST ---
    if form.validate_on_submit():
        if current_user.role == 'parent':
            flash("Vous n'êtes pas autorisé à déclarer des absences.", "danger")
            return redirect(url_for('main.absences'))

        try:
            nouvelle_absence = Absence(
                date_absence=form.date_absence.data,
                motif=form.motif.data,
                justifiee=form.justifiee.data,
                eleve_id=form.eleve_id.data,
                cours_id=form.cours_id.data
            )
            ajouter_ecole_id(nouvelle_absence)
            db.session.add(nouvelle_absence)
            db.session.commit()

            # --- Notification email ---
            eleve = filtre_par_ecole(Eleve.query.filter_by(id=form.eleve_id.data), Eleve).first()
            cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()

            if eleve and eleve.email_parent and cours:
                sujet = f"Absence de {eleve.prenom} {eleve.nom}"
                message = f"""Bonjour,
Nous vous informons que {eleve.prenom} {eleve.nom} a été absent(e) le {form.date_absence.data.strftime('%d/%m/%Y')}.
Motif: {form.motif.data}
Cours: {cours.nom}
Statut: {'Justifiée' if form.justifiee.data else 'Non justifiée'}

Cordialement,
L'équipe pédagogique"""
                try:
                    envoyer_email(eleve.email_parent, sujet, message)
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email absence: {e}")

            flash('Absence enregistrée avec succès', 'success')
            return redirect(url_for('main.absences'))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'enregistrement de l'absence.", "danger")
            current_app.logger.error(f"Erreur ajout absence: {e}")

    # --- Filtrage et tri des absences ---
    if current_user.role == 'parent':
        enfants_ids = [e.id for e in enfants]
        absences_query = filtre_par_ecole(
            Absence.query.filter(Absence.eleve_id.in_(enfants_ids))
                         .options(selectinload(Absence.eleve).selectinload(Eleve.classe)), Absence
        )
    else:
        absences_query = filtre_par_ecole(
            Absence.query.options(selectinload(Absence.eleve).selectinload(Eleve.classe)), Absence
        )

    absences_list = to_list(absences_query)
    # Tri en Python par classe puis nom élève
    absences_list.sort(key=lambda a: ((a.eleve.classe.nom if a.eleve.classe else ""), a.eleve.nom, a.date_absence), reverse=True)

    # --- Pagination manuelle pour SQLite (compatible) ---
    total = len(absences_list)
    start = (page - 1) * per_page
    end = start + per_page
    absences_paginated = absences_list[start:end]

    # --- Statistiques ---
    absences_justifiees = sum(1 for a in absences_list if a.justifiee)
    absences_non_justifiees = total - absences_justifiees
    show_form = current_user.role in ['admin', 'enseignant', 'professeur']

    return render_template(
        'absences.html',
        form=form,
        absences=absences_paginated,
        absences_justifiees=absences_justifiees,
        absences_non_justifiees=absences_non_justifiees,
        show_form=show_form,
        page=page,
        per_page=per_page,
        total=total
    )

# ------------------- Export Excel Absences -------------------
@main.route('/absences/export_excel')
@login_required
@role_required('admin')
def export_absences_excel():
    absences = filtre_par_ecole(
        Absence.query.options(selectinload(Absence.eleve).selectinload(Eleve.classe)), Absence
    ).all()
    
    data = {
        'Date': [a.date_absence.strftime('%d/%m/%Y') for a in absences],
        'Élève': [f"{a.eleve.prenom} {a.eleve.nom}" for a in absences],
        'Classe': [a.eleve.classe.nom if a.eleve.classe else 'Sans classe' for a in absences],
        'Motif': [a.motif for a in absences],
        'Justifiée': ['Oui' if a.justifiee else 'Non' for a in absences]
    }
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Absences', index=False)
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name="liste_absences.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ==============================================================================
# TABLEAUX DE BORD ET STATISTIQUES
# ==============================================================================

@main.route('/dashboard')
@login_required
def dashboard():
    """Redirection vers le tableau de bord approprié selon le rôle"""
    role = getattr(current_user, "role", None)
    endpoint_par_role = {
        "admin": "main.admin_dashboard",
        "enseignant": "main.enseignant_dashboard",
        "parent": "main.parent_dashboard",
    }
    return redirect(url_for(endpoint_par_role.get(role, "main.index")))

@main.route('/admin/dashboard')
@login_required
@role_required('admin', 'super_admin')
def admin_dashboard():
    """Tableau de bord administrateur filtré par école, interdit aux super admin"""
    
    # Redirection immédiate si c'est un super admin
    if current_user.role == 'super_admin':
        # Tu peux rediriger vers la page d'accueil ou vers un tableau de bord super admin
        return redirect(url_for('main.index'))  # <-- changer 'main.index' selon ton projet

    from app.middleware import get_ecole_courante, filtre_par_ecole

    ecole = get_ecole_courante()  # récupère l'école courante si sélectionnée

    # --- Récupération des années scolaires ---
    annees_scolaires = AnneeScolaire.query.filter_by(ecole_id=ecole.id).order_by(AnneeScolaire.nom.desc()).all()

    # Statistiques filtrées par école
    stats = {
        'total_eleves': filtre_par_ecole(Eleve.query, Eleve).count(),
        'total_professeurs': filtre_par_ecole(Professeur.query, Professeur).count(),
        'total_cours': filtre_par_ecole(Cours.query, Cours).count(),
        'total_classes': filtre_par_ecole(Classe.query, Classe).count(),
        'total_bulletins': filtre_par_ecole(Bulletin.query, Bulletin).count(),
        'paiements_attente': filtre_par_ecole(Paiement.query.filter_by(statut='en attente'), Paiement).count(),
        'paiements_valide': filtre_par_ecole(Paiement.query.filter_by(statut='valide'), Paiement).count(),
        'eleves_nouveaux': filtre_par_ecole(
            Eleve.query.filter(Eleve.date_inscription >= datetime.now().replace(day=1)),
            Eleve
        ).count(),
        'absences_recents': filtre_par_ecole(
            Absence.query.filter(Absence.date_absence >= datetime.now().replace(day=1)),
            Absence
        ).count(),
        'notes_recents': filtre_par_ecole(
            Note.query.filter(Note.date_evaluation >= datetime.now().replace(day=1)),
            Note
        ).count()
    }

    # Données récentes filtrées par école
    dernieres_notes = filtre_par_ecole(
        Note.query.order_by(Note.date_evaluation.desc()), Note
    ).limit(5).all()

    dernieres_absences = filtre_par_ecole(
        Absence.query.order_by(Absence.date_absence.desc()), Absence
    ).limit(5).all()

    derniers_paiements = filtre_par_ecole(
        Paiement.query.order_by(Paiement.date_paiement.desc()), Paiement
    ).limit(5).all()

    return render_template(
        'admin_dashboard.html',
        stats=stats,
        dernieres_notes=dernieres_notes,
        dernieres_absences=dernieres_absences,
        derniers_paiements=derniers_paiements,
        annees_scolaires=annees_scolaires
    )

@main.route('/parent/dashboard')
@login_required
@role_required('parent')
def parent_dashboard():
    """Tableau de bord parent avec pagination pour enfants et notes"""

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Nombre d'enfants par page, ajustable

    # Récupération paginée des enfants avec filtre école et relations chargées
    enfants_query = filtre_par_ecole(
        Eleve.query.options(
            db.joinedload(Eleve.classe),         # Classe de l'élève
            db.selectinload(Eleve.notes),        # Notes
            db.selectinload(Eleve.absences),     # Absences
            db.selectinload(Eleve.paiements)     # Paiements
        ).filter_by(parent_id=current_user.id),
        Eleve
    ).order_by(Eleve.nom, Eleve.prenom)

    enfants_pagination = enfants_query.paginate(page=page, per_page=per_page, error_out=False)
    enfants = enfants_pagination.items

    if not enfants:
        flash("Aucun élève n'est associé à votre compte parent", "warning")
        return render_template('parent_dashboard.html', enfants=[], pagination=enfants_pagination)

    # Calcul des statistiques pour chaque enfant
    for enfant in enfants:
        notes = enfant.notes
        absences = len(enfant.absences)
        paiements = len(enfant.paiements)

        total_pondere = sum(n.valeur * n.coefficient for n in notes)
        total_coefficients = sum(n.coefficient for n in notes)
        enfant.moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients > 0 else 0
        enfant.total_notes = len(notes)
        enfant.total_absences = absences
        enfant.total_paiements = paiements

    return render_template('parent_dashboard.html', enfants=enfants, pagination=enfants_pagination)



# ==============================================================================
# API POUR LES GRAPHIQUES ET STATISTIQUES
# ==============================================================================

# ------------------------------------------------------------------
# CACHE SIMPLE PAR UTILISATEUR
# ------------------------------------------------------------------
_stats_cache = {}
CACHE_DURATION = 60  # secondes

def get_cache(user_id, key):
    """Retourne le cache si valide pour un utilisateur."""
    now = datetime.now()
    if user_id in _stats_cache and key in _stats_cache[user_id]:
        data, timestamp = _stats_cache[user_id][key]
        if (now - timestamp).total_seconds() < CACHE_DURATION:
            return data
    return None

def set_cache(user_id, key, data):
    """Enregistre les données dans le cache pour un utilisateur."""
    now = datetime.now()
    if user_id not in _stats_cache:
        _stats_cache[user_id] = {}
    _stats_cache[user_id][key] = (data, now)

# ------------------------------------------------------------------
# API NOTES MOYENNES
# ------------------------------------------------------------------
@main.route('/api/stats/notes_moyennes')
@login_required
@role_required('admin', 'enseignant')
def api_stats_notes_moyennes():
    """Retourne les moyennes de notes par matière avec cache sécurisé."""
    user_id = current_user.id

    # Vérification du cache
    cached = get_cache(user_id, 'notes_moyennes')
    if cached:
        return jsonify(cached)

    # Filtrage par école
    if current_user.role == 'enseignant':
        cours_ids = [c.id for c in filtre_par_ecole(
            Cours.query.filter_by(professeur_id=current_user.id), Cours
        ).all()]
        result = db.session.query(
            Cours.nom,
            func.avg(Note.valeur).label('moyenne')
        ).join(Note).filter(Note.cours_id.in_(cours_ids)).group_by(Cours.nom).all()
    else:  # admin
        result = db.session.query(
            Cours.nom,
            func.avg(Note.valeur).label('moyenne')
        ).join(Note).group_by(Cours.nom).all()

    data = {
        'matieres': [r[0] for r in result],
        'moyennes': [float(r[1]) if r[1] else 0 for r in result]
    }

    # Mise en cache
    set_cache(user_id, 'notes_moyennes', data)
    return jsonify(data)

# ------------------------------------------------------------------
# API ABSENCES PAR MOIS
# ------------------------------------------------------------------
@main.route('/api/stats/absences_par_mois')
@login_required
@role_required('admin')
def api_stats_absences_par_mois():
    """Retourne le nombre d'absences par mois (derniers 6 mois) avec cache sécurisé."""
    user_id = current_user.id

    # Vérification du cache
    cached = get_cache(user_id, 'absences_par_mois')
    if cached:
        return jsonify(cached)

    six_mois = datetime.now() - timedelta(days=180)

    # Filtrage par école
    absences_query = filtre_par_ecole(
        Absence.query.filter(Absence.date_absence >= six_mois), Absence
    )

    # Compatibilité SQLite / PostgreSQL
    try:
        result = db.session.query(
            func.strftime('%Y', Absence.date_absence).label('annee'),
            func.strftime('%m', Absence.date_absence).label('mois'),
            func.count(Absence.id).label('total')
        ).filter(Absence.id.in_(absences_query.with_entities(Absence.id))).group_by('annee', 'mois').order_by('annee', 'mois').all()
    except Exception:
        # PostgreSQL
        result = db.session.query(
            func.extract('year', Absence.date_absence).label('annee'),
            func.extract('month', Absence.date_absence).label('mois'),
            func.count(Absence.id).label('total')
        ).filter(Absence.id.in_(absences_query.with_entities(Absence.id))).group_by('annee', 'mois').order_by('annee', 'mois').all()

    mois_labels = [f"{int(r.mois)}/{int(r.annee)}" for r in result]
    absences_data = [int(r.total) for r in result]

    data = {'mois': mois_labels, 'absences': absences_data}

    # Mise en cache
    set_cache(user_id, 'absences_par_mois', data)
    return jsonify(data)

# ==============================================================================
# GESTION DES ERREURS HTTP
# ==============================================================================

@main.errorhandler(404)
def page_not_found(e):
    """Gestionnaire d'erreur 404 - Page non trouvée"""
    return render_template('404.html'), 404

@main.errorhandler(403)
def access_denied(e):
    """Gestionnaire d'erreur 403 - Accès refusé"""
    return render_template('403.html'), 403

@main.errorhandler(500)
def server_error(e):
    """Gestionnaire d'erreur 500 - Erreur serveur interne"""
    return render_template('500.html'), 500
# ==============================================================================
# GESTION DES PROFILS UTILISATEURS
# ==============================================================================
@main.route('/profile')
@login_required
def profile():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('limit', 10, type=int), 100)
    search = request.args.get('search', '', type=str)
    role_filter = request.args.get('role', '', type=str)
    ecole_filter = request.args.get('ecole', '', type=str)
    classe_filter = request.args.get('classe', '', type=str)
    statut_filter = request.args.get('statut', '', type=str)
    sort = request.args.get('sort', 'nom', type=str)
    order = request.args.get('order', 'asc', type=str)

    # Base query
    query = Utilisateur.query

    # ---------------------- Gestion par rôle ----------------------
    if current_user.role == 'super_admin':
        query = query.filter(Utilisateur.role.in_(['admin', 'super_admin']))
        ecoles = get_ecole_filter_query(Ecole).all()
        classes = []
        eleves = []

    elif current_user.role == 'admin':
        query = query.filter(
            Utilisateur.ecole_id == current_user.ecole_id,
            Utilisateur.role != 'super_admin'
        )
        ecoles = [current_user.ecole] if current_user.ecole else []
        classes = Classe.query.filter_by(ecole_id=current_user.ecole_id).all()

        # Pagination des élèves
        eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).limit(100).all()

        # Filtre par classe sécurisé
        if classe_filter and classe_filter.isdigit():
            query = query.filter(Utilisateur.classe_id == int(classe_filter))

    else:
        query = query.filter(Utilisateur.id == current_user.id)
        ecoles = []
        classes = []
        eleves = []

    # ---------------------- Filtres supplémentaires ----------------------
    if search:
        query = query.filter(
            db.or_(
                Utilisateur.nom.ilike(f"%{search}%"),
                Utilisateur.prenom.ilike(f"%{search}%"),
                Utilisateur.email.ilike(f"%{search}%")
            )
        )

    if role_filter:
        query = query.filter(Utilisateur.role == role_filter)

    if ecole_filter and current_user.role == 'super_admin' and ecole_filter.isdigit():
        query = query.filter(Utilisateur.ecole_id == int(ecole_filter))

    if statut_filter:
        query = query.filter(Utilisateur.statut == statut_filter)

    # ---------------------- Tri sécurisé ----------------------
    colonnes_autorisees = ['nom', 'prenom', 'email', 'role', 'statut']
    if sort not in colonnes_autorisees:
        sort = 'nom'
    sort_col = getattr(Utilisateur, sort)
    sort_col = sort_col.desc() if order == 'desc' else sort_col.asc()
    query = query.order_by(sort_col)

    # ---------------------- Pagination principale ----------------------
    utilisateurs = query.paginate(page=page, per_page=per_page, error_out=False)

    # ---------------------- Professeurs et parents pour admin ----------------------
    professeurs = []
    parents = []
    if current_user.role == 'admin':
        professeurs = Utilisateur.query.filter(
            Utilisateur.ecole_id == current_user.ecole_id,
            Utilisateur.role == 'enseignant'
        ).limit(100).all()

        parents = Utilisateur.query.filter(
            Utilisateur.ecole_id == current_user.ecole_id,
            Utilisateur.role == 'parent'
        ).limit(100).all()

        # Chargement des enfants pour éviter N+1
        parent_ids = [p.id for p in parents]
        enfants = Eleve.query.filter(Eleve.parent_id.in_(parent_ids)).all()
        enfants_par_parent = {}
        for e in enfants:
            enfants_par_parent.setdefault(e.parent_id, []).append(e)
        for p in parents:
            p.enfants_list = enfants_par_parent.get(p.id, [])

    # ---------------------- Statistiques ----------------------
    if current_user.role == 'super_admin':
        statistiques = {
            "total_eleves": Eleve.query.count(),
            "total_professeurs": Utilisateur.query.filter_by(role='enseignant').count(),
            "total_classes": Classe.query.count(),
            "total_ecoles": Ecole.query.count(),
            "taux_occupation": 75
        }
    elif current_user.role == 'admin':
        total_capacite = sum(classe.capacite_max for classe in classes) if classes else 0
        total_eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).count()
        taux_occupation = int((total_eleves / total_capacite) * 100) if total_capacite > 0 else 0
        statistiques = {
            "total_eleves": total_eleves,
            "total_professeurs": len(professeurs),
            "total_classes": len(classes),
            "taux_occupation": taux_occupation
        }
    else:
        statistiques = None

    return render_template(
        'profile.html',
        utilisateurs=utilisateurs,
        professeurs=professeurs,
        parents=parents,
        eleves=eleves,
        ecoles=ecoles,
        classes=classes,
        search=search,
        role_filter=role_filter,
        sort=sort,
        order=order,
        statistiques=statistiques
    )

    

# ==============================================================================
# RAPPORTS ET STATISTIQUES AVANCÉES
# ==============================================================================
# Cache simple en mémoire pour les rapports
_rapports_cache = {
    'notes_par_classe': None,
    'absences_par_classe': None,
    'timestamp_notes': None,
    'timestamp_absences': None
}
CACHE_DURATION = 60  # en secondes

# -------------------------------------------------------------------
# API : Moyennes de notes par classe
# -------------------------------------------------------------------
@main.route('/rapport/notes_par_classe')
@login_required
@role_required('admin', 'super_admin')
def rapport_notes_par_classe():
    now = datetime.now()
    if _rapports_cache['notes_par_classe'] and (now - _rapports_cache['timestamp_notes']).total_seconds() < CACHE_DURATION:
        return jsonify(_rapports_cache['notes_par_classe'])

    # Filtrage selon rôle
    query_eleves = Eleve.query.options(db.joinedload(Eleve.notes), db.joinedload(Eleve.classe))
    if current_user.role == 'admin':
        query_eleves = query_eleves.filter(Eleve.ecole_id == current_user.ecole_id)

    eleves = query_eleves.all()

    classes_dict = {}
    for e in eleves:
        classe_nom = e.classe.nom if e.classe else "Non assigné"
        classes_dict.setdefault(classe_nom, []).append(e)

    data = {}
    for classe_nom in sorted(classes_dict.keys()):
        eleves_classe = classes_dict[classe_nom]
        notes = [n for e in eleves_classe for n in e.notes]
        if notes:
            total_pondere = sum(n.valeur * n.coefficient for n in notes)
            total_coefficients = sum(n.coefficient for n in notes)
            moyenne_classe = round(total_pondere / total_coefficients, 2)
        else:
            moyenne_classe = 0
        data[classe_nom] = moyenne_classe

    _rapports_cache['notes_par_classe'] = data
    _rapports_cache['timestamp_notes'] = now

    return jsonify(data)

# -------------------------------------------------------------------
# API : Absences par classe
# -------------------------------------------------------------------
@main.route('/rapport/absences_par_classe')
@login_required
@role_required('admin', 'super_admin')
def rapport_absences_par_classe():
    now = datetime.now()
    if _rapports_cache['absences_par_classe'] and (now - _rapports_cache['timestamp_absences']).total_seconds() < CACHE_DURATION:
        return jsonify(_rapports_cache['absences_par_classe'])

    query_eleves = Eleve.query.options(db.joinedload(Eleve.absences), db.joinedload(Eleve.classe))
    if current_user.role == 'admin':
        query_eleves = query_eleves.filter(Eleve.ecole_id == current_user.ecole_id)

    eleves = query_eleves.all()

    classes_dict = {}
    for e in eleves:
        classe_nom = e.classe.nom if e.classe else "Non assigné"
        classes_dict.setdefault(classe_nom, []).append(e)

    data = {}
    for classe_nom in sorted(classes_dict.keys()):
        eleves_classe = classes_dict[classe_nom]
        absences_count = sum(len(e.absences) for e in eleves_classe)
        data[classe_nom] = absences_count

    _rapports_cache['absences_par_classe'] = data
    _rapports_cache['timestamp_absences'] = now

    return jsonify(data)

# -------------------------------------------------------------------
# Page principale des rapports
# -------------------------------------------------------------------
@main.route('/rapports')
@login_required
@role_required('admin', 'super_admin')
def rapports():
    # Classes filtrées selon rôle
    classes_query = Classe.query
    if current_user.role == 'admin':
        classes_query = classes_query.filter(Classe.ecole_id == current_user.ecole_id)
    classes = classes_query.all()

    # Statistiques globales
    if current_user.role == 'admin':
        total_eleves = Eleve.query.filter(Eleve.ecole_id == current_user.ecole_id).count()
        total_professeurs = Utilisateur.query.filter_by(role='enseignant', ecole_id=current_user.ecole_id).count()
        total_classes = len(classes)
        capacite_totale = sum(c.capacite_max for c in classes) if classes else 1
    else:  # super_admin
        total_eleves = Eleve.query.count()
        total_professeurs = Utilisateur.query.filter_by(role='enseignant').count()
        total_classes = Classe.query.count()
        capacite_totale = sum(c.capacite_max for c in Classe.query.all()) if total_classes > 0 else 1

    taux_occupation = round((total_eleves / capacite_totale) * 100, 2) if capacite_totale > 0 else 0

    statistiques = {
        'total_eleves': total_eleves,
        'total_professeurs': total_professeurs,
        'total_classes': total_classes,
        'taux_occupation': taux_occupation
    }

    return render_template('rapports.html', classes=classes, role=current_user.role, statistiques=statistiques)
# ==============================================================================
# SYSTÈME DE NOTIFICATIONS
# ==============================================================================
@main.route('/notifications')
@login_required
def notifications():
    """
    Retourne les notifications pour l'utilisateur courant,
    avec filtrage multi-école pour les admins.
    """
    notifications = []
    now = datetime.now()

    # --- Admin ---
    if current_user.role == 'admin':
        # Paiements en attente uniquement pour l'école de l'admin
        paiements_attente = Paiement.query.join(Eleve).filter(
            Paiement.statut == 'en attente',
            Eleve.ecole_id == current_user.ecole_id
        ).count()
        if paiements_attente > 0:
            notifications.append({
                'type': 'warning',
                'message': f'{paiements_attente} paiement(s) en attente de validation',
                'lien': url_for('main.paiements'),
                'date': now.strftime("%d/%m/%Y %H:%M"),
                'priority': 2
            })

        # Nouvelles inscriptions ce mois-ci (filtrées par école)
        nouvelles_inscriptions = Eleve.query.filter(
            Eleve.ecole_id == current_user.ecole_id,
            Eleve.date_inscription >= now.replace(day=1)
        ).count()
        if nouvelles_inscriptions > 0:
            notifications.append({
                'type': 'info',
                'message': f'{nouvelles_inscriptions} nouvelle(s) inscription(s) ce mois-ci',
                'lien': url_for('main.eleves'),
                'date': now.strftime("%d/%m/%Y %H:%M"),
                'priority': 1
            })

    # --- Parent ---
    elif current_user.role == 'parent':
        # Récupérer uniquement ses propres enfants
        enfants = Eleve.query.filter_by(parent_id=current_user.id).all()
        for enfant in enfants:
            # Notes des 7 derniers jours
            nouvelles_notes = Note.query.filter(
                Note.eleve_id == enfant.id,
                Note.date_evaluation >= now - timedelta(days=7)
            ).count()
            if nouvelles_notes > 0:
                notifications.append({
                    'type': 'info',
                    'message': f'{nouvelles_notes} nouvelle(s) note(s) pour {enfant.prenom}',
                    'lien': url_for('main.portal_parent'),
                    'date': now.strftime("%d/%m/%Y %H:%M"),
                    'priority': 2
                })

            # Absences non justifiées des 7 derniers jours
            absences_non_justifiees = Absence.query.filter(
                Absence.eleve_id == enfant.id,
                Absence.justifiee == False,
                Absence.date_absence >= now - timedelta(days=7)
            ).count()
            if absences_non_justifiees > 0:
                notifications.append({
                    'type': 'warning',
                    'message': f'{absences_non_justifiees} absence(s) non justifiée(s) pour {enfant.prenom}',
                    'lien': url_for('main.portal_parent'),
                    'date': now.strftime("%d/%m/%Y %H:%M"),
                    'priority': 3
                })

    # --- Tri des notifications par priorité décroissante ---
    notifications.sort(key=lambda n: n['priority'], reverse=True)

    return render_template("notifications.html", notifications=notifications)
# ==============================================================================
# SYSTÈME DE RECHERCHE AVANCÉE

@main.route('/recherche')
@login_required
def recherche():
    terme = request.args.get('q', '').strip()
    type_recherche = request.args.get('type', 'all')
    classe_id = request.args.get('classe', type=int)

    if not terme:
        return render_template('recherche.html', results=None)

    ecole_id = None
    if current_user.role in ['admin', 'enseignant']:
        ecole_id = current_user.ecole_id

    queries = []

    # ---------- ÉLÈVES ----------
    if type_recherche in ['all', 'eleves']:
        eleve_query = db.session.query(
            Eleve.id.label('id'),
            Eleve.nom.label('nom'),
            Eleve.prenom.label('prenom'),
            Classe.nom.label('classe'),
            literal('eleve').label('type')
        ).join(Classe, isouter=True).filter(
            (Eleve.nom.ilike(f"%{terme}%")) | (Eleve.prenom.ilike(f"%{terme}%"))
        )

        if classe_id:
            eleve_query = eleve_query.filter(Eleve.classe_id == classe_id)
        if ecole_id:
            eleve_query = eleve_query.filter(Classe.ecole_id == ecole_id)

        if current_user.role == 'enseignant':
            professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
            if professeur:
                cours_ids = db.session.query(Cours.id).filter_by(professeur_id=professeur.id).subquery()
                eleve_ids = db.session.query(Note.eleve_id).filter(Note.cours_id.in_(cours_ids)).subquery()
                eleve_query = eleve_query.filter(Eleve.id.in_(eleve_ids))

        elif current_user.role == 'parent':
            eleve_query = eleve_query.filter(Eleve.parent_id == current_user.id)

        queries.append(eleve_query)

    # ---------- PROFESSEURS ----------
    if type_recherche in ['all', 'professeurs'] and current_user.role != 'enseignant':
        prof_query = db.session.query(
            Professeur.id.label('id'),
            Professeur.nom.label('nom'),
            Professeur.prenom.label('prenom'),
            Professeur.specialite.label('classe'),
            literal('professeur').label('type')
        ).join(Utilisateur)

        if ecole_id:
            prof_query = prof_query.filter(Utilisateur.ecole_id == ecole_id)

        prof_query = prof_query.filter(
            (Professeur.nom.ilike(f"%{terme}%")) |
            (Professeur.prenom.ilike(f"%{terme}%")) |
            (Professeur.specialite.ilike(f"%{terme}%"))
        )
        queries.append(prof_query)

    # ---------- COURS ----------
    if type_recherche in ['all', 'cours']:
        cours_query = db.session.query(
            Cours.id.label('id'),
            Cours.nom.label('nom'),
            Cours.description.label('description'),
            literal('').label('classe'),
            literal('cours').label('type')
        ).join(Professeur, isouter=True).join(Utilisateur, Professeur.utilisateur_id == Utilisateur.id, isouter=True)

        if ecole_id:
            cours_query = cours_query.filter(Utilisateur.ecole_id == ecole_id)

        if current_user.role == 'enseignant':
            professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
            if professeur:
                cours_query = cours_query.filter(Cours.professeur_id == professeur.id)

        cours_query = cours_query.filter(
            (Cours.nom.ilike(f"%{terme}%")) | (Cours.description.ilike(f"%{terme}%"))
        )
        queries.append(cours_query)

    # Union de toutes les requêtes (sans limit dans les sous-requêtes)
    if queries:
        final_query = queries[0]
        for q in queries[1:]:
            final_query = final_query.union_all(q)
        results_raw = final_query.limit(30).all()  # Limite globale après l'union
    else:
        results_raw = []

    results = {'eleves': [], 'professeurs': [], 'cours': [], 'total': len(results_raw)}
    for r in results_raw:
        if r.type == 'eleve':
            results['eleves'].append({'id': r.id, 'nom': r.nom, 'prenom': r.prenom, 'classe': r.classe})
        elif r.type == 'professeur':
            results['professeurs'].append({'id': r.id, 'nom': r.nom, 'prenom': r.prenom, 'specialite': r.classe})
        elif r.type == 'cours':
            results['cours'].append({'id': r.id, 'nom': r.nom, 'description': r.description, 'classe': r.classe})

    return render_template('recherche.html', results=results, terme=terme)

# ==============================================================================
# PAGES DE DÉTAILS (ÉLÈVES, PROFESSEURS, COURS)
# ==============================================================================

# ---------------------- Détail d'un élève ----------------------
# ---------------------- UTILITAIRES ----------------------
def check_ecole_access(obj, objet_type="generic"):
    """
    Vérifie que l'objet (Eleve, Professeur, Cours) appartient à l'école de l'utilisateur.
    Renvoie True si accès autorisé, False sinon.
    """
    if current_user.role == 'admin':
        if getattr(obj, 'ecole_id', None) != current_user.ecole_id:
            flash(f"Accès refusé : {objet_type} d'une autre école", "danger")
            return False
    elif current_user.role == 'enseignant':
        # L'enseignant ne peut voir que ses propres cours ou élèves de ses cours
        if isinstance(obj, Cours) and obj.professeur_id != current_user.id:
            flash("Accès non autorisé : cours non lié", "danger")
            return False
        if isinstance(obj, Eleve):
            cours_ids = [c.id for c in Professeur.query.get(current_user.id).cours]
            eleve_ids = [n.eleve_id for n in Note.query.filter(Note.cours_id.in_(cours_ids)).all()]
            if obj.id not in eleve_ids:
                flash("Accès non autorisé : élève non lié à vos cours", "danger")
                return False
    elif current_user.role == 'parent':
        # Le parent ne peut voir que ses propres enfants
        if getattr(obj, 'parent_id', None) != current_user.id:
            flash("Accès refusé : élève non lié à ce parent", "danger")
            return False
    return True

# ---------------------- Détail d'un élève ----------------------
@main.route('/voir_eleve/<int:eleve_id>')  # Au lieu de '/eleve/<int:eleve_id>'
@login_required
@role_required('admin', 'enseignant', 'parent')
@parent_access_required
def voir_eleve(eleve_id):
    eleve = Eleve.query.options(
        joinedload(Eleve.notes).joinedload(Note.cours),
        joinedload(Eleve.absences),
        joinedload(Eleve.paiements)
    ).get_or_404(eleve_id)

    if not check_ecole_access(eleve, "élève"):
        return redirect(url_for('main.profile'))

    notes = sorted(eleve.notes, key=lambda n: n.date_evaluation, reverse=True)
    absences = sorted(eleve.absences, key=lambda a: a.date_absence, reverse=True)
    paiements = sorted(eleve.paiements, key=lambda p: p.date_paiement, reverse=True)

    total_pondere = sum(n.valeur * n.coefficient for n in notes)
    total_coefficients = sum(n.coefficient for n in notes)
    moyenne_generale = round(total_pondere / total_coefficients, 2) if total_coefficients else 0

    moyennes_par_matiere = {}
    for n in notes:
        mat = n.cours.nom
        if mat not in moyennes_par_matiere:
            moyennes_par_matiere[mat] = {'total': 0, 'coef': 0}
        moyennes_par_matiere[mat]['total'] += n.valeur * n.coefficient
        moyennes_par_matiere[mat]['coef'] += n.coefficient
    for mat, data in moyennes_par_matiere.items():
        moyennes_par_matiere[mat] = round(data['total']/data['coef'], 2) if data['coef'] else 0

    return render_template('voir_eleve.html',
                           eleve=eleve,
                           notes=notes,
                           absences=absences,
                           paiements=paiements,
                           moyenne_generale=moyenne_generale,
                           moyennes_par_matiere=moyennes_par_matiere)

# ---------------------- Détail d'un professeur ----------------------
@main.route('/professeur/<int:id>')
@login_required
@role_required('admin')
def professeur_details(id):
    professeur = Professeur.query.options(
        joinedload(Professeur.cours).joinedload(Cours.notes)
    ).get_or_404(id)

    if not check_ecole_access(professeur, "professeur"):
        return redirect(url_for('main.profile'))

    total_eleves = len(set(n.eleve_id for c in professeur.cours for n in c.notes))
    total_notes = sum(len(c.notes) for c in professeur.cours)

    return render_template('professeur_details.html',
                           professeur=professeur,
                           cours=professeur.cours,
                           total_eleves=total_eleves,
                           total_notes=total_notes)

# ---------------------- Détail d'un cours ----------------------
@main.route('/cours/<int:id>')
@login_required
@role_required('admin', 'enseignant')
def cours_details(id):
    cours = Cours.query.options(
        joinedload(Cours.professeur),
        joinedload(Cours.notes).joinedload(Note.eleve)
    ).get_or_404(id)

    if not check_ecole_access(cours, "cours"):
        if current_user.role == 'enseignant':
            return redirect(url_for('main.enseignant_dashboard'))
        return redirect(url_for('main.profile'))

    notes = sorted(cours.notes, key=lambda n: n.date_evaluation, reverse=True)
    total_pondere = sum(n.valeur * n.coefficient for n in notes)
    total_coefficients = sum(n.coefficient for n in notes)
    moyenne_cours = round(total_pondere / total_coefficients, 2) if total_coefficients else 0
    eleves_avec_notes = len(set(n.eleve_id for n in notes))

    return render_template('cours_details.html',
                           cours=cours,
                           notes=notes,
                           moyenne_cours=moyenne_cours,
                           eleves_avec_notes=eleves_avec_notes)


# ==============================================================================
# IMPORT/EXPORT DE DONNÉES
# ==============================================================================


# ---------------------- Export des notes d’un cours ----------------------
@main.route('/cours/<int:id>/export_notes')
@login_required
@role_required('admin', 'enseignant')
def export_notes(id):
    """Export des notes d'un cours spécifique en Excel"""
    cours = Cours.query.options(joinedload(Cours.notes).joinedload(Note.eleve)).filter_by(
        id=id,
        ecole_id=current_user.ecole_id
    ).first_or_404()

    # Vérification d'accès pour les enseignants
    if current_user.role == 'enseignant' and cours.professeur_id != current_user.id:
        flash("Accès non autorisé à ce cours.", "danger")
        return redirect(url_for('main.enseignant_dashboard'))

    # Préparation des données
    data = [{
        "Élève ID": note.eleve.id,
        "Nom": note.eleve.nom,
        "Prénom": note.eleve.prenom,
        "Note": note.valeur,
        "Coefficient": note.coefficient,
        "Date": note.date_evaluation.strftime("%d/%m/%Y") if note.date_evaluation else ""
    } for note in cours.notes]

    # Création du fichier Excel
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=cours.nom[:30])

    output.seek(0)
    return send_file(
        output,
        download_name=f"Notes_{cours.nom}.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ---------------------- Import des notes depuis Excel/CSV ----------------------
@main.route('/cours/<int:id>/import_notes_excel', methods=['POST'])
@login_required
@role_required('admin', 'enseignant')
def import_notes_excel(id):
    """Import de notes depuis Excel/CSV avec audit et rapport d'erreurs"""
    cours = Cours.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    # Vérification d'accès pour les enseignants
    if current_user.role == 'enseignant' and cours.professeur_id != current_user.id:
        flash("Accès non autorisé à ce cours.", "danger")
        return redirect(url_for('main.enseignant_dashboard'))

    file = request.files.get("file")
    if not file or file.filename == '':
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for('main.cours_details', id=id))

    try:
        # Lecture du fichier (Excel ou CSV)
        if file.filename.lower().endswith(".csv"):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # Normalisation des noms de colonnes
        df.columns = [unidecode(c).lower().strip() for c in df.columns]

        # Colonnes acceptées
        required_cols = [
            ["nom", "prenom", "classe", "note"],
            ["eleve", "classe", "note"],
            ["eleve id", "note"]
        ]
        if not any(all(col in df.columns for col in cols) for cols in required_cols):
            flash("Format de fichier incorrect. Vérifiez les colonnes.", "danger")
            return redirect(url_for('main.cours_details', id=id))

        notes_importees, erreurs = 0, []

        # Préchargement des élèves de la même école
        eleves_dict = {e.id: e for e in Eleve.query.filter_by(ecole_id=current_user.ecole_id).all()}

        # Parcours du fichier
        for index, row in df.iterrows():
            try:
                eleve = None
                nom, prenom, classe = None, None, None

                # Recherche par ID
                if "eleve id" in df.columns and pd.notna(row["eleve id"]):
                    eleve = eleves_dict.get(int(row["eleve id"]))
                    if eleve:
                        nom, prenom = eleve.nom, eleve.prenom

                # Recherche par nom/prénom
                elif "nom" in df.columns and "prenom" in df.columns:
                    nom = str(row["nom"]).strip()
                    prenom = str(row["prenom"]).strip()
                    classe = str(row["classe"]).strip() if pd.notna(row.get("classe")) else None
                    eleve = next(
                        (e for e in eleves_dict.values()
                         if e.nom.lower() == nom.lower()
                         and e.prenom.lower() == prenom.lower()
                         and (not classe or e.classe.lower() == classe.lower())),
                        None
                    )

                # Recherche par colonne unique "élève"
                else:
                    nom_complet = str(row["eleve"]).strip()
                    parties = nom_complet.split()
                    if len(parties) >= 2:
                        prenom, nom = " ".join(parties[:-1]), parties[-1]
                        classe = str(row["classe"]).strip() if pd.notna(row.get("classe")) else None
                        eleve = next(
                            (e for e in eleves_dict.values()
                             if e.nom.lower() == nom.lower()
                             and e.prenom.lower() == prenom.lower()
                             and (not classe or e.classe.lower() == classe.lower())),
                            None
                        )

                if not eleve:
                    erreurs.append(f"Ligne {index+2}: Élève non trouvé ({prenom or '?'} {nom or '?'})")
                    continue

                # Vérification de la note
                try:
                    note_valeur = float(row["note"])
                    if not (0 <= note_valeur <= 20):
                        erreurs.append(f"Ligne {index+2}: Note invalide ({note_valeur})")
                        continue
                except:
                    erreurs.append(f"Ligne {index+2}: Format de note invalide ({row['note']})")
                    continue

                # Ajout / mise à jour
                note = Note.query.filter_by(cours_id=id, eleve_id=eleve.id).first()
                if note:
                    note.valeur = note_valeur
                else:
                    db.session.add(Note(cours_id=id, eleve_id=eleve.id, valeur=note_valeur))

                notes_importees += 1

            except Exception as e:
                erreurs.append(f"Ligne {index+2}: {str(e)}")
                continue

        # Commit global
        db.session.commit()

        # Génération du rapport d'erreurs s'il y en a
        fichier_erreurs = None
        if erreurs:
            imports_dir = os.path.join(current_app.root_path, "static", "imports")
            os.makedirs(imports_dir, exist_ok=True)

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            fichier_erreurs = f"errors_import_{timestamp}.csv"
            chemin_fichier = os.path.join(imports_dir, fichier_erreurs)
            pd.DataFrame({"Erreurs": erreurs}).to_csv(chemin_fichier, index=False, encoding='utf-8-sig')

        # Audit
        audit = HistoriqueImport(
            cours_id=cours.id,
            utilisateur_id=current_user.id,
            ecole_id=current_user.ecole_id,
            nb_notes=notes_importees,
            nb_erreurs=len(erreurs),
            fichier_erreurs=fichier_erreurs
        )
        db.session.add(audit)
        db.session.commit()

        # Feedback utilisateur
        if notes_importees:
            flash(f"{notes_importees} notes importées avec succès.", "success")
        if erreurs:
            flash(f"{len(erreurs)} erreurs détectées. Rapport disponible.", "warning")

    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de l'import : {str(e)}", "danger")

    return redirect(url_for('main.cours_details', id=id))

# ---------------------- Téléchargement fichier d'erreurs ----------------------
@main.route('/imports/telecharger/<filename>')
@login_required
def telecharger_import(filename):
    """Télécharger le fichier d'erreurs d'import"""

    from werkzeug.utils import secure_filename
    import os
    from flask import send_from_directory, abort, current_app

    # Nom de fichier sécurisé
    safe_filename = secure_filename(filename)

    # Vérification stricte du nom pour éviter les fichiers non autorisés
    if not safe_filename.startswith('errors_import_') or not safe_filename.endswith('.csv'):
        abort(404, "Fichier non autorisé")

    imports_dir = os.path.join(current_app.root_path, "static", "imports")
    file_path = os.path.join(imports_dir, safe_filename)

    # Vérification que le fichier existe bien
    if not os.path.isfile(file_path):
        abort(404, "Fichier non trouvé")

    return send_from_directory(imports_dir, safe_filename, as_attachment=True)


# ---------------------- Modèle d'importation de notes ----------------------
@main.route('/cours/<int:id>/modele_import_notes')
@login_required
@role_required('admin', 'enseignant')
def modele_import_notes(id):
    """Téléchargement d'un modèle d'importation de notes (Excel ou CSV)"""

    format_fichier = request.args.get('format', 'excel').lower()
    cours = Cours.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    # Vérification d'accès pour les enseignants
    if current_user.role == 'enseignant' and cours.professeur_id != current_user.id:
        flash("Accès non autorisé à ce cours.", "danger")
        return redirect(url_for('main.enseignant_dashboard'))

    colonnes = ['Nom', 'Prénom', 'Classe', 'Note', 'Coefficient', 'Type évaluation']
    df = pd.DataFrame(columns=colonnes)

    # --- Génération CSV ---
    if format_fichier == 'csv':
        output = BytesIO()
        df.to_csv(output, index=False, sep=',', encoding='utf-8-sig')
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=f"modele_import_notes_cours_{cours.nom}.csv",
            mimetype='text/csv'
        )

    # --- Génération Excel ---
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # Deux feuilles identiques pour donner un choix à l’utilisateur
        df.to_excel(writer, sheet_name='Format Standard', index=False)
        df.to_excel(writer, sheet_name='Format Alternatif', index=False)

        # Mise en forme visuelle
        workbook = writer.book
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#D7E4BC',
            'border': 1
        })
        for sheet_name in ['Format Standard', 'Format Alternatif']:
            worksheet = writer.sheets[sheet_name]
            for col_num, value in enumerate(df.columns):
                worksheet.write(0, col_num, value, header_format)
            for i in range(len(df.columns)):
                worksheet.set_column(i, i, 20)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"modele_import_notes_cours_{cours.nom}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ---------------------- Historique des imports ----------------------
@main.route('/imports/historique')
@login_required
@role_required('admin', 'enseignant', 'super_admin')
def imports_historique():
    """Affichage de l'historique des imports filtré par école"""
    historiques = (
        HistoriqueImport.query
        .join(HistoriqueImport.utilisateur)
        .filter(Utilisateur.ecole_id == current_user.ecole_id)
        .order_by(HistoriqueImport.date_import.desc())
        .all()
    )
    return render_template("imports_historique.html", historiques=historiques)

# ==============================================================================
# GÉNÉRATION DE PDF (BULLETINS, RELEVÉS)
# ==============================================================================

def generer_bulletin_pdf(
    eleve,
    notes_par_cours,
    moyennes_par_cours,
    moyenne_generale,
    logo_path=None,
    nom_ecole=None,
    adresse_ecole=None,
    contact_ecole=None
):
    import io, os
    from datetime import datetime
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, Image
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=50, bottomMargin=50,
        leftMargin=45, rightMargin=45
    )

    styles = getSampleStyleSheet()
    elements = []

    # --- Styles personnalisés ---
    title_style = ParagraphStyle(
        'Title', parent=styles['Heading1'],
        fontSize=20, textColor=colors.HexColor('#1A5276'),
        spaceAfter=20, alignment=1, fontName='Helvetica-Bold'
    )
    subtitle_style = ParagraphStyle(
        'Subtitle', parent=styles['Heading2'],
        fontSize=14, textColor=colors.HexColor('#2E86C1'),
        spaceAfter=10, fontName='Helvetica-Bold', alignment=1
    )
    header_style = ParagraphStyle(
        'HeaderStyle', parent=styles['Normal'],
        fontSize=11, textColor=colors.HexColor('#34495E'), spaceAfter=6
    )
    normal_center = ParagraphStyle('normal_center', parent=styles['Normal'], alignment=1)

    # --- Informations dynamiques école (fallback si non fournie) ---
    if not nom_ecole or not adresse_ecole or not contact_ecole:
        if hasattr(eleve, 'ecole') and eleve.ecole:
            nom_ecole = nom_ecole or eleve.ecole.nom
            adresse_ecole = adresse_ecole or eleve.ecole.adresse
            contact_ecole = contact_ecole or f"Tél: {eleve.ecole.telephone or '-'} - Email: {eleve.ecole.email or '-'}"
        else:
            nom_ecole = nom_ecole or "ÉCOLE INCONNUE"
            adresse_ecole = adresse_ecole or "Non renseignée"
            contact_ecole = contact_ecole or "-"

    # --- En-tête ---
    logo_cell = Image(logo_path, width=80, height=80) if logo_path and os.path.exists(logo_path) else Paragraph("", styles['Normal'])
    school_info = Paragraph(f"<b>{nom_ecole}</b><br/><font size='10'>{adresse_ecole}<br/>{contact_ecole}</font>", header_style)
    title = Paragraph("<b>BULLETIN SCOLAIRE</b>", title_style)

    header_data = [[logo_cell, school_info, title]]
    header_table = Table(header_data, colWidths=[80, 230, 180])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12)
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph("<hr width='100%' color='#3498DB' size='2'/>", styles['Normal']))
    elements.append(Spacer(1, 20))

    # --- Informations élève ---
    student_info = [
        ['INFORMATIONS ÉLÈVE', '', ''],
        ['Nom et Prénom', f"{eleve.nom} {eleve.prenom}", ''],
        ['Classe', eleve.classe.nom if eleve.classe else "Non renseignée", ''],
        ['Date de Naissance', eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else "Non renseignée", ''],
        ['Date d\'édition', datetime.now().strftime('%d/%m/%Y %H:%M'), '']
    ]
    student_table = Table(student_info, colWidths=[150, 220, 120])
    student_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2980B9')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#BDC3C7')),
        ('ROWBACKGROUNDS', (1,1), (-1,-1), [colors.white, colors.HexColor('#F8F9F9')])
    ]))
    elements.append(student_table)
    elements.append(Spacer(1, 25))

    # --- RÉSULTATS SCOLAIRES (Synthèse) ---
    elements.append(Paragraph("RÉSULTATS SCOLAIRES", subtitle_style))

    # Construire les lignes : Matière / Professeur / Moyenne / Appréciation
    data = [['Matière', 'Professeur', 'Moyenne', 'Appréciation']]

    # Si tu veux afficher les matières dans un ordre stable, trie par nom
    for cours in sorted(moyennes_par_cours.keys(), key=lambda x: (x or "").lower()):
        moyenne = moyennes_par_cours.get(cours, 0) or 0.0
        # Appréciation descriptive, avec symbole si souhaité
        if moyenne >= 16:
            appreciation = "⭐ Excellent"
        elif moyenne >= 14:
            appreciation = "✓ Très bien"
        elif moyenne >= 12:
            appreciation = "✓ Bien"
        elif moyenne >= 10:
            appreciation = "↔ Assez bien"
        else:
            appreciation = "■ Insuffisant"

        # Professeur : tenter d'extraire s'il y a un mapping depuis notes_par_cours
        prof_nom = 'Non assigné'
        notes_for_course = notes_par_cours.get(cours, [])
        # si une note a un cours avec professeur renseigné -> utiliser
        for n in notes_for_course:
            if getattr(n, 'cours', None) and getattr(n.cours, 'professeur', None):
                p = n.cours.professeur
                if p and getattr(p, 'prenom', None) or getattr(p, 'nom', None):
                    prof_nom = f"{p.prenom or ''} {p.nom or ''}".strip()
                    break

        # Formater la moyenne en paragraphe si on veut du bold ou colors
        moyenne_cell = Paragraph(f"<b>{moyenne:.2f}</b>", normal_center)
        data.append([cours, prof_nom, moyenne_cell, appreciation])

    # Ligne finale : moyenne générale mise en évidence
    overall_row = ['', '', Paragraph(f"<b>{moyenne_generale:.2f}</b>", normal_center), '']
    data.append(overall_row)

    table = Table(data, colWidths=[180, 130, 70, 130])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1F618D')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#7F8C8D')),
        ('ROWBACKGROUNDS', (1,1), (-1,-2), [colors.white, colors.HexColor('#F8F9F9')]),
        # Styliser la ligne finale (moyenne générale)
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#EBF5FB')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('SPAN', (0, -1), (1, -1)),  # on peut fusionner cellules si souhaité (ici non nécessaire)
    ]))
    elements.append(table)
    elements.append(Spacer(1, 30))

    # --- Observations / Signature ---
    signature_data = [
        ['OBSERVATIONS GÉNÉRALES:', ''],
        [Paragraph(f"<i>Moyenne générale : {moyenne_generale:.2f} — {'Très bon travail' if moyenne_generale >= 12 else 'Satisfaisant' if moyenne_generale >= 10 else 'Doit faire des efforts'}</i>", styles['Italic']), ''],
        ['', 'Le Directeur'],
        ['', '_________________________']
    ]
    signature_table = Table(signature_data, colWidths=[330, 150])
    signature_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('TOPPADDING', (0,0), (-1,-1), 6)
    ]))
    elements.append(signature_table)

    # --- Build & return buffer ---
    doc.build(elements)
    buffer.seek(0)
    return buffer

# ==============================================================================
# ROUTE BULLETIN
# ==============================================================================

@main.route('/bulletin_eleve/<int:id>')
@login_required
@role_required('admin', 'enseignant', 'parent')
def bulletin_eleve(id):
    """
    Génère le bulletin PDF d’un élève :
    - Sécurisé par école et rôle.
    - Accessible uniquement aux admins, enseignants et parents autorisés.
    """

    # Vérification d’accès spécifique au parent
    if current_user.role == 'parent':
        # Vérifie que le parent a bien accès à cet élève
        if not check_parent_access(id):
            flash("Accès non autorisé à cet élève.", "danger")
            return redirect(url_for('main.parent_dashboard'))

        # Vérifie qu'une période de bulletin est accessible
        if not bulletins_accessible_pour_parent():
            flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
            return redirect(url_for('main.parent_dashboard'))

    # 🔒 Vérification multi-école
    eleve = Eleve.query.filter_by(id=id, ecole_id=current_user.ecole_id).first()
    if not eleve:
        flash("Élève introuvable ou appartenant à une autre école.", "danger")
        return redirect(url_for('main.profile'))

    ecole = eleve.ecole

    # 🔹 Calcul des moyennes par cours
    moyennes = (
        Note.query.with_entities(
            Cours.nom.label('cours_nom'),
            (func.sum(Note.valeur * Note.coefficient) / func.sum(Note.coefficient)).label('moyenne')
        )
        .join(Cours, Note.cours_id == Cours.id)
        .filter(Note.eleve_id == id, Note.ecole_id == current_user.ecole_id)
        .group_by(Cours.nom)
        .all()
    )

    moyennes_par_cours = {
        m.cours_nom: round(m.moyenne, 2) if m.moyenne else 0
        for m in moyennes
    }

    moyenne_generale = (
        round(sum(moyennes_par_cours.values()) / len(moyennes_par_cours), 2)
        if moyennes_par_cours else 0
    )

    # 🔹 Notes détaillées par cours
    notes = (
        Note.query.options(joinedload(Note.cours))
        .filter_by(eleve_id=id, ecole_id=current_user.ecole_id)
        .order_by(Note.cours_id, Note.date_evaluation.desc())
        .all()
    )

    notes_par_cours = {}
    for note in notes:
        cours_nom = note.cours.nom if note.cours else "Non renseigné"
        notes_par_cours.setdefault(cours_nom, []).append(note)

    # 🔹 Génération du PDF
    try:
        buffer = generer_bulletin_pdf(
            eleve,
            notes_par_cours,
            moyennes_par_cours,
            moyenne_generale,
            logo_path=ecole.logo_path if ecole and ecole.logo_path else None,
            nom_ecole=ecole.nom if ecole else "École non renseignée",
            adresse_ecole=ecole.adresse if ecole else "-",
            contact_ecole=f"Tél: {ecole.telephone or '-'} - Email: {ecole.email or '-'}" if ecole else "-"
        )

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"bulletin_{eleve.prenom}_{eleve.nom}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        current_app.logger.error(f"Erreur lors de la génération du bulletin : {e}")
        flash("Erreur lors de la génération du bulletin PDF.", "danger")
        return redirect(url_for('main.profile'))


# ==============================================================================
# ROUTE QR CODE
# ==============================================================================
import os
import hashlib
from flask import current_app

def get_qr_cache_path(eleve):
    base_dir = os.path.join(current_app.root_path, "static", "qrcache")
    os.makedirs(base_dir, exist_ok=True)

    key = f"{eleve.id}-{eleve.updated_at}".encode()  # updated_at => régénération qd modifié
    filename = hashlib.md5(key).hexdigest() + ".png"

    return os.path.join(base_dir, filename)


@main.route('/eleve/<int:id>/qrcode')
@login_required
@role_required('admin')
def generer_qrcode_eleve(id):
    eleve = Eleve.query.get_or_404(id)

    cache_path = get_qr_cache_path(eleve)

    # → Si existe → renvoyer directement
    if os.path.exists(cache_path):
        return send_file(cache_path, mimetype='image/png',
                         download_name=f"qrcode_{eleve.prenom}_{eleve.nom}.png")

    # Sinon générer
    data = (
        f"ÉLÈVE: {eleve.prenom} {eleve.nom}\n"
        f"CLASSE: {eleve.classe.nom if eleve.classe else 'Non renseignée'}\n"
        f"DATE NAISSANCE: {eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else 'Non renseignée'}\n"
        f"TÉLÉPHONE: {eleve.telephone or 'Non renseigné'}\n"
        f"EMAIL: {eleve.email or 'Non renseigné'}\n"
    )

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image()

    img.save(cache_path)

    return send_file(cache_path, mimetype='image/png',
                     download_name=f"qrcode_{eleve.prenom}_{eleve.nom}.png")

@main.route('/qrcodes_etudiants')
@login_required
@role_required('admin', 'enseignant')
def qrcodes_etudiants():
    from collections import defaultdict
    import base64

    etudiants = (
        Eleve.query
        .filter_by(ecole_id=current_user.ecole_id)
        .order_by(Eleve.classe_id, Eleve.nom)
        .all()
    )

    qrcodes_par_classe = defaultdict(list)

    for e in etudiants:
        cache_path = get_qr_cache_path(e)

        # Génère si manquant
        if not os.path.exists(cache_path):
            data = f"{e.prenom} {e.nom}\nClasse: {e.classe.nom if e.classe else 'Non renseignée'}"
            qr = qrcode.make(data)
            qr.save(cache_path)

        # Charger en base64
        with open(cache_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()

        qrcodes_par_classe[e.classe.nom if e.classe else 'Non renseignée']\
            .append({'eleve': e, 'qr': img_data})

    return render_template('qrcodes_etudiants.html', qrcodes_par_classe=qrcodes_par_classe)



# ------------------ Routes pour l'aide ------------------

@main.route('/aide')
@login_required
def aide():
    """Page d'aide et support du site"""
    return render_template('aide.html')

# ------------------ Routes pour les tests ------------------

@main.route('/inscription_parent', methods=['GET', 'POST'])
def inscription_parent():
    # Récupérer toutes les classes disponibles pour le select
    classes = get_ecole_filter_query(Classe).all()

    if request.method == 'POST':
        nom_enfant = request.form.get('nom_enfant')
        prenom_enfant = request.form.get('prenom_enfant')
        date_naissance_str = request.form.get('date_naissance')
        classe_id = request.form.get('classe')  # on récupère l'id de la classe
        nom_parent = request.form.get('nom_parent')
        prenom_parent = request.form.get('prenom_parent')
        email_parent = request.form.get('email')
        telephone_parent = request.form.get('telephone_parent')

        if not all([nom_enfant, prenom_enfant, date_naissance_str, classe_id, nom_parent, prenom_parent, email_parent]):
            flash("Tous les champs sont obligatoires.", "warning")
            return redirect(url_for('main.inscription_parent'))

        # Conversion date de naissance
        try:
            date_naissance = datetime.strptime(date_naissance_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Format de date invalide.", "danger")
            return redirect(url_for('main.inscription_parent'))

        # Récupérer l'objet Classe
        classe_obj = Classe.query.get(classe_id)
        if not classe_obj:
            flash("Classe invalide.", "danger")
            return redirect(url_for('main.inscription_parent'))

        # Génération du code parent unique
        code_parent = Eleve.generer_code_parent()

        # Création de l'élève
        nouvel_eleve = Eleve(
            nom=nom_enfant,
            prenom=prenom_enfant,
            date_naissance=date_naissance,
            classe=classe_obj,       # association avec l'objet Classe
            code_parent=code_parent,
            email_parent=email_parent,
            telephone_parent=telephone_parent
        )
        db.session.add(nouvel_eleve)
        db.session.commit()

        flash("Inscription réussie !", "success")
        return redirect(url_for('main.inscription_parent'))

    return render_template('inscription_parent.html', classes=classes)

# ------------------ Routes pour les statistiques avancées ------------------
@main.route('/statistiques/avancees')
@login_required
@role_required('admin')
def statistiques_avancees():
    # -------------------------
    # Gestion des paramètres de période
    # -------------------------
    periode = request.args.get('periode', 'month')
    date_debut = request.args.get('date_debut')
    date_fin = request.args.get('date_fin')

    try:
        if date_debut:
            date_debut = datetime.strptime(date_debut, '%Y-%m-%d')
        if date_fin:
            date_fin = datetime.strptime(date_fin, '%Y-%m-%d')
    except ValueError:
        flash("Format de date invalide. Utilisation de la période par défaut.", "warning")
        date_debut = date_fin = None

    if not date_debut or not date_fin:
        now = datetime.now()
        if periode == 'today':
            date_debut = now.replace(hour=0, minute=0, second=0, microsecond=0)
            date_fin = now
        elif periode == 'week':
            date_debut = now - timedelta(days=7)
            date_fin = now
        elif periode == 'month':
            date_debut = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            date_fin = now
        elif periode == 'year':
            date_debut = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            date_fin = now
        else:  # all
            date_debut = datetime.min
            date_fin = now

    # -------------------------
    # Statistiques globales (multi-école)
    # -------------------------
    stats = {
        'eleves': get_ecole_filter_query(Eleve).count(),
        'enseignants': get_ecole_filter_query(Utilisateur).filter_by(role='professeur').count(),
        'cours': get_ecole_filter_query(Cours).count(),
        'absences_periode': get_ecole_filter_query(Absence).filter(
            Absence.date_absence.between(date_debut, date_fin)
        ).count(),
        'revenu_periode': get_ecole_filter_query(Paiement).with_entities(
            func.coalesce(func.sum(Paiement.montant), 0)
        ).filter(Paiement.date_paiement.between(date_debut, date_fin)).scalar(),
        'paiements': get_ecole_filter_query(Paiement).filter(
            Paiement.date_paiement.between(date_debut, date_fin)
        ).count(),
        'notes': get_ecole_filter_query(Note).filter(
            Note.date_evaluation.between(date_debut, date_fin)
        ).count(),
        'nouveaux_eleves': get_ecole_filter_query(Eleve).filter(
            Eleve.date_inscription.between(date_debut, date_fin)
        ).count()
    }

    # -------------------------
    # Moyenne générale
    # -------------------------
    moyenne_generale = get_ecole_filter_query(Note).with_entities(
        func.coalesce(func.sum(Note.valeur * Note.coefficient) / func.sum(Note.coefficient), 0)
    ).scalar()
    stats['moyenne_generale'] = round(moyenne_generale, 2)

    # -------------------------
    # Derniers élèves inscrits
    # -------------------------
    recent_eleves = get_ecole_filter_query(Eleve).order_by(Eleve.date_inscription.desc()).limit(5).all()

    # -------------------------
    # Absences par élève (top 10)
    # -------------------------
    absences_top = db.session.query(
        Eleve.prenom, Eleve.nom, func.count(Absence.id)
    ).join(Eleve.absences).filter(
        Absence.date_absence.between(date_debut, date_fin)
    ).group_by(Eleve.id).order_by(func.count(Absence.id).desc()).limit(10).all()

    noms_eleves = [f"{e[0]} {e[1]}" for e in absences_top]
    absences_par_eleve = [e[2] for e in absences_top]

    # -------------------------
    # Répartition des notes
    # -------------------------
    repartition_notes = [0, 0, 0, 0]  # <5 | 5-9.9 | 10-14.9 | 15+
    for note_val, _ in get_ecole_filter_query(Note).with_entities(Note.valeur, Note.id).all():
        if note_val < 5:
            repartition_notes[0] += 1
        elif note_val < 10:
            repartition_notes[1] += 1
        elif note_val < 15:
            repartition_notes[2] += 1
        else:
            repartition_notes[3] += 1

    # -------------------------
    # Top classes par moyenne
    # -------------------------
    classes_data = []
    classes_labels = []
    classes = get_ecole_filter_query(Classe).join(Classe.eleves).distinct().all()

    for classe in classes:
        eleves_ids = [e.id for e in classe.eleves]
        if eleves_ids:
            notes_query = get_ecole_filter_query(Note).filter(Note.eleve_id.in_(eleves_ids))
            total = notes_query.with_entities(func.coalesce(func.sum(Note.valeur * Note.coefficient), 0)).scalar()
            coeff = notes_query.with_entities(func.coalesce(func.sum(Note.coefficient), 0)).scalar()
            moyenne_classe = round(total / coeff, 2) if coeff > 0 else 0
            classes_labels.append(classe.nom)
            classes_data.append(moyenne_classe)

    # Trier et limiter au top 5
    if len(classes_data) > 5:
        combined = sorted(zip(classes_labels, classes_data), key=lambda x: x[1], reverse=True)[:5]
        classes_labels, classes_data = zip(*combined) if combined else ([], [])

    # -------------------------
    # Activités récentes (absences, paiements, notes)
    # -------------------------
    activites_recentes = []

    absences_recentes = get_ecole_filter_query(Absence).order_by(Absence.date_absence.desc()).limit(3).all()
    paiements_recent = get_ecole_filter_query(Paiement).order_by(Paiement.date_paiement.desc()).limit(3).all()
    notes_recentes = get_ecole_filter_query(Note).order_by(Note.date_evaluation.desc()).limit(3).all()

    for a in absences_recentes:
        activites_recentes.append({
            'type': 'absence',
            'details': f"{a.eleve.prenom} {a.eleve.nom} - {a.motif or 'Non spécifié'}",
            'date': datetime.combine(a.date_absence, datetime.min.time()) if isinstance(a.date_absence, date) else a.date_absence,
            'statut': 'completed'
        })
    for p in paiements_recent:
        activites_recentes.append({
            'type': 'paiement',
            'details': f"{p.eleve.prenom} {p.eleve.nom} - {p.montant} FCFA",
            'date': p.date_paiement,
            'statut': 'completed'
        })
    for n in notes_recentes:
        activites_recentes.append({
            'type': 'note',
            'details': f"{n.eleve.prenom} {n.eleve.nom} - {n.valeur}/20 en {n.cours.nom}",
            'date': n.date_evaluation,
            'statut': 'completed'
        })

    # Trier par date
    for act in activites_recentes:
        if isinstance(act['date'], date) and not isinstance(act['date'], datetime):
            act['date'] = datetime.combine(act['date'], datetime.min.time())
    activites_recentes.sort(key=lambda x: x['date'], reverse=True)
    activites_recentes = activites_recentes[:5]

    # -------------------------
    # Render template
    # -------------------------
    return render_template(
        'statistiques_avancees.html',
        stats=stats,
        recent_eleves=recent_eleves,
        noms_eleves=noms_eleves,
        absences_par_eleve=absences_par_eleve,
        repartition_notes=repartition_notes,
        classes_labels=classes_labels,
        classes_data=classes_data,
        activites_recentes=activites_recentes,
        periode=periode,
        date_debut=date_debut.strftime('%Y-%m-%d') if isinstance(date_debut, datetime) else '',
        date_fin=date_fin.strftime('%Y-%m-%d') if isinstance(date_fin, datetime) else ''
    )

# ------------------ Routes pour les alertes ------------------

# =======================================================================
# Route affichage alertes avec pagination
# =======================================================================
from flask import render_template, request, jsonify, url_for
from flask_login import login_required
from sqlalchemy import func
from datetime import datetime
import uuid
import threading

from app import db
from app.models import Eleve, Note, Absence, Paiement
from app.notifications import envoyer_email, envoyer_telegram

PER_PAGE_ALERTES = 10  # nombre d'alertes par page
TELEGRAM_CHAT_ID = "TON_CHAT_ID_GLOBAL"

# =======================================================================
# Génération automatique des alertes
# =======================================================================
def generer_alertes_automatiques(limit=None):
    alertes = []
    maintenant = datetime.now()
    mois_courant = maintenant.month
    annee_courante = maintenant.year
    mois_noms = ['Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
                 'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']

    # 1️⃣ Notes basses
    eleves = get_ecole_filter_query(Eleve).all()
    for eleve in eleves:
        notes = Note.query.filter_by(eleve_id=eleve.id).all()
        if notes:
            total_pondere = sum(n.valeur * n.coefficient for n in notes)
            total_coefficients = sum(n.coefficient for n in notes)
            moyenne = round(total_pondere / total_coefficients, 2)
            if moyenne < 10:
                alertes.append({
                    'id': f'note-{eleve.id}-{uuid.uuid4().hex}',
                    'type': 'danger',
                    'titre': 'Élève en difficulté académique',
                    'message': f'{eleve.prenom} {eleve.nom} ({eleve.classe}) a une moyenne de {moyenne}/20',
                    'date': maintenant,
                    'source': 'Notes',
                    'lien': url_for('main.voir_eleve', eleve_id=eleve.id),
                    'eleve_id': eleve.id,
                    'priorite': 3,
                    'notifie': False
                })

    # 2️⃣ Absences répétées
    absences = db.session.query(
        Absence.eleve_id,
        func.count(Absence.id).label('total_absences')
    ).filter_by(justifiee=False).group_by(Absence.eleve_id).all()

    for absence in absences:
        if absence.total_absences >= 3:
            eleve = Eleve.query.get(absence.eleve_id)
            alertes.append({
                'id': f'absence-{eleve.id}-{uuid.uuid4().hex}',
                'type': 'warning',
                'titre': 'Absences répétées non justifiées',
                'message': f'{eleve.prenom} {eleve.nom} ({eleve.classe}) a {absence.total_absences} absences non justifiées',
                'date': maintenant,
                'source': 'Absences',
                'lien': url_for('main.absences'),
                'eleve_id': eleve.id,
                'priorite': 2,
                'notifie': False
            })

    # 3️⃣ Paiements incomplets
    for eleve in eleves:
        paiements_eleve = Paiement.query.filter_by(eleve_id=eleve.id, annee=annee_courante).all()
        mois_payes = [p.mois for p in paiements_eleve]
        mois_manquants = [mois_noms[m-1] for m in range(1, mois_courant) if mois_noms[m-1] not in mois_payes]

        if mois_manquants:
            if len(mois_manquants) >= 3:
                type_alerte = 'danger'; priorite = 3
            elif len(mois_manquants) == 2:
                type_alerte = 'warning'; priorite = 2
            else:
                type_alerte = 'info'; priorite = 1

            message = (f"{eleve.prenom} {eleve.nom} ({eleve.classe}) n'a pas payé "
                       f"{'le mois de ' + mois_manquants[0] if len(mois_manquants)==1 else 'les mois de ' + ', '.join(mois_manquants)}")

            alertes.append({
                'id': f'paiement-{eleve.id}-{uuid.uuid4().hex}',
                'type': type_alerte,
                'titre': 'Paiements en retard',
                'message': message,
                'date': maintenant,
                'source': 'Paiements',
                'lien': url_for('main.paiements'),
                'eleve_id': eleve.id,
                'priorite': priorite,
                'details': {
                    'mois_manquants': mois_manquants,
                    'nombre_mois_manquants': len(mois_manquants),
                    'montant_total_du': eleve.frais_annuels / 12 * len(mois_manquants)
                },
                'notifie': False
            })

    # Tri par priorité puis date
    alertes.sort(key=lambda x: (-x['priorite'], x['date']))

    # Limite pour pagination côté API
    if limit:
        alertes = alertes[:limit]

    return alertes

# =======================================================================
# Notifications critiques en arrière-plan
# =======================================================================
def notifier_alertes(alertes):
    # Récupère l'objet Flask réel
    app = current_app._get_current_object()

    def worker(alertes_to_notify, flask_app):
        # Important : activer le contexte Flask dans le thread
        with flask_app.app_context():

            for a in alertes_to_notify:
                if a['type'] in ['danger', 'warning'] and not a.get('notifie'):

                    eleve = Eleve.query.get(a['eleve_id'])

                    message = (
                        f"{a['titre']}\n"
                        f"{a['message']}\n"
                        f"Source: {a['source']}"
                    )

                    if eleve and eleve.email_parent:
                        envoyer_email(
                            eleve.email_parent,
                            f"Alerte: {a['titre']}",
                            message
                        )

                    envoyer_telegram(message)
                    a['notifie'] = True

            # Commit SQLAlchemy
            try:
                db.session.commit()
            except:
                db.session.rollback()

    # Passage explicite de l'app au thread
    thread = threading.Thread(target=worker, args=(alertes, app))
    thread.start()


# =======================================================================
# Route principale alertes
# =======================================================================
@main.route('/alertes')
@login_required
@role_required('admin')
def alertes():
    page = request.args.get('page', 1, type=int)
    all_alertes = generer_alertes_automatiques()
    total_alertes = len(all_alertes)
    total_pages = (total_alertes + PER_PAGE_ALERTES - 1) // PER_PAGE_ALERTES
    start = (page - 1) * PER_PAGE_ALERTES
    end = start + PER_PAGE_ALERTES
    alertes_page = all_alertes[start:end]

    # Notifications en arrière-plan
    notifier_alertes(alertes_page)

    stats = {
        "alertes_urgentes": sum(1 for a in all_alertes if a["type"] == "danger"),
        "alertes_importantes": sum(1 for a in all_alertes if a["type"] == "warning"),
        "alertes_info": sum(1 for a in all_alertes if a["type"] == "info"),
        "alertes_system": 0,
        "alertes_traitees": 0,
        "alertes_total": total_alertes
    }

    return render_template(
        'alertes.html',
        alertes=alertes_page,
        stats=stats,
        page=page,
        total_pages=total_pages
    )

# =======================================================================
# API JSON des alertes
# =======================================================================
@main.route('/api/alertes', methods=['GET'])
@login_required
def api_alertes():
    alertes = generer_alertes_automatiques(limit=50)  # limite pour performance
    for a in alertes:
        if isinstance(a['date'], datetime):
            a['date'] = a['date'].strftime('%d/%m/%Y %H:%M')
    return jsonify({'alertes': alertes})

# =======================================================================
# API marquer alerte lue
# =======================================================================
@main.route('/api/alertes/<string:alert_id>/read', methods=['POST'])
@login_required
def marquer_alerte_lue(alert_id):
    return jsonify({'success': True, 'message': f'Alerte {alert_id} marquée comme lue'})

# =======================================================================
# API pour test notification
# =======================================================================
@main.route('/api/notifications/test', methods=['POST'])
@login_required
def envoyer_notification_test():
    try:
        data = request.get_json() or {}
        contact = data.get('contact', '').strip()
        message = data.get('message', 'Message de test depuis EduManage')
        channel = data.get('channel', 'app').lower()

        if channel not in ['app', 'telegram'] and not contact:
            return jsonify({'success': False, 'message': f'Contact requis pour le canal {channel}'}), 400

        if channel == 'app':
            return jsonify({'success': True, 'message': 'Notification ajoutée dans l’application'})

        if channel == 'email':
            ok = envoyer_email(contact, "Test de notification - EduManage", message)
            return jsonify({'success': ok, 'message': 'Email envoyé' if ok else 'Échec envoi email'})

        if channel == 'telegram':
            chat_id = contact if contact else TELEGRAM_CHAT_ID
            ok = envoyer_telegram(message, chat_id)
            return jsonify({'success': ok, 'message': 'Message Telegram envoyé' if ok else 'Échec envoi Telegram'})

        return jsonify({'success': False, 'message': 'Canal inconnu'}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# --------------------- Demande de réinitialisation ---------------------

# Dans votre fichier routes.py, remplacez les fonctions existantes par :

@main.route('/request_reset_password', methods=['GET', 'POST'])
def request_reset_password():
    """Page pour demander un lien de réinitialisation par email"""
    from app.forms import RequestResetPasswordForm
    from app.notifications import envoyer_email  # <-- Import correct, même que pour ajouter_eleve

    form = RequestResetPasswordForm()

    if form.validate_on_submit():
        email = form.email.data.lower()
        utilisateur = Utilisateur.query.filter_by(email=email).first()

        if utilisateur:
            # Génération du token sécurisé
            serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
            token = serializer.dumps(email, salt=current_app.config['SECURITY_PASSWORD_SALT'])

            reset_link = url_for('main.reset_password_token', token=token, _external=True)
            sujet = "Réinitialisation de votre mot de passe"
            
            # Message HTML compatible Gmail
            message = f"""
            <html>
            <body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:20px;">
                <div style="max-width:600px; margin:auto; background:#fff; border-radius:10px; padding:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);">
                    <h2 style="color:#4CAF50;">Bonjour {utilisateur.nom},</h2>
                    <p>Pour réinitialiser votre mot de passe, cliquez sur le lien suivant :</p>
                    <p><a href="{reset_link}" style="display:inline-block; padding:10px 20px; background:#4CAF50; color:#fff; text-decoration:none; border-radius:5px;">Réinitialiser mon mot de passe</a></p>
                    <p>Ce lien est valable 1 heure.</p>
                    <p>Si vous n'avez pas demandé cette réinitialisation, ignorez ce message.</p>
                    <p style="font-size:12px; color:#555;">Cordialement,<br>L'équipe EduManage</p>
                </div>
            </body>
            </html>
            """

            try:
                # Utilisation de la fonction centralisée comme pour ajouter_eleve
                envoyer_email(utilisateur.email, sujet, message)
                current_app.logger.info(f"Email de reset envoyé à {utilisateur.email}")
            except Exception as e:
                current_app.logger.error(f"Erreur envoi email reset: {e}")

        else:
            current_app.logger.info(f"Tentative de reset pour email inexistant: {email}")

        # Message générique pour éviter de révéler l'existence d'un compte
        flash("Si un compte existe pour cet email, un lien de réinitialisation a été envoyé.", "info")
        return redirect(url_for('main.login'))

    return render_template('request_reset_password.html', form=form)


@main.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password_token(token):
    """Réinitialisation du mot de passe via token sécurisé"""
    from app.forms import ResetPasswordConfirmForm

    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(
            token, 
            salt=current_app.config['SECURITY_PASSWORD_SALT'], 
            max_age=3600  # lien valable 1 heure
        )
    except Exception:
        flash("Le lien de réinitialisation est invalide ou expiré.", "danger")
        return redirect(url_for('main.login'))

    form = ResetPasswordConfirmForm()

    if form.validate_on_submit():
        new_password = form.new_password.data
        utilisateur = Utilisateur.query.filter_by(email=email).first()

        if utilisateur:
            try:
                utilisateur.mot_de_passe = generate_password_hash(new_password)
                db.session.commit()
                flash("Mot de passe réinitialisé avec succès ! Vous pouvez maintenant vous connecter.", "success")
                return redirect(url_for('main.login'))
            except Exception as e:
                current_app.logger.error(f"Erreur mise à jour mot de passe: {e}")
                flash("Erreur lors de la réinitialisation. Veuillez réessayer.", "danger")
                return redirect(url_for('main.login'))

        flash("Utilisateur introuvable.", "danger")
        return redirect(url_for('main.login'))

    return render_template('reset_password.html', form=form, token=token)

@main.route('/bulletins')
@login_required
def bulletins():
    # 🔒 Vérifier l'accès pour les parents
    if current_user.role == 'parent' and not bulletins_accessible_pour_parent():
        flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
        return redirect(url_for('main.parent_dashboard'))  # adapte selon ton projet

    # Récupérer tous les élèves de l'école du user
    eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).all()

    # Calculer les moyennes pour chaque élève
    eleves_avec_moyennes = []
    for eleve in eleves:
        notes = Note.query.filter_by(eleve_id=eleve.id).all()
        if notes:
            total_pondere = sum(n.valeur * n.coefficient for n in notes)
            total_coefficients = sum(n.coefficient for n in notes)
            moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients > 0 else 0
        else:
            moyenne = 0

        eleves_avec_moyennes.append({
            'eleve': eleve,
            'moyenne': moyenne,
            'notes_count': len(notes)
        })
    
    # Trier par moyenne décroissante
    eleves_avec_moyennes.sort(key=lambda x: x['moyenne'], reverse=True)
    
    # Calculer les stats globales
    if eleves_avec_moyennes:
        moyenne_generale = round(sum(e['moyenne'] for e in eleves_avec_moyennes) / len(eleves_avec_moyennes), 2)
        meilleure_moyenne = max(e['moyenne'] for e in eleves_avec_moyennes)
        taux_reussite = round(sum(1 for e in eleves_avec_moyennes if e['moyenne'] >= 10) / len(eleves_avec_moyennes) * 100, 1)
    else:
        moyenne_generale = 0
        meilleure_moyenne = 0
        taux_reussite = 0
    
    return render_template(
        'bulletins.html',
        eleves=eleves_avec_moyennes,
        moyenne_generale=moyenne_generale,
        meilleure_moyenne=meilleure_moyenne,
        taux_reussite=taux_reussite,
        total_eleves=len(eleves),
        bulletins_accessibles=bulletins_accessible_pour_parent()  # fonctionne maintenant
    )

@main.route('/note/<int:note_id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'enseignant')
def modifier_note(note_id):
    note = Note.query.get_or_404(note_id)

    # --- Récupérer l'école courante ---
    ecole = get_ecole_courante()

    # --- Vérification de l'année active de l'école ---
    annee_active = AnneeScolaire.query.filter_by(id=note.annee_id, ecole_id=ecole.id, statut='active').first()
    if not annee_active:
        flash("Vous ne pouvez modifier une note que pour une année scolaire active de votre école.", "warning")
        return redirect(url_for('main.notes'))

    # --- Vérification permissions enseignants ---
    if current_user.role == 'enseignant':
        professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
        if not professeur or (note.cours and note.cours.professeur_id != professeur.id):
            flash("Vous n'êtes pas autorisé à modifier cette note.", "danger")
            return redirect(url_for('main.notes'))

    form = NoteForm(obj=note)

    # --- Forcer l'année existante pour l'élève et le cours ---
    if form.eleve_id.data is None:
        form.eleve_id.data = note.eleve_id
    if form.cours_id.data is None:
        form.cours_id.data = note.cours_id
    if form.annee_id.data is None:
        form.annee_id.data = note.annee_id

    # --- Soumission formulaire ---
    if form.validate_on_submit():
        if current_user.role == 'enseignant':
            cours = Cours.query.get(form.cours_id.data)
            if not cours or cours.professeur_id != professeur.id:
                flash("Vous ne pouvez pas modifier cette note.", "danger")
                return redirect(url_for('main.notes'))

        # Mise à jour
        note.valeur = form.valeur.data
        note.coefficient = form.coefficient.data
        note.type_evaluation = form.type_evaluation.data
        note.periode = form.periode.data
        note.eleve_id = form.eleve_id.data
        note.cours_id = form.cours_id.data
        note.annee_id = form.annee_id.data

        db.session.commit()
        flash("Note modifiée avec succès", "success")
        return redirect(url_for('main.notes'))

    return render_template('modifier_note.html', form=form, note=note)

@main.route('/notes/supprimer/<int:note_id>', methods=['POST'])
@login_required
def supprimer_note(note_id):
    # Récupérer la note et la supprimer
    note = Note.query.get_or_404(note_id)
    db.session.delete(note)
    db.session.commit()
    flash("Note supprimée avec succès.", "success")
    return redirect(url_for('main.notes'))


@main.route('/absences/edit/<int:absence_id>', methods=['GET', 'POST'])
@login_required
def edit_absence(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    form = AbsenceForm(obj=absence)
    
    # Remplir les choix des élèves
    if current_user.role == 'parent':
        enfants = Eleve.query.filter_by(parent_id=current_user.id).all()
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]
        form.eleve_id.render_kw = {'disabled': True} if len(enfants) == 1 else {}
    else:
        eleves = Eleve.query.join(Classe, isouter=True).order_by(Classe.nom, Eleve.nom).all()
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in eleves
        ]
    
    # Remplir les choix des cours
    form.cours_id.choices = [(c.id, c.nom) for c in Cours.query.order_by(Cours.nom).all()]

    # Mettre à jour la sélection actuelle
    form.eleve_id.data = absence.eleve_id
    form.cours_id.data = absence.cours_id

    if form.validate_on_submit():
        absence.eleve_id = form.eleve_id.data
        absence.cours_id = form.cours_id.data
        absence.date_absence = form.date_absence.data
        absence.motif = form.motif.data
        absence.justifiee = form.justifiee.data

        db.session.commit()
        flash("Absence mise à jour avec succès.", "success")
        return redirect(url_for('main.absences'))
    
    return render_template('edit_absence.html', form=form)


@main.route('/absences/delete/<int:absence_id>', methods=['POST'])
@login_required
def delete_absence(absence_id):
    try:
        absence = Absence.query.get_or_404(absence_id)
        db.session.delete(absence)
        db.session.commit()
        flash("Absence supprimée avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")
    
    return redirect(url_for('main.absences'))

# ------------------ Routes pour les enseignants ------------------


@main.route('/enseignant/dashboard')
@login_required
@role_required('enseignant', 'professeur')
def enseignant_dashboard():
    """Tableau de bord enseignant avec données personnalisées"""
    professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
    
    if not professeur:
        flash("Profil enseignant non trouvé. Contactez l'administrateur.", "warning")
        return redirect(url_for('main.index'))
    
    mes_cours = Cours.query.filter_by(professeur_id=professeur.id).all()
    
    emplois = EmploiTemps.query.filter_by(professeur_id=professeur.id).order_by(
        EmploiTemps.jour, EmploiTemps.heure_debut
    ).all()
    
    stats = {
        'total_eleves': len(set([note.eleve_id for cours in mes_cours for note in cours.notes])),
        'total_cours': len(mes_cours),
        'moyenne_generale': db.session.query(func.avg(Note.valeur)).filter(
            Note.cours_id.in_([c.id for c in mes_cours])
        ).scalar() or 0
    }
    
    cours_ids = [c.id for c in mes_cours]
    dernieres_notes = Note.query.filter(Note.cours_id.in_(cours_ids)).order_by(Note.date_evaluation.desc()).limit(5).all()
    
    now = datetime.now()
    aujourdhui = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    
    return render_template(
        'enseignant_dashboard.html',
        stats=stats,
        mes_cours=mes_cours,
        dernieres_notes=dernieres_notes,
        emplois=emplois,
        now=now,
        aujourdhui=aujourdhui
    )


@main.route('/enseignant')
@login_required
@role_required('enseignant', 'professeur')
def enseignant_home():
    """Page d'accueil de l'enseignant avec emploi du temps"""
    professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
    
    if not professeur:
        flash("Profil enseignant non trouvé", "danger")
        return redirect(url_for('main.logout'))
    
    emplois = EmploiTemps.query.filter_by(professeur_id=professeur.id).order_by(
        EmploiTemps.jour, EmploiTemps.heure_debut
    ).all()
    
    return render_template('enseignant_home.html', emplois=emplois)

from app.utils import log_action

# Liste des emplois du temps
@main.route('/admin/emplois')
@login_required
@role_required('admin')
def admin_emplois():
    """Liste des emplois du temps - filtrée par école"""
    page = request.args.get('page', 1, type=int)
    per_page = 30

    query = get_ecole_filter_query(EmploiTemps).order_by(EmploiTemps.jour, EmploiTemps.heure_debut)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    delete_form = DeleteForm()

    # Compteurs statistiques (pour l'aperçu global)
    ecole_id = current_user.ecole_id
    classes_count = Classe.query.filter_by(ecole_id=ecole_id).count()
    professeurs_count = Professeur.query.filter_by(ecole_id=ecole_id).count()
    salles_count = EmploiTemps.query.filter_by(ecole_id=ecole_id).distinct(EmploiTemps.salle).count()

    return render_template('admin_emplois.html',
                           emplois=pagination.items,
                           pagination=pagination,
                           delete_form=delete_form,
                           classes_count=classes_count,
                           professeurs_count=professeurs_count,
                           salles_count=salles_count,
                           now=datetime.now())


@main.route('/admin/ajouter_emploi', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_emploi():
    """Ajout d'un emploi du temps - filtré par école"""
    ecole_id = current_user.ecole_id
    form = AjouterEmploiForm()

    # Menus déroulants filtrés par école
    form.classe_id.choices = [(c.id, c.nom) for c in Classe.query.filter_by(ecole_id=ecole_id).order_by(Classe.nom)]
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.nom)]
    form.cours_id.choices = [(c.id, c.nom) for c in Cours.query.filter_by(ecole_id=ecole_id).order_by(Cours.nom)]

    if form.validate_on_submit():
        # Vérifier les doublons
        conflit = EmploiTemps.query.filter_by(
            classe_id=form.classe_id.data,
            jour=form.jour.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
            ecole_id=ecole_id
        ).first()

        if conflit:
            flash("⚠️ Cet emploi existe déjà pour cette classe à la même heure.", "warning")
            return redirect(url_for('main.admin_emplois'))

        # Création
        emploi = EmploiTemps(
            classe_id=form.classe_id.data,
            professeur_id=form.professeur_id.data,
            cours_id=form.cours_id.data,
            jour=form.jour.data,
            salle=form.salle.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
            ecole_id=ecole_id
        )
        db.session.add(emploi)
        db.session.commit()

        # Journalisation
        log_action(current_user, f"Ajout emploi du temps pour la classe ID={form.classe_id.data}")

        flash("✅ Emploi du temps ajouté avec succès !", "success")
        return redirect(url_for('main.admin_emplois'))

    return render_template('admin_ajouter_emploi.html', form=form)


# Modifier un emploi du temps
@main.route('/emploi/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_emploi(id):
    emploi = EmploiTemps.query.get_or_404(id)
    form = AjouterEmploiForm(obj=emploi)  # Pré-remplit le formulaire avec l'objet existant

    if form.validate_on_submit():
        emploi.classe_id = form.classe_id.data
        emploi.professeur_id = form.professeur_id.data
        emploi.cours_id = form.cours_id.data
        emploi.jour = form.jour.data
        emploi.heure_debut = form.heure_debut.data
        emploi.heure_fin = form.heure_fin.data
        emploi.salle = form.salle.data
        db.session.commit()
        flash('Emploi du temps modifié avec succès', 'success')
        return redirect(url_for('main.admin_emplois'))

    return render_template('modifier_emploi.html', form=form, emploi=emploi)

# ------------------ Gestion des utilisateurs ------------------
# Ajouter ces routes après les imports existants


@main.route('/admin/utilisateurs')
@login_required
@role_required('admin')
def gestion_utilisateurs():
    page = request.args.get('page', 1, type=int)
    ecole = get_ecole_courante()

    try:
        if ecole:  # admin rattaché à une école
            utilisateurs_query = filtre_par_ecole(Utilisateur.query, Utilisateur)
            professeurs_query = filtre_par_ecole(Professeur.query, Professeur)
            parents_query = filtre_par_ecole(Utilisateur.query.filter_by(role='parent'), Utilisateur)

            utilisateurs = utilisateurs_query.paginate(page=page, per_page=10, error_out=False)
            professeurs = professeurs_query.paginate(page=page, per_page=10, error_out=False)
            parents = parents_query.paginate(page=page, per_page=10, error_out=False)

            return render_template(
                'profile.html',
                utilisateurs=utilisateurs,
                professeurs=professeurs,
                parents=parents
            )
        else:  # super-admin
            ecoles_query = Ecole.query.paginate(page=page, per_page=10, error_out=False)
            return render_template(
                'superadmin_ecoles.html',
                ecoles=ecoles_query
            )

    except Exception as e:
        current_app.logger.error(f"Erreur gestion utilisateurs: {e}")
        flash("Erreur lors de la récupération des utilisateurs.", "danger")
        return redirect(url_for('main.index'))



@main.route('/admin/creer_utilisateur', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def creer_utilisateur():
    from datetime import datetime
    from werkzeug.security import generate_password_hash

    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        email = request.form.get('email', '').strip()
        telephone = request.form.get('telephone', '').strip()
        role = request.form.get('role', '').strip()
        mot_de_passe = request.form.get('mot_de_passe', '').strip()

        if not all([nom, prenom, email, role, mot_de_passe]):
            flash("Tous les champs obligatoires doivent être remplis.", "warning")
            return redirect(url_for('main.creer_utilisateur'))

        if Utilisateur.query.filter_by(email=email).first():
            flash('Cet email est déjà utilisé', 'danger')
            return redirect(url_for('main.gestion_utilisateurs'))

        try:
            nouvel_utilisateur = Utilisateur(
                nom=nom,
                prenom=prenom,
                email=email,
                telephone=telephone,
                role=role,
                mot_de_passe=generate_password_hash(mot_de_passe),
                statut='actif'
            )

            ajouter_ecole_id(nouvel_utilisateur)

            db.session.add(nouvel_utilisateur)
            db.session.commit()

            # Créer automatiquement le profil Professeur si rôle enseignant
            if role == 'enseignant':
                prof_profile = Professeur(
                    utilisateur_id=nouvel_utilisateur.id,
                    nom=nom or "NomProf",
                    prenom=prenom or "PrenomProf",
                    email=email or "prof@exemple.com",
                    telephone=telephone or "+22700000000",
                    date_naissance=datetime(1990, 1, 1),
                    adresse="Adresse par défaut",
                    specialite="Non définie",
                    matieres_enseignees="Aucune",
                    photo="default_prof.png",
                    date_embauche=datetime.utcnow()
                )
                ajouter_ecole_id(prof_profile)
                db.session.add(prof_profile)
                db.session.commit()

            flash('Utilisateur créé avec succès', 'success')
            return redirect(url_for('main.gestion_utilisateurs'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur création utilisateur: {e}")
            flash("Erreur lors de la création de l'utilisateur.", "danger")
            return redirect(url_for('main.gestion_utilisateurs'))

    return render_template('admin/creer_utilisateur.html')


@main.route('/admin/utilisateur/<int:user_id>/statut', methods=['POST'])
@login_required
@role_required('admin')
def changer_statut_utilisateur(user_id):
    try:
        user = Utilisateur.query.get_or_404(user_id)
        data = request.get_json()
        if not data or 'statut' not in data:
            return jsonify({'success': False, 'message': 'Données JSON requises'}), 400

        nouveau_statut = data.get('statut')
        if nouveau_statut not in ['actif', 'bloque']:
            return jsonify({'success': False, 'message': 'Statut invalide'}), 400

        user.statut = nouveau_statut
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur changement statut utilisateur: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/admin/utilisateur/<int:user_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def supprimer_utilisateur(user_id):
    user = Utilisateur.query.get_or_404(user_id)

    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer vous-même'}), 403

    try:
        db.session.delete(user)
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression utilisateur: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/admin/eleve/<int:eleve_id>/regenerer-code', methods=['POST'])
@login_required
@role_required('admin')
def regenerer_code_parent(eleve_id):
    eleve = Eleve.query.get_or_404(eleve_id)
    try:
        nouveau_code = Eleve.generer_code_parent()
        eleve.code_parent = nouveau_code
        db.session.commit()
        return jsonify({'success': True, 'nouveau_code': nouveau_code}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur génération code parent: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/admin/parent/<int:parent_id>/envoyer-credentials', methods=['POST'])
@login_required
@role_required('admin')
def envoyer_credentials_parent(parent_id):
    parent = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=parent_id, role='parent').first()
    if not parent:
        return jsonify({'success': False, 'message': "Parent introuvable ou non autorisé."}), 404

    eleve = parent.enfants[0] if parent.enfants else None
    if not eleve:
        return jsonify({'success': False, 'message': "Aucun élève associé à ce parent."}), 400

    if not eleve.code_parent:
        eleve.code_parent = Eleve.generer_code_parent()
        db.session.commit()

    try:
        import qrcode, io, base64
        qr_data = f"Parent: {parent.prenom} {parent.nom}\nEmail: {parent.email}\nMot de passe: {eleve.code_parent}"
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()

        sujet = "Vos identifiants de connexion - EduManage"
        message = f"""
        <h3>Bonjour {parent.prenom or ''} {parent.nom},</h3>
        <p>Voici vos identifiants pour accéder au portail parent :</p>
        <ul>
            <li>Email : {parent.email}</li>
            <li>Code d'accès : {eleve.code_parent}</li>
        </ul>
        <img src="data:image/png;base64,{qr_base64}" width="150" height="150"/>
        """

        from app.notifications import envoyer_email
        if envoyer_email(parent.email, sujet, message):
            return jsonify({'success': True, 'message': 'Email envoyé avec succès.'}), 200
        else:
            return jsonify({'success': False, 'message': 'Erreur lors de l’envoi de l’email.'}), 500

    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Erreur lors de l’envoi des credentials: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/emploi/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_emploi(id):
    emploi = EmploiTemps.query.get_or_404(id)
    try:
        db.session.delete(emploi)
        db.session.commit()
        flash('Emploi du temps supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression emploi: {e}")
        flash('Erreur lors de la suppression de l\'emploi du temps.', 'danger')
    return redirect(url_for('main.admin_emplois'))






def generate_password(length=8):
    """Génère un mot de passe aléatoire avec lettres et chiffres"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

@main.route('/eleve/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_eleve(id):
    eleve = Eleve.query.get_or_404(id)

    # 🛡️ Sécurité multi-écoles : empêche la suppression inter-écoles
    if current_user.role != 'super_admin' and eleve.ecole_id != current_user.ecole_id:
        flash("Action non autorisée : cet élève appartient à une autre école.", "danger")
        return redirect(url_for('main.eleves'))

    # Vérifier s'il y a des données liées
    if eleve.notes or eleve.paiements or eleve.absences:
        flash("Impossible de supprimer cet élève car il a des données associées.", "danger")
        return redirect(url_for('main.eleves'))

    try:
        db.session.delete(eleve)
        db.session.commit()
        current_app.logger.info(f"Élève supprimé : {eleve.nom} (ID={eleve.id}) par {current_user.email}")
        flash("Élève supprimé avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur lors de la suppression de l’élève {eleve.id} : {e}")
        flash("Erreur lors de la suppression de l’élève.", "danger")

    return redirect(url_for('main.eleves'))


@main.route('/professeur/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_professeur(id):
    professeur = Professeur.query.get_or_404(id)

    # 🛡️ Sécurité multi-écoles : empêche la suppression inter-écoles
    if current_user.role != 'super_admin' and professeur.ecole_id != current_user.ecole_id:
        flash("Action non autorisée : ce professeur appartient à une autre école.", "danger")
        return redirect(url_for('main.professeurs'))

    # Vérifier s'il y a des cours associés
    if professeur.cours:
        flash("Impossible de supprimer ce professeur car il a des cours associés.", "danger")
        return redirect(url_for('main.professeurs'))

    try:
        # Supprimer aussi l'utilisateur associé si existe
        if professeur.utilisateur_id:
            utilisateur = Utilisateur.query.get(professeur.utilisateur_id)
            if utilisateur:
                db.session.delete(utilisateur)

        db.session.delete(professeur)
        db.session.commit()
        current_app.logger.info(f"Professeur supprimé : {professeur.nom} (ID={professeur.id}) par {current_user.email}")
        flash("Professeur supprimé avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur lors de la suppression du professeur {professeur.id} : {e}")
        flash("Erreur lors de la suppression du professeur.", "danger")

    return redirect(url_for('main.professeurs'))



@main.route('/cours/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_cours(id):
    # ✅ Sécurisation multi-écoles
    cours = filtre_par_ecole(Cours.query, Cours).filter_by(id=id).first_or_404()

    try:
        # Vérifier s'il y a des notes ou absences associées
        if cours.notes or cours.absences:
            flash("Impossible de supprimer ce cours car il a des données associées.", "danger")
            return redirect(url_for('main.cours'))

        ancienne_valeur = f"Cours: {cours.nom} (Prof: {cours.professeur_id}, Classe: {cours.classe_id})"

        db.session.delete(cours)
        db.session.commit()

        # ✅ Journalisation
        current_app.log_correction(
            action="suppression_cours",
            description=f"Cours supprimé : {cours.nom}",
            ecole_id=cours.ecole_id,
            cible_type="cours",
            cible_id=id,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=None,
            niveau="info"
        )

        flash("Cours supprimé avec succès.", "success")
        return redirect(url_for('main.cours'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression cours {id}: {e}")
        flash("Erreur inattendue lors de la suppression du cours.", "danger")
        return redirect(url_for('main.cours'))


@main.route('/paiement/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_paiement(id):
    try:
        # ✅ Sécurisation multi-écoles
        paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

        ancienne_valeur = f"Paiement ID {paiement.id} (Élève: {paiement.eleve_id}, Montant: {paiement.montant})"

        db.session.delete(paiement)
        db.session.commit()

        # ✅ Journalisation
        current_app.log_correction(
            action="suppression_paiement",
            description=f"Paiement supprimé ID {paiement.id}",
            ecole_id=paiement.ecole_id,
            cible_type="paiement",
            cible_id=id,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=None,
            niveau="info"
        )

        flash("Paiement supprimé avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression paiement {id}: {e}")
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")
    
    return redirect(url_for('main.paiements'))


@main.route('/eleve/<int:id>/supprimer-cascade', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_eleve_cascade(id):
    """Supprime un élève et toutes ses données associées, avec journalisation."""
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=id).first_or_404()

    try:
        ancienne_valeur = f"{eleve.nom} {eleve.prenom} (Classe: {eleve.classe_id})"

        # Supprimer toutes les données associées
        Note.query.filter_by(eleve_id=id).delete(synchronize_session=False)
        Paiement.query.filter_by(eleve_id=id).delete(synchronize_session=False)
        Absence.query.filter_by(eleve_id=id).delete(synchronize_session=False)
        Inscription.query.filter_by(eleve_id=id).delete(synchronize_session=False)  # <-- Ajouté

        db.session.delete(eleve)
        db.session.commit()

        # ✅ Journalisation complète
        current_app.log_correction(
            action="suppression_cascade",
            description=f"Élève et données associées supprimés : {eleve.nom} {eleve.prenom}",
            ecole_id=eleve.ecole_id,
            cible_type="eleve",
            cible_id=id,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=None,
            niveau="info"
        )

        flash("Élève et toutes ses données associées supprimés avec succès.", "success")
        return redirect(url_for('main.eleves'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression cascade élève {id}: {e}")
        flash("Erreur inattendue lors de la suppression.", "danger")
        return redirect(url_for('main.eleves'))





@main.route("/classes")
@login_required
def liste_classes():
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Nombre d'éléments par page
    
    # Filtres
    search = request.args.get('search', '')
    niveau = request.args.get('niveau', '')
    sort_by = request.args.get('sort', 'nom')
    
    # Base query pour l'école de l'utilisateur
    base_query = Classe.query.filter_by(ecole_id=current_user.ecole_id)
    
    if current_user.role == 'prof':
        # Professeur : récupérer uniquement sa classe
        if current_user.classe_id:
            base_query = base_query.filter(Classe.id == current_user.classe_id)
        else:
            classes_paginated = []
            total = 0
            return render_template("classes.html", classes=[], **get_statistics([]))
    
    # Appliquer les filtres
    if search:
        base_query = base_query.filter(Classe.nom.ilike(f'%{search}%'))
    
    if niveau:
        base_query = base_query.filter(Classe.niveau == niveau)
    
    # Appliquer le tri
    if sort_by == 'effectif':
        base_query = base_query.order_by(Classe.effectif.desc())
    elif sort_by == 'niveau':
        base_query = base_query.order_by(Classe.niveau)
    else:  # tri par nom par défaut
        base_query = base_query.order_by(Classe.nom)
    
    # Pagination
    classes_paginated = base_query.paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Récupérer toutes les classes pour les statistiques (sans pagination)
    all_classes = base_query.all()
    
    # Calculer les valeurs pour la pagination
    start_item = ((page - 1) * per_page) + 1
    end_item = min(page * per_page, classes_paginated.total)
    
    return render_template(
        "classes.html",
        classes=classes_paginated.items,
        pagination=classes_paginated,
        total_eleves=sum(c.effectif for c in all_classes),
        moyenne_effectif=int(sum(c.effectif for c in all_classes) / len(all_classes)) if all_classes else 0,
        classes_pleines=sum(1 for c in all_classes if c.effectif >= (c.capacite or 30)),
        current_filters={
            'search': search,
            'niveau': niveau,
            'sort': sort_by
        },
        start_item=start_item,
        end_item=end_item
    )

def get_statistics(classes):
    """Helper function to calculate statistics"""
    if not classes:
        return {
            'total_eleves': 0,
            'moyenne_effectif': 0,
            'classes_pleines': 0
        }
    
    total_eleves = sum(c.effectif for c in classes)
    moyenne_effectif = int(total_eleves / len(classes)) if classes else 0
    
    from sqlalchemy import func
    classes_pleines = 0
    for c in classes:
        nb_inscriptions = db.session.query(func.count(Inscription.id)).filter_by(classe_id=c.id).scalar()
        if nb_inscriptions >= (c.capacite or 30):
            classes_pleines += 1
    
    return {
        'total_eleves': total_eleves,
        'moyenne_effectif': moyenne_effectif,
        'classes_pleines': classes_pleines
    }
    
    
    
@main.route("/classes/add", methods=["GET", "POST"])
@login_required
@role_required('admin')
def ajouter_classe():
    form = ClasseForm()
    # Professeurs filtrés par école
    professeurs = get_ecole_filter_query(Professeur).filter_by(ecole_id=current_user.ecole_id).all()

    # Charger les années scolaires pour le form
    from app.models import AnneeScolaire
    annees_ecole = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).order_by(AnneeScolaire.id.desc()).all()
    form.annee_scolaire_id.choices = [(a.id, a.nom) for a in annees_ecole]

    # Pré-selection année active
    annee_active = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id, statut='active').first()
    if annee_active:
        form.annee_scolaire_id.data = annee_active.id

    if form.validate_on_submit():
        try:
            # Vérifier doublon : même nom + année + école
            existing = Classe.query.filter_by(
                nom=form.nom.data,
                annee_scolaire_id=form.annee_scolaire_id.data,
                ecole_id=current_user.ecole_id
            ).first()
            if existing:
                flash("Une classe avec ce nom existe déjà pour cette année scolaire.", "warning")
                return redirect(url_for("main.ajouter_classe"))

            classe = Classe(
                nom=form.nom.data,
                niveau=form.niveau.data,
                effectif=form.effectif.data,
                salle=form.salle.data,
                professeur_id=form.professeur_principal_id.data,
                annee_scolaire_id=form.annee_scolaire_id.data,
                ecole_id=current_user.ecole_id
            )
            db.session.add(classe)
            db.session.commit()
            flash("Classe ajoutée avec succès ✅", "success")
            return redirect(url_for("main.liste_classes"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur ajout classe : {e}")
            flash("Erreur lors de l'ajout de la classe.", "danger")
            return redirect(url_for("main.ajouter_classe"))

    return render_template("add_class.html", form=form, professeurs=professeurs, annees_ecole=annees_ecole, annee_active=annee_active)


@main.route("/classes/<int:classe_id>")
@login_required
def detail_classe(classe_id):
    # Sécurité multi-écoles : vérifier ecole_id
    classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
    
    # Professeur : accès uniquement à sa classe
    if current_user.role == 'prof' and current_user.classe_id != classe.id:
        flash("Accès non autorisé à cette classe.", "danger")
        return redirect(url_for('main.liste_classes'))

    # Récupérer inscriptions et notes avec optimisation N+1
    inscriptions = Inscription.query.options(
        joinedload(Inscription.eleve).joinedload(Eleve.notes).joinedload(Note.cours)
    ).filter_by(classe_id=classe.id).all()

    eleves_data = []
    for ins in inscriptions:
        eleve = ins.eleve
        notes_par_annee = {}
        for n in eleve.notes:
            annee = ins.annee_scolaire
            if annee not in notes_par_annee:
                notes_par_annee[annee] = []
            notes_par_annee[annee].append({
                "matiere": n.cours.nom if n.cours else "N/A",
                "valeur": n.valeur,
                "periode": n.periode
            })

        eleves_data.append({
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "classe": classe.nom,
            "parent": f"{eleve.parent.nom} {eleve.parent.prenom}" if eleve.parent else "N/A",
            "annee_premiere_ecole": getattr(eleve, 'annee_premiere_ecole', "N/A"),
            "notes_par_annee": notes_par_annee
        })

    return render_template(
        "class_detail.html",
        classe=classe,
        eleves_data=eleves_data
    )


@main.route("/sync-hors-ligne")
def sync_hors_ligne():
    return render_template("sync_hors_ligne.html")


from datetime import datetime

@main.route('/api/sync', methods=['POST'])
@login_required
def api_sync():
    """API pour synchroniser les données hors ligne"""
    try:
        # Vérifier le Content-Type
        if not request.is_json:
            return jsonify({
                'success': False, 
                'message': 'Content-Type doit être application/json'
            }), 400

        data = request.get_json()
        print(f"📥 Données reçues: {data}")
        print(f"📥 Type de données: {type(data)}")
        print(f"📥 Nombre d'éléments: {len(data) if data else 0}")
        
        if not data:
            return jsonify({
                'success': False, 
                'message': 'Aucune donnée reçue'
            }), 400

        # Vérifier que data est une liste
        if not isinstance(data, list):
            return jsonify({
                'success': False,
                'message': 'Les données doivent être un tableau'
            }), 400

        processed_count = 0
        errors = []

        for index, item in enumerate(data):
            try:
                if not isinstance(item, dict):
                    errors.append(f"Élément {index}: format invalide")
                    continue

                item_type = item.get('type')
                print(f"🔄 Traitement élément {index}: type={item_type}")

                if item_type == 'note':
                    # Vérifier les champs requis
                    required_fields = ['eleve_id', 'cours_id', 'valeur', 'date_evaluation']
                    missing = [f for f in required_fields if not item.get(f)]
                    if missing:
                        errors.append(f"Note {index}: champs manquants {missing}")
                        continue

                    # ✅ CONVERTIR LA DATE
                    date_eval = item.get('date_evaluation')
                    if isinstance(date_eval, str):
                        try:
                            # Format: '2025-11-05 05:25:04' ou '2025-11-05T05:25:04'
                            date_eval = datetime.strptime(
                                date_eval.replace('T', ' ')[:19], 
                                '%Y-%m-%d %H:%M:%S'
                            )
                        except ValueError as e:
                            errors.append(f"Note {index}: format de date invalide ({e})")
                            continue

                    # Vérifier si la note existe déjà
                    existing_note = Note.query.filter_by(
                        eleve_id=item.get('eleve_id'),
                        cours_id=item.get('cours_id'),
                        date_evaluation=date_eval,
                        type_evaluation=item.get('type_evaluation')
                    ).first()
                    
                    if not existing_note:
                        note = Note(
                            valeur=float(item.get('valeur')),
                            coefficient=float(item.get('coefficient', 1)),
                            type_evaluation=item.get('type_evaluation'),
                            periode=item.get('periode'),
                            eleve_id=int(item.get('eleve_id')),
                            cours_id=int(item.get('cours_id')),
                            date_evaluation=date_eval  # ✅ Objet datetime
                        )
                        db.session.add(note)
                        processed_count += 1
                        print(f"✅ Note ajoutée pour l'élève {item.get('eleve_id')}")
                    else:
                        print(f"⚠️ Note déjà existante pour l'élève {item.get('eleve_id')}")

                elif item_type == 'absence':
                    required_fields = ['eleve_id', 'cours_id', 'date_absence']
                    missing = [f for f in required_fields if not item.get(f)]
                    if missing:
                        errors.append(f"Absence {index}: champs manquants {missing}")
                        continue

                    # ✅ CONVERTIR LA DATE
                    date_abs = item.get('date_absence')
                    if isinstance(date_abs, str):
                        try:
                            # Format: '2025-11-05'
                            date_abs = datetime.strptime(date_abs, '%Y-%m-%d').date()
                        except ValueError as e:
                            errors.append(f"Absence {index}: format de date invalide ({e})")
                            continue

                    existing_absence = Absence.query.filter_by(
                        eleve_id=item.get('eleve_id'),
                        cours_id=item.get('cours_id'),
                        date_absence=date_abs
                    ).first()
                    
                    if not existing_absence:
                        absence = Absence(
                            date_absence=date_abs,  # ✅ Objet date
                            motif=item.get('motif'),
                            justifiee=bool(item.get('justifiee', False)),
                            eleve_id=int(item.get('eleve_id')),
                            cours_id=int(item.get('cours_id'))
                        )
                        db.session.add(absence)
                        processed_count += 1
                        print(f"✅ Absence ajoutée pour l'élève {item.get('eleve_id')}")
                    else:
                        print(f"⚠️ Absence déjà existante pour l'élève {item.get('eleve_id')}")

                elif item_type == 'paiement':
                    required_fields = ['eleve_id', 'montant', 'mois', 'annee']
                    missing = [f for f in required_fields if not item.get(f)]
                    if missing:
                        errors.append(f"Paiement {index}: champs manquants {missing}")
                        continue

                    existing_paiement = Paiement.query.filter_by(
                        eleve_id=item.get('eleve_id'),
                        mois=item.get('mois'),
                        annee=item.get('annee'),
                        reference=item.get('reference')
                    ).first()
                    
                    if not existing_paiement:
                        paiement = Paiement(
                            montant=float(item.get('montant')),
                            mois=int(item.get('mois')),
                            annee=int(item.get('annee')),
                            mode_paiement=item.get('mode_paiement'),
                            reference=item.get('reference'),
                            eleve_id=int(item.get('eleve_id'))
                        )
                        db.session.add(paiement)
                        processed_count += 1
                        print(f"✅ Paiement ajouté pour l'élève {item.get('eleve_id')}")
                    else:
                        print(f"⚠️ Paiement déjà existant pour l'élève {item.get('eleve_id')}")

                elif item_type == 'test':
                    print("🔧 Test reçu - ignoré")
                    continue
                    
                else:
                    errors.append(f"Élément {index}: type inconnu ({item_type})")
                    print(f"❌ Type inconnu: {item_type}")

            except Exception as e:
                error_msg = f"Élément {index}: {str(e)}"
                errors.append(error_msg)
                print(f"❌ Erreur sur l'élément {index}: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        # Commit seulement s'il y a des données traitées
        if processed_count > 0:
            db.session.commit()
            print(f"✅ Synchronisation terminée: {processed_count} éléments traités")

        response_data = {
            'success': True,
            'message': f'{processed_count} élément(s) synchronisé(s) avec succès',
            'processed': processed_count,
            'total': len(data)
        }
        
        if errors:
            response_data['errors'] = errors
            response_data['message'] = f'{processed_count} élément(s) synchronisé(s), {len(errors)} erreur(s)'

        return jsonify(response_data), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Erreur générale de synchronisation: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'Erreur de synchronisation: {str(e)}'
        }), 500
        
        # À ajouter dans vos routes Flask (routes.py)

from flask import send_from_directory, render_template_string

@main.route('/service-worker.js')
def service_worker():
    """Servir le Service Worker"""
    return send_from_directory('static', 'service-worker.js', mimetype='application/javascript')

@main.route('/offline')
def offline_page():
    """Page affichée quand l'utilisateur est hors ligne"""
    offline_html = """
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Hors ligne</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }
            .offline-container {
                text-align: center;
                color: white;
                padding: 2rem;
            }
            .offline-icon {
                font-size: 5rem;
                margin-bottom: 2rem;
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.1); }
            }
        </style>
    </head>
    <body>
        <div class="offline-container">
            <div class="offline-icon">
                <i class="fas fa-wifi-slash"></i>
            </div>
            <h1 class="mb-3">Vous êtes hors ligne</h1>
            <p class="lead mb-4">
                Vérifiez votre connexion Internet pour continuer.
            </p>
            <p class="text-white-50">
                Vos données seront automatiquement synchronisées dès que vous serez reconnecté.
            </p>
            <button class="btn btn-light mt-4" onclick="location.reload()">
                <i class="fas fa-sync-alt me-2"></i>
                Réessayer
            </button>
        </div>
        
        <script>
            // Recharger automatiquement quand la connexion revient
            window.addEventListener('online', function() {
                location.reload();
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(offline_html)
        
@main.route("/inscriptions")
@login_required
def voir_inscriptions():
    classe_filtre = request.args.get("classe")
    annee_filtre = request.args.get("annee")

    inscriptions_query = Inscription.query.options(
        joinedload(Inscription.eleve).joinedload(Eleve.parent),
        joinedload(Inscription.classe),
        joinedload(Inscription.eleve).joinedload(Eleve.notes).joinedload(Note.cours),
        joinedload(Inscription.annee_scolaire)
    ).join(Eleve).filter(Eleve.ecole_id == current_user.ecole_id)

    if classe_filtre:
        inscriptions_query = inscriptions_query.join(Classe).filter(Classe.nom == classe_filtre)

    if annee_filtre:
        inscriptions_query = inscriptions_query.join(AnneeScolaire).filter(AnneeScolaire.nom == annee_filtre)

    inscriptions_raw = inscriptions_query.all()

    classes_dict = {}
    classes_set = set()
    annees_set = set()

    for ins in inscriptions_raw:
        eleve = ins.eleve
        classe = ins.classe

        if not eleve or not classe:
            continue

        classes_set.add(classe.nom)

        if ins.annee_scolaire:
            annees_set.add(ins.annee_scolaire)

        # Calcul de la première année de l'élève dans l'école
        if eleve.inscriptions:
            premiere_inscription = min(
                [i for i in eleve.inscriptions if i.annee_scolaire],
                key=lambda i: i.annee_scolaire.date_debut,
                default=None
            )
            annee_premiere_ecole = premiere_inscription.annee_scolaire.nom if premiere_inscription else "N/A"
        else:
            annee_premiere_ecole = "N/A"

        ins_data = {
            "id": ins.id,
            "eleve_prenom": eleve.prenom,
            "eleve_nom": eleve.nom,
            "classe_nom": classe.nom,
            "annee_scolaire": ins.annee_scolaire.nom if ins.annee_scolaire else "N/A",
            "parent_nom": eleve.parent.nom if eleve.parent else "N/A",
            "annee_premiere_ecole": annee_premiere_ecole,
            "notes": [
                {
                    "cours_nom": note.cours.nom if note.cours else "N/A",
                    "valeur": note.valeur,
                    "periode": note.periode
                } for note in eleve.notes
            ] if eleve.notes else []
        }

        classes_dict.setdefault(classe.nom, []).append(ins_data)

    classes = sorted(classes_set)
    annees = sorted(annees_set, key=lambda a: a.date_debut)

    return render_template(
        "inscriptions.html",
        classes_dict=classes_dict,
        classes=classes,
        annees=annees,
        classe_filtre=classe_filtre,
        annee_filtre=annee_filtre
    )
@main.route("/recherche_json")
def recherche_json():
    inscription_id = request.args.get("inscription_id", type=int)
    if not inscription_id:
        return {"error": "inscription_id manquant"}, 400

    ins = Inscription.query.get(inscription_id)
    if not ins:
        return {"error": "Inscription introuvable"}, 404

    eleve = ins.eleve

    # Calculer la première année scolaire de l'élève
    annee_premiere_ecole = "N/A"
    if eleve and eleve.inscriptions:
        premiere_inscription = min(
            [i for i in eleve.inscriptions if i.annee_scolaire],
            key=lambda i: i.annee_scolaire.date_debut,
            default=None
        )
        if premiere_inscription and premiere_inscription.annee_scolaire:
            annee_premiere_ecole = premiere_inscription.annee_scolaire.nom

    return {
        "id": ins.id,
        "eleve_prenom": eleve.prenom if eleve else None,
        "eleve_nom": eleve.nom if eleve else None,
        "classe": ins.classe.nom if ins.classe else None,
        "annee_scolaire": ins.annee_scolaire.nom if ins.annee_scolaire else None,
        "parent_nom": eleve.parent.nom if eleve and eleve.parent else None,
        "annee_premiere_ecole": annee_premiere_ecole,
        "notes": [
            {
                "cours": note.cours.nom if note.cours else None,
                "valeur": note.valeur,
                "periode": note.periode
            }
            for note in eleve.notes
        ]
        if eleve and eleve.notes
        else []
    }




@main.route('/choisir-ecole')
@login_required
def choisir_ecole():
    """Afficher automatiquement les écoles accessibles et leurs journaux/problèmes"""
    
    # Pour un admin normal : récupérer son école et ses écoles gérées
    if current_user.role != "super_admin":
        ecoles = []
        if current_user.ecole:
            ecoles.append(current_user.ecole)
        if getattr(current_user, 'ecoles_gerees', None):
            ecoles.extend(current_user.ecoles_gerees)
        # éliminer doublons
        ecoles = list({e.id: e for e in ecoles}.values())
    
    # Pour super-admin : toutes les écoles
    else:
        ecoles = get_ecole_filter_query(Ecole).all()
    
    # Préparer les données de journaux et problèmes pour chaque école
    for ecole in ecoles:
        # journaux_correction et problemes doivent être des relations SQLAlchemy
        ecole.journaux_correction = getattr(ecole, 'journaux_correction', [])
        ecole.problemes = getattr(ecole, 'problemes', [])

    return render_template('choisir_ecole.html', ecoles=ecoles)


@main.route('/admin/ecoles')
@login_required
@role_required('super_admin')
def gestion_ecoles():
    """Gestion des écoles (super-admin seulement)"""
    try:
        ecoles = get_ecole_filter_query(Ecole).all()
        return render_template('admin/ecoles.html', ecoles=ecoles)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur récupération écoles : {e}")
        flash("Erreur lors de la récupération des écoles.", "danger")
        return render_template('admin/ecoles.html', ecoles=[])


@main.route('/admin/ecoles/ajouter', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def ajouter_ecole():
    """Ajouter une nouvelle école et son administrateur associé"""
    if request.method == 'POST':
        try:
            # Récupérer et valider les champs
            nom_ecole = request.form.get('nom_ecole', '').strip()
            adresse = request.form.get('adresse', '').strip()
            telephone = request.form.get('telephone', '').strip()
            email_ecole = request.form.get('email', '').strip()
            directeur = request.form.get('directeur', '').strip()
            nom_admin = request.form.get('nom_admin', '').strip()
            prenom_admin = request.form.get('prenom_admin', '').strip()
            email_admin = request.form.get('email_admin', '').strip()
            telephone_admin = request.form.get('telephone_admin', '').strip()
            mot_de_passe = request.form.get('mot_de_passe') or secrets.token_urlsafe(12)  # + sécurisé

            # Vérifier doublon email admin
            if Utilisateur.query.filter_by(email=email_admin).first():
                flash("Cet email est déjà utilisé pour un autre utilisateur.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            # --- Création de l'école ---
            ecole = Ecole(
                nom=nom_ecole,
                adresse=adresse,
                telephone=telephone,
                email=email_ecole,
                directeur=directeur,
                statut='active'
            )
            db.session.add(ecole)
            db.session.flush()  # pour obtenir ecole.id avant commit

            # --- Création de l'admin associé ---
            admin = Utilisateur(
                nom=nom_admin,
                prenom=prenom_admin,
                email=email_admin,
                telephone=telephone_admin,
                role='admin',
                mot_de_passe=generate_password_hash(mot_de_passe),
                ecole_id=ecole.id,
                statut='actif'
            )
            db.session.add(admin)
            db.session.commit()

            # --- Notifications ---
            sujet = f"Bienvenue sur EduManage - {ecole.nom}"
            message = f"""
Bonjour {admin.prenom} {admin.nom},

Votre école "{ecole.nom}" a été créée avec succès sur EduManage 🎉

Identifiants de connexion :
📧 Email : {admin.email}
🔑 Mot de passe : {mot_de_passe}

Merci d'utiliser notre plateforme !
"""
            try:
                envoyer_email(admin.email, sujet, message)
            except Exception as e:
                current_app.logger.warning(f"Échec envoi email : {e}")

            try:
                telegram_message = f"""
🏫 Nouvelle école créée : {ecole.nom}
👤 Admin : {admin.prenom} {admin.nom}
📧 {admin.email}
🔑 {mot_de_passe}
"""
                envoyer_telegram(telegram_message)
            except Exception as e:
                current_app.logger.warning(f"Échec envoi Telegram : {e}")

            flash("École et administrateur créés avec succès ✅", "success")
            return redirect(url_for('main.gestion_ecoles'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur création école+admin : {e}")
            flash("Erreur lors de la création de l'école et de son admin.", "danger")

    return render_template('admin/ajouter_ecole.html')


@main.route('/api/ecoles')
@login_required
def api_ecoles():
    """API pour récupérer les écoles accessibles"""
    try:
        if current_user.role == 'super_admin':
            ecoles = get_ecole_filter_query(Ecole).all()
        else:
            ecoles = []
            if getattr(current_user, 'ecole', None):
                ecoles.append(current_user.ecole)
            if getattr(current_user, 'ecoles_gerees', None):
                ecoles.extend(current_user.ecoles_gerees)
            # éliminer doublons
            ecoles = list({e.id: e for e in ecoles}.values())

        return jsonify([{'id': e.id, 'nom': e.nom} for e in ecoles])
    except Exception as e:
        current_app.logger.error(f"Erreur API écoles : {e}")
        return jsonify([]), 500


@main.route('/admin/ecoles/<int:ecole_id>/assigner', methods=['POST'])
@login_required
@role_required('super_admin')
def assigner_ecole(ecole_id):
    """Assigner une école à un gestionnaire"""
    try:
        utilisateur_id = int(request.form.get('utilisateur_id'))
        utilisateur = Utilisateur.query.get_or_404(utilisateur_id)
        ecole = Ecole.query.get_or_404(ecole_id)

        # Supprimer association existante si nécessaire
        db.session.execute(
            gestion_ecole.delete().where(
                (gestion_ecole.c.utilisateur_id == utilisateur_id) &
                (gestion_ecole.c.ecole_id == ecole_id)
            )
        )

        # Ajouter association
        db.session.execute(
            gestion_ecole.insert().values(
                utilisateur_id=utilisateur_id,
                ecole_id=ecole_id
            )
        )
        db.session.commit()
        flash(f"École '{ecole.nom}' assignée à {utilisateur.prenom} {utilisateur.nom}", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur assignation école : {e}")
        flash("Erreur lors de l'assignation de l'école.", "danger")
    return redirect(request.referrer or url_for('main.gestion_ecoles'))


@main.route('/admin/utilisateur/<int:user_id>/ecoles', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def gerer_ecoles_utilisateur(user_id):
    utilisateur = Utilisateur.query.get_or_404(user_id)
    toutes_ecoles = get_ecole_filter_query(Ecole).all()
    form = GererEcolesForm()

    ecoles_actuelles = [e.id for e in getattr(utilisateur, 'ecoles_gerees', [])]

    if form.validate_on_submit():
        try:
            ecoles_selectionnees = [int(ecole_id) for ecole_id in request.form.getlist('ecoles')]
            decochées = set(ecoles_actuelles) - set(ecoles_selectionnees)

            erreurs = []
            for ecole_id in decochées:
                nb_eleves = Eleve.query.filter_by(ecole_id=ecole_id).count()
                if nb_eleves > 0:
                    ecole = Ecole.query.get(ecole_id)
                    erreurs.append(f"L'école '{ecole.nom}' contient encore {nb_eleves} élèves et ne peut pas être retirée.")

            if erreurs:
                for err in erreurs:
                    flash(err, 'danger')
                return redirect(url_for('main.gerer_ecoles_utilisateur', user_id=user_id))

            # Supprimer associations existantes
            db.session.execute(gestion_ecole.delete().where(gestion_ecole.c.utilisateur_id == user_id))

            # Ajouter nouvelles associations
            for ecole_id in ecoles_selectionnees:
                db.session.execute(gestion_ecole.insert().values(
                    utilisateur_id=user_id,
                    ecole_id=ecole_id
                ))

            db.session.commit()
            flash("Écoles assignées avec succès", "success")
            return redirect(url_for('main.gestion_utilisateurs'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur gestion écoles utilisateur : {e}")
            flash(f"Erreur : {str(e)}", "danger")

    # Statistiques globales
    nb_ecoles = Ecole.query.count()
    nb_eleves = Eleve.query.count()
    nb_parents = Utilisateur.query.filter_by(role='parent').count()
    nb_enseignants = Utilisateur.query.filter_by(role='enseignant').count()
    nb_admins = Utilisateur.query.filter_by(role='admin').count()

    stats_ecoles = []
    for ecole in toutes_ecoles:
        nb_eleve = Eleve.query.filter_by(ecole_id=ecole.id).count()
        stats_ecoles.append({
            'id': ecole.id,
            'nom': ecole.nom,
            'eleves': nb_eleve
        })

    return render_template(
        "admin/gerer_ecoles_utilisateur.html",
        utilisateur=utilisateur,
        toutes_ecoles=toutes_ecoles,
        ecoles_actuelles=ecoles_actuelles,
        form=form,
        nb_ecoles=nb_ecoles,
        nb_eleves=nb_eleves,
        nb_parents=nb_parents,
        nb_enseignants=nb_enseignants,
        nb_admins=nb_admins,
        stats_ecoles=stats_ecoles
    )

@main.route('/journaux_corrections', methods=['GET'])
@login_required
def journaux_corrections():
    # Récupération des écoles et utilisateurs accessibles
    toutes_ecoles = get_ecole_filter_query(Ecole).all()
    tous_users = get_ecole_filter_query(User).all()

    # Récupération des filtres
    ecole_id = request.args.get('ecole_id', type=int)
    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action', '').strip()
    niveau = request.args.get('niveau', '').strip()
    date_debut = request.args.get('date_debut')
    date_fin = request.args.get('date_fin')

    # Construire la requête avec pré-chargement
    query = JournalCorrection.query.options(
        joinedload(JournalCorrection.ecole),
        joinedload(JournalCorrection.user)
    )

    # Filtrage multi-écoles si l'utilisateur n'est pas super_admin
    if current_user.role != 'super_admin':
        ecoles_accessibles = [e.id for e in getattr(current_user, 'ecoles_gerees', [])]
        if getattr(current_user, 'ecole', None):
            ecoles_accessibles.append(current_user.ecole.id)
        query = query.filter(JournalCorrection.ecole_id.in_(ecoles_accessibles))

    # Application des filtres
    if ecole_id:
        query = query.filter(JournalCorrection.ecole_id == ecole_id)
    if user_id:
        query = query.filter(JournalCorrection.user_id == user_id)
    if action:
        query = query.filter(JournalCorrection.action.ilike(f"%{action}%"))
    if niveau:
        query = query.filter(JournalCorrection.niveau == niveau)
    if date_debut:
        try:
            dt_start = datetime.strptime(date_debut, "%Y-%m-%d")
            query = query.filter(JournalCorrection.date >= dt_start)
        except ValueError:
            flash("Format de date de début invalide", "warning")
    if date_fin:
        try:
            dt_end = datetime.strptime(date_fin, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(JournalCorrection.date < dt_end)
        except ValueError:
            flash("Format de date de fin invalide", "warning")

    # Récupération des corrections
    corrections = query.order_by(JournalCorrection.date.desc()).all()

    return render_template(
        "journaux_corrections.html",
        corrections=corrections,
        toutes_ecoles=toutes_ecoles,
        tous_users=tous_users,
        filtre={
            "ecole_id": ecole_id,
            "user_id": user_id,
            "action": action,
            "niveau": niveau,
            "date_debut": date_debut,
            "date_fin": date_fin
        }
    )
 
    # Routes pour les actions des boutons
@main.route('/api/users/<int:user_id>/status', methods=['PUT'])
@login_required
def toggle_user_status(user_id):
    """Changer le statut d'un utilisateur"""
    if current_user.role not in ['super_admin', 'admin']:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403
        
    user = Utilisateur.query.get_or_404(user_id)
    
    # Vérifier les permissions
    if current_user.role == 'admin' and user.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403
        
    # Empêcher de se désactiver soi-même
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas modifier votre propre statut'}), 400
    
    user.statut = 'bloque' if user.statut == 'actif' else 'actif'
    db.session.commit()
    
    return jsonify({'success': True, 'new_status': user.statut})

@main.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """Supprimer un utilisateur et toutes ses dépendances (enfants + inscriptions)"""
    
    # Vérification des rôles
    if current_user.role not in ['super_admin', 'admin']:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    user = Utilisateur.query.get_or_404(user_id)

    # Empêcher un admin de supprimer un utilisateur d'une autre école
    if current_user.role == 'admin' and user.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    # Empêcher de se supprimer soi-même
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer'}), 400

    try:
        # 1️⃣ Supprimer les inscriptions des enfants
        for enfant in user.get_enfants():
            for inscription in enfant.inscriptions:
                db.session.delete(inscription)

        # 2️⃣ Supprimer les enfants
        for enfant in user.get_enfants():
            db.session.delete(enfant)

        # 3️⃣ Supprimer les relations professeur si existantes
        if user.professeur_rel:
            # Supprimer les cours enseignés par ce professeur si nécessaire
            for cours in user.cours_enseignes:
                cours.enseignant_id = None  # ou supprimer si tu veux
            db.session.delete(user.professeur_rel)

        # 4️⃣ Supprimer alertes et logs
        for alerte in user.alertes:
            db.session.delete(alerte)
        for log in user.logs:
            db.session.delete(log)

        # 5️⃣ Supprimer l’utilisateur
        db.session.delete(user)

        db.session.commit()
        return jsonify({'success': True, 'message': 'Utilisateur supprimé avec toutes ses dépendances.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur lors de la suppression: {str(e)}'}), 500

    
    # Route pour supprimer un professeur
@main.route('/professeur/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_professeur_route(id):
    """Supprimer un professeur"""
    professeur = Professeur.query.get_or_404(id)
    
    # Vérifier s'il y a des cours associés
    if professeur.cours:
        flash("Impossible de supprimer ce professeur car il a des cours associés.", "danger")
        return redirect(url_for('main.profile'))
    
    # Supprimer aussi l'utilisateur associé si existe
    if professeur.utilisateur_id:
        utilisateur = Utilisateur.query.get(professeur.utilisateur_id)
        if utilisateur:
            db.session.delete(utilisateur)
    
    db.session.delete(professeur)
    db.session.commit()
    flash("Professeur supprimé avec succès.", "success")
    return redirect(url_for('main.profile'))

# Route pour supprimer un élève
@main.route('/eleve/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_eleve_route(id):
    """Supprimer un élève"""
    eleve = Eleve.query.get_or_404(id)
    
    # Vérifier s'il y a des données liées
    if eleve.notes or eleve.paiements or eleve.absences:
        flash("Impossible de supprimer cet élève car il a des données associées.", "danger")
        return redirect(url_for('main.profile'))
    
    db.session.delete(eleve)
    db.session.commit()
    flash("Élève supprimé avec succès.", "success")
    return redirect(url_for('main.profile'))

# Route pour le statut des écoles (corrigée)
@main.route('/api/ecoles/<int:ecole_id>/status', methods=['PUT'])
@login_required
@role_required('super_admin')
def toggle_ecole_status(ecole_id):
    """Changer le statut d'une école"""
    if current_user.role != 'super_admin':
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403
        
    ecole = Ecole.query.get_or_404(ecole_id)
    ecole.statut = 'inactive' if ecole.statut == 'active' else 'active'
    db.session.commit()
    
    return jsonify({'success': True, 'new_status': ecole.statut})
   
    # Route pour supprimer une école
@main.route('/api/ecoles/<int:ecole_id>', methods=['DELETE'])
@login_required
@role_required('super_admin')
def supprimer_ecole(ecole_id):
    """Supprimer une école et tous ses utilisateurs associés"""
    ecole = Ecole.query.get_or_404(ecole_id)
    
    try:
        # Supprimer tous les utilisateurs liés
        for user in ecole.utilisateurs:
            # Supprimer les enfants et inscriptions
            for enfant in user.get_enfants():
                for inscription in enfant.inscriptions:
                    db.session.delete(inscription)
                db.session.delete(enfant)
            
            # Supprimer les cours si c'est un professeur
            if user.professeur_rel:
                for cours in user.cours_enseignes:
                    cours.enseignant_id = None
                db.session.delete(user.professeur_rel)
            
            # Supprimer alertes et logs
            for alerte in user.alertes:
                db.session.delete(alerte)
            for log in user.logs:
                db.session.delete(log)
            
            db.session.delete(user)
        
        # Supprimer l'école
        db.session.delete(ecole)
        db.session.commit()
        return jsonify({'success': True, 'message': 'École et utilisateurs supprimés avec succès.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur lors de la suppression: {str(e)}'}), 500


# Route pour supprimer un élève
@main.route('/api/eleves/<int:eleve_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def supprimer_eleve_api(eleve_id):
    """Supprimer un élève via API"""
    eleve = Eleve.query.get_or_404(eleve_id)
    
    # Vérifier que l'élève appartient à l'école de l'admin
    if current_user.role == 'admin' and eleve.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403
    
    # Vérifier s'il y a des données liées
    if eleve.notes or eleve.paiements or eleve.absences:
        return jsonify({
            'success': False, 
            'message': 'Impossible de supprimer cet élève car il a des données associées'
        }), 400
    
    db.session.delete(eleve)
    db.session.commit()
    
    return jsonify({'success': True})





@main.route('/annees', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def gestion_annees():
    csrf_form = CSRFForm()

    # 🔹 Récupération des écoles accessibles
    if current_user.role == 'super_admin':
        ecoles = get_ecole_filter_query(Ecole).all()
        # Super admin peut tout voir
        annees = AnneeScolaire.query.options(
            db.selectinload(AnneeScolaire.ecole)
        ).order_by(AnneeScolaire.date_debut.desc()).all()
    else:
        # Si un admin gère une seule école
        ecoles = [current_user.ecole]
        # ⚠️ Si un admin gère plusieurs écoles, décommente :
        # ecoles = current_user.ecoles_gerees
        ecole_ids = [e.id for e in ecoles]

        annees = AnneeScolaire.query.options(
            db.selectinload(AnneeScolaire.ecole)
        ).filter(AnneeScolaire.ecole_id.in_(ecole_ids)).order_by(
            AnneeScolaire.date_debut.desc()
        ).all()

    if request.method == 'POST' and csrf_form.validate_on_submit():
        action = request.form.get('action')
        annee_id = request.form.get('annee_id')

        if action == 'activer' and annee_id:
            annee = AnneeScolaire.query.get(int(annee_id))
            if annee and annee.ecole_id in [e.id for e in ecoles]:
                # Désactiver uniquement les années de la même école
                AnneeScolaire.query.filter_by(ecole_id=annee.ecole_id).update({'statut': 'archivee'})
                annee.statut = 'active'
                db.session.commit()
                flash(f"L'année {annee.nom} est maintenant active.", "success")
            else:
                flash("Action non autorisée pour cette école.", "danger")

        elif action == 'ajouter':
            nom = request.form.get('nom')
            date_debut_str = request.form.get('date_debut')
            date_fin_str = request.form.get('date_fin')
            ecole_id = request.form.get('ecole_id')

            try:
                # ✅ Sécurisation : si l'admin n’a qu’une seule école, on force automatiquement
                if not ecole_id and len(ecoles) == 1:
                    ecole_id = ecoles[0].id

                if not (nom and date_debut_str and date_fin_str and ecole_id):
                    flash("Tous les champs sont obligatoires.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                ecole_id = int(ecole_id)
                if ecole_id not in [e.id for e in ecoles]:
                    flash("Vous ne pouvez pas créer une année pour cette école.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                date_debut = datetime.strptime(date_debut_str, "%Y-%m-%d").date()
                date_fin = datetime.strptime(date_fin_str, "%Y-%m-%d").date()

                nouvelle_annee = AnneeScolaire(
                    nom=nom,
                    date_debut=date_debut,
                    date_fin=date_fin,
                    statut='planifiee',
                    ecole_id=ecole_id  # ✅ Jamais None
                )
                db.session.add(nouvelle_annee)
                db.session.commit()
                flash(f"Nouvelle année {nom} ajoutée.", "success")

            except Exception as e:
                db.session.rollback()
                flash(f"Erreur lors de l'ajout: {str(e)}", "danger")

        return redirect(url_for('main.gestion_annees'))

    return render_template('gestion_annees.html', annees=annees, ecoles=ecoles, csrf_form=csrf_form)


@main.route('/changer_annee/<int:annee_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def changer_annee(annee_id):
    annee = AnneeScolaire.query.get_or_404(annee_id)
    try:
        # Désactiver toutes les années de la même école
        AnneeScolaire.query.filter_by(ecole_id=annee.ecole_id).update({'statut': 'archivee'})
        # Activer l'année sélectionnée
        annee.statut = 'active'
        db.session.commit()
        flash(f"L'année {annee.nom} est maintenant active.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de l'activation: {str(e)}", "danger")

    return redirect(request.referrer or url_for('main.gestion_annees'))


@main.route('/get_classes/<int:annee_id>')
@login_required
@role_required('admin', 'enseignant', 'super_admin')
def get_classes(annee_id):
    from app.models import Classe

    if current_user.role == 'super_admin':
        classes = Classe.query.filter_by(annee_scolaire_id=annee_id).order_by(Classe.nom).all()
    else:
        classes = Classe.query.filter(
            Classe.ecole_id == current_user.ecole_id,
            Classe.annee_scolaire_id == annee_id
        ).order_by(Classe.nom).all()

    classes_list = [{'id': c.id, 'nom': c.nom or c.nom_complet} for c in classes]

    return jsonify({'classes': classes_list})


@main.route('/api/classes/annee/<int:annee_id>')
@login_required
@role_required('admin', 'super_admin')
def api_classes_par_annee(annee_id):
    """API pour récupérer les classes d'une année scolaire spécifique"""
    from app.models import Classe

    if current_user.role == 'super_admin':
        classes = Classe.query.filter_by(annee_scolaire_id=annee_id).order_by(Classe.nom).all()
    else:
        classes = Classe.query.filter(
            Classe.ecole_id == current_user.ecole_id,
            Classe.annee_scolaire_id == annee_id
        ).order_by(Classe.nom).all()
    
    classes_list = [{
        'id': c.id, 
        'nom': c.nom_complet,
        'niveau': c.niveau,
        'effectif': c.effectif_reel
    } for c in classes]
    
    return jsonify(classes_list)


from datetime import datetime, date

@main.route("/presence", methods=["GET", "POST"])
@login_required
@role_required("enseignant")
def presence():

    prof = current_user.professeur_rel
    if not prof:
        flash("Aucun profil professeur trouvé.", "danger")
        return redirect(url_for("main.index"))

    classes = prof.classes_assignees

    # -----------------------------
    # 1) Sélection classe
    # -----------------------------
    classe_id = request.values.get("classe_id", type=int)
    selected_classe = None
    eleves = []

    if classe_id:
        selected_classe = next((c for c in classes if c.id == classe_id), None)
        if selected_classe:
            eleves = selected_classe.eleves

    # -----------------------------
    # 2) Sélection date
    # -----------------------------
    date_str = request.values.get("date") or date.today().isoformat()

    try:
        date_selectionnee = datetime.strptime(date_str, "%Y-%m-%d").date()
    except:
        date_selectionnee = date.today()

    heure_selectionnee = request.form.get("heure_presence", "")
    matiere = request.form.get("matiere", "")

    # -----------------------------
    # 3) Présences existantes
    # -----------------------------
    presences_existantes = {}
    if eleves:
        for e in eleves:
            p = Presence.query.filter_by(
                eleve_id=e.id,
                date=date_selectionnee
            ).first()
            presences_existantes[e.id] = p.statut if p else None

    # -----------------------------
    # 4) POST : Enregistrement
    # -----------------------------
    if request.method == "POST" and eleves:

        date_p_str = request.form.get("date_presence")
        heure_p_str = request.form.get("heure_presence")
        matiere = request.form.get("matiere")

        # Conversion obligatoire
        try:
            date_p = datetime.strptime(date_p_str, "%Y-%m-%d").date()
        except:
            flash("Date invalide.", "danger")
            return redirect(request.url)

        # L'heure reste en string (pas besoin time())
        heure_p = heure_p_str  

        with db.session.no_autoflush:       # 🔥 corrige l'erreur SQLite
            for eleve in eleves:
                statut = request.form.get(f"eleve_{eleve.id}")
                if not statut:
                    continue

                ligne = Presence.query.filter_by(
                    eleve_id=eleve.id,
                    date=date_p
                ).first()

                if not ligne:
                    ligne = Presence(
                        eleve_id=eleve.id,
                        date=date_p,
                        heure=heure_p,
                        matiere=matiere,
                        statut=statut
                    )
                    db.session.add(ligne)
                else:
                    ligne.statut = statut
                    ligne.matiere = matiere
                    ligne.heure = heure_p

        db.session.commit()
        flash("Présences enregistrées.", "success")

        return redirect(url_for("main.presence",
                                classe_id=classe_id,
                                date=date_p_str))

    # -----------------------------
    # 5) Historique du jour
    # -----------------------------
    historique = []
    if classe_id:
        historique = Presence.query.join(Eleve)\
            .filter(Eleve.classe_id == classe_id)\
            .filter(Presence.date == date_selectionnee)\
            .all()

    # -----------------------------
    # 6) Render
    # -----------------------------
    return render_template(
        "presence.html",
        classes=classes,
        classe_id=classe_id,
        eleves=eleves,
        presences_existantes=presences_existantes,
        date_selectionnee=date_selectionnee.isoformat(),
        heure_selectionnee=heure_selectionnee,
        matiere=matiere,
        historique=historique
    )


    

@main.route('/professeur/<int:id>/assigner_classes', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def assigner_classes_professeur(id):
    professeur = Professeur.query.get_or_404(id)

    # ✅ Vérification multi-école
    if professeur.ecole_id != current_user.ecole_id:
        flash("Accès refusé : ce professeur appartient à une autre école", "danger")
        return redirect(url_for("main.professeurs"))

    form = AssignerClassesForm()

    # Classes de l'école et année scolaire active
    classes_ecole = Classe.query.join(AnneeScolaire).filter(
        AnneeScolaire.ecole_id == current_user.ecole_id,
        AnneeScolaire.statut == "active"
    ).all()

    form.classes.choices = [(c.id, f"{c.nom} - {c.niveau}") for c in classes_ecole]

    if form.validate_on_submit():
        try:
            # Supprimer anciennes assignations
            db.session.execute(
                professeur_classes.delete().where(
                    professeur_classes.c.professeur_id == professeur.id
                )
            )

            # Ajouter nouvelles classes
            for classe_id in form.classes.data:
                classe = Classe.query.get(classe_id)
                if classe and classe.ecole_id == current_user.ecole_id:  # ✅ Sécurité en plus
                    db.session.execute(
                        professeur_classes.insert().values(
                            professeur_id=professeur.id,
                            classe_id=classe_id,
                            ecole_id=classe.ecole_id,
                            date_assignation=datetime.utcnow()
                        )
                    )

            db.session.commit()
            db.session.refresh(professeur)

            # Journalisation
            current_app.log_correction(
                action="modification",
                description=f"Assignation classes pour {professeur.prenom} {professeur.nom}",
                ecole_id=professeur.ecole_id,
                cible_type="professeur",
                cible_id=professeur.id,
                ancienne_valeur=None,
                nouvelle_valeur=f"Classes: {[c.nom for c in professeur.classes_assignees]}",
                niveau="info"
            )

            flash(f"Classes assignées avec succès à {professeur.prenom} {professeur.nom}", "success")
            return redirect(url_for('main.professeur_details', id=professeur.id))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'assignation des classes", "danger")
            current_app.logger.error(f"Erreur assignation classes: {e}")

    form.classes.data = [c.id for c in professeur.classes_assignees.all()]

    return render_template(
        'assigner_classes.html',
        form=form,
        professeur=professeur,
        classes_ecole=classes_ecole
    )

    
    
    
@main.route("/mes_classes")
@login_required
@role_required('enseignant', 'professeur')  # seulement pour consultation
def mes_classes():
    # Récupération de l'objet Professeur lié à l'utilisateur
    prof = current_user.professeur_rel
    if not prof:
        flash("Aucune information de professeur trouvée.", "warning")
        return redirect(url_for('main.index'))

    # Récupérer uniquement les classes assignées au professeur
    try:
        classes = prof.classes_assignees.all()  # si lazy='dynamic'
    except AttributeError:
        classes = prof.classes_assignees  # si lazy='select'

    return render_template("mes_classes.html", classes=classes)




@main.route('/toggle_periode/<int:id>')
@login_required
@role_required('admin')
def toggle_periode(id):
    periode = PeriodeBulletin.query.get_or_404(id)
    periode.publie = not periode.publie  # on inverse l’état
    if periode.publie:
        periode.date_publication = datetime.utcnow()
    db.session.commit()
    flash(f"Période {periode.nom} {'activée' if periode.publie else 'désactivée'} avec succès.", "success")
    return redirect(url_for('main.gestion_periodes'))

@main.route('/periodes')
@login_required
@role_required('admin')
def gestion_periodes():
    periodes = get_ecole_filter_query(PeriodeBulletin).all()
    return render_template("gestion_periodes.html", periodes=periodes)



@main.route('/activer_periode/<int:id>')
@login_required
@role_required('admin')
def activer_periode(id):
    """Rendre une période active (une seule période active à la fois)"""
    # Désactiver toutes les périodes
    PeriodeBulletin.query.filter_by(ecole_id=current_user.ecole_id).update({'periode_active': False})
    
    # Activer la période sélectionnée
    periode = PeriodeBulletin.query.get_or_404(id)
    periode.periode_active = True
    periode.publie = True  # S'assurer qu'elle est publiée
    periode.date_publication = datetime.utcnow()
    
    db.session.commit()
    flash(f"Période {periode.nom} activée avec succès. Les parents peuvent maintenant accéder aux bulletins.", "success")
    return redirect(url_for('main.gestion_periodes'))




@main.route('/creer_periode', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def creer_periode():
    """Créer une nouvelle période de bulletin"""
    form = PeriodeForm()
    
    # Remplir les choix de l'année scolaire
    form.annee_id.choices = [(a.id, a.nom) for a in AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).all()]
    
    if form.validate_on_submit():
        nom = form.nom.data
        annee_id = form.annee_id.data
        
        # Créer la période
        nouvelle_periode = PeriodeBulletin(
            nom=nom,
            annee_id=annee_id,
            ecole_id=current_user.ecole_id,
            publie=False,
            periode_active=False
        )
        
        db.session.add(nouvelle_periode)
        db.session.commit()
        
        flash(f"Période '{nom}' créée avec succès.", "success")
        return redirect(url_for('main.gestion_periodes'))
    
    # GET - Afficher le formulaire
    return render_template('creer_periode.html', form=form)




