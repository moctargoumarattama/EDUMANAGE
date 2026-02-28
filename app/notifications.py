import smtplib
from email.mime.text import MIMEText
import requests
import logging
import io

# app/notifications.py

# ========================
# Configuration
# ========================
TELEGRAM_BOT_TOKEN = "8029594293:AAHlPe-14bhZUw9MHM9IgGcBbVDBm4wAU9M"
TELEGRAM_CHAT_ID = "6724470801"

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

# ========================
# Telegram
# ========================
def envoyer_telegram(message, chat_id=TELEGRAM_CHAT_ID):
    """Envoie un message Telegram via un bot."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=5)
        if r.status_code == 200:
            logger.info("Message Telegram envoyé")
        else:
            logger.warning(f"Erreur Telegram: {r.text}")
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Exception Telegram: {e}")
        return False

def envoyer_telegram_image(image_buffer, caption="", chat_id=TELEGRAM_CHAT_ID):
    """
    Envoie une image via Telegram en utilisant un buffer en mémoire.
    image_buffer : BytesIO contenant l'image PNG
    caption : texte optionnel à afficher avec l'image
    """
    try:
        image_buffer.seek(0)  # s'assurer que le buffer est au début
        files = {'photo': ('qr.png', image_buffer)}
        data = {'chat_id': chat_id, 'caption': caption}
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        r = requests.post(url, files=files, data=data, timeout=10)
        if r.status_code == 200:
            logger.info("Image Telegram envoyée")
        else:
            logger.warning(f"Erreur Telegram (image): {r.text}")
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Exception Telegram (image): {e}")
        return False
