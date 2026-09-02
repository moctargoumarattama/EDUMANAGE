# Shared imports and compatibility helpers for the main route package.
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import secrets
import shutil
import smtplib
import sqlite3
import string
import sys
import tempfile
import threading
import uuid
import zipfile
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache, wraps
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import URLSafeTimedSerializer
from markupsafe import escape
from sqlalchemy import func, literal, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, joinedload, selectinload
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.authorization import (
    check_parent_access,
    parent_access_required,
    role_required,
)
from app.forms import (
    AbsenceForm,
    AjouterEmploiForm,
    AssignerClassesForm,
    CSRFForm,
    ChoisirEcoleForm,
    ClasseForm,
    CoursForm,
    CreateUserForm,
    DeleteForm,
    EcoleForm,
    EleveForm,
    GererEcolesForm,
    LoginForm,
    NoteForm,
    PaiementForm,
    ParentLoginForm,
    PeriodeForm,
    ProfesseurForm,
    ResetPasswordForm,
)
from app.middleware import (
    ajouter_ecole_id,
    ecole_required,
    filtre_par_ecole,
    get_ecole_courante,
    require_ecole,
)
from app.models import (
    Absence,
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Ecole,
    Eleve,
    EmploiTemps,
    HistoriqueImport,
    Inscription,
    JournalCorrection,
    Note,
    Paiement,
    PeriodeBulletin,
    Presence,
    Professeur,
    Utilisateur,
    gestion_ecole,
    professeur_classes,
)
from app.models import Professeur as User
from app.notifications import (
    TELEGRAM_CHAT_ID,
    envoyer_email,
    envoyer_telegram,
    envoyer_telegram_image,
)
from app.utils import (
    allowed_file,
    bulletins_accessible_pour_parent,
    get_ecole_filter_query,
    log_action,
    validate_sort_param,
)


bcrypt = Bcrypt()

# In the former app/routes.py this pointed to the project root.
sys.path.append(str(Path(__file__).resolve().parents[2]))

try:
    SMTP_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("MAIL_PORT", 587))
    EMAIL_ADDRESS = os.environ["MAIL_USERNAME"]
    EMAIL_PASSWORD = os.environ["MAIL_PASSWORD"]
except KeyError as e:
    raise RuntimeError(
        f"⚠️ Variable d'environnement manquante : {e.args[0]} "
        "(nécessaire pour l'envoi d'e-mails)"
    )


def envoyer_email_smtp(destinataire, sujet, message):
    """Fonction historique d'envoi SMTP, conservée pour compatibilité interne."""
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = destinataire
        msg["Subject"] = sujet
        msg.attach(MIMEText(message, "plain"))

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
    """Fonction pour envoyer des SMS (simulée, à implémenter avec un service SMS)."""
    current_app.logger.info(f"📱 SMS simulé à {numero}: {message}")
    return True


try:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://",
    )
    if current_app:
        limiter.init_app(current_app)
except Exception:
    class DummyLimiter:
        def limit(self, *_args, **_kwargs):
            def decorator(f):
                return f
            return decorator

    limiter = DummyLimiter()
