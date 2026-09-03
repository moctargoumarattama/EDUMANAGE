import smtplib
from email.mime.text import MIMEText
import logging

# app/notifications.py

# Configurer le logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================
# Email
# ========================
def envoyer_email(to, sujet, corps):
    """Envoie un email via SMTP Gmail."""
    try:
        msg = MIMEText(corps, "html")  # HTML pour afficher le QR code
        msg['Subject'] = sujet
        msg['From'] = "moctargoumarattama@gmail.com"
        msg['To'] = to

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login("moctargoumarattama@gmail.com", "pmcfedouowvrtztx")  # mot de passe d'application Gmail
        server.send_message(msg)
        server.quit()

        logger.info(f"Email envoyé à {to}")
        return True
    except Exception as e:
        logger.error(f"Erreur Email: {e}")
        return False
