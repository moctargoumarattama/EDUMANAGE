import logging
import re

from flask_mail import Message

from app.extensions import mail


logger = logging.getLogger(__name__)


def _html_to_text(html):
    text = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def envoyer_email(to, sujet, corps, reply_to=None, context=None):
    """Envoie un email technique via Flask-Mail et retourne un booléen."""
    email_context = context or "platform_email"
    recipient = (to or "").strip() if isinstance(to, str) else ""
    subject = (sujet or "").strip() if isinstance(sujet, str) else ""

    if not recipient:
        logger.warning(
            "EMAIL_FAILED type=%s recipient=empty subject=%s error=empty_recipient",
            email_context,
            subject,
        )
        return False

    try:
        msg = Message(subject=subject, recipients=[recipient])
        msg.html = corps
        msg.body = _html_to_text(corps) or subject
        if reply_to:
            msg.reply_to = reply_to

        mail.send(msg)
        logger.info("EMAIL_SUCCESS type=%s recipient=%s subject=%s", email_context, recipient, subject)
        return True
    except Exception as exc:
        logger.exception(
            "EMAIL_FAILED type=%s recipient=%s subject=%s error_type=%s error=%s",
            email_context,
            recipient,
            subject,
            type(exc).__name__,
            str(exc),
        )
        return False


def envoyer_email_ecole(ecole_id, to, sujet, corps):
    """
    Envoie un email au nom de l'école via son compte Gmail connecté (OAuth 2.0).
    Lève SchoolMailNotConfiguredError si le Gmail de l'établissement n'est pas connecté.
    """
    from app.services.google_mail import send_school_email

    return send_school_email(ecole_id, to, sujet, corps)
