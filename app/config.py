# app/config.py
import os

class Config:
    """Configuration principale de l'application Flask"""

    # --- Sécurité ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "ma_cle_ultra_secrete")
    SECURITY_PASSWORD_SALT = os.environ.get("SECURITY_PASSWORD_SALT", "mon_salt_securise")

    # --- Base de données ---
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///ecole.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}

    # --- Protection anti-abus ---
    RATELIMIT_HEADERS_ENABLED = True

    # --- Flask-Mail (Gmail) ---
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True") == "True"
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "False") == "True"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)

    # --- Support KLASORA ---
    SUPPORT_WHATSAPP_NUMBER = os.environ.get("SUPPORT_WHATSAPP_NUMBER", "212770010264")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "moctargoumarattama@gmail.com")

    # --- Divers ---
    VERSION = "2.2.0"
    SEND_FILE_MAX_AGE_DEFAULT = 2592000  # 30 jours de cache pour les fichiers statiques
