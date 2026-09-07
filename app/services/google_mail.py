"""
Service d'intégration Gmail par École pour KLASORA
OAuth 2.0 (Gmail API REST) avec chiffrement Fernet des tokens au repos.

RÈGLE ARCHITECTURALE FONDAMENTALE :
- Ce service gère les e-mails émis par les ÉCOLES (absences, bulletins, notifications parents, paiements).
- Les e-mails de la PLATEFORME (création d'école, de compte, réinitialisation de mot de passe)
  sont strictement isolés et gérés par send_platform_email via l'adresse globale plateforme.
- Si le Gmail d'une école n'est pas connecté, aucune bascule silencieuse vers la plateforme n'est effectuée :
  une exception explicite SchoolMailNotConfiguredError est levée.
"""

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Tuple
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet
from flask import current_app, url_for

from app import db
from app.models import Ecole, EcoleGoogleMailConfig

logger = logging.getLogger(__name__)

# Endpoints Google OAuth 2.0 & Gmail API
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# Scope minimal pour l'envoi d'emails et l'identification de l'adresse
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
]


class SchoolMailError(Exception):
    """Exception de base pour les erreurs liées à la messagerie école."""
    pass


class SchoolMailNotConfiguredError(SchoolMailError):
    """Levée quand l'école n'a pas connecté son compte Gmail."""
    pass


class SchoolMailSendError(SchoolMailError):
    """Levée en cas d'erreur lors de l'envoi d'un e-mail via l'API Gmail."""
    pass


class GoogleOAuthError(SchoolMailError):
    """Levée lors d'un échec de négociation OAuth 2.0 avec Google."""
    pass


# -----------------------------------------------------------------------------
# Chiffrement / Déchiffrement des Tokens au repos (Fernet)
# -----------------------------------------------------------------------------
def get_fernet_cipher() -> Fernet:
    """
    Retourne l'instance Fernet pour chiffrer/déchiffrer les tokens.
    Utilise MAIL_TOKEN_ENCRYPTION_KEY si disponible.
    À défaut, dérive une clé sécurisée 32 octets base64 depuis SECRET_KEY.
    """
    key = None
    if current_app:
        key = current_app.config.get("MAIL_TOKEN_ENCRYPTION_KEY")
    if not key:
        key = os.environ.get("MAIL_TOKEN_ENCRYPTION_KEY")

    if key:
        try:
            if isinstance(key, str):
                key = key.strip().encode("utf-8")
            return Fernet(key)
        except Exception as e:
            logger.warning(f"Clé MAIL_TOKEN_ENCRYPTION_KEY invalide ({e}), repli sur dérivation SECRET_KEY.")

    secret = "klasora-default-secret-salt"
    if current_app and current_app.config.get("SECRET_KEY"):
        secret = current_app.config["SECRET_KEY"]
    elif os.environ.get("SECRET_KEY"):
        secret = os.environ["SECRET_KEY"]

    derived = hashlib.sha256(secret.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt_token(raw_token: Optional[str]) -> Optional[str]:
    """Chiffre une chaîne de token en base64 via Fernet."""
    if not raw_token:
        return None
    cipher = get_fernet_cipher()
    return cipher.encrypt(raw_token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: Optional[str]) -> Optional[str]:
    """Déchiffre un token chiffré."""
    if not encrypted_token:
        return None
    cipher = get_fernet_cipher()
    try:
        return cipher.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"Erreur de déchiffrement du token: {e}")
        return None


# -----------------------------------------------------------------------------
# Configuration Google OAuth
# -----------------------------------------------------------------------------
def get_google_client_id() -> str:
    if current_app and current_app.config.get("GOOGLE_CLIENT_ID"):
        return current_app.config["GOOGLE_CLIENT_ID"]
    return os.environ.get("GOOGLE_CLIENT_ID", "")


def get_google_client_secret() -> str:
    if current_app and current_app.config.get("GOOGLE_CLIENT_SECRET"):
        return current_app.config["GOOGLE_CLIENT_SECRET"]
    return os.environ.get("GOOGLE_CLIENT_SECRET", "")


def get_google_redirect_uri() -> str:
    if current_app and current_app.config.get("GOOGLE_REDIRECT_URI"):
        return current_app.config["GOOGLE_REDIRECT_URI"]
    configured = os.environ.get("GOOGLE_REDIRECT_URI")
    if configured:
        return configured
    try:
        return url_for("main.google_mail_callback", _external=True)
    except Exception:
        return "http://localhost:5000/google/mail/callback"


def is_google_mail_configured_on_server() -> bool:
    """Vérifie si les identifiants OAuth Google client sont renseignés sur le serveur."""
    return bool(get_google_client_id() and get_google_client_secret())


def get_google_auth_url(state: str) -> str:
    """
    Génère l'URL de redirection vers l'écran de consentement Google.
    - prompt=consent : garantit l'obtention d'un refresh_token
    - access_type=offline : permet l'accès en arrière-plan sans session active
    - scope : gmail.send + openid + email
    """
    client_id = get_google_client_id()
    if not client_id:
        raise GoogleOAuthError("GOOGLE_CLIENT_ID non configuré sur le serveur KLASORA.")

    params = {
        "client_id": client_id,
        "redirect_uri": get_google_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Échange le code d'autorisation contre les access_token et refresh_token."""
    client_id = get_google_client_id()
    client_secret = get_google_client_secret()
    redirect_uri = get_google_redirect_uri()

    if not client_id or not client_secret:
        raise GoogleOAuthError("Identifiants Google OAuth manquants sur le serveur.")

    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        resp = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Erreur échange code Google: {resp.status_code} - {resp.text}")
            raise GoogleOAuthError(f"Google a refusé l'autorisation (code {resp.status_code}).")
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"Échec de connexion aux serveurs Google OAuth: {e}")
        raise GoogleOAuthError("Impossible de joindre les serveurs d'authentification Google.")


def fetch_google_user_email(access_token: str) -> str:
    """Récupère l'adresse e-mail du compte Google connecté."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        resp = requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Erreur récupération email Google: {resp.status_code} - {resp.text}")
            raise GoogleOAuthError("Impossible de récupérer l'adresse Gmail connectée.")
        data = resp.json()
        email = data.get("email")
        if not email:
            raise GoogleOAuthError("Aucune adresse e-mail retournée par Google.")
        return email
    except requests.RequestException as e:
        logger.error(f"Erreur requête userinfo Google: {e}")
        raise GoogleOAuthError("Impossible de contacter Google pour identifier le compte.")


# -----------------------------------------------------------------------------
# Gestion et Rafraîchissement des Tokens
# -----------------------------------------------------------------------------
def connect_school_gmail(ecole_id: int, code: str) -> str:
    """
    Exécute l'échange de code, récupère l'e-mail, chiffre les tokens et
    met à jour ou crée la configuration EcoleGoogleMailConfig.
    Retourne l'adresse email connectée.
    """
    tokens = exchange_code_for_tokens(code)
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 3600)

    if not access_token:
        raise GoogleOAuthError("Google n'a pas renvoyé de jeton d'accès.")

    email = fetch_google_user_email(access_token)

    # Récupération ou initialisation de la configuration de l'école
    config = EcoleGoogleMailConfig.query.filter_by(ecole_id=ecole_id).first()
    if not config:
        config = EcoleGoogleMailConfig(ecole_id=ecole_id)
        db.session.add(config)

    config.google_email = email
    config.google_access_token_encrypted = encrypt_token(access_token)
    if refresh_token:
        config.google_refresh_token_encrypted = encrypt_token(refresh_token)

    config.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
    config.is_connected = True
    config.connected_at = datetime.utcnow()
    config.updated_at = datetime.utcnow()

    db.session.commit()
    logger.info(f"Gmail connecté avec succès pour l'école {ecole_id} : {email}")
    return email


def get_valid_access_token(ecole_id: int) -> Tuple[str, str]:
    """
    Retourne (access_token_déchiffré, google_email) valide pour une école.
    Rafraîchit automatiquement le jeton si expiré ou proche de l'expiration.
    Lève SchoolMailNotConfiguredError si le compte n'est pas connecté.
    """
    config = EcoleGoogleMailConfig.query.filter_by(ecole_id=ecole_id).first()
    if not config or not config.is_connected or not config.google_email:
        raise SchoolMailNotConfiguredError(
            "Le compte Gmail de votre établissement n'est pas encore connecté. "
            "Veuillez vous rendre dans Paramètres > E-mail de l'établissement."
        )

    now = datetime.utcnow()
    # Si le jeton d'accès est présent et expire dans plus de 5 minutes
    if (
        config.google_access_token_encrypted
        and config.token_expiry
        and config.token_expiry > (now + timedelta(minutes=5))
    ):
        raw_token = decrypt_token(config.google_access_token_encrypted)
        if raw_token:
            return raw_token, config.google_email

    # Le token est expiré ou absent : rafraîchissement avec le refresh token
    if not config.google_refresh_token_encrypted:
        config.is_connected = False
        db.session.commit()
        raise SchoolMailNotConfiguredError(
            "La session Gmail de l'établissement a expiré. "
            "Veuillez reconnecter votre compte Gmail depuis les Paramètres."
        )

    refresh_token = decrypt_token(config.google_refresh_token_encrypted)
    if not refresh_token:
        config.is_connected = False
        db.session.commit()
        raise SchoolMailNotConfiguredError(
            "Impossible de déchiffrer le jeton de rafraîchissement Gmail. "
            "Veuillez reconnecter votre compte Gmail."
        )

    client_id = get_google_client_id()
    client_secret = get_google_client_secret()
    if not client_id or not client_secret:
        raise GoogleOAuthError("Identifiants Google OAuth manquants sur le serveur KLASORA.")

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        resp = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Échec rafraîchissement token Google école {ecole_id}: {resp.status_code} - {resp.text}")
            config.is_connected = False
            db.session.commit()
            raise SchoolMailNotConfiguredError(
                "L'autorisation Google a été révoquée ou a expiré. "
                "Veuillez reconnecter votre compte Gmail."
            )

        new_tokens = resp.json()
        new_access_token = new_tokens.get("access_token")
        expires_in = new_tokens.get("expires_in", 3600)

        if not new_access_token:
            raise SchoolMailSendError("Réponse de renouvellement Google invalide.")

        # Sauvegarder les nouveaux jetons
        config.google_access_token_encrypted = encrypt_token(new_access_token)
        config.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
        if new_tokens.get("refresh_token"):
            config.google_refresh_token_encrypted = encrypt_token(new_tokens["refresh_token"])

        config.updated_at = datetime.utcnow()
        db.session.commit()

        return new_access_token, config.google_email

    except requests.RequestException as e:
        logger.error(f"Erreur réseau lors du renouvellement token Google: {e}")
        raise SchoolMailSendError("Impossible de contacter Google pour renouveler l'accès e-mail.")


def revoke_and_disconnect_school_gmail(ecole_id: int) -> bool:
    """
    Révoque l'autorisation auprès de Google et supprime les tokens en base.
    Passe is_connected à False.
    """
    config = EcoleGoogleMailConfig.query.filter_by(ecole_id=ecole_id).first()
    if not config:
        return True

    refresh_token = decrypt_token(config.google_refresh_token_encrypted)
    if refresh_token:
        try:
            requests.post(GOOGLE_REVOKE_URL, params={"token": refresh_token}, timeout=5)
        except Exception as e:
            logger.warning(f"Erreur lors de la révocation Google token école {ecole_id}: {e}")

    config.is_connected = False
    config.google_email = None
    config.google_access_token_encrypted = None
    config.google_refresh_token_encrypted = None
    config.token_expiry = None
    config.updated_at = datetime.utcnow()
    db.session.commit()
    logger.info(f"Gmail déconnecté pour l'école {ecole_id}")
    return True


def get_school_mail_status(ecole_id: int) -> dict:
    """Retourne l'état de configuration Gmail pour une école."""
    config = EcoleGoogleMailConfig.query.filter_by(ecole_id=ecole_id).first()
    server_ready = is_google_mail_configured_on_server()

    if not config or not config.is_connected:
        return {
            "is_connected": False,
            "email": None,
            "connected_at": None,
            "server_ready": server_ready,
        }

    return {
        "is_connected": True,
        "email": config.google_email,
        "connected_at": config.connected_at,
        "server_ready": server_ready,
    }


# -----------------------------------------------------------------------------
# Envoi d'e-mails (École via Gmail REST API & Plateforme via SMTP)
# -----------------------------------------------------------------------------
def send_school_email(
    ecole_id: int,
    to: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None
) -> bool:
    """
    Envoie un e-mail au nom de l'école via son propre compte Gmail connecté.
    Utilise l'API REST Gmail (users.messages.send) avec encodage base64url du RFC 2822.
    """
    access_token, sender_email = get_valid_access_token(ecole_id)
    ecole = Ecole.query.get(ecole_id)
    school_name = ecole.nom if ecole else "Établissement scolaire"

    # Construction du message MIME
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{school_name} <{sender_email}>"
    msg["To"] = to

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"raw": raw_message}

    try:
        resp = requests.post(GMAIL_SEND_URL, headers=headers, json=payload, timeout=15)
        if resp.status_code not in (200, 201):
            logger.error(f"Échec envoi Gmail API (école {ecole_id}): {resp.status_code} - {resp.text}")
            raise SchoolMailSendError(
                f"Google n'a pas pu envoyer le message (Erreur {resp.status_code})."
            )
        logger.info(f"Email envoyé avec succès via Gmail de l'école {ecole_id} à {to}")
        return True
    except requests.RequestException as e:
        logger.error(f"Erreur réseau lors de l'envoi Gmail API (école {ecole_id}): {e}")
        raise SchoolMailSendError("Impossible de contacter le service Gmail pour envoyer le message.")


def send_platform_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None
) -> bool:
    """
    Envoie un e-mail système pour la plateforme KLASORA (création école, mot de passe oublié).
    Utilise le serveur SMTP configuré globalement.
    """
    from app.notifications import envoyer_email
    return envoyer_email(to, subject, html_body)

