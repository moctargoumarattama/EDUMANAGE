import os


class ConfigError(RuntimeError):
    pass


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Configuration commune de l'application Flask."""

    APP_ENV = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    DEBUG = False
    TESTING = False

    # Securite
    SECRET_KEY = os.environ.get("SECRET_KEY", "ma_cle_ultra_secrete")
    SECURITY_PASSWORD_SALT = os.environ.get("SECURITY_PASSWORD_SALT", "mon_salt_securise")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Base de donnees
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///ecole.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}

    # Protection anti-abus
    RATELIMIT_HEADERS_ENABLED = True
    REDIS_URL = os.environ.get("REDIS_URL")
    RATELIMIT_STORAGE_URI = REDIS_URL or "memory://"

    # Cache optionnel. Aucune donnee metier sensible n'est cachee par defaut.
    CACHE_TYPE = "RedisCache" if REDIS_URL else "SimpleCache"
    CACHE_REDIS_URL = REDIS_URL
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", "300"))

    # Uploads
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 20 * 1024 * 1024))

    # Flask-Mail conserve pour compatibilite, hors perimetre de cette phase.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_USE_SSL = _env_bool("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)

    # Support KLASORA
    SUPPORT_WHATSAPP_NUMBER = os.environ.get("SUPPORT_WHATSAPP_NUMBER", "212770010264")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "moctargoumarattama@gmail.com")

    # Divers
    VERSION = "2.2.0"
    SEND_FILE_MAX_AGE_DEFAULT = 2592000
    USE_PROXY_FIX = False

    @classmethod
    def validate(cls):
        return True


class DevelopmentConfig(Config):
    APP_ENV = "development"
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


class ProductionConfig(Config):
    APP_ENV = "production"
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    USE_PROXY_FIX = True

    @classmethod
    def validate(cls):
        weak_values = {
            "",
            "ma_cle_ultra_secrete",
            "change-me",
            "changer-cette-cle-secrete-en-production",
        }
        secret = os.environ.get("SECRET_KEY", "")
        if secret in weak_values or len(secret) < 32:
            raise ConfigError("SECRET_KEY de production absente ou trop faible.")
        return True


class TestingConfig(Config):
    APP_ENV = "testing"
    TESTING = True
    DEBUG = False
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


def get_config():
    env = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    if env in {"prod", "production"}:
        return ProductionConfig
    if env in {"test", "testing"}:
        return TestingConfig
    return DevelopmentConfig
