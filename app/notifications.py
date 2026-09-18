import logging
from flask_mail import Message
from app.extensions import mail

# app/notifications.py

# Configurer le logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================
# Email Plateforme (SMTP)
# ========================
def envoyer_email(to, sujet, corps, reply_to=None):
    """Envoie un email technique via l'instance Flask-Mail configurée (utilise MAIL_DEFAULT_SENDER)."""
    try:
        msg = Message(subject=sujet, recipients=[to])
        msg.html = corps
        if reply_to:
            msg.reply_to = reply_to

        mail.send(msg)
        logger.info(f"Email technique envoyé à {to}")
        return True
    except Exception as e:
        logger.error(f"Erreur d'envoi Email technique: {e}")
        return False


def envoyer_email_ecole(ecole_id, to, sujet, corps):
    """
    Envoie un email au nom de l'école via son compte Gmail connecté (OAuth 2.0).
    Lève SchoolMailNotConfiguredError si le Gmail de l'établissement n'est pas connecté.
    """
    from app.services.google_mail import send_school_email
    return send_school_email(ecole_id, to, sujet, corps)
